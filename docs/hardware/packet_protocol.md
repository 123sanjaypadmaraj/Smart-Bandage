# Packet protocol (Phase 7)

The wire format one `RawMeasurement` takes from the ESP32 to the backend.
Defined once, in Python (`common/protocol/packet.py`, tested by
`tests/test_firmware_phase7_protocol.py`), and mirrored byte-for-byte in C
(`firmware/drivers/packet.h`). **Change both together or not at all.**

Fixed-size, little-endian, 41 bytes, one measurement per packet:

| Bytes | Field | Type | Notes |
|---|---|---|---|
| 0 | `magic` | `u8` | always `0xA5` — lets a receiver resync after a dropped byte |
| 1 | `version` | `u8` | `1` — bump on any layout change, both sides must agree |
| 2–13 | `device_id` | `char[12]` | ASCII, NUL-padded, e.g. `"SB-001"` |
| 14–25 | `channel_id` | `char[12]` | ASCII, NUL-padded, e.g. `"CH-01"` |
| 26–29 | `timestamp` | `u32` | unix seconds, UTC (whole-second precision only) |
| 30–33 | `raw_signal` | `f32` | unprocessed electrochemical/ADC reading |
| 34–37 | `temperature` | `f32` | thermistor reading; ignored unless `flags` bit 0 is set |
| 38 | `battery` | `u8` | percent 0–100; `0xFF` means "not reported" |
| 39 | `flags` | `u8` | bit0 = has_temperature, bit1 = has_battery |
| 40 | `checksum` | `u8` | sum of bytes 0–39, mod 256 |

## Why this shape

- **Fixed size** — no length prefix or delimiter to get wrong on the
  firmware side; a receiver just reads exactly `PACKET_SIZE` bytes.
- **`magic` + `checksum`, not a CRC** — a wearable BLE link drops whole
  packets far more often than it flips a single bit inside one; a cheap
  additive checksum catches "this frame is garbage" without spending
  ESP32 cycles on a real CRC. If Phase 9 field data shows silent bit
  corruption is actually a problem, swap in CRC-8 without changing the
  rest of the layout (still 1 byte).
- **Absent fields via `flags`, not a sentinel float** — `temperature`
  packs as `0.0` when absent rather than `NaN`/`-999`, because a sentinel
  float is one more thing to get wrong on both sides; `flags` bit 0 is
  the single source of truth for "is this reading present."
- **`u32` unix seconds** — the ESP32 doesn't have a real-time clock;
  Phase 7's BLE handshake syncs it to the phone/gateway's clock at
  connect time (see `firmware/esp32/smart_bandage_firmware.ino`), then
  free-runs off `millis()`. Sub-second precision isn't meaningful at a
  ~1 Hz sample rate, so it's not spent on wire bytes.

## What's tested vs. what needs hardware

`common/protocol/packet.py` — encode, decode, round-trip, and every
rejection path (bad length, bad magic/version, checksum mismatch,
oversized id, out-of-range battery) — is real and passes under `pytest`
with zero hardware involved; it's the half `common/interfaces/real_sensor.py`
(Phase 8) actually depends on.

`firmware/drivers/packet.h` and `firmware/esp32/smart_bandage_firmware.ino`
implement the identical layout in C for the ESP32 side, but **have not
been compiled or flashed** — there's no Arduino/ESP-IDF toolchain or
device in this environment. They're written to the spec above and ready
to build against once Phase 7 hardware exists; treat them as a first
draft to compile-check and bench-test, not as verified firmware.
