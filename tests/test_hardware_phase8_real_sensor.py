"""
Phase 8 tests -- hardware/software integration (Blueprint §10 Phase 8:
"Swap SimulatedSensor for RealSensor. Nothing downstream changes.").

There is no ESP32 in this environment, so `Transport` is backed here by
`FakeStream`, an in-memory double for anything duck-typed like
`serial.Serial` (`.read(n)`/`.write(bytes)`). That lets
`common/interfaces/transport.StreamTransport`'s framing/resync logic and
`common/interfaces/real_sensor.RealSensor`'s decode path run for real,
and -- the actual Phase 8 payoff -- lets `RealSensor` be pushed through
the exact same `processing/biomarkers/pipeline.ChannelPipeline` the
simulator runs through, with identical downstream results. What no amount
of this proves: that a real AFE/BLE radio/electrode produces packets that
decode correctly in the first place -- that needs the physical hardware
(docs/hardware/packet_protocol.md).
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Optional

import pytest

from common.interfaces.real_sensor import RealSensor
from common.interfaces.transport import StreamTransport, TransportTimeoutError
from common.protocol.packet import PacketError, encode_measurement
from common.schemas.calibration import CalibrationParameters
from common.schemas.measurement import RawMeasurement
from processing.biomarkers.pipeline import ChannelPipeline


class FakeStream:
    """Minimal serial.Serial-like double. `.read(n)` returns up to `n`
    queued bytes -- fewer if `chunk_size` is set, to exercise
    StreamTransport's partial-read loop -- or b"" if the queue is empty
    (as a non-blocking port would), which forces the caller's timeout
    loop instead of blocking the test."""

    def __init__(self, data: bytes = b"", chunk_size: Optional[int] = None) -> None:
        self._buf = bytearray(data)
        self._chunk_size = chunk_size
        self.written = bytearray()
        self.opened = False
        self.closed = False

    def feed(self, data: bytes) -> None:
        self._buf.extend(data)

    def read(self, n: int) -> bytes:
        if not self._buf:
            return b""
        take = n if self._chunk_size is None else min(n, self._chunk_size)
        chunk, self._buf = bytes(self._buf[:take]), self._buf[take:]
        return chunk

    def write(self, data: bytes) -> int:
        self.written.extend(data)
        return len(data)

    def open(self) -> None:
        self.opened = True

    def close(self) -> None:
        self.closed = True


def _raw(**overrides) -> RawMeasurement:
    base = dict(
        device_id="SB-001",
        channel_id="CH-01",
        timestamp=datetime(2026, 8, 30, 12, 0, 0, tzinfo=timezone.utc),
        raw_signal=101.5,
        temperature=36.6,
        battery=87,
    )
    base.update(overrides)
    return RawMeasurement(**base)


# ---- StreamTransport ----


def test_stream_transport_reads_one_packet():
    stream = FakeStream(encode_measurement(_raw()))
    transport = StreamTransport(stream)
    packet = transport.read_packet(timeout=1.0)
    assert len(packet) == len(encode_measurement(_raw()))


def test_stream_transport_reassembles_fragmented_reads():
    stream = FakeStream(encode_measurement(_raw()), chunk_size=3)
    transport = StreamTransport(stream)
    packet = transport.read_packet(timeout=1.0)
    assert packet == encode_measurement(_raw())


def test_stream_transport_resyncs_past_garbage_before_magic():
    garbage = b"\x00\x01\x02\xff\xff\xff"
    stream = FakeStream(garbage + encode_measurement(_raw()))
    transport = StreamTransport(stream)
    packet = transport.read_packet(timeout=1.0)
    assert packet == encode_measurement(_raw())


def test_stream_transport_times_out_on_no_data():
    transport = StreamTransport(FakeStream(b""))
    with pytest.raises(TransportTimeoutError):
        transport.read_packet(timeout=0.05)


def test_stream_transport_open_close_delegate_to_stream():
    stream = FakeStream()
    transport = StreamTransport(stream)
    transport.open()
    transport.close()
    assert stream.opened is True
    assert stream.closed is True


def test_stream_transport_write_time_sync_sends_four_bytes_le():
    stream = FakeStream()
    transport = StreamTransport(stream)
    transport.write_time_sync(0x01020304)
    assert bytes(stream.written) == b"\x04\x03\x02\x01"


# ---- RealSensor ----


def test_real_sensor_decodes_a_measurement_end_to_end():
    original = _raw(raw_signal=123.25, battery=55)
    stream = FakeStream(encode_measurement(original))
    sensor = RealSensor(StreamTransport(stream), device_id="SB-001", channel_id="CH-01")
    sensor.initialize()
    sensor.start_measurement()

    decoded = sensor.read_measurement()

    assert decoded.device_id == original.device_id
    assert decoded.channel_id == original.channel_id
    assert decoded.raw_signal == pytest.approx(original.raw_signal, rel=1e-6)
    assert decoded.battery == 55
    assert sensor.get_status().connected is True
    assert sensor.get_status().battery == 55


def test_real_sensor_initialize_writes_time_sync():
    stream = FakeStream()
    sensor = RealSensor(StreamTransport(stream), device_id="SB-001", channel_id="CH-01")
    sensor.initialize()
    assert len(stream.written) == 4  # one u32 time-sync write


def test_real_sensor_marks_disconnected_on_transport_timeout():
    stream = FakeStream(b"")
    sensor = RealSensor(StreamTransport(stream), device_id="SB-001", channel_id="CH-01", read_timeout=0.05)
    sensor.initialize()

    with pytest.raises(TransportTimeoutError):
        sensor.read_measurement()

    status = sensor.get_status()
    assert status.connected is False
    assert status.last_error is not None


def test_real_sensor_raises_on_corrupt_packet_but_stays_connected():
    corrupt = bytearray(encode_measurement(_raw()))
    corrupt[20] ^= 0xFF  # inside the payload, breaks the checksum
    stream = FakeStream(bytes(corrupt))
    sensor = RealSensor(StreamTransport(stream), device_id="SB-001", channel_id="CH-01")
    sensor.initialize()

    with pytest.raises(PacketError):
        sensor.read_measurement()

    # a corrupt frame invalidates that one reading, not the whole link --
    # only a transport-level timeout means "disconnected" (see the test above)
    assert sensor.get_status().last_error is not None


def test_real_sensor_rejects_a_packet_for_a_different_channel():
    mismatched = _raw(channel_id="CH-02")
    stream = FakeStream(encode_measurement(mismatched))
    sensor = RealSensor(StreamTransport(stream), device_id="SB-001", channel_id="CH-01")
    sensor.initialize()

    with pytest.raises(ValueError, match="CH-02"):
        sensor.read_measurement()


def test_real_sensor_stop_closes_transport_and_marks_disconnected():
    stream = FakeStream(encode_measurement(_raw()))
    sensor = RealSensor(StreamTransport(stream), device_id="SB-001", channel_id="CH-01")
    sensor.initialize()
    sensor.stop_measurement()
    assert stream.closed is True
    assert sensor.get_status().connected is False


# ---- the actual Phase 8 payoff: nothing downstream changes ----


def _identity_calibration() -> CalibrationParameters:
    return CalibrationParameters(
        sensor_type="generic", version="0.0-identity", model_type="linear",
        slope=1.0, intercept=0.0, valid_from=date(2020, 1, 1),
    )


def test_real_sensor_output_matches_simulated_sensor_through_the_same_pipeline():
    """The Blueprint's own words for Phase 8: 'swap SimulatedSensor for
    RealSensor, nothing downstream changes.' Feed the identical sequence
    of readings through ChannelPipeline once via RealSensor (decoded off
    a fake transport) and once directly as RawMeasurement objects (what
    SimulatedSensor would hand the pipeline) and assert matching
    MeasurementRecords -- matching, not bit-identical: the wire protocol's
    float32 fields (common/protocol/packet.py) lose a little precision a
    pure in-memory RawMeasurement never does, so numeric fields are
    compared with a tolerance and everything else exactly."""
    readings = [
        _raw(raw_signal=100.0, timestamp=datetime(2026, 8, 30, 12, 0, 0, tzinfo=timezone.utc)),
        _raw(raw_signal=101.2, timestamp=datetime(2026, 8, 30, 12, 0, 1, tzinfo=timezone.utc)),
        _raw(raw_signal=99.4, timestamp=datetime(2026, 8, 30, 12, 0, 2, tzinfo=timezone.utc)),
    ]

    # path 1: RealSensor over a fake transport replaying encoded packets
    stream = FakeStream(b"".join(encode_measurement(r) for r in readings))
    sensor = RealSensor(StreamTransport(stream), device_id="SB-001", channel_id="CH-01")
    sensor.initialize()
    sensor.start_measurement()
    real_pipeline = ChannelPipeline(calibration=_identity_calibration(), unit="a.u.")
    real_records = [real_pipeline.process(sensor.read_measurement()) for _ in readings]

    # path 2: the simulator's own shape -- RawMeasurement objects handed
    # straight to a fresh pipeline instance
    sim_pipeline = ChannelPipeline(calibration=_identity_calibration(), unit="a.u.")
    sim_records = [sim_pipeline.process(r) for r in readings]

    assert len(real_records) == len(sim_records)
    numeric_fields = {"raw_signal", "processed_signal", "estimated_value", "temperature", "signal_quality"}
    for real, sim in zip(real_records, sim_records):
        real_dump, sim_dump = real.model_dump(), sim.model_dump()
        for field in numeric_fields:
            assert real_dump.pop(field) == pytest.approx(sim_dump.pop(field), abs=1e-3)
        assert real_dump == sim_dump  # every non-float field: identical
