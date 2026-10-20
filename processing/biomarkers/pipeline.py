"""
Phase 3 deliverable: "raw simulated data becomes processed sensor data"
(Blueprint §10 roadmap).

`ChannelPipeline` runs the full stage order from the blueprint:

    validation -> outlier rejection -> filtering -> baseline/drift
    correction -> feature extraction (quality) -> calibration

for one (device_id, channel_id) stream, turning each RawMeasurement into a
storable MeasurementRecord. One instance per channel — filters and the
outlier/drift trackers are stateful across reads, same as a real DSP chain.
"""
from __future__ import annotations

import math
from typing import Optional, Tuple

from common.schemas.calibration import CalibrationParameters
from common.schemas.measurement import (
    BiomarkerResult,
    MeasurementRecord,
    ProcessedMeasurement,
    RawMeasurement,
)
from processing.filtering.filtering import ExponentialSmoothingFilter, MovingAverageFilter, OutlierRejector
from processing.quality.quality import estimate_signal_quality

TEMPERATURE_RANGE = (20.0, 45.0)
BATTERY_RANGE = (0, 100)


def validate_raw(raw: RawMeasurement) -> Tuple[bool, Optional[str]]:
    """Stage 1: sanity-check a raw reading before it enters the DSP chain."""
    if not math.isfinite(raw.raw_signal):
        return False, "raw_signal is not finite"
    if raw.temperature is not None and not (TEMPERATURE_RANGE[0] <= raw.temperature <= TEMPERATURE_RANGE[1]):
        return False, f"temperature {raw.temperature} outside plausible range {TEMPERATURE_RANGE}"
    if raw.battery is not None and not (BATTERY_RANGE[0] <= raw.battery <= BATTERY_RANGE[1]):
        return False, f"battery {raw.battery} outside {BATTERY_RANGE}"
    return True, None


class ChannelPipeline:
    """
    Stateful per-channel processing pipeline.

    The drift corrector is a deliberate Phase 3 simplification: it assumes
    genuine concentration changes happen faster than `drift_alpha` tracks,
    so a slow exponential baseline can be subtracted off as "drift" without
    eating real signal. Phase 9 replaces this with a model fit to measured
    drift instead of an assumed time-constant.
    """

    def __init__(
        self,
        calibration: Optional[CalibrationParameters] = None,
        unit: str = "a.u.",
        filter_window: int = 5,
        outlier_window: int = 20,
        outlier_z: float = 3.5,
        drift_alpha: float = 0.01,
    ) -> None:
        self.calibration = calibration
        self.unit = unit
        self._filter = MovingAverageFilter(window=filter_window)
        self._outliers = OutlierRejector(window=outlier_window, z_threshold=outlier_z)
        self._drift_tracker = ExponentialSmoothingFilter(alpha=drift_alpha)
        self._reference_baseline: Optional[float] = None

    def process(
        self,
        raw: RawMeasurement,
        device_signal_quality: Optional[float] = None,
    ) -> MeasurementRecord:
        is_valid, reason = validate_raw(raw)
        if not is_valid:
            return MeasurementRecord.from_pipeline(raw, status="error")

        # stage 2: outlier rejection (flags, doesn't drop -- the reading is
        # still stored so drift/degradation can be diagnosed later)
        is_outlier = self._outliers.check(raw.raw_signal)

        # stage 3: filtering
        filtered = self._filter.update(raw.raw_signal)

        # stage 4: baseline/drift correction
        drift_est = self._drift_tracker.update(filtered)
        if self._reference_baseline is None:
            self._reference_baseline = drift_est
        corrected = filtered - (drift_est - self._reference_baseline)

        # stage 5: feature extraction -> quality score
        noise_ratio = abs(raw.raw_signal - filtered) / max(abs(filtered), 1e-6)
        quality = estimate_signal_quality(
            is_outlier=is_outlier,
            filter_ready=self._filter.ready,
            device_signal_quality=device_signal_quality,
            noise_ratio=noise_ratio,
        )
        processed = ProcessedMeasurement(processed_signal=round(corrected, 4), signal_quality=quality)

        # stage 6: calibration
        biomarker = None
        if self.calibration is not None:
            estimated = self.calibration.apply(corrected)
            biomarker = BiomarkerResult(
                estimated_value=round(estimated, 4),
                unit=self.unit,
                confidence=quality,
            )

        status = "invalid" if is_outlier else "valid"
        return MeasurementRecord.from_pipeline(raw, processed, biomarker, status=status)
