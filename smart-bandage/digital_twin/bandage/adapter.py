"""
SensorInterface adapter: lets one channel of a BandageTwin stand in anywhere a
ScenarioSensor or RealSensor does (MultiChannelSensor, the ingestion pipeline).

SensorInterface is single-channel, one read per call, paced by the caller; a
bandage is several electrodes sharing one wound bed, one battery and one
clock. The twin resolves that with a read-cycle rule (BandageTwin.read_in_cycle):
each channel's first read in a cycle shares the current instant, and asking
the same channel again starts the next cycle by advancing one sample interval.
A caller that reads CH-01, CH-02, CH-03, CH-01, ... therefore sees the three
channels sampled together, every interval, with no bookkeeping of its own.
"""
from __future__ import annotations

from typing import Dict

from common.interfaces.sensor_interface import SensorInterface
from common.schemas.device import DeviceStatus
from common.schemas.measurement import RawMeasurement
from digital_twin.bandage.twin import BandageTwin


class BandageTwinSensor(SensorInterface):
    def __init__(self, twin: BandageTwin, channel_id: str) -> None:
        if channel_id not in twin.electrodes:
            raise KeyError(f"unknown channel_id {channel_id!r} for design {twin.design.name!r}")
        self.twin = twin
        self.channel_id = channel_id
        self._running = False

    def initialize(self) -> None:
        # the twin (wound, battery, clock) is shared between channels, so one
        # channel can't rewind it; build a new twin to start over
        pass

    def start_measurement(self) -> None:
        self._running = True

    def read_measurement(self) -> RawMeasurement:
        if not self._running:
            raise RuntimeError("call start_measurement() before read_measurement()")
        return self.twin.read_in_cycle(self.channel_id)

    def stop_measurement(self) -> None:
        self._running = False

    def get_status(self) -> DeviceStatus:
        return self.twin.status(self.channel_id)


def sensors_for(twin: BandageTwin) -> Dict[str, BandageTwinSensor]:
    """One SensorInterface per electrode, all sharing `twin`."""
    return {cid: BandageTwinSensor(twin, cid) for cid in twin.electrodes}
