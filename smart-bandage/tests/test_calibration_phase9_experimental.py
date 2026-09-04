"""
Phase 9 tests -- processing/calibration/experimental.py, validated against
synthetic ground truth (a known slope/intercept plus noise generated
below), since no real experimental calibration data exists yet (see that
module's docstring). What this proves: the fitting/reporting statistics
themselves are correct. What it can't prove: what a real electrode/aptamer
actually does -- that's Phase 9 proper, once the hardware exists.
"""
from __future__ import annotations

import random

import pytest

from common.schemas.calibration import CalibrationParameters
from processing.calibration.experimental import (
    build_calibration_parameters,
    build_experiment_report,
    drift,
    fit_calibration_curve,
    limit_of_detection,
    linearity,
    selectivity_ratio,
    sensitivity,
    stability,
)

from datetime import date, datetime, timedelta, timezone

TRUE_SLOPE = 4.2       # response units per concentration unit
TRUE_INTERCEPT = 10.0  # response units at zero concentration


def _synthetic_curve(n=12, noise_std=0.05, seed=42):
    rng = random.Random(seed)
    concentrations = [i * 2.0 for i in range(n)]  # 0, 2, 4, ... ng/mL
    pairs = [
        (c, TRUE_SLOPE * c + TRUE_INTERCEPT + rng.gauss(0.0, noise_std))
        for c in concentrations
    ]
    return pairs


# ---- fit_calibration_curve / sensitivity / linearity ----


def test_fit_recovers_known_slope_and_intercept_from_low_noise_data():
    pairs = _synthetic_curve(noise_std=0.01)
    slope, intercept, r_squared = fit_calibration_curve(pairs)
    assert slope == pytest.approx(TRUE_SLOPE, abs=0.05)
    assert intercept == pytest.approx(TRUE_INTERCEPT, abs=0.2)
    assert r_squared > 0.999


def test_sensitivity_matches_fitted_slope():
    pairs = _synthetic_curve()
    assert sensitivity(pairs) == pytest.approx(fit_calibration_curve(pairs)[0])


def test_linearity_degrades_with_more_noise():
    clean = linearity(_synthetic_curve(noise_std=0.01, seed=1))
    noisy = linearity(_synthetic_curve(noise_std=5.0, seed=1))
    assert clean > noisy
    assert clean > 0.99


def test_fit_requires_at_least_two_points():
    with pytest.raises(ValueError):
        fit_calibration_curve([(0.0, 10.0)])


# ---- build_calibration_parameters: the inversion ----


def test_built_calibration_inverts_the_fitted_curve_correctly():
    pairs = _synthetic_curve(noise_std=0.001)  # near-noiseless: check the inversion math precisely
    calibration = build_calibration_parameters(pairs, sensor_type="test-analyte", version="1.0", valid_from=date(2026, 1, 1))

    assert isinstance(calibration, CalibrationParameters)
    # applying the built calibration to a known response should recover
    # the concentration that produced it (within the small residual noise)
    for concentration, response in pairs:
        assert calibration.apply(response) == pytest.approx(concentration, abs=0.05)


def test_build_calibration_parameters_rejects_zero_sensitivity():
    flat_pairs = [(c, 5.0) for c in range(2, 10)]  # response doesn't move at all
    with pytest.raises(ValueError):
        build_calibration_parameters(flat_pairs, sensor_type="x", version="1.0", valid_from=date(2026, 1, 1))


# ---- limit_of_detection ----


def test_limit_of_detection_scales_with_blank_noise_and_inversely_with_sensitivity():
    rng = random.Random(7)
    quiet_blanks = [rng.gauss(10.0, 0.1) for _ in range(20)]
    noisy_blanks = [rng.gauss(10.0, 2.0) for _ in range(20)]

    lod_quiet = limit_of_detection(quiet_blanks, sensitivity_value=TRUE_SLOPE)
    lod_noisy = limit_of_detection(noisy_blanks, sensitivity_value=TRUE_SLOPE)
    assert lod_noisy > lod_quiet

    lod_less_sensitive = limit_of_detection(quiet_blanks, sensitivity_value=TRUE_SLOPE / 10)
    assert lod_less_sensitive > lod_quiet  # same noise, weaker sensor -> worse (higher) LOD


