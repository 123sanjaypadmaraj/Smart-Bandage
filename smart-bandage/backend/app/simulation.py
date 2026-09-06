"""
Phase 4 deliverable: "simulator -> backend -> database", wired to the
Phase 2 simulator and Phase 3 processing pipeline, driven by
POST /simulation/start · /stop · /scenario and pushed live over
WS /ws/devices/{device_id}.

DT-6 deliverable: a twin-backed mode alongside that, still behind the same
three endpoints -- POST /simulation/start optionally names a patient
profile (`_PATIENT_PROFILES` below), which seeds a
digital_twin.observation.DigitalTwinDevice in place of the usual
MultiChannelSensor/ScenarioSensor. Everything downstream of "one raw
reading per channel per tick" -- pipelines, alerts, WS broadcast shape --
is unchanged; a caller that never passes `patient_profile` gets the exact
scenario-backed behavior back.

Deliberately NOT built against digital_twin/profiles.py's TwinProfile
registry (DT-4) or digital_twin/patient_profile.py -- both were either
broken or still shifting underneath concurrent work on this repo's other
digital_twin/ modules as of this ticket. `_PATIENT_PROFILES` below is a
small, self-contained stand-in scoped to this file; migrate it to a shared
registry once digital_twin/profiles.py settles. Its only dependency on
digital_twin/state.py's WoundState shape is isolated to
`_true_channel_signal()` below -- if that shape changes, that's the one
place to update.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, replace
from datetime import date, datetime, timezone
from typing import Dict, Iterable, List, Optional, Union

from backend.app import crud
from backend.app.alerts import device_error_alert, evaluate_measurement
from backend.app.config import settings
from backend.app.database import SessionLocal
from backend.app.intelligence import IntelligenceEngine
from backend.app.ws_manager import manager
from common.schemas.calibration import CalibrationParameters
from common.schemas.device import DeviceStatus
from common.schemas.measurement import RawMeasurement
from digital_twin.observation import ChannelProfile, DigitalTwinDevice, get_channel_profile
from digital_twin.state import WoundState
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

# DT-6: named starting points for a twin-backed simulation. Each is a
# WoundState with its `*_target` fields set to match its initial values, so
# the twin mean-reverts to its own baseline (see WoundState.step()) instead
# of drifting back toward WoundState's own defaults -- otherwise a
# "diabetic" run would look healthy again after a few minutes.
_PATIENT_PROFILES: Dict[str, WoundState] = {
    "healthy_baseline": WoundState(),
    "diabetic_slow_healing": WoundState(
        inflammation=0.35,
        inflammation_target=0.35,
        bacterial_load=0.4,
        bacterial_load_target=0.4,
        moisture=0.6,
        moisture_target=0.6,
        perfusion=0.5,
        perfusion_target=0.5,
    ),
    "immunocompromised_high_risk": WoundState(
        inflammation=0.6,
        inflammation_target=0.6,
        bacterial_load=0.75,
        bacterial_load_target=0.75,
        moisture=0.65,
        moisture_target=0.65,
        perfusion=0.4,
        perfusion_target=0.4,
    ),
}
DEFAULT_PATIENT_PROFILE = "healthy_baseline"

_PATIENT_PROFILE_DESCRIPTIONS: Dict[str, str] = {
    "healthy_baseline": "WoundState defaults -- low inflammation/bacterial load, well-perfused.",
    "diabetic_slow_healing": "Elevated inflammation and bacterial load, reduced perfusion -- impaired local immune response.",
    "immunocompromised_high_risk": "High inflammation and bacterial load, the most reduced perfusion -- least headroom before a serious infection.",
}

# digital_twin/observation.py's ChannelProfile registry is keyed by
# sensor_type, not channel_id. "pathogen_channel_1" is the preset most
# sensitive to inflammation/bacterial_load (see observation.py), the
# natural default for a smart-bandage infection use case; a later ticket
# can let a caller map specific channel_ids to specific sensor_types.
_TWIN_SENSOR_TYPE = "pathogen_channel_1"


def list_patient_profiles() -> List[Dict[str, str]]:
    """name + description for every registered profile -- backs
    GET /simulation/patient-profiles, which lets the dashboard/mobile twin
    control panel populate its picker without hardcoding this registry."""
    return [{"name": name, "description": _PATIENT_PROFILE_DESCRIPTIONS[name]} for name in sorted(_PATIENT_PROFILES)]


def get_patient_profile(name: str) -> WoundState:
    """A fresh copy of the named profile's starting WoundState --
    `dataclasses.replace` because WoundState is mutable (`.step()` mutates
    in place) and this registry's entries must never be handed out shared,
    or two simulations "started" from the same profile would silently
    mutate each other's state."""
    try:
        return replace(_PATIENT_PROFILES[name])
    except KeyError as exc:
        raise KeyError(f"unknown patient profile {name!r} -- choices are {sorted(_PATIENT_PROFILES)}") from exc


