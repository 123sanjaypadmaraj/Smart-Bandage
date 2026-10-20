"""
Phase 4 deliverable: "simulator -> backend -> database", wired to the
Phase 2 simulator and Phase 3 processing pipeline, driven by
POST /simulation/start · /stop · /scenario and pushed live over
WS /ws/devices/{device_id}.

DT-6 deliverable: a twin-backed mode alongside that, still behind the same
three endpoints -- POST /simulation/start optionally names a patient
profile (see digital_twin/profiles.py), which seeds a
digital_twin.observation.DigitalTwinDevice in place of the usual
MultiChannelSensor/ScenarioSensor. Everything downstream of "one raw
reading per channel per tick" -- pipelines, alerts, WS broadcast shape --
is unchanged; a caller that never passes `patient_profile` gets the exact
scenario-backed behavior back.

Consolidation note: this originally carried its own small, self-contained
`_PATIENT_PROFILES` stand-in ("migrate it to a shared registry once
digital_twin/profiles.py settles" -- see git history) because
digital_twin/profiles.py's registry was still shifting underneath
concurrent work on this repo's other digital_twin/ modules. It's settled
now (one real WoundState, one registry with all six profiles -- the three
static risk levels this file originally hardcoded plus DT-4's three
scripted-onset scenarios), so `list_patient_profiles`/`get_patient_profile`
below are now thin wrappers over it, kept as the stable names
backend/app/routers/simulation.py already imports.

Bandage-twin mode: a third engine beside those two. POST /simulation/start
with `bandage_design` (a digital_twin/bandage/design.py PRESETS key) runs
the physics-based digital_twin.bandage.BandageTwin -- the switch
docs/architecture/bandage_twin.md had listed under "Limits" as not yet
wired. The design's electrodes define the device's channels; each tick
advances the one shared twin by wall-clock dt * time_scale and samples every
electrode at that same instant (so, like read_in_cycle, all channels share a
timestamp). Its AFE already emits the final reading -- normalized a.u.,
100 = a fresh sensor's nominal response -- so its pipelines use the same
identity calibration as scenario playback rather than DT-6's
ChannelProfile units. Two WS messages are new and additive: dev-only
`twin_bandage_truth` (the twin's hidden Truth; DT-6's `twin_ground_truth`
is left exactly as it was) and `twin_eis` (the aptamer electrode's live
impedance spectrum -- device output, so not dev-gated) for the dashboard's
Bandage 3D Twin tab. The scenario and DT-6 paths are unchanged.
"""
from __future__ import annotations

import asyncio
import dataclasses
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Union

from backend.app import crud
from backend.app.alerts import device_error_alert, evaluate_measurement
from backend.app.config import settings
from backend.app.database import SessionLocal
from backend.app.intelligence import IntelligenceEngine
from backend.app.schemas import TwinBandageTruthMessage, TwinEisMessage
from backend.app.ws_manager import manager
from common.schemas.calibration import CalibrationParameters
from common.schemas.device import DeviceStatus
from common.schemas.measurement import RawMeasurement
from digital_twin.bandage.design import PRESETS as BANDAGE_DESIGN_PRESETS
from digital_twin.bandage.design import BandageDesign
from digital_twin.bandage.eis_adapter import eis_from_twin
from digital_twin.bandage.kernel import derive_seed
from digital_twin.bandage.twin import BandageTwin, TwinFrame
from digital_twin.observation import DigitalTwinDevice, get_channel_profile
from digital_twin.profiles import DEFAULT_PROFILE, TwinProfile, get_profile, list_profiles
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

DEFAULT_PATIENT_PROFILE = DEFAULT_PROFILE

# The channel-type rotation a twin-backed device's *extra* channels get --
# a real multi-channel bandage has electrodes for different analytes, not
# three copies of the same one. The profile's own sensor_type always wins
# the first (and, for a single-channel device, only) slot, since that's
# what GET /simulation/twin/{device_id}'s ground-truth overlay defaults to
# and what digital_twin/profiles.py designed each profile's narrative
# around; channels beyond that cycle through the rest of this list so a
# 3-channel device reads as pathogen + pH + glucose, not one assay in
# triplicate.
_CHANNEL_TYPE_ROTATION = ("pathogen_channel_1", "pH", "glucose")


