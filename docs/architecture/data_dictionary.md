# Data dictionary — every field, its type, and a realistic value

Companion to `docs/architecture/overview.md`. That doc explains *why* the
contracts in `common/schemas/` exist and how they flow between layers; this
one is the field-by-field reference — Python/Pydantic type, unit, realistic
range, and an example — for every shape that crosses a layer boundary
(`common/schemas/`, `backend/app/schemas.py`). ORM columns in
`backend/app/models.py` mirror these 1:1 (see that file's docstring) and
aren't repeated below.

A rendered, browsable version of this table is published as the "Smart
Bandage Data Dictionary" artifact (`Artifact(action: "list")`).

## Conventions used throughout

- **Wire-format IDs** (`device_id`, `channel_id`): ASCII, 1-12 bytes. The
  upper bound isn't arbitrary — it's `common/protocol/packet.py:ID_FIELD_LEN`,
  the fixed-width field a real ESP32 packs these into for the Phase 7 BLE
  wire protocol. `"SB-001"` / `"CH-01"` fit with room to spare.
- **Free-form fields** (`sensor_type`, `unit`, `firmware_version`, calibration
  `version`) are deliberately plain `str`, not `Literal`/`Enum` — the
  codebase already uses values an enum would have to special-case (`unit`
  `"a.u."` for the identity/uncalibrated pipeline in
  `ml/evaluation/scenario_backtest.py`; `sensor_type` `"generic"`, `"unknown"`,
  `"x"`; `firmware_version` `"sim-0.1.0"`, which isn't valid semver). Realism
  here means *documented examples*, not a closed vocabulary that would reject
  real assay names.
- **Physical plausibility bounds are not always schema constraints.**
  `temperature` looks unbounded in the tables below on purpose:
  `processing/biomarkers/pipeline.py:TEMPERATURE_RANGE` (20–45°C) is the
  actual check, applied *after* construction so an implausible reading still
  becomes a stored `status="error"` record instead of crashing ingestion —
  see `tests/test_processing_phase3.py::test_pipeline_marks_out_of_range_reading_as_error`.
  Where a bound below *is* enforced at the schema (`battery`, `signal_quality`,
  `confidence`), it's because being out of range there is a caller bug
  (percent/probability math), not a plausible-but-bad sensor reading.

---

## `common/schemas/measurement.py` — the pipeline stages

`RawMeasurement` → `ProcessedMeasurement` → `BiomarkerResult` → `MeasurementRecord`

### `RawMeasurement` — exactly what a sensor (real or simulated) hands back

| Field | Type | Realistic value | Notes |
|---|---|---|---|
| `device_id` | `str`, 1–12 chars | `"SB-001"` | ASCII; must fit the wire packet's 12-byte field |
| `channel_id` | `str`, 1–12 chars | `"CH-01"` | same wire-format constraint |
| `timestamp` | `datetime` (tz-aware) | `2026-08-31T14:52:00Z` | always UTC in this codebase — see `backend/app/crud.py:as_utc` |
| `raw_signal` | `float`, unbounded | `~100.0 ± noise` (simulator baseline) | unitless ADC counts / raw voltage; range is AFE- and sensor-specific |
| `temperature` | `float \| None`, unbounded | `36.5 ± 0.2` °C | realistic wearable range ~25–42°C; plausibility enforced downstream, not here |
| `battery` | `int \| None`, 0–100 | `87` | percent state of charge |

### `ProcessedMeasurement` — after filtering, baseline/drift correction

| Field | Type | Realistic value | Notes |
|---|---|---|---|
| `processed_signal` | `float` | `~100.0` | same unit as `raw_signal`, post-filtering |
| `signal_quality` | `float`, 0.0–1.0 | `0.94` | Phase 3 quality scoring; 1.0 = clean, near 0 = degraded/noisy |

### `BiomarkerResult` — after the calibration curve is applied

| Field | Type | Realistic value | Notes |
|---|---|---|---|
| `estimated_value` | `float` | `123.4` | concentration/level in `unit` — magnitude is assay-specific |
| `unit` | `str` (`BiomarkerUnit`) | `"ng/mL"`, `"mM"`, `"µM"`, `"mg/dL"`, `"%"`, `"pH"`, `"a.u."` | free-form; these are the values actually used in this repo |
| `confidence` | `float \| None`, 0.0–1.0 | `0.9` | Phase 6 confidence scoring |

### `MeasurementRecord` — the full, storable/servable record

Union of the three stages above plus:

| Field | Type | Realistic value | Notes |
|---|---|---|---|
| `status` | `Literal["valid","invalid","error"]` | `"valid"` | `"error"` = failed plausibility/quality check but kept for debugging (see conventions above) |

---

## `common/schemas/device.py`

### `SensorChannel`

| Field | Type | Realistic value | Notes |
|---|---|---|---|
| `channel_id` | `str`, 1–12 chars | `"CH-01"` | |
| `device_id` | `str`, 1–12 chars | `"SB-001"` | |
| `sensor_type` | `str`, free-form | `"pathogen_channel_1"`, `"glucose"`, `"pH"` | no fixed vocabulary — electrode/aptamer design-specific |
| `biomarker_target` | `str \| None` | `"C-reactive protein"`, `"cortisol"` | human-readable analyte name |
| `calibration_id` | `str \| None` | `"pathogen_channel_1:1.0"` | conventionally `f"{sensor_type}:{version}"` |

### `Device`

| Field | Type | Realistic value | Notes |
|---|---|---|---|
| `device_id` | `str`, 1–12 chars | `"SB-001"` | |
| `name` | `str`, 1–120 chars | `"Prototype #1"` | |
| `firmware_version` | `str`, 1–32 chars | `"0.1.0"`, `"sim-0.1.0"` | free-form; not enforced semver |
| `channels` | `list[str]` | `["CH-01", "CH-02"]` | channel_ids |
| `status` | `Literal["online","offline","unknown"]` | `"online"` | |
| `last_seen` | `datetime \| None` | — | UTC |

### `DeviceStatus` — live health snapshot

| Field | Type | Realistic value | Notes |
|---|---|---|---|
| `connected` | `bool` | `true` | |
| `battery` | `int \| None`, 0–100 | `73` | percent |
| `signal_quality` | `float \| None`, 0.0–1.0 | `0.88` | |
| `last_error` | `str \| None`, ≤500 chars | `"comms timeout"` | |

---

## `common/schemas/calibration.py` — `CalibrationParameters`

| Field | Type | Realistic value | Notes |
|---|---|---|---|
| `sensor_type` | `str` | `"pathogen_channel_1"` | matches `SensorChannel.sensor_type` |
| `version` | `str`, 1–32 chars | `"1.0"`, `"0.0-identity"` | free-form, not semver |
| `model_type` | `Literal["linear","polynomial"]` | `"linear"` | |
| `slope` | `float \| None` | `1.25` | required when `model_type="linear"`; unit is (biomarker unit)/(raw_signal unit) |
| `intercept` | `float \| None` | `-3.4` | same unit as `estimated_value` |
| `coefficients` | `list[float] \| None`, 1–8 items | `[0.002, -0.1, 4.0]` | highest degree first; required when `model_type="polynomial"`; capped at degree 7 |
| `valid_from` | `date` | `2026-01-01` | |
| `valid_to` | `date \| None` | — | open-ended if unset |

---

## `backend/app/schemas.py` — API-only shapes

| Model.field | Type | Realistic value | Notes |
|---|---|---|---|
| `LoginRequest.username` | `str`, 1–64 chars | `"admin"` | |
| `LoginRequest.password` | `str`, 1–128 chars | `"admin"` (dev seed only) | bcrypt truncates at 72 bytes regardless — see `backend/app/security.py` |
| `DeviceRegisterRequest.device_id` | `str`, 1–12 chars | `"SB-001"` | wire-format constraint |
| `DeviceRegisterRequest.channels` | `list[str]`, each 1–12 chars | `["CH-01"]` | |
| `Alert.type` | `Literal[...]` (8 values) | `"SUSTAINED_TREND"` | threshold-based + Phase 6 history-based types |
| `Alert.severity` | `Literal["info","warning","critical"]` | `"warning"` | |
| `Alert.message` | `str`, 1–500 chars | `"CH-01 signal quality degrading"` | |
| `SimulationStartRequest.noise` | `float \| None`, ≥0 | `1.5` | stddev multiplier on the scenario's base noise |
| `SimulationStartRequest.duration` | `float \| None`, ≥0 | `120.0` | seconds; omit to run until `/simulation/stop` |
| `BiomarkerWithTrend.trend` | `Literal["rising","falling","stable","unknown"]` | `"rising"` | |
| `AIChatMessage.role` | `Literal["user","model"]` | `"user"` | Gemini chat-turn role |
| `AIInsightResponse.summary` | `str` | *(Gemini-generated prose)* | grounded only in real pipeline output — see `processing/intelligence/ai_insight.py` |

---

## Why this file exists separately from the code

`common/schemas/*.py` and `backend/app/schemas.py` are the source of truth —
this file (and the published artifact) is a *reading* of them, kept
deliberately example-heavy rather than restating every `Field(...)` call
verbatim. If a field's constraint changes, update the schema first and this
table second; a mismatch here is a documentation bug, not a contract bug.
