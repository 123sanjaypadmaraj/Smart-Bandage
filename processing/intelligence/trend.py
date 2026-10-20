"""
Phase 6 trend detection (Blueprint §10 Phase 6: "trend detection").

Fits a least-squares line through a window of (timestamp, value) readings
and classifies the result. This is what replaces the naive two-point diff
`backend/app/routers/biomarkers.py` used before Phase 6 existed with
something that survives single-reading noise and reports how much to
trust the classification (`r_squared`), not just the label. Stdlib only,
matching processing/filtering.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import List, Literal, Sequence, Tuple

TrendLabel = Literal["rising", "falling", "stable", "unknown"]


@dataclass(frozen=True)
class TrendResult:
    label: TrendLabel
    slope_per_second: float
    r_squared: float  # 0-1, how well the line explains the readings


def _least_squares(xs: Sequence[float], ys: Sequence[float]) -> Tuple[float, float, float]:
    """Returns (slope, intercept, r_squared) for the OLS fit y = slope*x + intercept."""
    n = len(xs)
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    ss_xx = sum((x - mean_x) ** 2 for x in xs)
    if ss_xx == 0:
        # every reading at the same instant (or n==1) -- no slope is estimable
        return 0.0, mean_y, 0.0

    ss_xy = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    slope = ss_xy / ss_xx
    intercept = mean_y - slope * mean_x

    ss_tot = sum((y - mean_y) ** 2 for y in ys)
    if ss_tot == 0:
        return slope, intercept, 1.0  # perfectly flat series, perfectly explained

    ss_res = sum((y - (slope * x + intercept)) ** 2 for x, y in zip(xs, ys))
    r_squared = max(0.0, 1.0 - ss_res / ss_tot)
    return slope, intercept, r_squared


def detect_trend(
    readings: Sequence[Tuple[datetime, float]],
    *,
    min_samples: int = 3,
    relative_flat_threshold: float = 0.005,
) -> TrendResult:
    """
    `readings` must be sorted oldest -> newest.

    `relative_flat_threshold` is the fraction of the series' own mean
    magnitude the fitted slope must move per second before it counts as a
    real trend rather than noise around a flat line -- 0.005 means "less
    than 0.5% of the mean value per second is 'stable'". Kept relative
    (not an absolute units/sec threshold) so the same detector works
    unmodified across channels with very different value ranges.

    The default is tuned to the wearable's actual streaming cadence
    (Blueprint: ~1 reading/second) -- the same overall percentage change
    spread across minutes instead of seconds divides down to a much
    smaller per-second slope and can read as "stable". Readings sampled
    far apart in wall-clock time (e.g. sparse manual entries, not the live
    simulation/device stream) should pass a smaller `relative_flat_threshold`.
    """
    if len(readings) < max(2, min_samples):
        return TrendResult(label="unknown", slope_per_second=0.0, r_squared=0.0)

    t0 = readings[0][0]
    xs = [(t - t0).total_seconds() for t, _ in readings]
    ys: List[float] = [v for _, v in readings]

    slope, _intercept, r_squared = _least_squares(xs, ys)

    mean_magnitude = max(abs(sum(ys) / len(ys)), 1e-9)
    relative_slope = slope / mean_magnitude

    label: TrendLabel
    if abs(relative_slope) < relative_flat_threshold:
        label = "stable"
    elif slope > 0:
        label = "rising"
    else:
        label = "falling"

    return TrendResult(label=label, slope_per_second=slope, r_squared=round(r_squared, 4))
