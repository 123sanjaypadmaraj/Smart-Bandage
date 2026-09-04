"""
Phase 3 filtering primitives (Blueprint §10: "filtering, baseline/drift
correction, calibration").

Streaming (`update(value)` per reading), not batch, because the real
pipeline processes one measurement at a time as it arrives — from the
simulator today, from BLE in Phase 7/8. Stdlib only, matching the rest of
common/ and simulator/.
"""
from __future__ import annotations

import math
from collections import deque
from typing import Deque, Optional


class MovingAverageFilter:
    """Simple rolling-window mean. `ready` is False until the window fills,
    so callers can discount early readings (see processing/quality)."""

    def __init__(self, window: int = 5) -> None:
        if window < 1:
            raise ValueError("window must be >= 1")
        self.window = window
        self._buf: Deque[float] = deque(maxlen=window)

    def update(self, value: float) -> float:
        self._buf.append(value)
        return sum(self._buf) / len(self._buf)

    @property
    def ready(self) -> bool:
        return len(self._buf) == self.window


class ExponentialSmoothingFilter:
    """y_t = alpha*x_t + (1-alpha)*y_{t-1}. Used both as a fast smoother and,
    with a small alpha, as the slow baseline tracker for drift correction."""

    def __init__(self, alpha: float = 0.3) -> None:
        if not 0.0 < alpha <= 1.0:
            raise ValueError("alpha must be in (0, 1]")
        self.alpha = alpha
        self._value: Optional[float] = None

    def update(self, value: float) -> float:
        self._value = value if self._value is None else self.alpha * value + (1 - self.alpha) * self._value
        return self._value

    @property
    def value(self) -> Optional[float]:
        return self._value


class OutlierRejector:
    """
    Rolling z-score outlier detector. A flagged outlier is *not* folded into
    the rolling statistics — otherwise one spike drags the mean/std toward
    it and masks the next spike (Blueprint scenario 3: sudden abnormal
    signal must stay visibly abnormal, not get absorbed).
    """

    def __init__(self, window: int = 20, z_threshold: float = 3.5, min_samples: int = 5) -> None:
        if window < min_samples:
            raise ValueError("window must be >= min_samples")
        self.window = window
        self.z_threshold = z_threshold
        self.min_samples = min_samples
        self._buf: Deque[float] = deque(maxlen=window)

    def check(self, value: float) -> bool:
        """Returns True if `value` looks like an outlier against recent history."""
        if len(self._buf) < self.min_samples:
            self._buf.append(value)
            return False

        mean = sum(self._buf) / len(self._buf)
        variance = sum((v - mean) ** 2 for v in self._buf) / len(self._buf)
        std = math.sqrt(variance)

        is_outlier = (value != mean) if std == 0 else (abs(value - mean) / std > self.z_threshold)
        if not is_outlier:
            self._buf.append(value)
        return is_outlier
