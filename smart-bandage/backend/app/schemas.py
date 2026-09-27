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
from typing import Annotated, Dict, List, Literal, Optional

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
    # Bandage-twin mode: the physics-based digital_twin.bandage.BandageTwin
    # (docs/architecture/bandage_twin.md) as a third engine beside scenario
    # playback and DT-6's DigitalTwinDevice. Mutually exclusive with
    # patient_profile -- both pick the engine, so the router 400s if both are
    # set. `time_scale` applies here too; `channels`/`twin_channel_id` don't
    # (the design's electrodes define the device's channels).
    bandage_design: Optional[str] = Field(
        None,
        description="enables bandage-twin mode: runs digital_twin.bandage.BandageTwin built from this named design "
        "preset (see GET /simulation/bandage-designs); the design's electrodes replace `channels`. Mutually "
        "exclusive with patient_profile",
        examples=["standard", "low_power", "fast_response"],
    )
    seed: Optional[int] = Field(
        None,
        ge=0,
        lt=2**63,
        description="bandage-twin mode only: master seed for the twin's random streams (same design + seed + "
        "time_scale -> same physics); omit for a fresh random seed",
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


class BandageDesignInfo(BaseModel):
    """GET /simulation/bandage-designs -- same role and shape as
    PatientProfileInfo, for digital_twin/bandage/design.py's PRESETS: lets
    the twin control panel populate its design picker without hardcoding
    preset names. `name` is what SimulationStartRequest.bandage_design takes."""

    name: str
    description: str


# ---- Bandage-twin WS messages (backend/app/simulation.py, DeviceSimulation._tick) ----
# Frozen wire contract shared with frontend/src/types.ts. The nested models
# mirror digital_twin/bandage dataclasses field-for-field (Truth, Analytes,
# ChannelTruth, EisParams, EisReading.to_dict()) -- they validate what's
# sent, they don't rename anything.


class BandageAnalytes(BaseModel):
    pathogen_marker: float  # nM
    glucose: float  # mM
    pH: float
    temperature_C: float
    moisture: float


class BandageChannelTruth(BaseModel):
    local_concentration: float
    wound_concentration: float
    ideal_au: float
    sensor_health: float
    fouling: float


class BandageTruthState(BaseModel):
    """dataclasses.asdict(digital_twin.bandage.twin.Truth)."""

    bacterial_load: float
    inflammation: float
    moisture: float
    perfusion: float
    analytes: BandageAnalytes
    contact: float
    link_quality: float
    battery_pct: float
    channels: Dict[str, BandageChannelTruth]


class TwinBandageTruthMessage(BaseModel):
    """WS "twin_bandage_truth" -- dev-only (never broadcast when
    Settings.is_production, same gate as twin_ground_truth): the bandage
    twin's hidden state at the instant this tick sampled. Deliberately a
    separate message from DT-6's twin_ground_truth, whose
    patient_profile/time_scale/one-channel shape doesn't fit this engine."""

    type: Literal["twin_bandage_truth"] = "twin_bandage_truth"
    device_id: _WireId
    bandage_design: str = Field(..., description="the preset key the simulation was started with")
    timestamp: datetime
    truth: BandageTruthState


class EisParamsPayload(BaseModel):
    rs: float
    rct: float
    cdl: float
    sigma: float
    n: float


class EisSpectrumPoint(BaseModel):
    freq: float
    re: float
    im: float
    mag: float
    phase_deg: float  # the dashboard maps this to twin3d ImpedancePoint.phase


class EisZRef(BaseModel):
    freq: float
    mag: float
    phase_deg: float


class EisDelta(BaseModel):
    z_ref_pct: float
    rct_pct: float
    cdl_pct: float


class EisDetection(BaseModel):
    detected: bool
    estimated_cfu: Optional[float]


class EisReadingPayload(BaseModel):
    """digital_twin.bandage.eis_adapter.EisReading.to_dict()."""

    channel_id: _WireId
    t_s: float
    local_concentration_nM: float
    theta: float
    cfu_equivalent_per_ml: float
    baseline_params: EisParamsPayload
    params: EisParamsPayload
    spectrum: List[EisSpectrumPoint]
    z_ref: EisZRef
    delta: EisDelta
    detection: EisDetection


class TwinEisMessage(BaseModel):
    """WS "twin_eis" -- one per aptamer (pathogen-marker) channel per tick.
    Not dev-gated: the impedance spectrum and derived Rct/Cdl/detection are
    what an EIS-reading bandage would actually report, not hidden state."""

    type: Literal["twin_eis"] = "twin_eis"
    device_id: _WireId
    channel_id: _WireId
    timestamp: datetime
    eis: EisReadingPayload


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
