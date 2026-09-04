"""Phase 4 DB access. Thin on purpose -- one function per query/write the
routers need, converting between ORM rows and the common/ Pydantic
contracts at the boundary."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models import AlertORM, DeviceORM, MeasurementORM, SensorChannelORM
from backend.app.schemas import Alert
from common.schemas.device import Device
from common.schemas.measurement import MeasurementRecord


def as_utc(dt: Optional[datetime]) -> Optional[datetime]:
    """Every datetime this app writes is UTC (simulator, pipeline, alerts all
    use datetime.now(timezone.utc)) -- but SQLite drops tzinfo on round-trip,
    so a naive value read back always means UTC too. Without this, the
    dashboard's `new Date(iso_string)` treats a naive ISO string as *local*
    time instead of UTC, silently shifting every historical timestamp by the
    browser's UTC offset relative to live WebSocket-pushed records (which
    never touch SQLite and keep their tzinfo)."""
    if dt is None or dt.tzinfo is not None:
        return dt
    return dt.replace(tzinfo=timezone.utc)


def as_naive_utc(dt: Optional[datetime]) -> Optional[datetime]:
    """Inverse of as_utc, for building query filters against the naive
    values actually stored in the column."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


# ---- devices ----


def upsert_device(db: Session, device: Device) -> DeviceORM:
    row = db.get(DeviceORM, device.device_id)
    if row is None:
        row = DeviceORM(device_id=device.device_id)
        db.add(row)
    row.name = device.name
    row.firmware_version = device.firmware_version
    row.status = device.status
    row.last_seen = device.last_seen
    db.flush()

    existing_channel_ids = {c.channel_id for c in row.channels}
    for channel_id in device.channels:
        if channel_id not in existing_channel_ids:
            db.add(SensorChannelORM(channel_id=channel_id, device_id=device.device_id, sensor_type="unknown"))
    db.commit()
    db.refresh(row)
    return row


def list_devices(db: Session) -> List[DeviceORM]:
    return list(db.scalars(select(DeviceORM)))


def get_device(db: Session, device_id: str) -> Optional[DeviceORM]:
    return db.get(DeviceORM, device_id)


def device_to_schema(row: DeviceORM) -> Device:
    return Device(
        device_id=row.device_id,
        name=row.name,
        firmware_version=row.firmware_version,
        channels=[c.channel_id for c in row.channels],
        status=row.status,  # type: ignore[arg-type]
        last_seen=as_utc(row.last_seen),
    )


def touch_device_last_seen(db: Session, device_id: str, when: datetime, connected: bool) -> None:
    row = db.get(DeviceORM, device_id)
    if row is None:
        return
    row.last_seen = when
    row.status = "online" if connected else "offline"
    db.commit()


# ---- measurements ----


def store_measurement(db: Session, record: MeasurementRecord) -> MeasurementORM:
    row = MeasurementORM(
        device_id=record.device_id,
        channel_id=record.channel_id,
        timestamp=record.timestamp,
        raw_signal=record.raw_signal,
        processed_signal=record.processed_signal,
        estimated_value=record.estimated_value,
        unit=record.unit,
        signal_quality=record.signal_quality,
        temperature=record.temperature,
        battery=record.battery,
        status=record.status,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def measurement_to_schema(row: MeasurementORM) -> MeasurementRecord:
    return MeasurementRecord(
        device_id=row.device_id,
        channel_id=row.channel_id,
        timestamp=as_utc(row.timestamp),
        raw_signal=row.raw_signal,
        processed_signal=row.processed_signal,
        estimated_value=row.estimated_value,
        unit=row.unit,
        signal_quality=row.signal_quality,
        temperature=row.temperature,
        battery=row.battery,
        status=row.status,  # type: ignore[arg-type]
    )


def query_measurements(
    db: Session,
    device_id: Optional[str] = None,
    channel_id: Optional[str] = None,
    since: Optional[datetime] = None,
    limit: int = 100,
) -> List[MeasurementORM]:
    stmt = select(MeasurementORM).order_by(MeasurementORM.timestamp.desc()).limit(limit)
    if device_id is not None:
        stmt = stmt.where(MeasurementORM.device_id == device_id)
    if channel_id is not None:
        stmt = stmt.where(MeasurementORM.channel_id == channel_id)
    if since is not None:
        stmt = stmt.where(MeasurementORM.timestamp >= as_naive_utc(since))
    return list(db.scalars(stmt))


def get_measurement(db: Session, measurement_id: int) -> Optional[MeasurementORM]:
    return db.get(MeasurementORM, measurement_id)


def latest_measurements_for_channel(db: Session, channel_id: str, limit: int = 2) -> List[MeasurementORM]:
    stmt = (
        select(MeasurementORM)
        .where(MeasurementORM.channel_id == channel_id, MeasurementORM.estimated_value.is_not(None))
        .order_by(MeasurementORM.timestamp.desc())
        .limit(limit)
    )
    return list(db.scalars(stmt))


def distinct_channel_ids(db: Session) -> List[str]:
    stmt = select(MeasurementORM.channel_id).distinct()
    return [row for row in db.scalars(stmt)]


# ---- alerts ----


def create_alert(db: Session, alert: Alert) -> AlertORM:
    row = AlertORM(
        type=alert.type,
        severity=alert.severity,
        device_id=alert.device_id,
        channel=alert.channel,
        message=alert.message,
        timestamp=alert.timestamp,
        resolved=alert.resolved,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def alert_to_schema(row: AlertORM) -> Alert:
    return Alert(
        id=row.id,
        type=row.type,  # type: ignore[arg-type]
        severity=row.severity,  # type: ignore[arg-type]
        device_id=row.device_id,
        channel=row.channel,
        message=row.message,
        timestamp=as_utc(row.timestamp),
        resolved=row.resolved,
    )


def list_active_alerts(db: Session, device_id: Optional[str] = None) -> List[AlertORM]:
    stmt = select(AlertORM).where(AlertORM.resolved.is_(False)).order_by(AlertORM.timestamp.desc())
    if device_id is not None:
        stmt = stmt.where(AlertORM.device_id == device_id)
    return list(db.scalars(stmt))
