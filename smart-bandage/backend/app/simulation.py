"""
Phase 4 deliverable: "simulator -> backend -> database", wired to the
Phase 2 simulator and Phase 3 processing pipeline, driven by
POST /simulation/start · /stop · /scenario and pushed live over
WS /ws/devices/{device_id}.
"""
from __future__ import annotations

import asyncio
import time
from datetime import date
from typing import Dict, Iterable, Optional

from backend.app import crud
from backend.app.alerts import device_error_alert, evaluate_measurement
from backend.app.database import SessionLocal
from backend.app.intelligence import IntelligenceEngine
from backend.app.ws_manager import manager
from common.schemas.calibration import CalibrationParameters
from processing.biomarkers.pipeline import ChannelPipeline
from simulator.sensors.multi_channel_sensor import MultiChannelSensor

_IDENTITY_CALIBRATION = CalibrationParameters(
    sensor_type="generic",
    version="0.0-identity",
    model_type="linear",
    slope=1.0,
    intercept=0.0,
    valid_from=date(2020, 1, 1),
)


class DeviceSimulation:
    """One running virtual bandage: a MultiChannelSensor plus one
    ChannelPipeline per channel, ticking on a fixed interval."""

    def __init__(self, device_id: str, channel_ids: Iterable[str], scenario: str) -> None:
        self.device_id = device_id
        self.sensor = MultiChannelSensor(device_id, channel_ids, default_scenario=scenario)
        self.pipelines: Dict[str, ChannelPipeline] = {
            channel_id: ChannelPipeline(calibration=_IDENTITY_CALIBRATION, unit="a.u.")
            for channel_id in self.sensor.channels
        }
        self.intelligence = IntelligenceEngine()
        self.task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()

    def stop(self) -> None:
        self._stop_event.set()

    async def run(self, interval_seconds: float, duration: Optional[float]) -> None:
        self.sensor.initialize()
        self.sensor.start_measurement()
        start = time.monotonic()
        try:
            while not self._stop_event.is_set():
                if duration is not None and (time.monotonic() - start) >= duration:
                    break
                await self._tick()
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=interval_seconds)
                except asyncio.TimeoutError:
                    pass
        finally:
            self.sensor.stop_measurement()

    async def _tick(self) -> None:
        db = SessionLocal()
        try:
            for channel_id, channel_sensor in self.sensor.channels.items():
                try:
                    raw = channel_sensor.read_measurement()
                except Exception as exc:  # noqa: BLE001 - simulated hardware fault, not a bug
                    alert = device_error_alert(self.device_id, channel_id, str(exc))
                    crud.create_alert(db, alert)
                    crud.touch_device_last_seen(db, self.device_id, alert.timestamp, connected=False)
                    await manager.broadcast(
                        self.device_id, {"type": "alert", "alert": alert.model_dump(mode="json")}
                    )
                    continue

                status = channel_sensor.get_status()
                record = self.pipelines[channel_id].process(raw, device_signal_quality=status.signal_quality)
                crud.store_measurement(db, record)
                crud.touch_device_last_seen(db, self.device_id, record.timestamp, status.connected)

                for alert in evaluate_measurement(record):
                    crud.create_alert(db, alert)
                    await manager.broadcast(
                        self.device_id, {"type": "alert", "alert": alert.model_dump(mode="json")}
                    )

                for alert in self.intelligence.evaluate(record):
                    crud.create_alert(db, alert)
                    await manager.broadcast(
                        self.device_id, {"type": "alert", "alert": alert.model_dump(mode="json")}
                    )

                await manager.broadcast(
                    self.device_id, {"type": "measurement", "measurement": record.model_dump(mode="json")}
                )
        finally:
            db.close()


class SimulationManager:
    """Registry of running DeviceSimulation instances, one per device_id."""

    def __init__(self) -> None:
        self._sims: Dict[str, DeviceSimulation] = {}

    def is_running(self, device_id: str) -> bool:
        return device_id in self._sims

    async def start(
        self,
        device_id: str,
        channel_ids: Iterable[str],
        scenario: str,
        duration: Optional[float] = None,
        interval_seconds: float = 1.0,
    ) -> None:
        if device_id in self._sims:
            await self.stop(device_id)

        sim = DeviceSimulation(device_id, channel_ids, scenario)
        self._sims[device_id] = sim
        sim.task = asyncio.create_task(sim.run(interval_seconds, duration))

        def _cleanup(_task: asyncio.Task) -> None:
            if self._sims.get(device_id) is sim:
                self._sims.pop(device_id, None)

        sim.task.add_done_callback(_cleanup)

    async def stop(self, device_id: str) -> bool:
        sim = self._sims.pop(device_id, None)
        if sim is None:
            return False
        sim.stop()
        if sim.task is not None:
            try:
                await sim.task
            except Exception:  # noqa: BLE001 - task cancellation/errors already surfaced via alerts
                pass
        return True

    def set_scenario(self, device_id: str, scenario: str, channel_id: Optional[str] = None) -> None:
        sim = self._sims.get(device_id)
        if sim is None:
            raise KeyError(f"no running simulation for device {device_id!r}")
        if channel_id is not None:
            sim.sensor.set_scenario(channel_id, scenario)
        else:
            sim.sensor.set_scenario_all(scenario)

    def live_status(self, device_id: str, channel_id: Optional[str] = None):
        sim = self._sims.get(device_id)
        if sim is None:
            return None
        return sim.sensor.get_status(channel_id)


simulation_manager = SimulationManager()
