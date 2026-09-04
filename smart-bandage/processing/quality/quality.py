"""
Phase 3 quality scoring (Blueprint §10 "feature extraction", §5 measurement
contract's `signal_quality` field).

Combines what the pipeline can see locally (is this reading an outlier? has
the filter seen enough history yet? how noisy does the raw trace look
against its own filtered value?) with whatever the sensor/device itself
reports — e.g. an electrode reporting its own declining quality
(Blueprint scenario 4) via DeviceStatus.signal_quality.
"""
from __future__ import annotations

from typing import Optional


def estimate_signal_quality(
    *,
    is_outlier: bool,
    filter_ready: bool,
    device_signal_quality: Optional[float] = None,
    noise_ratio: Optional[float] = None,
) -> float:
    """Returns a 0-1 confidence score for one processed reading."""
    quality = 1.0
    if is_outlier:
        quality -= 0.5
    if not filter_ready:
        quality -= 0.15  # not enough history yet to trust the filtered value fully
    if noise_ratio is not None:
        quality -= min(0.6, noise_ratio)

    quality = max(0.0, min(1.0, quality))
    if device_signal_quality is not None:
        quality *= max(0.0, min(1.0, device_signal_quality))
    return round(quality, 4)
