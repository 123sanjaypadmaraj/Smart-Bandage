"""
Phase 2 deliverable: "a virtual bandage generating continuous measurements"
across more than one channel at once.

Not itself a SensorInterface implementation — SensorInterface is
single-channel by design (Blueprint §3), and a real device has one AFE
read cycle per channel anyway. This is the composition layer: one
SensorInterface backend per channel, coordinated together, which is what
backend/app (Phase 4) actually drives per device.

DT-3: that per-channel backend is opt-in, not fixed to ScenarioSensor.
Passing `engine="digital_twin"` backs every channel with a
DigitalTwinSensor (digital_twin/adapter.py) instead -- same
SensorInterface, same per-channel isolation in `read_all()`, but ticking
on DigitalTwinEngine's seeded, wall-clock-decoupled loop
(digital_twin/engine.py). Default stays "scenario" so nothing existing
changes; a caller has to ask for the twin explicitly.

`default_scenario` names a value from a different registry depending on
`engine`: one of simulator/scenarios/scenarios.py's 7 fixed scenarios for
"scenario", or one of digital_twin/profiles.py's clinical profiles for
"digital_twin" -- the two were never interchangeable, so leaving it at
`None` picks each engine's own sensible default instead of a literal
default that would only be valid for one of them.
"""
from __future__ import annotations

from typing import Dict, Iterable, Literal, Optional

from common.interfaces.sensor_interface import SensorInterface
from common.schemas.device import DeviceStatus
from common.schemas.measurement import RawMeasurement
from digital_twin.adapter import DigitalTwinSensor
from digital_twin.engine import DigitalTwinEngine
from digital_twin.profiles import DEFAULT_PROFILE
from simulator.sensors.scenario_sensor import ScenarioSensor

EngineName = Literal["scenario", "digital_twin"]

_DEFAULT_PHASE2_SCENARIO = "normal"


class MultiChannelSensor:
    """Owns one SensorInterface backend per channel_id for a single device."""

    def __init__(
        self,
        device_id: str,
        channel_ids: Iterable[str],
        default_scenario: Optional[str] = None,
        engine: EngineName = "scenario",
        seed: Optional[int] = None,
        dt_seconds: float = 1.0,
    ) -> None:
        self.device_id = device_id
        self.engine = engine
        if engine == "scenario":
            scenario = default_scenario or _DEFAULT_PHASE2_SCENARIO
            self.channels: Dict[str, SensorInterface] = {
                channel_id: ScenarioSensor(device_id, channel_id, scenario=scenario)
                for channel_id in channel_ids
            }
        elif engine == "digital_twin":
            default_scenario = default_scenario or DEFAULT_PROFILE
            # Distinct seed per channel (still derived from one base seed)
            # so a reproducible device doesn't play the identical noise
            # trace on every channel; `seed=None` stays non-reproducible
            # per channel, same as leaving it unset on a single sensor.
            self.channels = {
                channel_id: DigitalTwinSensor(
                    device_id,
                    channel_id,
                    scenario=default_scenario,
                    dt_seconds=dt_seconds,
                    seed=None if seed is None else seed + index,
                )
                for index, channel_id in enumerate(channel_ids)
            }
        else:
            raise ValueError(f"unknown engine {engine!r}; expected 'scenario' or 'digital_twin'")

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

    def engine_for(self, channel_id: str) -> Optional[DigitalTwinEngine]:
        """The channel's underlying DigitalTwinEngine, or None when this
        device was built with `engine="scenario"`. The escape hatch to
        DigitalTwinEngine.run() -- generating hours or days of trajectory
        in one call -- which SensorInterface's one-read-per-call contract
        (what read_channel()/read_all() speak) has no room to express."""
        sensor = self.channels[channel_id]
        return sensor.engine if isinstance(sensor, DigitalTwinSensor) else None