def _assign_channel_sensor_types(channel_ids: List[str], primary_sensor_type: str) -> Dict[str, str]:
    others = [t for t in _CHANNEL_TYPE_ROTATION if t != primary_sensor_type]
    rotation = [primary_sensor_type, *others] or [primary_sensor_type]
    return {
        channel_id: rotation[i % len(rotation)] for i, channel_id in enumerate(channel_ids)
    }


def _twin_pipeline(sensor_type: str) -> ChannelPipeline:
    """A calibrated `ChannelPipeline` for one twin-backed channel, built
    from that sensor_type's `digital_twin.observation.ChannelProfile` --
    real units/ranges (see that module) instead of the scenario-backed
    path's identity calibration."""
    profile = get_channel_profile(sensor_type)
    calibration = CalibrationParameters(
        sensor_type=sensor_type,
        version="digital-twin-sim-1.0",
        model_type="linear",
        slope=profile.cal_slope,
        intercept=profile.cal_intercept,
        valid_from=date(2020, 1, 1),
    )
    return ChannelPipeline(calibration=calibration, unit=profile.unit)


def list_patient_profiles() -> List[Dict[str, str]]:
    """name + description for every registered profile -- backs
    GET /simulation/patient-profiles, which lets the dashboard/mobile twin
    control panel populate its picker without hardcoding this registry."""
    return [{"name": name, "description": get_profile(name).description} for name in list_profiles()]


def get_patient_profile(name: str) -> TwinProfile:
    """The named digital_twin/profiles.py TwinProfile -- raises the same
    shape of error `_PATIENT_PROFILES` used to for an unknown name."""
    try:
        return get_profile(name)
    except KeyError as exc:
        raise KeyError(f"unknown patient profile {name!r} -- choices are {list_profiles()}") from exc


# Per-point measurement noise on the live twin_eis spectrum, % of |Z| --
# the same 1.5 % baseline-noise SD digital_twin/bandage/impedance.py's
# Calibration assumes when it sets the 3-sigma detection threshold.
BANDAGE_EIS_NOISE_PCT = 1.5


def _eis_channel_ids(design: BandageDesign) -> List[str]:
    """The electrodes eis_from_twin() can read: aptamer (pathogen-marker) ones."""
    return [e.channel_id for e in design.electrodes if e.analyte == "pathogen_marker"]


def list_bandage_designs() -> List[Dict[str, str]]:
    """Every digital_twin/bandage/design.py preset -- backs
    GET /simulation/bandage-designs, the bandage-twin counterpart of
    list_patient_profiles()."""
    return [
        {"name": key, "description": " ".join((factory.__doc__ or factory().name).split())}
        for key, factory in BANDAGE_DESIGN_PRESETS.items()
    ]


def get_bandage_design(name: str) -> BandageDesign:
    """A fresh BandageDesign from the named preset -- KeyError (-> 400 in
    the router) for an unknown name, same shape as get_patient_profile."""
    factory = BANDAGE_DESIGN_PRESETS.get(name)
    if factory is None:
        raise KeyError(f"unknown bandage design {name!r} -- choices are {sorted(BANDAGE_DESIGN_PRESETS)}")
    return factory()


