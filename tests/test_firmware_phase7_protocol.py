"""
Phase 7 tests -- the wire packet protocol (common/protocol/packet.py) that
firmware/drivers/packet.h mirrors in C. This is the one piece of Phase 7
("embedded firmware") that's actually testable without an ESP32: the byte
layout both sides agree to speak. See that module's docstring, and
docs/hardware/packet_protocol.md, for what firmware/ itself needs real
hardware to validate.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from common.protocol.packet import (
    PACKET_SIZE,
    PacketError,
    PacketFields,
    decode_packet,
    decode_to_measurement,
    encode_measurement,
    encode_packet,
)
from common.schemas.measurement import RawMeasurement


def _fields(**overrides) -> PacketFields:
    base = dict(
        device_id="SB-001",
        channel_id="CH-01",
        timestamp=datetime(2026, 8, 30, 12, 0, 0, tzinfo=timezone.utc),
        raw_signal=123.456,
        temperature=36.7,
        battery=87,
    )
    base.update(overrides)
    return PacketFields(**base)


def test_packet_has_a_fixed_size():
    packet = encode_packet(_fields())
    assert len(packet) == PACKET_SIZE == 41


def test_round_trip_preserves_all_fields():
    original = _fields()
    decoded = decode_packet(encode_packet(original))

    assert decoded.device_id == original.device_id
    assert decoded.channel_id == original.channel_id
    assert decoded.timestamp == original.timestamp  # whole-second precision, matches the fixture
    assert decoded.raw_signal == pytest.approx(original.raw_signal, rel=1e-6)
    assert decoded.temperature == pytest.approx(original.temperature, rel=1e-6)
    assert decoded.battery == original.battery


def test_round_trip_truncates_timestamp_to_whole_seconds():
    original = _fields(timestamp=datetime(2026, 8, 30, 12, 0, 0, 123456, tzinfo=timezone.utc))
    decoded = decode_packet(encode_packet(original))
    assert decoded.timestamp == original.timestamp.replace(microsecond=0)


def test_absent_temperature_and_battery_round_trip_as_none():
    original = _fields(temperature=None, battery=None)
    decoded = decode_packet(encode_packet(original))
    assert decoded.temperature is None
    assert decoded.battery is None


def test_zero_battery_is_not_confused_with_absent():
    original = _fields(battery=0)
    decoded = decode_packet(encode_packet(original))
    assert decoded.battery == 0


def test_device_id_longer_than_field_is_rejected():
    with pytest.raises(PacketError):
        encode_packet(_fields(device_id="SB-WAY-TOO-LONG-FOR-THE-FIELD"))


def test_out_of_range_battery_is_rejected():
    with pytest.raises(PacketError):
        encode_packet(_fields(battery=150))


def test_wrong_length_is_rejected():
    with pytest.raises(PacketError):
        decode_packet(b"\x00" * 10)


def test_corrupted_byte_fails_checksum():
    packet = bytearray(encode_packet(_fields()))
    packet[10] ^= 0xFF  # flip a bit inside the device_id/channel_id region
    with pytest.raises(PacketError, match="checksum"):
        decode_packet(bytes(packet))


def test_bad_magic_is_rejected():
    packet = bytearray(encode_packet(_fields()))
    packet[0] = 0x00
    # recompute the checksum so this test isolates the magic-byte check,
    # not an incidental checksum failure from tampering with byte 0
    from common.protocol.packet import _checksum  # noqa: PLC0415 - test-only peek at the internal helper

    packet[-1] = _checksum(bytes(packet[:-1]))
    with pytest.raises(PacketError, match="magic"):
        decode_packet(bytes(packet))


def test_encode_measurement_and_decode_to_measurement_round_trip():
    raw = RawMeasurement(
        device_id="SB-001",
        channel_id="CH-01",
        timestamp=datetime(2026, 8, 30, 12, 0, 0, tzinfo=timezone.utc),
        raw_signal=101.5,
        temperature=36.6,
        battery=42,
    )
    decoded = decode_to_measurement(encode_measurement(raw))

    assert isinstance(decoded, RawMeasurement)
    assert decoded.device_id == raw.device_id
    assert decoded.channel_id == raw.channel_id
    assert decoded.timestamp == raw.timestamp
    assert decoded.raw_signal == pytest.approx(raw.raw_signal, rel=1e-6)
    assert decoded.temperature == pytest.approx(raw.temperature, rel=1e-6)
    assert decoded.battery == raw.battery
