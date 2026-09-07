"""
Phase 4 API schemas.

Response bodies reuse the Phase 1 contracts directly (`Device`,
`DeviceStatus`, `MeasurementRecord`, `BiomarkerResult` from common/schemas)
wherever docs/api/openapi.yaml already ties the API to one of them — that's
the whole point of Phase 1 being schema-first. Only genuinely API-only
shapes (auth, simulation control, alerts) get their own models here.
"""
from __future__ import annotations

from datetime import datetime
from typing import Annotated, List, Literal, Optional

from pydantic import BaseModel, Field

from common.schemas.measurement import BiomarkerUnit

# See common/schemas/measurement.py:_ID_MAX_LEN -- device_id/channel_id must
# fit the Phase 7 wire packet's fixed 12-byte ASCII field.
_ID_MAX_LEN = 12
_WireId = Annotated[str, Field(min_length=1, max_length=_ID_MAX_LEN)]

AlertType = Literal[
    "INFO",
    "WARNING",
    "CRITICAL",
    "DEVICE_ERROR",
    "SENSOR_ERROR",
    # Phase 6 intelligence engine (backend/app/intelligence.py) -- history-
    # based, as opposed to the threshold checks above which judge one
    # reading at a time
    "RAPID_TREND",
    "SUSTAINED_TREND",
    "QUALITY_DEGRADING",
]
AlertSeverity = Literal["info", "warning", "critical"]


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=64, examples=["admin"])
    password: str = Field(..., min_length=1, max_length=128, examples=["admin"])


class Token(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshRequest(BaseModel):
    refresh_token: str


class DeviceRegisterRequest(BaseModel):
    device_id: _WireId = Field(..., examples=["SB-001"])
    name: str = Field(..., min_length=1, max_length=120, examples=["Prototype #1"])
    firmware_version: str = Field(..., min_length=1, max_length=32, examples=["0.1.0"])
    channels: List[_WireId] = Field(default_factory=list, description="channel_ids, e.g. ['CH-01']")


class Alert(BaseModel):
    id: Optional[int] = None
    type: AlertType
    severity: AlertSeverity
    device_id: _WireId
    channel: Optional[_WireId] = None
    message: str = Field(..., min_length=1, max_length=500)
    timestamp: datetime
    resolved: bool = False


class SimulationStartRequest(BaseModel):
    device_id: _WireId = Field(..., examples=["SB-001"])
    channels: List[_WireId] = Field(default_factory=lambda: ["CH-01"])
    scenario: str = "normal"
    duration: Optional[float] = Field(None, ge=0, description="seconds; omit to run until /simulation/stop")
    noise: Optional[float] = Field(None, ge=0, description="stddev multiplier on the scenario's base noise")
    drift: Optional[float] = Field(None, description="per-second drift added to the scenario's base drift")
    # DT-6: twin-backed mode (backend/app/simulation.py's DigitalTwinDevice
    # integration). Omit patient_profile to get the existing scenario-only
    # simulation unchanged -- these three fields are a no-op without it.
    patient_profile: Optional[str] = Field(
        None,
        description="enables twin-backed mode: seeds a digital_twin.observation.DigitalTwinDevice from this named "
        "patient profile (see GET /simulation/patient-profiles) instead of running the scenario script",
        examples=["healthy_baseline", "diabetic_slow_healing", "immunocompromised_high_risk"],
    )
    time_scale: float = Field(
        1.0,
        gt=0,
        description="twin-backed mode only: multiplies wall-clock seconds into physiological seconds fed to the "
        "twin each tick -- 60 means one real second of simulation covers one minute of disease progression",
    )
    twin_channel_id: Optional[_WireId] = Field(
        None, description="twin-backed mode only: which channel the twin drives; defaults to the first of `channels`"
    )


class SimulationScenarioRequest(BaseModel):
    device_id: _WireId
    scenario: str = Field(..., examples=["normal", "rising_concentration", "electrode_degradation"])
    channel_id: Optional[_WireId] = Field(None, description="omit to apply to every channel on the device")


class PatientProfileInfo(BaseModel):
    """GET /simulation/patient-profiles -- lets the dashboard/mobile twin
    control panel populate its picker without hardcoding the registry."""

    name: str
    description: str


class TwinGroundTruth(BaseModel):
    """GET /simulation/twin/{device_id} -- dev-only "ground truth overlay"
    data (backend/app/config.py Settings.is_production gates this off in
    production, same as the seeded dev login): the hidden WoundState the
    twin actually integrated, next to the clean/true and actual/estimated
    values for one channel, so the dashboard/mobile panel can plot both."""

    device_id: _WireId
    channel_id: _WireId
    patient_profile: str
    time_scale: float
    inflammation: float
    bacterial_load: float
    moisture: float
    perfusion: float
    true_signal: float = Field(..., description="the clean value this channel would report with no noise/fouling")
    estimated_signal: Optional[float] = Field(
        None, description="the channel's actual (noisy, fouled) reported estimated_value"
    )
    timestamp: datetime


class BiomarkerWithTrend(BaseModel):
    channel_id: _WireId
    estimated_value: float
    unit: BiomarkerUnit
    confidence: Optional[float] = Field(None, ge=0.0, le=1.0)
    trend: Literal["rising", "falling", "stable", "unknown"] = "unknown"
    anomaly: bool = Field(False, description="Phase 6: is this channel's recent history anomalous right now?")
    timestamp: datetime


# ---- Phase 10: AI analysis (backend/app/routers/ai.py, gemini_client.py) ----


class AIInsightResponse(BaseModel):
    device_id: str
    summary: str = Field(..., description="Plain-language, Gemini-generated summary of the device's recent data")
    model: str
    generated_at: datetime


class AIChatMessage(BaseModel):
    role: Literal["user", "model"]
    text: str


class AIChatRequest(BaseModel):
    message: str = Field(..., min_length=1)
    history: List[AIChatMessage] = Field(default_factory=list, description="Prior turns, oldest first")


class AIChatResponse(BaseModel):
    device_id: str
    reply: str
    model: str
