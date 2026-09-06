"""
DT-3 interface adapter: wraps DigitalTwinEngine behind SensorInterface so
it's a drop-in alternative to ScenarioSensor -- anything built against
SensorInterface (MultiChannelSensor, backend/app/simulation.py) can't tell
them apart.

Kept separate from engine.py for the same reason ScenarioSensor is kept
separate from simulator/signals/generators.py: the engine is the pure
tick-driven simulation; this is the one-read-per-call shim something else
paces. A caller that wants the engine's bulk-generation path instead
(a day of trajectory in one call) reaches through `.engine` for it --
SensorInterface's one-reading-per-call contract has no room to express
that itself.
"""
from __future__ import annotations

from typing import Optional

from common.interfaces.sensor_interface import SensorInterface
from common.schemas.device import DeviceStatus
from common.schemas.measurement import RawMeasurement
from digital_twin.engine import DigitalTwinEngine


class DigitalTwinSensor(SensorInterface):
    """SensorInterface backed by a DigitalTwinEngine instead of
    ScenarioSensor's wall-clock loop. One `read_measurement()` call == one
    engine tick -- real-time if the caller paces its own reads (e.g. once a
    second, the same cadence a real device or ScenarioSensor would see),
    or as fast as the caller loops otherwise. `set_scenario()` mirrors
    ScenarioSensor's so callers that duck-type against it (e.g.
    MultiChannelSensor.set_scenario) work unmodified against either.
    """

    def __init__(
        self,
        device_id: str,
        channel_id: str,
        scenario: str = "normal",
        dt_seconds: float = 1.0,
        seed: Optional[int] = None,
        battery_start_pct: float = 100.0,
        battery_drain_pct_per_hour: float = 2.0,
    ) -> None:
        self.device_id = device_id
        self.channel_id = channel_id
        self._engine = DigitalTwinEngine(
            device_id,
            channel_id,
            scenario=scenario,
            dt_seconds=dt_seconds,
            seed=seed,
            battery_start_pct=battery_start_pct,
            battery_drain_pct_per_hour=battery_drain_pct_per_hour,
        )

    @property
    def engine(self) -> DigitalTwinEngine:
        """Escape hatch to the underlying engine -- e.g. `.engine.run(n)`
        for a bulk-generated trajectory, or `.engine.seed` to record what
        reproduces this exact run."""
        return self._engine

    @property
    def scenario_name(self) -> str:
        return self._engine.scenario_name

    def set_scenario(self, scenario: str) -> None:
        self._engine.set_scenario(scenario)

    def initialize(self) -> None:
        self._engine.reset(seed=self._engine.seed)

    def start_measurement(self) -> None:
        self._engine.start()

    def read_measurement(self) -> RawMeasurement:
        return self._engine.tick()

    def stop_measurement(self) -> None:
        self._engine.stop()

    def get_status(self) -> DeviceStatus:
        return self._engine.get_status()
