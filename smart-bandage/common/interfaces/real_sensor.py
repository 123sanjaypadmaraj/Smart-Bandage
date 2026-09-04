"""
Phase 8 deliverable, named literally in the Blueprint roadmap: "Swap
SimulatedSensor for RealSensor. Nothing downstream changes." This is that
swap -- same `SensorInterface` contract as
`simulator/sensors/scenario_sensor.py`, decoding
`common/protocol/packet.py` frames off a `Transport`
(common/interfaces/transport.py) instead of generating synthetic ones.

`tests/test_hardware_phase8_real_sensor.py` proves the "nothing downstream
changes" claim directly: it runs a `RealSensor` (backed by a fake, in-memory
transport replaying encoded packets -- there is no ESP32 in this
environment) through the exact same `processing.biomarkers.pipeline.ChannelPipeline`
`backend/app/simulation.py` runs against `SimulatedSensor`/`ScenarioSensor`,
and asserts identical output. What real hardware still has to prove on its
own: that a real AFE, real BLE radio, and a real electrode actually produce
packets this decodes correctly -- see docs/hardware/packet_protocol.md.
"""
from __future__ import annotations

import time
from typing import Optional

from common.interfaces.sensor_interface import SensorInterface
from common.interfaces.transport import Transport, TransportTimeoutError
from common.protocol.packet import PacketError, decode_to_measurement
from common.schemas.device import DeviceStatus
from common.schemas.measurement import RawMeasurement


class RealSensor(SensorInterface):
    """One physical channel, read over `transport`. One instance per
    (device_id, channel_id) -- same granularity as `ScenarioSensor`, so
    `MultiChannelSensor`-style composition (or a Phase 8 equivalent that
    demuxes one shared BLE link by channel_id) works unmodified."""

    def __init__(
        self,
        transport: Transport,
        device_id: str,
        channel_id: str,
        read_timeout: float = 2.0,
    ) -> None:
        self.transport = transport
        self.device_id = device_id
        self.channel_id = channel_id
        self.read_timeout = read_timeout
        self._connected = False
        self._last_error: Optional[str] = None
        self._last_battery: Optional[int] = None

    def initialize(self) -> None:
        self.transport.open()
        self.transport.write_time_sync(int(time.time()))
        self._connected = True
        self._last_error = None

    def start_measurement(self) -> None:
        # Nothing to arm: the device streams continuously once connected.
        # start/stop is a software-side session boundary here, not a
        # signal sent over the wire -- unlike ScenarioSensor, where it
        # resets a simulated read_index/t0.
        pass

    def read_measurement(self) -> RawMeasurement:
        try:
            packet = self.transport.read_packet(timeout=self.read_timeout)
        except TransportTimeoutError as exc:
            self._connected = False
            self._last_error = f"transport timeout: {exc}"
            raise

        try:
            raw = decode_to_measurement(packet)
        except PacketError as exc:
            # the link is still up -- just this one frame was corrupt --
            # so _connected is left alone; only the timeout path above
            # means "not connected"
            self._last_error = f"corrupt packet: {exc}"
            raise

        if raw.device_id != self.device_id or raw.channel_id != self.channel_id:
            # a shared transport carrying more than one channel (or device)
            # can hand back a packet that isn't this instance's -- reject
            # it rather than silently attribute a reading to the wrong
            # channel
            self._last_error = (
                f"packet for {raw.device_id}/{raw.channel_id}, expected {self.device_id}/{self.channel_id}"
            )
            raise ValueError(self._last_error)

        self._connected = True
        self._last_error = None
        if raw.battery is not None:
            self._last_battery = raw.battery
        return raw

    def stop_measurement(self) -> None:
        self.transport.close()
        self._connected = False

    def get_status(self) -> DeviceStatus:
        return DeviceStatus(
            connected=self._connected,
            battery=self._last_battery,
            # Real hardware doesn't hand back its own signal-quality figure
            # the way the simulator's electrode_degradation scenario does
            # (simulator/signals/generators.declining_quality) -- Phase 3's
            # estimate_signal_quality() (processing/quality/quality.py)
            # derives quality from the DSP chain instead, and is written to
            # work fine with device_signal_quality=None.
            signal_quality=None,
            last_error=self._last_error,
        )
