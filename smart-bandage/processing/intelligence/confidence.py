"""
Phase 6 confidence scoring (Blueprint §10 Phase 6: "confidence scoring").

Distinct from `processing/quality.estimate_signal_quality` (Phase 3, which
judges one raw reading against its own recent noise). This judges how much
to trust the *interpretation* layered on top -- the trend/anomaly call --
by discounting the device's own signal_quality when the trend estimate is
statistically weak (low r_squared) or the channel is mid-anomaly. It never
overrides signal_quality upward: a device reporting a bad reading stays
low-confidence no matter how clean the trend looks.
"""
from __future__ import annotations

from typing import Optional


def estimate_confidence(
    *,
    signal_quality: Optional[float],
    trend_r_squared: Optional[float] = None,
    anomaly_score: float = 0.0,
) -> float:
    """Returns a 0-1 confidence score for a channel's current interpretation
    (its latest value plus whatever trend/anomaly call rides on it)."""
    confidence = 1.0 if signal_quality is None else max(0.0, min(1.0, signal_quality))

    if trend_r_squared is not None:
        # an unstable trend fit doesn't invalidate the reading itself, but
        # it means "rising"/"falling" is a weaker claim -- discount lightly
        confidence *= 0.7 + 0.3 * max(0.0, min(1.0, trend_r_squared))

    confidence *= 1.0 - 0.4 * max(0.0, min(1.0, anomaly_score))

    return round(max(0.0, min(1.0, confidence)), 4)
