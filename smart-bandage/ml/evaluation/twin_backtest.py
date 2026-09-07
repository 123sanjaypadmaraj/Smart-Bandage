"""
DT-5 "Ground truth & validation harness" (Smart Bandage Digital Twin
artifact, build order DT-5).

ml/evaluation/scenario_backtest.py scores the processing+intelligence
pipeline against the Phase 2 simulator's 7 hand-scripted scenarios --
ground truth there is "which scenario we chose to run", not a number, so
it can only check whether an expected signal fired at all somewhere in
the run. A DigitalTwinDevice's WoundState (digital_twin/state.py) is real
(if simulated) ground truth instead, so a run against it can score the two
things scenario_backtest.py structurally can't:

  - how far `estimated_value` drifts from the clean signal the twin
    actually integrated (digital_twin/ground_truth.py), tracked as a mean
    absolute error over the whole run instead of eyeballed off a chart;
  - how many ticks the alert engine lags a scripted clinical onset -- or
    fires on nothing at all, in a run with no onset scripted.

Runs the exact production pipeline -- processing/biomarkers/pipeline.py:
ChannelPipeline, backend/app/alerts.py:evaluate_measurement,
backend/app/intelligence.py:IntelligenceEngine -- against a
DigitalTwinDevice, the same twin backend/app/simulation.py's twin-backed
mode drives; nothing here is a reimplementation of either.
tests/test_digital_twin_dt5_ground_truth.py runs this in CI;
`python -m ml.evaluation.twin_backtest` prints a human report.

This is one more harness Phase 9 experimental calibration will eventually
feed real device data through -- for now, like scenario_backtest.py, it
only validates against simulated ground truth, and says so.
"""
from __future__ import annotations

import random
import statistics
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta, timezone
from typing import List, Optional

from backend.app.alerts import evaluate_measurement
from backend.app.intelligence import IntelligenceEngine
from common.schemas.calibration import CalibrationParameters
from digital_twin.ground_truth import get_ground_truth
from digital_twin.observation import DigitalTwinDevice
from digital_twin.state import WoundState
from processing.biomarkers.pipeline import ChannelPipeline
from simulator.faults.faults import CommsTimeoutError, SensorDisconnectedError

_IDENTITY_CALIBRATION = CalibrationParameters(
    sensor_type="generic",
    version="0.0-identity",
    model_type="linear",
    slope=1.0,
    intercept=0.0,
    valid_from=date(2020, 1, 1),
)

# One simulated second per tick, same reasoning as scenario_backtest.py's
# TICK_SECONDS.
TICK_SECONDS = 1.0
DEVICE_ID = "SB-DTBT"  # <=12 chars -- common/schemas/measurement.py:RawMeasurement.device_id
CHANNEL_ID = "CH-01"
# digital_twin/observation.py's ChannelProfile registry is keyed by
# sensor_type, not channel_id -- "pathogen_channel_1" is the same default
# backend/app/simulation.py's twin-backed mode uses (_TWIN_SENSOR_TYPE),
# the preset most sensitive to inflammation/bacterial_load.
SENSOR_TYPE = "pathogen_channel_1"

# Bacterial-load level past which "an infection is clinically present",
# for scoring how many ticks the alert engine lags a scripted onset.
# backend/app/simulation.py's diabetic_slow_healing profile targets 0.4,
# immunocompromised_high_risk 0.75 (see its _PATIENT_PROFILES); 0.3 sits
# below both so a scripted onset toward either eventually crosses it.
ONSET_THRESHOLD = 0.3


@dataclass(frozen=True)
class TwinTrajectory:
    """One scripted run: a starting WoundState, optionally retargeted
    mid-run to script a clinical onset -- WoundState's own "retarget one
    field to move the whole state over time" (see its docstring), not a
    scripted checkpoint array. `onset_tick=None` scripts nothing: a stable
    control run where every alert is a false alarm by construction."""

    name: str
    description: str
    initial_state: WoundState = field(default_factory=WoundState)
    onset_tick: Optional[int] = None
    onset_bacterial_load_target: float = 0.05
    onset_inflammation_target: float = 0.05
    ticks: int = 600


# Mirrors backend/app/simulation.py's _PATIENT_PROFILES starting points and
# naming (healthy_baseline / diabetic_slow_healing / immunocompromised_
# high_risk) rather than importing them -- ml/evaluation stays independent
# of backend/app the same way scenario_backtest.py never imports it either;
# see digital_twin/ground_truth.py's docstring for the same tradeoff on the
# transfer function itself.
TRAJECTORIES: List[TwinTrajectory] = [
    TwinTrajectory(
        name="stable_healthy",
        description="healthy_baseline WoundState defaults, no scripted event -- alerts here are false alarms by construction.",
        initial_state=WoundState(),
        onset_tick=None,
        ticks=300,
    ),
    TwinTrajectory(
        name="infection_onset",
        description="healthy_baseline for 60 ticks, then retargeted toward diabetic_slow_healing's severity -- how many ticks does the alert engine lag the true crossing.",
        initial_state=WoundState(),
        onset_tick=60,
        onset_bacterial_load_target=0.4,
        onset_inflammation_target=0.35,
        ticks=600,
    ),
    TwinTrajectory(
        name="severe_onset",
        description="healthy_baseline for 60 ticks, then retargeted toward immunocompromised_high_risk's severity -- a faster, larger swing than infection_onset.",
        initial_state=WoundState(),
        onset_tick=60,
        onset_bacterial_load_target=0.75,
        onset_inflammation_target=0.6,
        ticks=600,
    ),
]


