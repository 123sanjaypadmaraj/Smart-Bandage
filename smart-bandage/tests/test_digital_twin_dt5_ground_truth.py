"""
DT-5 tests -- the ground-truth accessor (digital_twin/ground_truth.py) and
the digital-twin validation harness it feeds (ml/evaluation/twin_backtest.py).

Runs ml/evaluation/twin_backtest.py under pytest, the same way
tests/test_ml_phase6_backtest.py runs scenario_backtest.py -- see that
harness's module docstring for why there's no trained model, and
digital_twin/ground_truth.py's for why this accessor is deliberately kept
off SensorInterface.

The transfer-function formula itself (baseline + gains * state) is
DigitalTwinDevice.true_signal()'s (digital_twin/observation.py) -- this
module used to duplicate it as a standalone `true_channel_signal` helper;
that's gone post digital-twin consolidation, so the formula is exercised
here only through the shared method, not reimplemented in the test.
"""
from __future__ import annotations

import pytest

from digital_twin.ground_truth import GroundTruth, get_ground_truth
from digital_twin.observation import DigitalTwinDevice, get_channel_profile
from digital_twin.state import WoundState
from ml.evaluation.twin_backtest import ONSET_THRESHOLD, TRAJECTORIES, run_twin_backtest


def test_true_signal_matches_linear_response_formula():
    device = DigitalTwinDevice(
        "SB-001",
        channels={"CH-01": "test"},
        state=WoundState(inflammation=0.2, bacterial_load=0.1, moisture=0.6, perfusion=0.8),
    )
    device.initialize()
    profile = get_channel_profile("test")  # unregistered sensor_type -> _GENERIC_PROFILE

    expected = (
        profile.baseline
        + profile.inflammation_gain * 0.2
        + profile.bacterial_load_gain * 0.1
        + profile.moisture_gain * (0.6 - 0.5)
        + profile.perfusion_gain * (0.8 - 0.7)
    )
    assert device.true_signal("CH-01") == pytest.approx(expected)


def test_true_signal_is_unaffected_by_moisture_and_perfusion_at_their_defaults():
    """WoundState()'s moisture/perfusion defaults (0.5, 0.7) are exactly
    each gain's zero point in the transfer function -- a fresh
    healthy_baseline twin's true signal should come entirely from
    baseline + its (small, nonzero) resting inflammation/bacterial_load,
    with no contribution from moisture or perfusion."""
    device = DigitalTwinDevice("SB-001", channels={"CH-01": "pathogen_channel_1"})
    device.initialize()
    profile = get_channel_profile("pathogen_channel_1")
    state = device.state
    expected = profile.baseline + profile.inflammation_gain * state.inflammation + profile.bacterial_load_gain * state.bacterial_load
    assert device.true_signal("CH-01") == pytest.approx(expected)


def test_get_ground_truth_reads_the_devices_current_state():
    device = DigitalTwinDevice(
        "SB-001", channels={"CH-01": "pathogen_channel_1"}, state=WoundState(inflammation=0.3, bacterial_load=0.2)
    )
    device.initialize()
    device.start_measurement()
    device.read_channel("CH-01", dt_seconds=0.0)  # dt=0 -- no-op advance, state stays exactly as constructed

    gt = get_ground_truth(device, "CH-01", estimated_signal=123.4)

    assert isinstance(gt, GroundTruth)
    assert gt.device_id == "SB-001"
    assert gt.channel_id == "CH-01"
    assert gt.inflammation == pytest.approx(0.3)
    assert gt.bacterial_load == pytest.approx(0.2)
    assert gt.estimated_signal == pytest.approx(123.4)
    assert gt.true_signal == pytest.approx(device.true_signal("CH-01"))


def test_get_ground_truth_rejects_unknown_channel():
    device = DigitalTwinDevice("SB-001", channels={"CH-01": "pathogen_channel_1"})
    device.initialize()
    device.start_measurement()

    with pytest.raises(KeyError):
        get_ground_truth(device, "CH-99")


# --- ml/evaluation/twin_backtest.py -----------------------------------

_REPORTS = run_twin_backtest()


@pytest.mark.parametrize("report", _REPORTS, ids=lambda r: r.trajectory)
def test_twin_backtest_reports_are_well_formed(report):
    assert report.ticks_run > 0
    assert report.mean_abs_error >= 0
    assert report.max_abs_error >= report.mean_abs_error
    assert 0.0 <= report.false_alarm_rate <= 1.0


@pytest.mark.parametrize(
    "report", [r for r in _REPORTS if r.trajectory != "stable_healthy"], ids=lambda r: r.trajectory
)
def test_scripted_onset_actually_crosses_threshold_and_gets_caught(report):
    """Both onset trajectories retarget bacterial_load well past
    ONSET_THRESHOLD (infection_onset to 0.4, severe_onset to 0.75) -- if
    the crossing is never observed, the trajectory itself is
    misconfigured, not just "the alert engine didn't catch it"."""
    assert report.onset_true_cross_tick is not None, (
        f"{report.trajectory}: WoundState never reverted its bacterial_load past "
        f"ONSET_THRESHOLD={ONSET_THRESHOLD} -- check its onset_bacterial_load_target"
    )
    assert report.onset_detected, (
        f"{report.trajectory}: true onset at tick {report.onset_true_cross_tick} was never flagged by "
        f"evaluate_measurement()/IntelligenceEngine over the rest of the {report.ticks_run}-tick run"
    )
    # lead time is signed (negative = caught before the threshold was even
    # crossed); bound it loosely -- this is a regression guard against a
    # detector that's stopped reacting at all, not a precise SLA.
    assert -200 < report.alert_lead_time_ticks < 200


def test_stable_trajectory_has_no_scripted_onset():
    """The control run's false_alarm_count is only meaningful if nothing
    was ever scripted to be found -- sanity-checks the fixture, same
    reason test_ml_phase6_backtest.py checks its own scenario labels."""
    stable = next(t for t in TRAJECTORIES if t.name == "stable_healthy")
    assert stable.onset_tick is None

    report = next(r for r in _REPORTS if r.trajectory == "stable_healthy")
    assert report.onset_true_cross_tick is None
    assert report.onset_alert_tick is None
    # occasional noise-driven trend/quality blips are expected over 300
    # ticks of a mean-reverting process; this just guards against the
    # detector firing on nearly everything.
    assert report.false_alarm_rate < 0.15
