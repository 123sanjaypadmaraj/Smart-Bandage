# Performance / load testing (DISPATCH-6)

Status: **pending** — the digital-twin consolidation (DT-2/3/4/6/7) has not
yet landed on `main` as of this writing, and this ticket is gated on that
merge to be meaningful (per the ticket: "This ticket needs the real, merged
digital-twin system to be meaningful, so it's gated"). The harness below is
scaffolded and ready; the numbers in this document will be filled in once:

1. `main` gets the DT consolidation (tracked separately; DISPATCH-6 is
   waiting on a notification from the coordinator).
2. `git merge main` is run on this `load-testing` branch.
3. The load test is (re-)run against the merged system via
   `docker compose up --build` (the Postgres path) at 50 and 200 concurrent
   simulated devices.
4. Obvious bottlenecks found are fixed and re-measured.

Everything past this point is the methodology and the pre-merge groundwork;
treat any numbers here as provisional until this note is removed.

## Methodology

- **Harness**: k6 (`loadtest/k6/smart_bandage_load.js`, see
  `loadtest/README.md` for exact invocation). Chosen over locust for
  first-class WebSocket support alongside HTTP in one script and no Python
  dependency conflicts with the backend's own venv.
- **Target**: `docker compose up --build` — the Postgres-backed path
  (`docker-compose.yml`), not the SQLite override, since that's the
  production-shaped path (`ENVIRONMENT=production` requires Postgres per
  `backend/app/config.py:Settings.validate()`).
- **Simulated devices**: registered via `POST /devices/register` then
  driven server-side via `POST /simulation/start` with a rotating mix of
  scenarios (`normal`, `rising_concentration`, `sudden_abnormal`,
  `electrode_degradation`, `high_noise`, `sensor_disconnect`,
  `comms_failure` pre-merge; expected to become/alias the consolidated
  digital-twin profiles post-merge) — the same provisioning pattern as
  `scripts/seed_demo_data.py`.
- **Load shape**: two concurrent k6 scenarios —
  - `rest_load`: a configurable number of VUs (dashboard-client stand-ins)
    continuously polling `GET /devices`, `GET /devices/{id}`,
    `GET /measurements`, `GET /biomarkers`, `GET /biomarkers/{name}`, and
    `GET /alerts`.
  - `ws_load`: one VU per simulated device (or a configurable subset)
    holding open a `/ws/devices/{device_id}` connection for the run
    duration, measuring connect latency, message throughput, and
    approximate end-to-end push latency (server `timestamp` vs. receipt
    time).
- **Runs**: 50 concurrent simulated devices, then 200, each for a few
  minutes after a ramp-up, capturing p50/p95/p99 latency per REST endpoint
  and WS message throughput/lag.

## Results

_Not yet captured — pending the digital-twin merge. Will be filled in as:_

```
### 50 concurrent devices

| endpoint                  | p50 | p95 | p99 |
|----------------------------|-----|-----|-----|
| GET /devices                |     |     |     |
| GET /devices/{id}            |     |     |     |
| GET /measurements             |     |     |     |
| GET /biomarkers                |     |     |     |
| GET /biomarkers/{name}          |     |     |     |
| GET /alerts                      |     |     |     |

WS: connect p95 ___ ms · throughput ___ msg/s · measurement lag p95 ___ ms · drops ___

### 200 concurrent devices

(same table)
```

## Bottlenecks found (pre-merge code read, to be confirmed against the
merged system)

These were spotted reading the current (pre-DT-merge) backend and are
listed here so they aren't lost; each will be re-verified with real numbers
before being called "fixed":

1. **N+1 on `GET /devices`** — `backend/app/crud.py`'s `list_devices()`
   returns bare `DeviceORM` rows with no eager load, and
   `device_to_schema()` accesses `row.channels` per row, so listing N
   devices issues 1 (list) + N (channels) queries instead of one join.
   Likely fix: `selectinload(DeviceORM.channels)` in `list_devices`.
2. **No composite index on `measurements`** — `MeasurementORM` has separate
   single-column indexes on `device_id`, `channel_id`, and `timestamp`
   (`backend/app/models.py`), but `crud.query_measurements()` (used by
   `GET /measurements`, driven hard by the REST load scenario) filters on
   `device_id` + `channel_id` and orders by `timestamp desc` with a
   `LIMIT`. A composite index on `(device_id, channel_id, timestamp)` would
   let Postgres satisfy that query with a single index scan instead of
   combining three separate indexes (or falling back to a sort).
3. **Unsized SQLAlchemy connection pool** — `backend/app/database.py`
   creates the engine with no `pool_size`/`max_overflow`, so it uses
   SQLAlchemy's defaults (5 + 10 = 15 connections) regardless of
   `DATABASE_URL` pointing at Postgres. At 200 concurrent simulated devices
   (each with its own background `DeviceSimulation` asyncio task opening a
   `SessionLocal()` per tick) plus REST/WS load, this is a plausible
   contention point; needs confirming with real pool-wait numbers before
   deciding a fix (bump pool size vs. batching writes).
4. **Sequential WS broadcast fanout** — `backend/app/ws_manager.py`'s
   `ConnectionManager.broadcast()` awaits each connection's `send_json` one
   at a time in a loop. It's scoped per-`device_id` (not global fan-out
   across every connected client), so it's bounded by connections-per-device
   rather than unbounded — but a slow/stalled client socket on a
   multi-viewer device would still head-of-line block the others. Worth
   re-checking under the 200-device WS run whether this shows up as
   measurement-lag tail latency.

## Fixes applied

_Pending the merge + real run. This section will list what was actually
changed (with before/after numbers), not just hypothesized._
