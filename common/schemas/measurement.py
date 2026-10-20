"""
Measurement data contracts (Blueprint §5 "Data contracts").

The same shapes have to come out whether the source is the simulator, a
real ESP32 over BLE, a CSV import, or a REST call — see
docs/architecture/overview.md for why this file is Phase 1, not Phase 3.

Pipeline stages, in order:

    RawMeasurement        -- what SensorInterface.read_measurement() returns
        -> ProcessedMeasurement   (Phase 3: signal_processing)
        -> BiomarkerResult        (Phase 3/4: calibration + biomarkers)
        -> MeasurementRecord      (Phase 4: what gets stored/served)
"""
from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal, Optional

from pydantic import BaseModel, Field

MeasurementStatus = Literal["valid", "invalid", "error"]

# Every RawMeasurement that will ever cross real hardware gets encoded into a
# Phase 7 wire packet (common/protocol/packet.py:ID_FIELD_LEN), which stores
# device_id/channel_id in a fixed 12-byte ASCII field -- kept in sync with
# that constant by hand since packet.py already imports from this module
# (importing back would be circular). A value that fits here is guaranteed
# to survive the BLE round-trip once real hardware exists; one that doesn't
# fails fast in this schema instead of failing silently at encode time.
_ID_MAX_LEN = 12


class RawMeasurement(BaseModel):
    """Exactly what a sensor (real or simulated) hands back from one read."""

    device_id: str = Field(
        ..., min_length=1, max_length=_ID_MAX_LEN, examples=["SB-001"],
        description="ASCII, <=12 bytes -- must fit the Phase 7 wire packet's fixed device_id field",
    )
    channel_id: str = Field(
        ..., min_length=1, max_length=_ID_MAX_LEN, examples=["CH-01"],
        description="ASCII, <=12 bytes -- must fit the Phase 7 wire packet's fixed channel_id field",
    )
    timestamp: datetime
    raw_signal: float = Field(
        ...,
        description="Unprocessed electrochemical/ADC reading -- unitless counts or raw voltage; "
        "range depends on the AFE (e.g. AD5940/AD5933) and sensor_type, so intentionally unbounded here",
    )
    temperature: Optional[float] = Field(
        None,
        description="Degrees C, on-board thermistor against skin -- realistic wearable range is "
        "~25-42C. Deliberately unbounded here (not ge/le) so an implausible reading still "
        "constructs and reaches processing/biomarkers/pipeline.py:TEMPERATURE_RANGE (20-45C), which "
        "flags it as status='error' instead of a raw sensor fault crashing ingestion outright",
    )
    battery: Optional[int] = Field(None, ge=0, le=100, description="State of charge, percent (0-100)")


class ProcessedMeasurement(BaseModel):
    """RawMeasurement after filtering, baseline and drift correction."""

    processed_signal: float
    signal_quality: float = Field(..., ge=0.0, le=1.0, description="0-1 confidence in this reading")


# Free-form on purpose -- real biomarker units vary by assay (concentration,
# percent saturation, pH, or "a.u." for an uncalibrated/identity pipeline in
# tests and ml/evaluation/scenario_backtest.py) so this stays a plain str
# rather than a Literal/Enum; examples document the common real-world cases.
BiomarkerUnit = Annotated[str, Field(examples=["ng/mL", "mM", "µM", "mg/dL", "%", "pH", "a.u."])]


class BiomarkerResult(BaseModel):
    """ProcessedMeasurement after the calibration curve is applied."""

    estimated_value: float = Field(..., description="Concentration/level in `unit` -- sign and magnitude are assay-specific")
    unit: BiomarkerUnit
    confidence: Optional[float] = Field(None, ge=0.0, le=1.0, description="0-1, Phase 6 confidence scoring")


class MeasurementRecord(BaseModel):
    """
    The full, storable/servable record (Blueprint §5, "Measurement" contract).

    This is deliberately flat and deliberately keeps raw_signal alongside
    estimated_value — see docs/architecture/overview.md, "why keep raw
    data": debugging drift/degradation later needs the raw trace, not just
    the final number.
    """

    device_id: str = Field(..., min_length=1, max_length=_ID_MAX_LEN, examples=["SB-001"])
    channel_id: str = Field(..., min_length=1, max_length=_ID_MAX_LEN, examples=["CH-01"])
    timestamp: datetime
    raw_signal: float
    processed_signal: Optional[float] = None
    estimated_value: Optional[float] = None
    unit: Optional[BiomarkerUnit] = None
    signal_quality: Optional[float] = Field(None, ge=0.0, le=1.0)
    temperature: Optional[float] = Field(None, description="Degrees C (see RawMeasurement.temperature)")
    battery: Optional[int] = Field(None, ge=0, le=100, description="Percent (0-100)")
    status: MeasurementStatus = "valid"

    @classmethod
    def from_pipeline(
        cls,
        raw: RawMeasurement,
        processed: Optional[ProcessedMeasurement] = None,
        biomarker: Optional[BiomarkerResult] = None,
        status: MeasurementStatus = "valid",
    ) -> "MeasurementRecord":
        """Assemble a full record from the three pipeline stages."""
        return cls(
            device_id=raw.device_id,
            channel_id=raw.channel_id,
            timestamp=raw.timestamp,
            raw_signal=raw.raw_signal,
            processed_signal=processed.processed_signal if processed else None,
            signal_quality=processed.signal_quality if processed else None,
            estimated_value=biomarker.estimated_value if biomarker else None,
            unit=biomarker.unit if biomarker else None,
            temperature=raw.temperature,
            battery=raw.battery,
            status=status,
        )
