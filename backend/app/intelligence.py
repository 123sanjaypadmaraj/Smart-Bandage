"""
Phase 6 intelligence engine wiring (Blueprint §10 Phase 6: "the system
interprets, not just displays").

Runs after `backend/app/alerts.py`'s single-reading threshold checks on
every processed measurement, using `processing/intelligence` to reason
over each channel's *history* instead of one reading at a time -- the
"still open" half of Phase 6 called out in
docs/architecture/overview.md's "Next (Phase 6+)" section.
"""
from __future__ import annotations

from typing import Dict, List, Tuple

from backend.app.schemas import Alert, AlertSeverity, AlertType
from common.schemas.measurement import MeasurementRecord
from processing.intelligence.anomaly import AnomalyReason, ChannelAnomalyDetector

# reason -> (alert type, severity, message template)
_REASON_ALERTS: Dict[AnomalyReason, Tuple[AlertType, AlertSeverity, str]] = {
    "rapid_change": ("RAPID_TREND", "warning", "{channel}: value changed sharply between readings"),
    "sustained_trend": ("SUSTAINED_TREND", "warning", "{channel}: sustained {direction} trend detected"),
    "quality_degrading": ("QUALITY_DEGRADING", "warning", "{channel}: signal quality trending down over time"),
}


class IntelligenceEngine:
    """One instance per running `DeviceSimulation` -- mirrors the
    per-channel `ChannelPipeline` dict it sits alongside in
    backend/app/simulation.py, so trend/anomaly state survives across
    ticks the same way filter/drift state does."""

    def __init__(self) -> None:
        self._detectors: Dict[str, ChannelAnomalyDetector] = {}

    def evaluate(self, record: MeasurementRecord) -> List[Alert]:
        """Deliberately does *not* require status == "valid": Phase 3's
        OutlierRejector never folds a flagged outlier back into its own
        rolling stats (processing/filtering.py), so a genuine sustained
        rise eventually drifts past that frozen baseline and gets every
        later reading marked "invalid" forever -- exactly the case Phase 6
        exists to catch (docs/architecture/overview.md, "Next (Phase 6+)":
        "catch drift or anomalies that a single-reading threshold can't
        see"). The calibration step still runs regardless of the outlier
        flag (processing/biomarkers/pipeline.py), so estimated_value is
        populated whenever there's anything to reason about; only a
        validation failure (status == "error") leaves it None."""
        if record.estimated_value is None:
            return []

        detector = self._detectors.setdefault(record.channel_id, ChannelAnomalyDetector())
        result = detector.update(record.timestamp, record.estimated_value, record.signal_quality)

        alerts: List[Alert] = []
        for reason in result.reasons:
            alert_type, severity, template = _REASON_ALERTS[reason]
            direction = result.value_trend.label if result.value_trend else "unknown"
            alerts.append(
                Alert(
                    type=alert_type,
                    severity=severity,
                    device_id=record.device_id,
                    channel=record.channel_id,
                    message=template.format(channel=record.channel_id, direction=direction),
                    timestamp=record.timestamp,
                )
            )
        return alerts
