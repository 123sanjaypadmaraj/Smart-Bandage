"""
Phase 9 experimental calibration (Blueprint §10 Phase 9: "known
concentration -> sensor response -> sensitivity, linearity, LOD, drift,
selectivity, stability").

Blueprint §11 and docs/architecture/overview.md are both explicit that
Phase 9 needs the physical electrode and real calibration runs to exist
first -- there is no real experimental data in this repository. What this
module is: the fitting/reporting *tooling* Phase 9 will point at that data
once it exists, built and unit-tested now against synthetic ground truth
(a known slope/intercept plus noise, generated in the tests) so the
statistics themselves -- not the numbers they'll eventually be run
on -- are verified. Swapping in real data means calling these same
functions with real (concentration, response) pairs; nothing here
changes.

Deliberately separate from `common/schemas/calibration.CalibrationParameters`,
which is just the *data shape* a calibration takes (Phase 1) -- this module
is what *produces* one from a lab run, plus the accompanying quality
report a real calibration write-up needs.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional, Sequence, Tuple

from common.schemas.calibration import CalibrationParameters


def _ordinary_least_squares(xs: Sequence[float], ys: Sequence[float]) -> Tuple[float, float, float]:
    """Returns (slope, intercept, r_squared) for y = slope*x + intercept.
    Same method as processing/intelligence/trend.py's fit -- duplicated
    rather than imported because the two mean different things (a
    calibration curve fit here vs. a time trend there) and evolve for
    different reasons; both are small enough that sharing a private helper
    across packages isn't worth the coupling."""
    n = len(xs)
    if n < 2:
        raise ValueError("need at least 2 points to fit a line")

    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    ss_xx = sum((x - mean_x) ** 2 for x in xs)
    if ss_xx == 0:
        raise ValueError("all x values identical -- no slope is estimable")

    ss_xy = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    slope = ss_xy / ss_xx
    intercept = mean_y - slope * mean_x

    ss_tot = sum((y - mean_y) ** 2 for y in ys)
    if ss_tot == 0:
        return slope, intercept, 1.0
    ss_res = sum((y - (slope * x + intercept)) ** 2 for x, y in zip(xs, ys))
    r_squared = max(0.0, 1.0 - ss_res / ss_tot)
    return slope, intercept, r_squared


def fit_calibration_curve(pairs: Sequence[Tuple[float, float]]) -> Tuple[float, float, float]:
    """`pairs` = (known_concentration, sensor_response) -- the calibration
    curve the way a lab run actually measures it: response as a function
    of a known, prepared concentration. Returns
    (slope, intercept, r_squared) for response = slope*concentration + intercept.
    """
    concentrations = [c for c, _r in pairs]
    responses = [r for _c, r in pairs]
    return _ordinary_least_squares(concentrations, responses)


def build_calibration_parameters(
    pairs: Sequence[Tuple[float, float]],
    *,
    sensor_type: str,
    version: str,
    valid_from: date,
) -> CalibrationParameters:
    """Inverts the fitted response-vs-concentration curve into the
    concentration = slope*response + intercept shape
    `CalibrationParameters.apply()` expects -- that's the direction
    `processing/biomarkers/pipeline.py` actually calls at runtime,
    converting one live processed signal back into a concentration
    estimate."""
    slope, intercept, _r_squared = fit_calibration_curve(pairs)
    if slope == 0:
        raise ValueError("zero sensitivity -- response doesn't vary with concentration, can't invert")
    return CalibrationParameters(
        sensor_type=sensor_type,
        version=version,
        model_type="linear",
        slope=1.0 / slope,
        intercept=-intercept / slope,
        valid_from=valid_from,
    )


def sensitivity(pairs: Sequence[Tuple[float, float]]) -> float:
    """Response units per concentration unit -- the slope of the
    response-vs-concentration calibration curve. Larger magnitude means a
    more responsive sensor over the tested range."""
    slope, _intercept, _r_squared = fit_calibration_curve(pairs)
    return slope


def linearity(pairs: Sequence[Tuple[float, float]]) -> float:
    """R² of the linear fit -- how well a straight line explains the
    calibration curve over the tested concentration range. Low r_squared
    means either genuine curvature (a linear CalibrationParameters is the
    wrong model -- see model_type="polynomial") or noisy replicates."""
    _slope, _intercept, r_squared = fit_calibration_curve(pairs)
    return r_squared