def _true_channel_signal(state: WoundState, profile: ChannelProfile) -> float:
    """The clean (no noise, no electrode fouling) value `profile` would
    report for hidden state `state` -- the "ground truth" the dev-only
    twin overlay compares a channel's actual (noisy, fouled) estimated
    value against. Mirrors digital_twin/observation.py:
    DigitalTwinDevice._observe_signal's linear response term exactly, minus
    the fouling attenuation and noise it adds on top for the real reading.

    Isolated here as the one place that reaches into WoundState's specific
    fields -- see module docstring."""
    return (
        profile.baseline
        + profile.inflammation_gain * state.inflammation
        + profile.bacterial_load_gain * state.bacterial_load
        + profile.moisture_gain * (state.moisture - 0.5)
        + profile.perfusion_gain * (state.perfusion - 0.7)
    )


@dataclass(frozen=True)
class TwinConfig:
    """DT-6: what makes one running simulation twin-backed instead of pure
    scenario-script playback. `channel_id=None` defaults to the first
    channel the device was started with -- see DeviceSimulation.__init__."""

    patient_profile: str = DEFAULT_PATIENT_PROFILE
    time_scale: float = 1.0
    channel_id: Optional[str] = None


@dataclass(frozen=True)
class TwinGroundTruthSnapshot:
    """The hidden WoundState the twin actually integrated on its last tick,
    next to the clean/true and actual/estimated values for one channel --
    what the dev-only "ground truth overlay" (frontend/mobile) plots
    against the live measurement stream. See GET /simulation/twin/{device_id}."""

    channel_id: str
    patient_profile: str
    time_scale: float
    inflammation: float
    bacterial_load: float
    moisture: float
    perfusion: float
    true_signal: float
    estimated_signal: Optional[float]
    timestamp: datetime


