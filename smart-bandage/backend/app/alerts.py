"""
Phase 4/6 alert engine (Blueprint §10 Phase 6: "alert engine"; started early
here because GET /alerts is already part of the Phase 1 API surface).

Deliberately simple threshold rules for now -- trend/anomaly detection
(Phase 6 proper) builds on top of these once there's a history of real
records to reason about; this module is where that logic plugs in later.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional

from backend.app.schemas import Alert
from common.schemas.measurement import MeasurementRecord

LOW_QUALITY_THRESHOLD = 0.5
LOW_BATTERY_THRESHOLD = 15


def evaluate_measurement(record: MeasurementRecord) -> List[Alert]:
    """Rule-based checks run against one freshly processed record."""
    alerts: List[Alert] = []

    if record.status == "error":
        alerts.append(
            Alert(
                type="SENSOR_ERROR",
                severity="warning",
                device_id=record.device_id,
                channel=record.channel_id,
                message=f"{record.channel_id}: reading failed validation",
                timestamp=record.timestamp,
            )
        )
    elif record.status == "invalid":
        alerts.append(
            Alert(
                type="CRITICAL",
                severity="critical",
                device_id=record.device_id,
                channel=record.channel_id,
                message=f"{record.channel_id}: abnormal signal detected (outlier)",
                timestamp=record.timestamp,
            )
        )

    if record.signal_quality is not None and record.signal_quality < LOW_QUALITY_THRESHOLD:
        alerts.append(
            Alert(
                type="WARNING",
                severity="warning",
                device_id=record.device_id,
                channel=record.channel_id,
                message=f"{record.channel_id}: signal quality low ({record.signal_quality:.0%})",
                timestamp=record.timestamp,
            )
        )

    if record.battery is not None and record.battery <= LOW_BATTERY_THRESHOLD:
        alerts.append(
            Alert(
                type="WARNING",
                severity="warning",
                device_id=record.device_id,
                channel=record.channel_id,
                message=f"{record.device_id}: battery low ({record.battery}%)",
                timestamp=record.timestamp,
            )
        )

    return alerts


def device_error_alert(device_id: str, channel_id: Optional[str], message: str) -> Alert:
    """A read that raised entirely (disconnect, comms timeout) rather than
    producing a degraded-but-present record."""
    return Alert(
        type="DEVICE_ERROR",
        severity="critical",
        device_id=device_id,
        channel=channel_id,
        message=message,
        timestamp=datetime.now(timezone.utc),
    )
