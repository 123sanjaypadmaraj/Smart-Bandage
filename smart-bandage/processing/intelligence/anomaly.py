"""
Phase 6 anomaly detection (Blueprint §10 Phase 6: "anomaly detection").

Operates one level above `processing/filtering.OutlierRejector`: that
detector flags a single spike within one DSP tick against its own recent
*raw* signal (window=20 raw readings, reset by every filtered/rejected
value). This module watches the *processed* stream of an already-valid
channel -- `estimated_value` and `signal_quality` -- over a longer window
and catches patterns a single reading can't reveal on its own:

- a sustained rise/fall (Blueprint scenario 2, "rising_concentration")
- a declining signal_quality trend (Blueprint scenario 4,
  "electrode_degradation")
- a sharp jump between consecutive *processed* readings that outlier
  rejection didn't already catch, because each individual raw reading
  was, on its own, still plausible

Stateful, one instance per (device_id, channel_id) -- mirrors
`processing/biomarkers/pipeline.ChannelPipeline`.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Deque, List, Optional, Tuple

from processing.intelligence.trend import TrendResult, detect_trend

AnomalyReason = str  # "rapid_change" | "sustained_trend" | "quality_degrading"


@dataclass
class AnomalyResult:
    is_anomaly: bool
    reasons: List[AnomalyReason] = field(default_factory=list)
    score: float = 0.0  # 0-1, roughly "how many independent signs of trouble"
    value_trend: Optional[TrendResult] = None
    quality_trend: Optional[TrendResult] = None


class ChannelAnomalyDetector:
    """Feed it every valid, calibrated reading for one channel, in order."""

    def __init__(
        self,
        window: int = 12,
        min_samples: int = 5,
        rapid_change_ratio: float = 0.15,
        trend_r_squared_threshold: float = 0.6,
        trend_flat_threshold: float = 0.005,
        quality_flat_threshold: float = 0.002,
    ) -> None:
        self.window = window
        self.min_samples = min_samples
        self.rapid_change_ratio = rapid_change_ratio
        self.trend_r_squared_threshold = trend_r_squared_threshold
        self.trend_flat_threshold = trend_flat_threshold
        self.quality_flat_threshold = quality_flat_threshold
        self._values: Deque[Tuple[datetime, float]] = deque(maxlen=window)
        self._quality: Deque[Tuple[datetime, float]] = deque(maxlen=window)

    def update(
        self,
        timestamp: datetime,
        value: float,
        signal_quality: Optional[float] = None,
    ) -> AnomalyResult:
        reasons: List[AnomalyReason] = []

        if self._values:
            _prev_t, prev_v = self._values[-1]
            magnitude = max(abs(prev_v), 1e-9)
            if abs(value - prev_v) / magnitude > self.rapid_change_ratio:
                reasons.append("rapid_change")

        self._values.append((timestamp, value))
        if signal_quality is not None:
            self._quality.append((timestamp, signal_quality))

        value_trend: Optional[TrendResult] = None
        if len(self._values) >= self.min_samples:
            value_trend = detect_trend(
                list(self._values),
                min_samples=self.min_samples,
                relative_flat_threshold=self.trend_flat_threshold,
            )
            if value_trend.label in ("rising", "falling") and value_trend.r_squared >= self.trend_r_squared_threshold:
                reasons.append("sustained_trend")

        quality_trend: Optional[TrendResult] = None
        if len(self._quality) >= self.min_samples:
            quality_trend = detect_trend(
                list(self._quality),
                min_samples=self.min_samples,
                relative_flat_threshold=self.quality_flat_threshold,
            )
            if quality_trend.label == "falling" and quality_trend.r_squared >= self.trend_r_squared_threshold:
                reasons.append("quality_degrading")

        score = round(min(1.0, 0.4 * len(reasons)), 4)
        return AnomalyResult(
            is_anomaly=bool(reasons),
            reasons=reasons,
            score=score,
            value_trend=value_trend,
            quality_trend=quality_trend,
        )
