"""
ORM models (Phase 4). These mirror the Phase 1 Pydantic contracts in
common/schemas/ 1:1 -- the API layer (backend/app/schemas.py) is what
converts between the two, so the contracts nothing else may drift from
stay defined in exactly one place (common/).
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class UserORM(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String, unique=True, index=True)
    hashed_password: Mapped[str] = mapped_column(String)


class DeviceORM(Base):
    __tablename__ = "devices"

    device_id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String)
    firmware_version: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default="unknown")
    last_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    channels: Mapped[list["SensorChannelORM"]] = relationship(
        back_populates="device", cascade="all, delete-orphan"
    )


class SensorChannelORM(Base):
    __tablename__ = "sensor_channels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    channel_id: Mapped[str] = mapped_column(String, index=True)
    device_id: Mapped[str] = mapped_column(ForeignKey("devices.device_id"))
    sensor_type: Mapped[str] = mapped_column(String)
    biomarker_target: Mapped[str | None] = mapped_column(String, nullable=True)
    calibration_id: Mapped[str | None] = mapped_column(String, nullable=True)

    device: Mapped[DeviceORM] = relationship(back_populates="channels")


class MeasurementORM(Base):
    __tablename__ = "measurements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    device_id: Mapped[str] = mapped_column(String, index=True)
    channel_id: Mapped[str] = mapped_column(String, index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    raw_signal: Mapped[float] = mapped_column(Float)
    processed_signal: Mapped[float | None] = mapped_column(Float, nullable=True)
    estimated_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    unit: Mapped[str | None] = mapped_column(String, nullable=True)
    signal_quality: Mapped[float | None] = mapped_column(Float, nullable=True)
    temperature: Mapped[float | None] = mapped_column(Float, nullable=True)
    battery: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String, default="valid")


class AlertORM(Base):
    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    type: Mapped[str] = mapped_column(String)
    severity: Mapped[str] = mapped_column(String)
    device_id: Mapped[str] = mapped_column(String, index=True)
    channel: Mapped[str | None] = mapped_column(String, nullable=True)
    message: Mapped[str] = mapped_column(String)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    resolved: Mapped[bool] = mapped_column(Boolean, default=False)


class CalibrationORM(Base):
    __tablename__ = "calibrations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sensor_type: Mapped[str] = mapped_column(String, index=True)
    version: Mapped[str] = mapped_column(String)
    model_type: Mapped[str] = mapped_column(String)
    slope: Mapped[float | None] = mapped_column(Float, nullable=True)
    intercept: Mapped[float | None] = mapped_column(Float, nullable=True)
    coefficients_json: Mapped[str | None] = mapped_column(String, nullable=True)
    valid_from: Mapped[str] = mapped_column(String)
    valid_to: Mapped[str | None] = mapped_column(String, nullable=True)
