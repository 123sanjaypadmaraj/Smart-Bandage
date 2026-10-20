/*
 * Phase 7 analog front-end (AFE) driver interface.
 *
 * NOT COMPILED OR VALIDATED AGAINST REAL SILICON -- no AFE part number has
 * been selected yet (Blueprint: electrode + aptamer hardware doesn't
 * exist). This header defines the *shape* the ESP32 firmware codes
 * against (Blueprint §3's hardware-abstraction principle, applied on the
 * firmware side the same way common/interfaces/sensor_interface.py
 * applies it on the software side) so afe_driver.cpp can be swapped for
 * whatever part gets chosen without touching smart_bandage_firmware.ino.
 *
 * Multiplexing note: one physical AFE channel is read per electrode
 * (Blueprint §7 "multi-channel"); sb_afe_select_channel() switches an
 * analog mux ahead of a single ADC rather than assuming one ADC per
 * channel, since that's the cheaper BOM for a disposable/low-power
 * wearable -- adjust if the chosen AFE has its own per-channel ADCs.
 */
#ifndef SMART_BANDAGE_AFE_DRIVER_H
#define SMART_BANDAGE_AFE_DRIVER_H

#include <stdint.h>

typedef enum {
    SB_AFE_OK = 0,
    SB_AFE_ERR_NOT_INITIALIZED,
    SB_AFE_ERR_I2C_TIMEOUT,
    SB_AFE_ERR_CHANNEL_OUT_OF_RANGE,
} sb_afe_status_t;

/* One-time bring-up: configure the I2C/SPI bus, reset the AFE, verify its
 * device ID register. Returns SB_AFE_OK or an error -- the caller
 * (setup() in the main sketch) should refuse to start_measurement() on
 * failure and instead blink an error pattern / report over BLE. */
sb_afe_status_t sb_afe_init(void);

/* Switches the analog mux ahead of the shared ADC to `channel_index`
 * (0-based). Must settle before the next sb_afe_read_raw() call --
 * implementations should include whatever mux settling delay the chosen
 * part's datasheet specifies. */
sb_afe_status_t sb_afe_select_channel(uint8_t channel_index);

/* One raw ADC read from whichever channel is currently selected.
 * `out_raw_signal` is the *unprocessed* reading -- no filtering,
 * calibration, or unit conversion here, matching RawMeasurement.raw_signal
 * (common/schemas/measurement.py) exactly: that conversion is Phase 3's
 * job (processing/), running on the backend, not the microcontroller. */
sb_afe_status_t sb_afe_read_raw(float *out_raw_signal);

/* On-board thermistor, independent of the AFE channel currently selected. */
sb_afe_status_t sb_afe_read_temperature(float *out_temperature_celsius);

/* Battery percent from the fuel-gauge/ADC divider, 0-100. */
sb_afe_status_t sb_afe_read_battery_pct(int16_t *out_battery_pct);

#endif  // SMART_BANDAGE_AFE_DRIVER_H