class DeviceSimulation:
    """One running virtual bandage, ticking on a fixed interval: either a
    MultiChannelSensor (scenario-backed, Phase 4) or a DigitalTwinDevice
    (twin-backed, DT-6), plus one ChannelPipeline per channel either way."""

    def __init__(
        self,
        device_id: str,
        channel_ids: Iterable[str],
        scenario: str,
        twin: Optional[TwinConfig] = None,
    ) -> None:
        self.device_id = device_id
        self.twin = twin
        self.sensor: Optional[MultiChannelSensor] = None
        self.twin_device: Optional[DigitalTwinDevice] = None
        self._ground_truth_channel_id: Optional[str] = None
        self.last_ground_truth: Optional[TwinGroundTruthSnapshot] = None
        self._last_tick_time: Optional[float] = None

        channel_ids = list(channel_ids)
        if twin is not None:
            if not channel_ids:
                raise ValueError("twin-backed simulation requires at least one channel")
            if twin.channel_id is not None and twin.channel_id not in channel_ids:
                raise ValueError(
                    f"twin_channel_id {twin.channel_id!r} is not among this device's channels {channel_ids!r}"
                )
            self.twin_device = DigitalTwinDevice(
                device_id,
                channels={channel_id: _TWIN_SENSOR_TYPE for channel_id in channel_ids},
                state=get_patient_profile(twin.patient_profile),
            )
            self._ground_truth_channel_id = twin.channel_id or channel_ids[0]
            pipeline_channel_ids = channel_ids
        else:
            self.sensor = MultiChannelSensor(device_id, channel_ids, default_scenario=scenario)
            pipeline_channel_ids = list(self.sensor.channels)

        self.pipelines: Dict[str, ChannelPipeline] = {
            channel_id: ChannelPipeline(calibration=_IDENTITY_CALIBRATION, unit="a.u.")
            for channel_id in pipeline_channel_ids
        }
        self.intelligence = IntelligenceEngine()
        self.task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()

    def stop(self) -> None:
        self._stop_event.set()

    async def run(self, interval_seconds: float, duration: Optional[float]) -> None:
        source = self.twin_device or self.sensor
        source.initialize()
        source.start_measurement()
        self._last_tick_time = time.monotonic()
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
            source.stop_measurement()

    def _read_all(self, dt_wall_seconds: float) -> Dict[str, Union[RawMeasurement, Exception]]:
        if self.twin_device is not None:
            return self.twin_device.read_all(dt_seconds=dt_wall_seconds * self.twin.time_scale)
        return self.sensor.read_all()

    def _channel_status(self, channel_id: str) -> DeviceStatus:
        if self.twin_device is not None:
            return self.twin_device.get_status(channel_id)
        return self.sensor.channels[channel_id].get_status()

    async def _tick(self) -> None:
        db = SessionLocal()
        try:
            now = time.monotonic()
            dt_wall = 0.0 if self._last_tick_time is None else max(0.0, now - self._last_tick_time)
            self._last_tick_time = now

            for channel_id, result in self._read_all(dt_wall).items():
                if isinstance(result, Exception):
                    alert = device_error_alert(self.device_id, channel_id, str(result))
                    crud.create_alert(db, alert)
                    crud.touch_device_last_seen(db, self.device_id, alert.timestamp, connected=False)
                    await manager.broadcast(
                        self.device_id, {"type": "alert", "alert": alert.model_dump(mode="json")}
                    )
                    continue

                raw = result
                status = self._channel_status(channel_id)
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

                if self.twin_device is not None and channel_id == self._ground_truth_channel_id:
                    self._record_ground_truth(channel_id, record.estimated_value)
                    # Dev-only "ground truth overlay": never ships the
                    # twin's hidden state to a production build, same as
                    # Settings.is_production gating the seeded dev login --
                    # see backend/app/config.py.
                    if not settings.is_production:
                        gt = self.last_ground_truth
                        await manager.broadcast(
                            self.device_id,
                            {
                                "type": "twin_ground_truth",
                                "ground_truth": {
                                    "device_id": self.device_id,
                                    "channel_id": gt.channel_id,
                                    "patient_profile": gt.patient_profile,
                                    "time_scale": gt.time_scale,
                                    "inflammation": gt.inflammation,
                                    "bacterial_load": gt.bacterial_load,
                                    "moisture": gt.moisture,
                                    "perfusion": gt.perfusion,
                                    "true_signal": gt.true_signal,
                                    "estimated_signal": gt.estimated_signal,
                                    "timestamp": gt.timestamp.isoformat(),
                                },
                            },
                        )
        finally:
            db.close()

    def _record_ground_truth(self, channel_id: str, estimated_signal: Optional[float]) -> None:
        assert self.twin_device is not None and self.twin is not None
        state = self.twin_device.state
        true_signal = _true_channel_signal(state, get_channel_profile(_TWIN_SENSOR_TYPE))
        self.last_ground_truth = TwinGroundTruthSnapshot(
            channel_id=channel_id,
            patient_profile=self.twin.patient_profile,
            time_scale=self.twin.time_scale,
            inflammation=state.inflammation,
            bacterial_load=state.bacterial_load,
            moisture=state.moisture,
            perfusion=state.perfusion,
            true_signal=round(true_signal, 4),
            estimated_signal=estimated_signal,
            timestamp=datetime.now(timezone.utc),
        )


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
        patient_profile: Optional[str] = None,
        time_scale: float = 1.0,
        twin_channel_id: Optional[str] = None,
    ) -> None:
        if device_id in self._sims:
            await self.stop(device_id)

        # DT-6: passing a patient_profile is what turns this on. Every
        # other simulation.start() caller keeps getting plain scenario
        # playback -- see DeviceSimulation.__init__.
        twin = (
            TwinConfig(patient_profile=patient_profile, time_scale=time_scale, channel_id=twin_channel_id)
            if patient_profile
            else None
        )

        sim = DeviceSimulation(device_id, channel_ids, scenario, twin=twin)
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
        if sim.twin_device is not None:
            raise ValueError(
                f"device {device_id!r} is running in twin-backed mode -- scenario injection isn't "
                "supported there; its patient_profile drives the physiology instead"
            )
        if channel_id is not None:
            sim.sensor.set_scenario(channel_id, scenario)
        else:
            sim.sensor.set_scenario_all(scenario)

    def live_status(self, device_id: str, channel_id: Optional[str] = None):
        sim = self._sims.get(device_id)
        if sim is None:
            return None
        source = sim.twin_device or sim.sensor
        return source.get_status(channel_id)

    def twin_ground_truth(self, device_id: str) -> Optional[TwinGroundTruthSnapshot]:
        """DT-6: the twin's most recent hidden-state snapshot, or None if
        this device isn't running (or isn't twin-backed, or hasn't ticked
        yet). Backs GET /simulation/twin/{device_id}."""
        sim = self._sims.get(device_id)
        if sim is None:
            return None
        return sim.last_ground_truth


simulation_manager = SimulationManager()
