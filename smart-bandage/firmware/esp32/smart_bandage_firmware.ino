/*
 * Smart Bandage ESP32 firmware -- Phase 7 (Blueprint §10: "ESP32 + AFE +
 * electrodes: acquisition, multiplexing, calibration, BLE, device health,
 * packet protocol").
 *
 * NOT COMPILED, FLASHED, OR RUN ON HARDWARE. Written to the packet spec in
 * docs/hardware/packet_protocol.md against the NimBLE-Arduino API, but
 * there is no ESP32 device, Arduino/ESP-IDF toolchain, or AFE part in the
 * environment this was authored in to build or bench-test it against.
 * Treat this as a first draft to compile-check once Phase 7 hardware
 * exists (docs/architecture/overview.md: Phases 7-9 all need the physical
 * electrode and ESP32 to exist first) -- not as verified firmware.
 *
 * What downstream depends on, and doesn't: the backend (Phase 4) and
 * processing pipeline (Phase 3) only ever see bytes matching
 * firmware/drivers/packet.h over the BLE characteristic below, decoded by
 * common/protocol/packet.py -- nothing about *how* this sketch produces
 * those bytes is a contract anyone else codes against (Blueprint §3).
 */
#include <NimBLEDevice.h>

#include "../drivers/afe_driver.h"
#include "../drivers/packet.h"

// --- configuration -----------------------------------------------------

#define DEVICE_ID "SB-001"          // one physical unit per firmware build for now
#define NUM_CHANNELS 2
static const char *CHANNEL_IDS[NUM_CHANNELS] = {"CH-01", "CH-02"};

#define SAMPLE_INTERVAL_MS 1000     // ~1 Hz, matches the simulator's default tick (Blueprint §7)
#define BLE_SERVICE_UUID "b5a3f000-0001-4a4b-9c1e-6f2d1a7e0a01"
#define BLE_MEASUREMENT_CHAR_UUID "b5a3f000-0002-4a4b-9c1e-6f2d1a7e0a01"
#define BLE_TIME_SYNC_CHAR_UUID "b5a3f000-0003-4a4b-9c1e-6f2d1a7e0a01"

// --- state ---------------------------------------------------------------

static NimBLECharacteristic *measurementChar = nullptr;
static NimBLECharacteristic *timeSyncChar = nullptr;
static volatile bool deviceConnected = false;

// Wall-clock time isn't battery-backed on this board -- the gateway
// (phone/backend BLE client) writes the current unix time once on
// connect, and this offset lets millis() free-run as wall-clock time
// after that. See docs/hardware/packet_protocol.md, "why u32 unix seconds".
static uint32_t timeSyncUnixSeconds = 0;
static uint32_t timeSyncAtMillis = 0;
static bool timeSynced = false;

static uint32_t currentUnixSeconds() {
    if (!timeSynced) {
        return 0;  // pre-sync: packets still send, timestamped 0 -- gateway
                   // should treat timestamp==0 as "device clock not yet synced"
                   // rather than a real 1970 reading
    }
    return timeSyncUnixSeconds + (millis() - timeSyncAtMillis) / 1000;
}

// --- BLE callbacks -------------------------------------------------------

class ServerCallbacks : public NimBLEServerCallbacks {
    void onConnect(NimBLEServer *server, NimBLEConnInfo &connInfo) override {
        deviceConnected = true;
    }
    void onDisconnect(NimBLEServer *server, NimBLEConnInfo &connInfo, int reason) override {
        deviceConnected = false;
        NimBLEDevice::startAdvertising();  // resume advertising so the gateway can reconnect
    }
};

class TimeSyncCallbacks : public NimBLECharacteristicCallbacks {
    void onWrite(NimBLECharacteristic *characteristic, NimBLEConnInfo &connInfo) override {
        std::string value = characteristic->getValue();
        if (value.size() != sizeof(uint32_t)) return;
        memcpy(&timeSyncUnixSeconds, value.data(), sizeof(uint32_t));
        timeSyncAtMillis = millis();
        timeSynced = true;
    }
};

// --- setup / loop ----------------------------------------------------------

void setupBLE() {
    NimBLEDevice::init(DEVICE_ID);
    NimBLEServer *server = NimBLEDevice::createServer();
    server->setCallbacks(new ServerCallbacks());

    NimBLEService *service = server->createService(BLE_SERVICE_UUID);

    measurementChar = service->createCharacteristic(
        BLE_MEASUREMENT_CHAR_UUID,
        NIMBLE_PROPERTY::NOTIFY
    );

    timeSyncChar = service->createCharacteristic(
        BLE_TIME_SYNC_CHAR_UUID,
        NIMBLE_PROPERTY::WRITE
    );
    timeSyncChar->setCallbacks(new TimeSyncCallbacks());

    service->start();

    NimBLEAdvertising *advertising = NimBLEDevice::getAdvertising();
    advertising->addServiceUUID(BLE_SERVICE_UUID);
    advertising->start();
}

void setup() {
    Serial.begin(115200);

    if (!sb_packet_selftest()) {
        // struct packing didn't match the spec -- refuse to run rather than
        // silently send corrupt packets (docs/hardware/packet_protocol.md)
        Serial.println("FATAL: sb_packet_t size mismatch, check compiler struct packing");
        while (true) { delay(1000); }
    }

    sb_afe_status_t afe_status = sb_afe_init();
    if (afe_status != SB_AFE_OK) {
        Serial.printf("FATAL: AFE init failed, status=%d\n", afe_status);
        while (true) { delay(1000); }
    }

    setupBLE();
}

// One channel's worth of acquisition + packet send. Device health (battery,
// AFE error) rides in every packet via packet.h's optional fields rather
// than a separate health message -- one code path, matching how
// common/schemas puts battery/temperature directly on RawMeasurement.
void sampleAndSendChannel(uint8_t channelIndex) {
    sb_afe_status_t status = sb_afe_select_channel(channelIndex);
    if (status != SB_AFE_OK) {
        Serial.printf("channel %u select failed, status=%d\n", channelIndex, status);
        return;
    }

    float rawSignal = 0.0f;
    status = sb_afe_read_raw(&rawSignal);
    if (status != SB_AFE_OK) {
        Serial.printf("channel %u read failed, status=%d\n", channelIndex, status);
        return;  // skip this tick's packet for this channel rather than send garbage
    }

    float temperature = 0.0f;
    bool haveTemperature = (sb_afe_read_temperature(&temperature) == SB_AFE_OK);

    int16_t batteryPct = 0;
    bool haveBattery = (sb_afe_read_battery_pct(&batteryPct) == SB_AFE_OK);

    sb_packet_t packet;
    sb_packet_encode(
        &packet,
        DEVICE_ID,
        CHANNEL_IDS[channelIndex],
        currentUnixSeconds(),
        rawSignal,
        haveTemperature ? &temperature : nullptr,
        haveBattery ? &batteryPct : nullptr
    );

    if (deviceConnected && measurementChar != nullptr) {
        measurementChar->setValue((uint8_t *)&packet, sizeof(packet));
        measurementChar->notify();
    }
    // not connected: reading is simply not sent this tick. A production
    // build would ring-buffer a few packets here and flush on reconnect;
    // left as a Phase 7 hardware-bench TODO, not simulable without a real
    // BLE stack's actual reconnect timing.
}

void loop() {
    static uint32_t lastSampleAt = 0;
    uint32_t now = millis();
    if (now - lastSampleAt < SAMPLE_INTERVAL_MS) {
        return;
    }
    lastSampleAt = now;

    for (uint8_t i = 0; i < NUM_CHANNELS; i++) {
        sampleAndSendChannel(i);
    }
}