def test_limit_of_detection_requires_replicates_and_nonzero_sensitivity():
    with pytest.raises(ValueError):
        limit_of_detection([1.0], sensitivity_value=1.0)
    with pytest.raises(ValueError):
        limit_of_detection([1.0, 1.1, 0.9], sensitivity_value=0.0)


# ---- drift ----


def test_drift_recovers_a_known_slow_slope():
    rng = random.Random(3)
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    true_drift_per_hour = 0.3
    readings = [
        (t0 + timedelta(hours=h), 100.0 + true_drift_per_hour * h + rng.gauss(0.0, 0.02))
        for h in range(24)
    ]
    result = drift(readings)
    assert result.drift_per_hour == pytest.approx(true_drift_per_hour, abs=0.02)
    assert result.r_squared > 0.99


def test_drift_requires_at_least_two_readings():
    with pytest.raises(ValueError):
        drift([(datetime(2026, 1, 1, tzinfo=timezone.utc), 1.0)])


# ---- stability ----


def test_stability_is_low_for_tight_replicates_and_high_for_spread_ones():
    tight = stability([100.0, 100.1, 99.9, 100.05, 99.95])
    spread = stability([100.0, 110.0, 90.0, 105.0, 95.0])
    assert tight < spread
    assert tight < 1.0  # sub-1% CV for near-identical replicates


def test_stability_requires_replicates_and_nonzero_mean():
    with pytest.raises(ValueError):
        stability([1.0])
    with pytest.raises(ValueError):
        stability([1.0, -1.0])  # mean == 0


# ---- selectivity_ratio ----


def test_selectivity_ratio_favors_the_more_sensitive_target():
    assert selectivity_ratio(target_sensitivity=8.0, interferent_sensitivity=2.0) == pytest.approx(4.0)
    assert selectivity_ratio(target_sensitivity=1.0, interferent_sensitivity=1.0) == pytest.approx(1.0)


def test_selectivity_ratio_rejects_zero_interferent_sensitivity():
    with pytest.raises(ValueError):
        selectivity_ratio(target_sensitivity=1.0, interferent_sensitivity=0.0)


# ---- build_experiment_report: the end-to-end lab write-up ----


def test_experiment_report_with_only_the_calibration_curve():
    pairs = _synthetic_curve()
    report = build_experiment_report(pairs, sensor_type="test-analyte", version="1.0", valid_from=date(2026, 1, 1))

    assert report.n_points == len(pairs)
    assert report.sensitivity == pytest.approx(TRUE_SLOPE, abs=0.1)
    assert report.linearity_r_squared > 0.99
    assert report.limit_of_detection is None
    assert report.drift_per_hour is None
    assert report.stability_cv_pct is None


def test_experiment_report_fills_in_every_optional_experiment_when_provided():
    pairs = _synthetic_curve()
    rng = random.Random(11)
    blanks = [rng.gauss(10.0, 0.1) for _ in range(10)]
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    drift_readings = [(t0 + timedelta(hours=h), 100.0 + 0.1 * h) for h in range(10)]
    replicates = [50.0, 50.2, 49.9, 50.1]

    report = build_experiment_report(
        pairs,
        sensor_type="test-analyte",
        version="1.0",
        valid_from=date(2026, 1, 1),
        blank_responses=blanks,
        drift_readings=drift_readings,
        stability_replicates=replicates,
    )

    assert report.limit_of_detection is not None and report.limit_of_detection > 0
    assert report.drift_per_hour == pytest.approx(0.1, abs=0.01)
    assert report.stability_cv_pct is not None and report.stability_cv_pct < 1.0
