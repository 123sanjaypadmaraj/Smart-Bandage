"""
Phase 7 wire packet protocol (Blueprint §10 Phase 7: "packet protocol").

The canonical spec for how one `RawMeasurement` crosses the wire from real
hardware (ESP32 -> BLE notification, Blueprint Phase 7/8) is defined here,
in Python -- which makes it testable without a device: encode a packet,
decode it back, assert the round-trip and that corruption is caught.
`firmware/drivers/packet.h` implements the *identical* byte layout in C
for the ESP32 side (`docs/hardware/packet_protocol.md` has the field
table); the two must change together. There is no way to unit-test the C
side without real hardware -- `tests/test_firmware_phase7_protocol.py`
pins this Python side, which is the half Phase 8's `RealSensor` actually
depends on (see common/interfaces/real_sensor.py).

Fixed-size, little-endian, one measurement per packet -- simple enough to
encode correctly on a microcontroller, small enough (41 bytes) to fit one
BLE notification once MTU is negotiated above the default 23-byte ATT MTU
(standard on ESP32's NimBLE stack).
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from common.schemas.measurement import RawMeasurement

MAGIC = 0xA5
VERSION = 1

ID_FIELD_LEN = 12  # bytes, ASCII, NUL-padded -- fits "SB-001", "pathogen_channel_1"-style short ids
BATTERY_ABSENT = 0xFF  # battery is 0-100; 0xFF marks "not reported" instead of a bool flag byte alone

_FLAG_HAS_TEMPERATURE = 0b01
_FLAG_HAS_BATTERY = 0b10

# magic(u8) version(u8) device_id(12s) channel_id(12s) timestamp(u32, unix
# seconds) raw_signal(f32) temperature(f32) battery(u8) flags(u8) checksum(u8)
_STRUCT = struct.Struct("<BB12s12sIffBBB")
PACKET_SIZE = _STRUCT.size  # 41 bytes


class PacketError(ValueError):
    """Malformed packet: wrong length, bad magic/version, or a checksum
    that doesn't match -- a corrupted BLE notification or serial frame."""


@dataclass(frozen=True)
class PacketFields:
    """What the wire format actually carries. `encode_measurement` /
    `decode_to_measurement` below are the usual entry points -- these
    lower-level functions exist mainly so tests can construct an invalid
    packet on purpose."""

    device_id: str
    channel_id: str
    timestamp: datetime
    raw_signal: float
    temperature: Optional[float]
    battery: Optional[int]


def _pad_id(value: str, field_name: str) -> bytes:
    try:
        encoded = value.encode("ascii")
    except UnicodeEncodeError as exc:
        raise PacketError(f"{field_name} {value!r} must be ASCII") from exc
    if len(encoded) > ID_FIELD_LEN:
        raise PacketError(f"{field_name} {value!r} is longer than {ID_FIELD_LEN} bytes")
    return encoded.ljust(ID_FIELD_LEN, b"\x00")


def _checksum(body: bytes) -> int:
    """Sum-mod-256 -- not cryptographic, just cheap enough to run on an
    ESP32 per packet and catch a corrupted frame."""
    return sum(body) & 0xFF


def encode_packet(fields: PacketFields) -> bytes:
    flags = 0
    temperature = fields.temperature if fields.temperature is not None else 0.0
    if fields.temperature is not None:
        flags |= _FLAG_HAS_TEMPERATURE

    battery = fields.battery if fields.battery is not None else BATTERY_ABSENT
    if fields.battery is not None:
        if not (0 <= fields.battery <= 100):
            raise PacketError(f"battery {fields.battery} out of range 0-100")
        flags |= _FLAG_HAS_BATTERY

    epoch_seconds = int(fields.timestamp.astimezone(timezone.utc).timestamp())
    if not (0 <= epoch_seconds <= 0xFFFFFFFF):
        raise PacketError(f"timestamp {fields.timestamp!r} outside the u32 unix-seconds range")

    body = _STRUCT.pack(
        MAGIC,
        VERSION,
        _pad_id(fields.device_id, "device_id"),
        _pad_id(fields.channel_id, "channel_id"),
        epoch_seconds,
        fields.raw_signal,
        temperature,
        battery,
        flags,
        0,  # checksum placeholder -- overwritten below once the rest is known
    )
    checksum = _checksum(body[:-1])
    return body[:-1] + bytes([checksum])


def decode_packet(data: bytes) -> PacketFields:
    if len(data) != PACKET_SIZE:
        raise PacketError(f"expected a {PACKET_SIZE}-byte packet, got {len(data)}")

    if _checksum(data[:-1]) != data[-1]:
        raise PacketError("checksum mismatch -- corrupted packet")

    magic, version, device_id_raw, channel_id_raw, epoch_seconds, raw_signal, temperature, battery, flags, _crc = (
        _STRUCT.unpack(data)
    )
    if magic != MAGIC:
        raise PacketError(f"bad magic byte: {magic:#x} (expected {MAGIC:#x})")
    if version != VERSION:
        raise PacketError(f"unsupported packet version {version} (this build speaks v{VERSION})")

    return PacketFields(
        device_id=device_id_raw.rstrip(b"\x00").decode("ascii"),
        channel_id=channel_id_raw.rstrip(b"\x00").decode("ascii"),
        timestamp=datetime.fromtimestamp(epoch_seconds, tz=timezone.utc),
        raw_signal=raw_signal,
        temperature=temperature if flags & _FLAG_HAS_TEMPERATURE else None,
        battery=battery if flags & _FLAG_HAS_BATTERY else None,
    )


# ---- common/schemas/measurement.RawMeasurement <-> wire bytes ----
# Kept as thin wrappers so nothing outside this module needs to know the
# wire format exists -- common/interfaces/real_sensor.py imports only these.


def encode_measurement(raw: RawMeasurement) -> bytes:
    return encode_packet(
        PacketFields(
            device_id=raw.device_id,
            channel_id=raw.channel_id,
            timestamp=raw.timestamp,
            raw_signal=raw.raw_signal,
            temperature=raw.temperature,
            battery=raw.battery,
        )
    )


def decode_to_measurement(data: bytes) -> RawMeasurement:
    fields = decode_packet(data)
    return RawMeasurement(
        device_id=fields.device_id,
        channel_id=fields.channel_id,
        timestamp=fields.timestamp,
        raw_signal=fields.raw_signal,
        temperature=fields.temperature,
        battery=fields.battery,
    )