@dataclass(frozen=True)
class BandageConfig:
    """What makes one running simulation bandage-twin-backed: the design
    preset, how many simulated seconds each wall-clock second covers, and
    the twin's master seed (None -> BandageTwin picks one and keeps it on
    `.seed`, so a run is still reproducible after the fact)."""

    design: str
    time_scale: float = 1.0
    seed: Optional[int] = None


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
    """One running virtual bandage, ticking on a fixed interval: a
    MultiChannelSensor (scenario-backed, Phase 4), a DigitalTwinDevice
    (twin-backed, DT-6) or a BandageTwin (bandage-twin-backed), plus one
    ChannelPipeline per channel whichever it is."""

    def __init__(
        self,
        device_id: str,
        channel_ids: Iterable[str],
        scenario: str,
        twin: Optional[TwinConfig] = None,
        bandage: Optional[BandageConfig] = None,
    ) -> None:
        if twin is not None and bandage is not None:
            raise ValueError("patient_profile and bandage_design are mutually exclusive -- pick one engine")
        self.device_id = device_id
        self.twin = twin
        self.bandage = bandage
        self.sensor: Optional[MultiChannelSensor] = None
        self.twin_device: Optional[DigitalTwinDevice] = None
        self.bandage_twin: Optional[BandageTwin] = None
        self._twin_profile: Optional[TwinProfile] = None
        self._twin_elapsed_days = 0.0
        self._ground_truth_channel_id: Optional[str] = None
        self.last_ground_truth: Optional[TwinGroundTruthSnapshot] = None
        # bandage-twin mode: the frame sampled on the last tick (readings +
        # Truth at one instant), and the EIS-capable channels
        self.last_bandage_frame: Optional[TwinFrame] = None
        self._eis_channel_ids: List[str] = []
        self._bandage_ticks = 0
        self._last_tick_time: Optional[float] = None
        self.pipelines: Dict[str, ChannelPipeline]

        channel_ids = list(channel_ids)
        if bandage is not None:
            design = get_bandage_design(bandage.design)
            self.bandage_twin = BandageTwin(design=design, seed=bandage.seed, device_id=device_id)
            self._eis_channel_ids = _eis_channel_ids(design)
            # The twin's AFE output is already the device's final reading
            # (a.u., nominal 100) -- identity calibration, not a second one.
            self.pipelines = {
                cid: ChannelPipeline(calibration=_IDENTITY_CALIBRATION, unit="a.u.")
                for cid in self.bandage_twin.electrodes
            }
        elif twin is not None:
            if not channel_ids:
                raise ValueError("twin-backed simulation requires at least one channel")
            if twin.channel_id is not None and twin.channel_id not in channel_ids:
                raise ValueError(
                    f"twin_channel_id {twin.channel_id!r} is not among this device's channels {channel_ids!r}"
                )
            self._twin_profile = get_patient_profile(twin.patient_profile)
            channel_sensor_types = _assign_channel_sensor_types(channel_ids, self._twin_profile.sensor_type)
            self.twin_device = DigitalTwinDevice(
                device_id,
                channels=channel_sensor_types,
                state=self._twin_profile.initial_state(),
            )
            self._ground_truth_channel_id = twin.channel_id or channel_ids[0]
            self.pipelines = {
                channel_id: _twin_pipeline(sensor_type)
                for channel_id, sensor_type in channel_sensor_types.items()
            }
        else:
            self.sensor = MultiChannelSensor(device_id, channel_ids, default_scenario=scenario)
            self.pipelines = {
                channel_id: ChannelPipeline(calibration=_IDENTITY_CALIBRATION, unit="a.u.")
                for channel_id in self.sensor.channels
            }
        self.intelligence = IntelligenceEngine()
        self.task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()

    @property
    def channel_ids(self) -> List[str]:
        return list(self.pipelines)

    def stop(self) -> None:
        self._stop_event.set()

    async def run(self, interval_seconds: float, duration: Optional[float]) -> None:
        # A BandageTwin has no initialize/start/stop lifecycle: it's built
        # ready to advance(), and a fresh one per run is the only "reset".
        source = self.twin_device or self.sensor
        if source is not None:
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
            if source is not None:
                source.stop_measurement()

    def _read_all(self, dt_wall_seconds: float) -> Dict[str, Union[RawMeasurement, Exception]]:
        if self.bandage_twin is not None:
            # One advance for the whole bandage, then every electrode read at
            # that same instant -- the shared-clock contract read_in_cycle
            # gives SensorInterface callers, without its one-channel-at-a-time
            # bookkeeping. sample() already isolates a faulting channel as an
            # Exception entry, same as MultiChannelSensor.read_all().
            self.bandage_twin.advance(dt_wall_seconds * self.bandage.time_scale)
            frame = self.bandage_twin.sample()
            self.last_bandage_frame = frame
            return frame.readings
        if self.twin_device is not None:
            dt_seconds = dt_wall_seconds * self.twin.time_scale
            # Apply this profile's scripted perturbations (infection onset,
            # dressing disturbance -- digital_twin/perturbations.py) before
            # advancing the state they retarget, same order
            # digital_twin/profiles.py:simulate() uses for a backtest of the
            # same profile, so a live run and a backtest agree.
            for perturbation in self._twin_profile.perturbations:
                perturbation.apply(self.twin_device.state, self._twin_elapsed_days)
            results = self.twin_device.read_all(dt_seconds=dt_seconds)
            self._twin_elapsed_days += dt_seconds / 86400.0
            return results
        return self.sensor.read_all()

    def _channel_status(self, channel_id: str) -> DeviceStatus:
        if self.bandage_twin is not None:
            return self.bandage_twin.status(channel_id)
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

            if self.bandage_twin is not None:
                await self._broadcast_bandage_frame()
        finally:
            db.close()

    # ------------------------------------------------ bandage-twin broadcasts
    async def _broadcast_bandage_frame(self) -> None:
        """After the per-channel measurement/alert broadcasts: the twin's
        hidden Truth (dev-only) and each aptamer electrode's live EIS
        reading (always -- it's device output), both at the instant this
        tick sampled."""
        self._bandage_ticks += 1
        # Same reasoning as twin_ground_truth's gate above: hidden
        # physiological/electrode state a real device never reports.
        if not settings.is_production:
            truth = self.bandage_truth_message()
            if truth is not None:
                await manager.broadcast(self.device_id, truth)
        for message in self.eis_messages():
            await manager.broadcast(self.device_id, message)

    def bandage_truth_message(self) -> Optional[Dict[str, Any]]:
        """The WS "twin_bandage_truth" message for the last sampled frame
        (schemas.TwinBandageTruthMessage), or None before the first tick."""
        frame = self.last_bandage_frame
        if self.bandage is None or frame is None:
            return None
        return TwinBandageTruthMessage(
            device_id=self.device_id,
            bandage_design=self.bandage.design,
            timestamp=datetime.now(timezone.utc),
            truth=dataclasses.asdict(frame.truth),  # nested Analytes/ChannelTruth -> dicts, names unchanged
        ).model_dump(mode="json")

    def eis_messages(self) -> List[Dict[str, Any]]:
        """One WS "twin_eis" message (schemas.TwinEisMessage) per aptamer
        electrode, read off the twin now. The noise seed is derived from the
        twin's own master seed and a tick counter, so a seeded run's spectra
        are reproducible yet each tick's noise is fresh."""
        if self.bandage_twin is None:
            return []
        now = datetime.now(timezone.utc)
        out = []
        for cid in self._eis_channel_ids:
            seed = derive_seed(self.bandage_twin.seed, "live-eis", str(self._bandage_ticks)) % (2**63)
            reading = eis_from_twin(self.bandage_twin, cid, noise_pct=BANDAGE_EIS_NOISE_PCT, seed=seed)
            out.append(
                TwinEisMessage(
                    device_id=self.device_id, channel_id=cid, timestamp=now, eis=reading.to_dict()
                ).model_dump(mode="json")
            )
        return out

    def _record_ground_truth(self, channel_id: str, estimated_signal: Optional[float]) -> None:
        assert self.twin_device is not None and self.twin is not None
        state = self.twin_device.state
        true_signal = self.twin_device.true_signal(channel_id)
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
        bandage_design: Optional[str] = None,
        seed: Optional[int] = None,
    ) -> DeviceSimulation:
        if patient_profile and bandage_design:
            raise ValueError("patient_profile and bandage_design are mutually exclusive -- pick one engine")
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
        bandage = (
            BandageConfig(design=bandage_design, time_scale=time_scale, seed=seed) if bandage_design else None
        )

        sim = DeviceSimulation(device_id, channel_ids, scenario, twin=twin, bandage=bandage)
        self._sims[device_id] = sim
        sim.task = asyncio.create_task(sim.run(interval_seconds, duration))

        def _cleanup(_task: asyncio.Task) -> None:
            if self._sims.get(device_id) is sim:
                self._sims.pop(device_id, None)

        sim.task.add_done_callback(_cleanup)
        return sim

    def get(self, device_id: str) -> Optional[DeviceSimulation]:
        return self._sims.get(device_id)

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
        if sim.bandage_twin is not None:
            raise ValueError(
                f"device {device_id!r} is running in bandage-twin mode -- scenario injection isn't "
                "supported there; its bandage_design's wound-bed physics drives the readings instead"
            )
        if channel_id is not None:
            sim.sensor.set_scenario(channel_id, scenario)
        else:
            sim.sensor.set_scenario_all(scenario)

    def live_status(self, device_id: str, channel_id: Optional[str] = None):
        sim = self._sims.get(device_id)
        if sim is None:
            return None
        if sim.bandage_twin is not None:
            twin = sim.bandage_twin
            if channel_id is not None:
                # same KeyError an unknown channel gets from the other engines
                return twin.status(channel_id)
            return {cid: twin.status(cid) for cid in twin.electrodes}
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