@dataclass
class TwinBacktestReport:
    trajectory: str
    ticks_run: int
    mean_abs_error: float
    max_abs_error: float
    onset_true_cross_tick: Optional[int]
    onset_alert_tick: Optional[int]
    # onset_alert_tick - onset_true_cross_tick: positive means the alert
    # engine lagged the true crossing by that many ticks; negative means it
    # fired first (a trend caught before the threshold was even crossed);
    # None if there was no scripted onset, or the onset never got flagged.
    alert_lead_time_ticks: Optional[int]
    alert_count: int
    false_alarm_count: int
    false_alarm_rate: float

    @property
    def onset_detected(self) -> bool:
        return self.onset_true_cross_tick is not None and self.onset_alert_tick is not None


def run_trajectory(trajectory: TwinTrajectory, *, seed: Optional[int] = None) -> TwinBacktestReport:
    if seed is not None:
        random.seed(seed)

    device = DigitalTwinDevice(
        DEVICE_ID,
        channels={CHANNEL_ID: SENSOR_TYPE},
        state=replace(trajectory.initial_state),  # independent copy -- WoundState.step() mutates in place
    )
    device.initialize()
    device.start_measurement()
    pipeline = ChannelPipeline(calibration=_IDENTITY_CALIBRATION, unit="a.u.")
    intelligence = IntelligenceEngine()

    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    errors: List[float] = []
    alert_ticks: List[int] = []
    onset_true_cross_tick: Optional[int] = None
    ticks_run = 0

    for tick in range(trajectory.ticks):
        if trajectory.onset_tick is not None and tick == trajectory.onset_tick:
            device.state.bacterial_load_target = trajectory.onset_bacterial_load_target
            device.state.inflammation_target = trajectory.onset_inflammation_target

        try:
            raw = device.read_channel(CHANNEL_ID, dt_seconds=TICK_SECONDS)
        except (SensorDisconnectedError, CommsTimeoutError):
            # read_channel() advances the shared state before it checks the
            # link, so the twin's hidden state (and onset-crossing check
            # below) must still see this tick even though there's no
            # reading to score.
            gt = get_ground_truth(device, CHANNEL_ID)
            if (
                onset_true_cross_tick is None
                and trajectory.onset_tick is not None
                and gt.bacterial_load >= ONSET_THRESHOLD
            ):
                onset_true_cross_tick = tick
            ticks_run += 1
            continue

        ticks_run += 1
        raw = raw.model_copy(update={"timestamp": t0 + timedelta(seconds=tick * TICK_SECONDS)})
        status = device.get_status(CHANNEL_ID)
        record = pipeline.process(raw, device_signal_quality=status.signal_quality)

        gt = get_ground_truth(device, CHANNEL_ID, estimated_signal=record.estimated_value)
        if record.estimated_value is not None:
            errors.append(abs(record.estimated_value - gt.true_signal))

        if (
            onset_true_cross_tick is None
            and trajectory.onset_tick is not None
            and gt.bacterial_load >= ONSET_THRESHOLD
        ):
            onset_true_cross_tick = tick

        # same two layers backend/app/simulation.py:DeviceSimulation._tick()
        # runs on every processed record -- threshold rules, then trend/
        # anomaly history.
        alerts = evaluate_measurement(record) + intelligence.evaluate(record)
        if alerts:
            alert_ticks.append(tick)

    if trajectory.onset_tick is not None:
        onset_alert_tick = next((t for t in alert_ticks if t >= trajectory.onset_tick), None)
        false_alarm_count = sum(1 for t in alert_ticks if t < trajectory.onset_tick)
        false_alarm_denominator = trajectory.onset_tick
        lead_time = (
            onset_alert_tick - onset_true_cross_tick
            if onset_alert_tick is not None and onset_true_cross_tick is not None
            else None
        )
    else:
        onset_alert_tick = None
        false_alarm_count = len(alert_ticks)
        false_alarm_denominator = ticks_run
        lead_time = None

    return TwinBacktestReport(
        trajectory=trajectory.name,
        ticks_run=ticks_run,
        mean_abs_error=round(statistics.mean(errors), 4) if errors else 0.0,
        max_abs_error=round(max(errors), 4) if errors else 0.0,
        onset_true_cross_tick=onset_true_cross_tick,
        onset_alert_tick=onset_alert_tick,
        alert_lead_time_ticks=lead_time,
        alert_count=len(alert_ticks),
        false_alarm_count=false_alarm_count,
        false_alarm_rate=round(false_alarm_count / false_alarm_denominator, 4) if false_alarm_denominator else 0.0,
    )


def run_twin_backtest(seed: Optional[int] = 20260906) -> List[TwinBacktestReport]:
    """`seed` reseeds `random` before *each* trajectory (not once for the
    whole run) so trajectory order never affects any individual result --
    same reasoning as scenario_backtest.py:run_backtest()."""
    return [run_trajectory(t, seed=seed) for t in TRAJECTORIES]


if __name__ == "__main__":
    reports = run_twin_backtest()
    for r in reports:
        lead = "n/a" if r.alert_lead_time_ticks is None else f"{r.alert_lead_time_ticks:+d}"
        cross = "-" if r.onset_true_cross_tick is None else str(r.onset_true_cross_tick)
        alert = "-" if r.onset_alert_tick is None else str(r.onset_alert_tick)
        print(
            f"{r.trajectory:16s} ticks={r.ticks_run:4d}  MAE={r.mean_abs_error:6.2f}  max_err={r.max_abs_error:6.2f}  "
            f"true_cross={cross:>4}  alert={alert:>4}  lead={lead:>4}  "
            f"alerts={r.alert_count:3d}  false_alarms={r.false_alarm_count:3d} ({r.false_alarm_rate:.1%})"
        )
