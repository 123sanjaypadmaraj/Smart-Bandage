"""
Phase 6 "ML experiments" (Blueprint §10 Phase 6).

Blueprint §11 explicitly prioritizes real ML last -- "only once there's
real, labelled data to train on" -- so there is deliberately no trained
model in this repository yet. What *can* exist before real data does: a
backtest that runs the Phase 3 processing pipeline and the Phase 6
intelligence layer against the Phase 2 simulator's 7 scenarios. Each
scenario is a labelled dataset by construction -- we know exactly which
fault or pattern produced each reading -- so this checks the system's own
verdict (record status, quality, trend/anomaly reasons) against that
label, the same way a trained classifier would eventually be scored
against real annotated data. `tests/test_ml_phase6_backtest.py` runs this
in CI; `python -m ml.evaluation.scenario_backtest` prints a human report.

This is the harness Phase 9 experimental calibration will eventually feed
real device data through -- for now it only validates against simulated
ground truth, and says so.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional, Set

from common.schemas.calibration import CalibrationParameters
from processing.biomarkers.pipeline import ChannelPipeline
from processing.intelligence.anomaly import ChannelAnomalyDetector
from simulator.sensors.scenario_sensor import ScenarioSensor

_IDENTITY_CALIBRATION = CalibrationParameters(
    sensor_type="generic",
    version="0.0-identity",
    model_type="linear",
    slope=1.0,
    intercept=0.0,
    valid_from=date(2020, 1, 1),
)

# One simulated second per tick -- matches the app's own default
# SIMULATION_INTERVAL_SECONDS (backend/app/config.py) so
# processing/intelligence/trend.py's per-second slope threshold behaves
# the same here as it does live. Reads happen back-to-back with no real
# sleep, so we stamp synthetic timestamps rather than trust the wall clock.
TICK_SECONDS = 1.0
WARMUP_TICKS = 10  # a stable "normal" baseline before the target scenario, same as tests/test_processing_phase3.py

# For each scenario: which signal(s) the system is expected to raise at
# least once within `ticks` reads of the target scenario. Ground truth by
# construction (we chose the scenario), not measured against real-world
# labels -- see module docstring.
EXPECTED_SIGNALS: Dict[str, Set[str]] = {
    "normal": set(),
    "rising_concentration": {"sustained_trend"},
    "sudden_abnormal": {"invalid_status"},
    "electrode_degradation": {"quality_degrading"},
    "high_noise": {"low_quality"},
    "sensor_disconnect": {"read_error"},
    "comms_failure": {"read_error"},
}


@dataclass
class ScenarioReport:
    scenario: str
    ticks_run: int
    signals_seen: Set[str] = field(default_factory=set)
    expected: Set[str] = field(default_factory=set)

    @property
    def passed(self) -> bool:
        return self.expected <= self.signals_seen

    @property
    def unexpected(self) -> Set[str]:
        """Signals seen that weren't in the label -- not necessarily wrong
        (e.g. sudden_abnormal's spike can also look like a rapid_change),
        just worth a human glancing at."""
        return self.signals_seen - self.expected


def run_scenario(
    scenario: str,
    ticks: int = 40,
    *,
    device_id: str = "SB-BACKTEST",
    channel_id: str = "CH-01",
    seed: Optional[int] = None,
) -> ScenarioReport:
    if seed is not None:
        random.seed(seed)

    sensor = ScenarioSensor(device_id, channel_id, scenario="normal")
    sensor.initialize()
    sensor.start_measurement()
    pipeline = ChannelPipeline(calibration=_IDENTITY_CALIBRATION, unit="a.u.")
    detector = ChannelAnomalyDetector()

    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    signals: Set[str] = set()
    tick_index = 0

    def _tick(scenario_signals: Set[str]) -> None:
        nonlocal tick_index
        try:
            raw = sensor.read_measurement()
        except Exception:  # noqa: BLE001 - simulated fault (disconnect/comms), not a bug
            scenario_signals.add("read_error")
            tick_index += 1
            return
        raw = raw.model_copy(update={"timestamp": t0 + timedelta(seconds=tick_index * TICK_SECONDS)})
        tick_index += 1

        status = sensor.get_status()
        record = pipeline.process(raw, device_signal_quality=status.signal_quality)

        if record.status == "invalid":
            scenario_signals.add("invalid_status")
        elif record.status == "error":
            scenario_signals.add("error_status")
        if record.signal_quality is not None and record.signal_quality < 0.5:
            scenario_signals.add("low_quality")

        # mirrors backend/app/intelligence.py: no status == "valid" gate --
        # a genuine sustained rise drifts past Phase 3's frozen outlier
        # baseline and gets marked "invalid" forever, which is exactly what
        # Phase 6 needs to see, not filter out
        if record.estimated_value is not None:
            result = detector.update(record.timestamp, record.estimated_value, record.signal_quality)
            scenario_signals.update(result.reasons)

    # warm up a stable baseline -- same reason
    # tests/test_processing_phase3.py::test_pipeline_flags_sudden_abnormal_spike_as_invalid
    # does this: a fresh OutlierRejector/quality trend has nothing to
    # compare a genuine fault against otherwise
    warmup_signals: Set[str] = set()
    for _ in range(WARMUP_TICKS):
        _tick(warmup_signals)

    sensor.set_scenario(scenario)
    ticks_before = tick_index
    for _ in range(ticks):
        _tick(signals)

    return ScenarioReport(
        scenario=scenario,
        ticks_run=tick_index - ticks_before,
        signals_seen=signals,
        expected=EXPECTED_SIGNALS.get(scenario, set()),
    )


def run_backtest(ticks: int = 40, seed: Optional[int] = 20260830) -> List[ScenarioReport]:
    """`seed` reseeds `random` before *each* scenario (not once for the
    whole run) so scenario order never affects any individual result."""
    return [run_scenario(name, ticks=ticks, seed=seed) for name in EXPECTED_SIGNALS]


if __name__ == "__main__":
    reports = run_backtest()
    for report in reports:
        mark = "PASS" if report.passed else "FAIL"
        expected_str = ", ".join(sorted(report.expected)) or "-"
        seen_str = ", ".join(sorted(report.signals_seen)) or "-"
        print(f"[{mark}] {report.scenario:22s} expected={expected_str:20} seen={seen_str}")
    failures = [r for r in reports if not r.passed]
    print(f"\n{len(reports) - len(failures)}/{len(reports)} scenarios matched their expected signal(s).")
