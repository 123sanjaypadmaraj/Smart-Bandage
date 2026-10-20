"""
Phase 6 tests -- trend detection, anomaly detection, and confidence
scoring (processing/intelligence/), exercised standalone and then against
the same scenario shapes tests/test_simulator_phase2.py already pins the
simulator to (rising_concentration's checkpoints, electrode_degradation's
quality curve).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from processing.intelligence.anomaly import ChannelAnomalyDetector
from processing.intelligence.confidence import estimate_confidence
from processing.intelligence.trend import detect_trend

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _series(values, step_seconds=1.0, start=T0):
    return [(start + timedelta(seconds=i * step_seconds), v) for i, v in enumerate(values)]


# ---- trend ----


def test_detect_trend_too_few_samples_is_unknown():
    result = detect_trend(_series([100.0]))
    assert result.label == "unknown"

    result = detect_trend(_series([100.0, 101.0]), min_samples=3)
    assert result.label == "unknown"


def test_detect_trend_flags_clean_rise():
    result = detect_trend(_series([100.0, 110.0, 125.0, 140.0, 155.0, 170.0]))
    assert result.label == "rising"
    assert result.slope_per_second > 0
    assert result.r_squared > 0.99  # near-perfect line


def test_detect_trend_flags_clean_fall():
    result = detect_trend(_series([170.0, 155.0, 140.0, 125.0, 110.0, 100.0]))
    assert result.label == "falling"
    assert result.slope_per_second < 0


def test_detect_trend_flat_noisy_series_is_stable():
    # small oscillation around 100, well under the 0.5%/s relative threshold
    values = [100.0, 100.4, 99.7, 100.2, 99.9, 100.3, 99.8]
    result = detect_trend(_series(values))
    assert result.label == "stable"


def test_detect_trend_identical_values_is_stable_and_perfectly_explained():
    result = detect_trend(_series([50.0, 50.0, 50.0, 50.0]))
    assert result.label == "stable"
    assert result.r_squared == 1.0


def test_detect_trend_relative_threshold_scales_with_magnitude():
    # same absolute slope (0.1/s), very different relative significance
    small_scale = detect_trend(_series([1.0, 1.1, 1.2, 1.3, 1.4]))
    large_scale = detect_trend(_series([10000.0, 10000.1, 10000.2, 10000.3, 10000.4]))
    assert small_scale.label == "rising"
    assert large_scale.label == "stable"


# ---- anomaly ----


def test_anomaly_detector_quiet_channel_reports_no_anomaly():
    det = ChannelAnomalyDetector()
    result = None
    for t, v in _series([100.0, 100.4, 99.7, 100.2, 99.9, 100.3, 99.8, 100.1]):
        result = det.update(t, v, signal_quality=0.95)
    assert result.is_anomaly is False
    assert result.score == 0.0


def test_anomaly_detector_flags_sustained_rise_like_rising_concentration_scenario():
    det = ChannelAnomalyDetector()
    result = None
    for t, v in _series([100.0, 110.0, 125.0, 140.0, 155.0, 170.0]):
        result = det.update(t, v, signal_quality=0.95)
    assert "sustained_trend" in result.reasons
    assert result.value_trend.label == "rising"
    assert result.is_anomaly is True


def test_anomaly_detector_flags_rapid_change_on_a_sudden_jump():
    det = ChannelAnomalyDetector(rapid_change_ratio=0.15)
    values = [105.0, 108.0, 110.0, 160.0, 185.0, 210.0]  # Blueprint scenario 3 checkpoints
    reasons_seen = []
    for t, v in _series(values):
        result = det.update(t, v, signal_quality=0.95)
        reasons_seen.append(result.reasons)
    # the 110 -> 160 jump (scenario's spike) must trip rapid_change
    assert any("rapid_change" in r for r in reasons_seen)
    assert "rapid_change" not in reasons_seen[0]  # first reading has nothing to compare against


def test_anomaly_detector_flags_declining_quality_like_electrode_degradation_scenario():
    det = ChannelAnomalyDetector()
    quality_curve = [0.98, 0.97, 0.94, 0.88, 0.76, 0.62]
    timestamps = [t for t, _ in _series(range(len(quality_curve)))]
    result = None
    for t, quality in zip(timestamps, quality_curve):
        result = det.update(t, 100.0, signal_quality=quality)  # stable value, only quality moves
    assert "quality_degrading" in result.reasons
    assert result.quality_trend.label == "falling"


def test_anomaly_detector_window_forgets_old_history():
    # a sustained rise that ages out of the window should stop being flagged
    # once the channel goes flat for longer than the window
    det = ChannelAnomalyDetector(window=6, min_samples=5)
    for t, v in _series([100.0, 110.0, 120.0, 130.0, 140.0, 150.0]):
        det.update(t, v, signal_quality=0.95)
    flat_start = T0 + timedelta(seconds=6)
    result = None
    for i, v in enumerate([150.0, 150.2, 149.8, 150.1, 149.9, 150.0, 150.1]):
        result = det.update(flat_start + timedelta(seconds=i), v, signal_quality=0.95)
    assert "sustained_trend" not in result.reasons


# ---- confidence ----


def test_confidence_matches_signal_quality_when_no_other_signal():
    assert estimate_confidence(signal_quality=0.9) == pytest.approx(0.9)


def test_confidence_none_quality_defaults_to_full_trust():
    assert estimate_confidence(signal_quality=None) == 1.0


def test_confidence_discounted_by_unstable_trend():
    stable_fit = estimate_confidence(signal_quality=0.9, trend_r_squared=1.0)
    noisy_fit = estimate_confidence(signal_quality=0.9, trend_r_squared=0.0)
    assert noisy_fit < stable_fit
    assert stable_fit == pytest.approx(0.9)


def test_confidence_discounted_by_anomaly_score():
    calm = estimate_confidence(signal_quality=0.9, anomaly_score=0.0)
    anomalous = estimate_confidence(signal_quality=0.9, anomaly_score=1.0)
    assert anomalous < calm
    assert anomalous == pytest.approx(0.9 * 0.6)


def test_confidence_never_exceeds_signal_quality():
    # trend_r_squared and anomaly_score only ever discount, never boost
    conf = estimate_confidence(signal_quality=0.5, trend_r_squared=1.0, anomaly_score=0.0)
    assert conf <= 0.5001

    conf = estimate_confidence(signal_quality=0.5, trend_r_squared=None, anomaly_score=0.0)
    assert conf == 0.5


def test_confidence_stays_in_unit_range():
    for sq in (0.0, 0.5, 1.0, None):
        for r2 in (0.0, 0.5, 1.0, None):
            for score in (0.0, 0.5, 1.0):
                c = estimate_confidence(signal_quality=sq, trend_r_squared=r2, anomaly_score=score)
                assert 0.0 <= c <= 1.0
