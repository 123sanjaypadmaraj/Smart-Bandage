# Architecture overview — Phases 1-5

Full visual breakdown (diagrams, tech stack, roadmap, scenarios): see the
published blueprint artifact from this session. If you don't have the link,
ask Claude — it can be relisted with `Artifact(action: "list")`.

## What Phase 1 establishes

Contracts, not features. Every later phase codes against what's in this
directory instead of re-deciding it:

1. **`common/schemas/`** — the shapes that move between every layer:
   - `measurement.py` — `RawMeasurement` → `ProcessedMeasurement` →
     `BiomarkerResult` → `MeasurementRecord`
   - `device.py` — `Device`, `SensorChannel`, `DeviceStatus`
   - `calibration.py` — `CalibrationParameters` (linear or polynomial)
2. **`common/interfaces/sensor_interface.py`** — `SensorInterface`, the
   hardware abstraction boundary. `RealSensor` (Phase 8) and
   `SimulatedSensor` (Phase 2, skeleton here) both implement it; nothing
   else in the codebase is allowed to import ESP32/AFE specifics or
   simulator internals directly.
3. **`docs/api/openapi.yaml`** — the REST/WebSocket surface the backend
   (Phase 4) implements and the dashboard (Phase 5) is written against.

## Why a `common/` package

The repository layout in the blueprint doesn't call out a shared package
explicitly, but the simulator, backend, and processing layers all need the
*identical* Pydantic models — duplicating them would silently reintroduce
the "two dashboards" failure mode the blueprint warns against (§28: never
build a separate fake dashboard for simulation). `common/` is the one
place `MeasurementRecord` is defined; everything imports it from there.

## Why raw_signal is kept alongside estimated_value

`MeasurementRecord` stores `raw_signal` even once `estimated_value` exists.
Debugging electrode degradation, drift, or a bad calibration later requires
the raw trace — recomputing it from a final concentration number is not
possible. See blueprint §7.

## What's real vs. scaffolded right now

| Path | Status |
|---|---|
| `common/schemas/*.py` | Real — validated by `tests/test_contracts.py` |
| `common/interfaces/sensor_interface.py` | Real |
| `simulator/sensors/simulated_sensor.py` | Real, minimal Phase 1 reference implementation — kept as-is for the contract tests to pin to |
| `simulator/signals/`, `simulator/faults/`, `simulator/scenarios/`, `simulator/sensors/scenario_sensor.py`, `simulator/sensors/multi_channel_sensor.py` | Real (Phase 2) — the 7 scenarios, fault injection, multi-channel coordination. `tests/test_simulator_phase2.py` |
| `processing/filtering/`, `processing/quality/`, `processing/calibration/`, `processing/biomarkers/pipeline.py` | Real (Phase 3) — validation → outlier rejection → filtering → drift correction → quality → calibration, one `ChannelPipeline` per channel. `tests/test_processing_phase3.py` |
| `docs/api/openapi.yaml` | Real — every path implemented by `backend/app/` (Phase 4) |
| `backend/app/` | Real (Phase 4) — FastAPI + SQLAlchemy (SQLite by default, Postgres via `DATABASE_URL`), JWT auth, simulation manager wired to `simulator/` + `processing/`, WebSocket push, rule-based alert engine. `backend/tests/test_api.py` |
| `frontend/src/` | Real (Phase 5) — React + TypeScript + Tailwind dashboard: login, device registry, live status, measurement chart, alerts, simulation controls |
| `processing/intelligence/` | Real (Phase 6) — `trend.py` (least-squares trend classification), `anomaly.py` (`ChannelAnomalyDetector`, history-based), `confidence.py`. Wired into `backend/app/intelligence.py` (new alert types) and `backend/app/routers/biomarkers.py`. `tests/test_intelligence_phase6.py` |
| `ml/evaluation/scenario_backtest.py` | Real (Phase 6 "ML experiments") — backtests the Phase 3+6 pipeline against the 7 simulator scenarios as labelled-by-construction data; no trained model yet (Blueprint §11: real ML waits on real labelled data). `tests/test_ml_phase6_backtest.py` |
| `common/protocol/packet.py` | Real (Phase 7) — the wire packet format, encode/decode/validate. `tests/test_firmware_phase7_protocol.py` |
| `firmware/` | Written to that spec (Phase 7), **not compiled or flashed** — no ESP32/toolchain in this environment. See `docs/hardware/packet_protocol.md` |
| `common/interfaces/real_sensor.py`, `transport.py` | Real (Phase 8) — `RealSensor` decodes Phase 7 packets off an injectable `Transport`; proven against the real `ChannelPipeline` using a fake in-memory transport. `tests/test_hardware_phase8_real_sensor.py` |
| `processing/calibration/experimental.py` | Real (Phase 9) — sensitivity/linearity/LOD/drift/selectivity/stability tooling, validated against synthetic ground truth (no real calibration data exists yet). `tests/test_calibration_phase9_experimental.py` |
| `processing/intelligence/ai_insight.py`, `backend/app/gemini_client.py`, `backend/app/routers/ai.py` | Real (Phase 10) — Gemini-backed natural-language insight (`GET /devices/{id}/ai/insight`) and grounded chat (`POST /devices/{id}/ai/chat`), both built from a device's real Phase 3/6 pipeline output, never invented. The Gemini transport is a swappable `Transport` (mirrors `common/interfaces/transport.py`'s Phase 7/8 pattern), so `tests/test_gemini_client_phase10.py`, `tests/test_ai_insight_phase10.py`, and `backend/tests/test_ai_analysis.py` all run with zero network access via a fake transport / mocked FastAPI dependency. Opt-in behind `GEMINI_API_KEY` — unset, the two endpoints 503 and nothing else changes. Surfaced on both the dashboard (`frontend/src/components/AIInsightPanel.tsx`) and mobile (`mobile/src/screens/MonitorScreen.tsx`) |

## Next

What no amount of software can finish from here: Phase 7's firmware
compiled and flashed onto real ESP32 + AFE hardware, Phase 8's
`RealSensor`/`Transport` proven against that hardware's actual BLE/serial
link instead of a fake one, and Phase 9's calibration tooling run against
a real electrode's measured response instead of synthetic data. All three
need the physical electrode and ESP32 to exist first — nothing in
`processing/`, `backend/`, or `common/protocol/` needs to change once they
do; that's the point of `SensorInterface` and `Transport` both being
abstractions real code already runs against today.
