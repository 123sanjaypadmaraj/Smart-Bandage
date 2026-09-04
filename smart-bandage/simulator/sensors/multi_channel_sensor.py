"""
Phase 2 deliverable: "a virtual bandage generating continuous measurements"
across more than one channel at once.

Not itself a SensorInterface implementation — SensorInterface is
single-channel by design (Blueprint §3), and a real device has one AFE
read cycle per channel anyway. This is the composition layer: one
ScenarioSensor per channel, coordinated together, which is what
backend/app (Phase 4) actually drives per device.
"""
from __future__ import annotations

from typing import Dict, Iterable, Optional

from common.schemas.device import DeviceStatus
from common.schemas.measurement import RawMeasurement
from simulator.sensors.scenario_sensor import ScenarioSensor


class MultiChannelSensor:
    """Owns one ScenarioSensor per channel_id for a single device."""

    def __init__(
        self,
        device_id: str,
        channel_ids: Iterable[str],
        default_scenario: str = "normal",
    ) -> None:
        self.device_id = device_id
        self.channels: Dict[str, ScenarioSensor] = {
            channel_id: ScenarioSensor(device_id, channel_id, scenario=default_scenario)
            for channel_id in channel_ids
        }

    def initialize(self) -> None:
        for sensor in self.channels.values():
            sensor.initialize()

    def start_measurement(self) -> None:
        for sensor in self.channels.values():
            sensor.start_measurement()

    def stop_measurement(self) -> None:
        for sensor in self.channels.values():
            sensor.stop_measurement()

    def read_channel(self, channel_id: str) -> RawMeasurement:
        return self.channels[channel_id].read_measurement()

    def read_all(self) -> Dict[str, RawMeasurement | Exception]:
        """One read per channel. A channel whose scenario raises (disconnect,
        comms dropout) contributes its exception instead of a reading, so one
        failing channel never blocks the others."""
        results: Dict[str, RawMeasurement | Exception] = {}
        for channel_id, sensor in self.channels.items():
            try:
                results[channel_id] = sensor.read_measurement()
            except Exception as exc:  # noqa: BLE001 - deliberately surfaced, not swallowed
                results[channel_id] = exc
        return results

    def set_scenario(self, channel_id: str, scenario: str) -> None:
        self.channels[channel_id].set_scenario(scenario)

    def set_scenario_all(self, scenario: str) -> None:
        for sensor in self.channels.values():
            sensor.set_scenario(scenario)

    def get_status(self, channel_id: Optional[str] = None) -> DeviceStatus | Dict[str, DeviceStatus]:
        if channel_id is not None:
            return self.channels[channel_id].get_status()
        return {cid: sensor.get_status() for cid, sensor in self.channels.items()}
