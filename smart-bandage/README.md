# Smart Bandage

A wearable biosensor platform: screen-printed electrode + aptamer recognition
on the hardware side, a full software stack (signal processing, calibration,
backend, dashboard) built and tested against a **simulated** sensor before
the physical bandage exists.

Full architecture, diagrams, tech stack, and the 9-phase roadmap: see the
"Smart Bandage Blueprint" artifact published alongside this repo (ask
Claude for the link, or `Artifact(action: "list")` in this project).

## Status

| Phase | What it delivers | Status |
|---|---|---|
| 1 — Architecture | repo layout, data contracts, sensor interface, API spec | ✅ done |
| 2 — Hardware simulator | multi-channel virtual sensor, 7 fault/noise scenarios | ✅ done |
| 3 — Signal processing | filtering, baseline/drift correction, calibration | ✅ done |
| 4 — Backend | FastAPI + PostgreSQL(-or-SQLite) implementing `docs/api/openapi.yaml` | ✅ done |
| 5 — Dashboard | React monitoring UI | ✅ done |
| 6 — Intelligence | trend/anomaly detection, quality scoring, alert engine, confidence scoring, ML experiments | ✅ done — `processing/intelligence/`, wired into the alert engine and `/biomarkers/{name}`; ML experiments scoped to a backtest against simulated ground truth (see `ml/evaluation/scenario_backtest.py`) since Blueprint §11 puts real ML training last, after real labelled data exists |
| 7 — Embedded firmware | ESP32 + AFE + BLE + packet protocol | 🟡 partial — the wire packet protocol (`common/protocol/packet.py`) is real and tested; `firmware/` (the actual ESP32 sketch + drivers) is written to that spec but **not compiled or flashed** — needs real hardware to build/bench-test |
| 8 — Hardware integration | swap `SimulatedSensor` for `RealSensor` | ✅ done in software — `common/interfaces/real_sensor.py` decodes the Phase 7 protocol off an injectable `Transport`; proven against the real `ChannelPipeline` using a fake in-memory transport (no ESP32 exists to test the transport's other end against) |
| 9 — Experimental calibration | real calibration data replaces simulated assumptions | 🟡 partial — the fitting/reporting tooling (`processing/calibration/experimental.py`: sensitivity, linearity, LOD, drift, selectivity, stability) is built and validated against synthetic ground truth; running it against a *real* calibration curve needs the physical electrode |
| — Mobile | React Native counterpart to the dashboard | ✅ done — `mobile/`, three screens (sign in, devices, live monitor) against the same Phase 4 backend; verified via `npm run web` (react-native-web) end to end (auth, device list, live WS readings + alerts). EAS Build config (`mobile/app.json`, `mobile/eas.json`) is scaffolded for real-device builds — 🟡 partial, not yet run against a real Expo/App Store/Play Console account (see `mobile/README.md` § Building for real devices) |
| — Containerization | Docker images + compose for backend/dashboard | ✅ done — `docker/`, `docker-compose.yml`; Postgres is now the default (`docker compose up --build`), matching what `ENVIRONMENT=production` actually requires — the old zero-dependency SQLite path moved to the standalone `docker-compose.sqlite.yml`. `docker-compose.tls.yml` overlay adds real HTTPS (nginx + uvicorn's built-in TLS) — verified end to end locally with a self-signed cert (`openssl req ... -subj "/CN=localhost"`): backend served `/health`/`/docs` over `:8443`, nginx served the dashboard over `:443`, and the plain-HTTP `:80`→`:443` redirect all worked. Still needs a real domain + CA-issued certificate (e.g. via `certbot`) for an actual public deploy — no such domain exists in this environment |
| — Dev/ops scripts | seed data, DB reset, one-command dev startup, CI-equivalent checks, production secret generation | ✅ done — `scripts/` (`seed_demo_data.py`, `reset_db.py`, `dev_up.sh`, `run_checks.sh`, `generate_secret.py`), all run and verified |
| 10 — AI analysis | Gemini-backed natural-language insight + chat, grounded in real device data | ✅ done — `backend/app/gemini_client.py` (transport, mockable), `processing/intelligence/ai_insight.py` (prompt building), `backend/app/routers/ai.py` (`GET`/`POST /devices/{id}/ai/{insight,chat}`); dashboard panel (`frontend/src/components/AIInsightPanel.tsx`) and mobile screen section (`mobile/src/screens/MonitorScreen.tsx`) both wired up. Opt-in: unset `GEMINI_API_KEY` returns a clean 503, nothing else in the app depends on it |

Phases 1-6, 10, and the software half of 7-9 are exercised end to end by
`pytest` (149 tests: schemas, the 7 simulator scenarios, the processing +
intelligence pipeline, the full FastAPI surface including the WebSocket
loop and the Phase 10 AI endpoints (mocked Gemini transport, no network
calls), the wire packet protocol, `RealSensor` against a fake transport,
the calibration-fitting tooling against synthetic data, and the
production-safety startup guard below) and were verified live in a browser
against a running backend + dashboard. `.github/workflows/ci.yml` now runs
the same pytest + frontend build + mobile typecheck on every push/PR —
`scripts/run_checks.sh` is still the one-command local equivalent.

### Production readiness

Every schema in `common/schemas/` and `backend/app/schemas.py` carries
realistic per-field types now, not just `str`/`float` — length limits tied
to the actual Phase 7 wire-packet field width, `0-100`/`0-1` bounds on every
percent/probability field, and documented (but not over-constrained) units.
See `docs/architecture/data_dictionary.md` for the full field-by-field
reference, or the published "Smart Bandage Data Dictionary" artifact for a
browsable version.

Startup now also refuses to run with dev-only defaults once you mean it:

```bash
ENVIRONMENT=production DATABASE_URL=postgresql+psycopg2://... JWT_SECRET=<32+ random chars> uvicorn backend.app.main:app
```

Set `ENVIRONMENT=production` and the app raises `ConfigurationError` at
startup if `JWT_SECRET` is still the dev default (or under 16 chars) or
`DATABASE_URL` is still SQLite — and skips seeding the `admin`/`admin` login
entirely instead of shipping it. Development and `pytest` are unaffected
(`ENVIRONMENT` defaults to `"development"`). See
`backend/app/config.py:Settings.validate()`,
`backend/tests/test_config_production_safety.py`.

Generate the real `JWT_SECRET` with `scripts/generate_secret.py` rather than
typing one by hand — it never touches disk, only prints to stdout, so
piping it straight into wherever the secret actually needs to live doesn't
risk it landing in a checked-in file:

```bash
export JWT_SECRET=$(python scripts/generate_secret.py)
```

Then hand that value to your platform's secret store — a cloud provider's
secrets manager, `docker secret create`, your CI provider's encrypted
secrets, a Kubernetes `Secret` — and set it as an environment variable at
deploy time. It never belongs in `.env` (gitignored, but still a file on a
real box) or anywhere under version control; see
`backend/tests/test_generate_secret_script.py`.

For Postgres locally or via Docker: `docker compose up --build` brings up a
`db` service alongside `backend`/`frontend` — Postgres is the default deploy
target now, since that's what `ENVIRONMENT=production` actually requires.
The old zero-setup SQLite path (no `db` container) is still available as a
standalone file: `docker compose -f docker-compose.sqlite.yml up --build`.

## Repository layout

```
common/          schemas (measurement, device, calibration) + SensorInterface (Phase 1)
                 protocol/ — the Phase 7 wire packet spec, real + tested
                 interfaces/real_sensor.py, transport.py — Phase 8's RealSensor
firmware/        ESP32 sketch + drivers (Phase 7) — written to the protocol spec,
                 not compiled/flashed; needs real hardware
simulator/       scenarios/ signals/ faults/ sensors/ — the Phase 2 virtual bandage
processing/      filtering/ calibration/ biomarkers/ quality/ — the Phase 3 pipeline
                 intelligence/ — the Phase 6 trend/anomaly/confidence layer, plus
                 intelligence/ai_insight.py — Phase 10's Gemini prompt building
                 calibration/experimental.py — Phase 9's fitting/reporting tooling
backend/         FastAPI app (Phase 4) — app/, tests/; intelligence.py wires Phase 6 in;
                 gemini_client.py + routers/ai.py are Phase 10's AI analysis endpoints
frontend/        React + TS + Tailwind dashboard (Phase 5); AIInsightPanel.tsx is Phase 10
mobile/          React Native (Expo) counterpart to the dashboard — see mobile/README.md
ml/              evaluation/scenario_backtest.py — Phase 6's "ML experiments",
                 scoped to a backtest against simulated ground truth; a trained
                 model waits on real labelled data (Blueprint §11)
docs/            architecture notes, API spec, hardware notes, calibration notes
docker/          backend.Dockerfile, frontend.Dockerfile, nginx.conf — see docker-compose.yml
scripts/         seed_demo_data.py, reset_db.py, dev_up.sh, run_checks.sh,
                 generate_secret.py
tests/           cross-cutting tests (common/, simulator/, processing/, ml/)
```

## The one rule that makes this work

Real hardware and the simulator both implement `SensorInterface`
(`common/interfaces/sensor_interface.py`) and both return the same
`RawMeasurement` / `MeasurementRecord` shapes (`common/schemas/`).
Processing, the backend, and the dashboard are written once against those
contracts and never learn which source they're looking at. See
`docs/architecture/overview.md`.

## Getting started

### Backend + processing + simulator (Python)

```bash
python -m venv .venv
. .venv/Scripts/activate   # Windows; use `source .venv/bin/activate` on macOS/Linux
pip install -r requirements-dev.txt
pytest
```

That runs everything: Phase 1 contract tests, the Phase 2 simulator/scenario
tests, the Phase 3 processing pipeline tests, and the Phase 4 API tests
(auth, devices, measurements, biomarkers, alerts, simulation, WebSocket).

Run the API itself:

```bash
uvicorn backend.app.main:app --reload
```

Defaults to a local `smart_bandage.db` SQLite file and a dev-only seed login
(`admin` / `admin`) — see `backend/app/config.py` and `backend/app/security.py`.
Point `DATABASE_URL` at Postgres for anything real. Interactive API docs at
`http://localhost:8000/docs`.

To enable Phase 10's AI analysis (`GET`/`POST /devices/{id}/ai/insight`,
`/ai/chat`), set a [Gemini API key](https://aistudio.google.com/apikey)
before starting the backend:

```bash
export GEMINI_API_KEY=your-key-here   # Windows: $env:GEMINI_API_KEY = "..."
```

Left unset, those two endpoints return `503` and everything else in the
app works exactly as before — it's an optional layer, not a dependency.

### Dashboard (Node)

```bash
cd frontend
npm install
cp .env.example .env.local   # point VITE_API_BASE_URL / VITE_WS_BASE_URL at your backend
npm run dev
```

Sign in with the seed login, register a device, then use the Simulation
panel to start/stop a virtual bandage and inject any of the 7 scenarios —
live readings, alerts, and status all stream over the WebSocket.

### Mobile (Expo)

See `mobile/README.md`. Quickest path: `cd mobile && npm install && npm run web`.

### Everything via Docker

```bash
docker compose up --build
```

Backend on `http://localhost:8000` (docs at `/docs`), dashboard on
`http://localhost:5175`, Postgres persisted in the `postgres-data` volume --
this is the production-shaped path and the default. For the old
zero-dependency SQLite path instead (no `db` container, quick demo only):

```bash
docker compose -f docker-compose.sqlite.yml up --build
```

### Deploy behind TLS

Both `docker compose up` above serve plain HTTP -- fine for local dev, not
for a real deploy. `docker-compose.tls.yml` is an overlay (combine it with
the base file, don't replace it) that terminates real HTTPS on both
services: nginx for the dashboard (`docker/nginx.tls.conf`), uvicorn's
built-in `--ssl-certfile`/`--ssl-keyfile` for the backend -- no extra
reverse-proxy container needed for the API.

```bash
# 1. Edit docker/nginx.tls.conf -- replace your-domain.example with your
#    real domain (must match the cert's CN/SAN) in both server blocks.
# 2. Get a cert. For real use (needs a public domain + port 80 reachable):
certbot certonly --standalone -d your-domain.example
mkdir certs && cp /etc/letsencrypt/live/your-domain.example/{fullchain,privkey}.pem certs/
#    Or for local testing, a self-signed pair:
mkdir certs && openssl req -x509 -nodes -newkey rsa:2048 -days 365 \
  -keyout certs/privkey.pem -out certs/fullchain.pem -subj "/CN=localhost"

# 3. Rebuild the frontend pointed at your real backend URL (Vite bakes this
#    in at build time -- see frontend.build.args in docker-compose.yml),
#    then bring both files up together:
docker compose -f docker-compose.yml -f docker-compose.tls.yml up --build
```

Dashboard on `https://your-domain.example`, API directly on
`https://your-domain.example:8443` (plain HTTP on 80/8000 stays up
alongside it in this overlay -- port 80 redirects to 443; drop the `8000:8000`
mapping in `docker-compose.yml` once you're confident 8443 is the only path
you want open). `certs/` is git-ignored -- never commit a real key.

Set `ENVIRONMENT=production` on the backend at the same time (see
"Production readiness" above) so it also refuses dev-only defaults instead
of just gaining a padlock icon.

### Convenience scripts

```bash
./scripts/dev_up.sh              # backend + dashboard together, Ctrl+C stops both
python scripts/seed_demo_data.py # register demo devices + start a simulation
python scripts/reset_db.py       # wipe the local smart_bandage.db
./scripts/run_checks.sh          # pytest + frontend typecheck/build, CI-equivalent
```
