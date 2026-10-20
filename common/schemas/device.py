"""
Device / channel data contracts (Blueprint §5, §16 data model).
"""
from __future__ import annotations

from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, Field

DeviceConnectionStatus = Literal["online", "offline", "unknown"]

# See common/schemas/measurement.py:_ID_MAX_LEN -- device_id/channel_id must
# fit the Phase 7 wire packet's fixed 12-byte ASCII field.
_ID_MAX_LEN = 12


class SensorChannel(BaseModel):
    """One electrode/channel on a device, and what it's calibrated to detect."""

    channel_id: str = Field(..., min_length=1, max_length=_ID_MAX_LEN, examples=["CH-01"])
    device_id: str = Field(..., min_length=1, max_length=_ID_MAX_LEN, examples=["SB-001"])
    # Free-form: real assay names vary by electrode/aptamer design (a
    # generic sensor_type has no fixed vocabulary), unlike device_id/
    # channel_id which are wire-format-constrained above.
    sensor_type: str = Field(..., examples=["pathogen_channel_1", "glucose", "pH"])
    biomarker_target: Optional[str] = Field(None, examples=["C-reactive protein", "glucose", "cortisol"])
    calibration_id: Optional[str] = Field(None, examples=["pathogen_channel_1:1.0"])


class Device(BaseModel):
    """A registered Smart Bandage device (physical or simulated)."""

    device_id: str = Field(
        ..., min_length=1, max_length=_ID_MAX_LEN, examples=["SB-001"],
        description="ASCII, <=12 bytes -- must fit the Phase 7 wire packet's device_id field",
    )
    name: str = Field(..., min_length=1, max_length=120, examples=["Prototype #1"])
    firmware_version: str = Field(
        ..., min_length=1, max_length=32, examples=["0.1.0", "sim-0.1.0"],
        description="Free-form version string -- semver by convention, but not enforced "
        "(the simulator tags itself 'sim-0.1.0', which isn't valid semver)",
    )
    channels: List[str] = Field(default_factory=list, description="channel_ids")
    status: DeviceConnectionStatus = "unknown"
    last_seen: Optional[datetime] = None


class DeviceStatus(BaseModel):
    """
    Live health snapshot returned by SensorInterface.get_status() and
    exposed via GET /device-status.
    """

    connected: bool
    battery: Optional[int] = Field(None, ge=0, le=100, description="Percent (0-100)")
    signal_quality: Optional[float] = Field(None, ge=0.0, le=1.0, description="0-1, Phase 3 quality scoring")
    last_error: Optional[str] = Field(None, max_length=500)