def limit_of_detection(blank_responses: Sequence[float], sensitivity_value: float, k: float = 3.0) -> float:
    """Standard LOD definition: `blank_mean + k * blank_std`, converted
    from response units into concentration units via the curve's
    sensitivity. `blank_responses` are repeated readings of a
    zero-concentration ("blank") sample; k=3 is the conventional
    3-sigma detection criterion (k=10 is sometimes used instead for
    "limit of quantitation")."""
    n = len(blank_responses)
    if n < 2:
        raise ValueError("need at least 2 blank replicates to estimate a standard deviation")
    if sensitivity_value == 0:
        raise ValueError("zero sensitivity -- LOD is undefined")

    mean_blank = sum(blank_responses) / n
    variance = sum((v - mean_blank) ** 2 for v in blank_responses) / (n - 1)  # sample std, n-1
    std_blank = math.sqrt(variance)
    return abs(k * std_blank / sensitivity_value)


@dataclass(frozen=True)
class DriftResult:
    drift_per_hour: float  # response units/hour
    r_squared: float       # how linear the drift itself is


def drift(readings: Sequence[Tuple[datetime, float]]) -> DriftResult:
    """Repeated measurements of one fixed, known sample over time -- fits
    a line through (elapsed_hours, response) and reports the slope as
    response-units/hour. `readings` must be sorted oldest -> newest.
    This is a *response*-level drift measurement (how much the sensor's
    reading of a constant sample creeps over hours/days), distinct from
    processing/biomarkers/pipeline.py's per-session baseline drift
    correction, which assumes an exponential model tuned for
    minutes-scale sessions, not a multi-hour calibration-stability check."""
    if len(readings) < 2:
        raise ValueError("need at least 2 readings to estimate drift")
    t0 = readings[0][0]
    xs = [(t - t0).total_seconds() / 3600.0 for t, _v in readings]
    ys = [v for _t, v in readings]
    slope, _intercept, r_squared = _ordinary_least_squares(xs, ys)
    return DriftResult(drift_per_hour=slope, r_squared=round(r_squared, 4))


def stability(repeated_responses: Sequence[float]) -> float:
    """Coefficient of variation, as a percent: std / |mean| * 100, over
    repeated readings of one fixed sample. The standard repeatability
    figure in an analytical calibration write-up -- lower is more stable."""
    n = len(repeated_responses)
    if n < 2:
        raise ValueError("need at least 2 replicates to estimate stability")
    mean = sum(repeated_responses) / n
    if mean == 0:
        raise ValueError("mean response is zero -- coefficient of variation is undefined")
    variance = sum((v - mean) ** 2 for v in repeated_responses) / (n - 1)
    std = math.sqrt(variance)
    return abs(std / mean) * 100.0


def selectivity_ratio(target_sensitivity: float, interferent_sensitivity: float) -> float:
    """Target-analyte sensitivity divided by an interfering substance's
    sensitivity (both from separate `sensitivity()` calls against
    single-substance calibration runs) -- the standard selectivity
    coefficient. >1 means the sensor responds more to the intended
    biomarker than to the interferent; close to 1 means it can't really
    tell them apart."""
    if interferent_sensitivity == 0:
        raise ValueError("zero interferent sensitivity -- selectivity ratio is undefined (would be infinite)")
    return abs(target_sensitivity / interferent_sensitivity)


@dataclass(frozen=True)
class CalibrationExperimentReport:
    """Everything Blueprint §10 Phase 9 asks a calibration run to
    produce, gathered in one place for a write-up or a
    CalibrationParameters version bump."""

    calibration: CalibrationParameters
    sensitivity: float
    linearity_r_squared: float
    n_points: int
    limit_of_detection: Optional[float] = None
    drift_per_hour: Optional[float] = None
    stability_cv_pct: Optional[float] = None


def build_experiment_report(
    pairs: Sequence[Tuple[float, float]],
    *,
    sensor_type: str,
    version: str,
    valid_from: date,
    blank_responses: Optional[Sequence[float]] = None,
    drift_readings: Optional[Sequence[Tuple[datetime, float]]] = None,
    stability_replicates: Optional[Sequence[float]] = None,
) -> CalibrationExperimentReport:
    """One call that runs a full calibration curve fit plus whichever of
    the optional companion experiments (blank replicates for LOD, a
    multi-hour drift run, repeatability replicates) were actually
    collected. Every argument beyond `pairs` is optional because a real
    lab calibration session builds these up incrementally, not all at
    once."""
    calibration = build_calibration_parameters(pairs, sensor_type=sensor_type, version=version, valid_from=valid_from)
    sens = sensitivity(pairs)
    r_squared = linearity(pairs)

    lod = limit_of_detection(blank_responses, sens) if blank_responses is not None else None
    drift_per_hour = drift(drift_readings).drift_per_hour if drift_readings is not None else None
    stability_cv = stability(stability_replicates) if stability_replicates is not None else None

    return CalibrationExperimentReport(
        calibration=calibration,
        sensitivity=sens,
        linearity_r_squared=r_squared,
        n_points=len(pairs),
        limit_of_detection=lod,
        drift_per_hour=drift_per_hour,
        stability_cv_pct=stability_cv,
    )
