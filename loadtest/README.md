# Load / perf test harness (DISPATCH-6)

k6 script driving N simulated devices (server-side digital-twin profiles via
`POST /simulation/start`, same provisioning pattern as
`scripts/seed_demo_data.py`) against the REST surface (auth, devices,
measurements, biomarkers, alerts) and the `/ws/devices/{device_id}` live
stream.

## Status

Scaffolded pre-merge against the current (non-digital-twin-consolidated)
`main`, per the DISPATCH-6 ticket's gate: real numbers and the writeup in
`docs/performance.md` only count once this branch has `git merge main` with
the DT-2/3/4/6/7 consolidation landed, and the run below has been repeated
against that merged system.

## Running it

```sh
# 1. bring up the Postgres-backed stack from the repo root
docker compose up --build

# 2. in another shell, run the k6 script against it (Docker, no local k6 install needed)
docker run --rm -i --network host \
  -e API_BASE=http://localhost:8000 \
  -e WS_BASE=ws://localhost:8000 \
  -e DEVICE_COUNT=50 \
  -e REST_VUS=20 \
  -e WS_VUS=50 \
  -e DURATION=2m \
  grafana/k6 run - < loadtest/k6/smart_bandage_load.js

# 200-device run
docker run --rm -i --network host \
  -e API_BASE=http://localhost:8000 \
  -e WS_BASE=ws://localhost:8000 \
  -e DEVICE_COUNT=200 \
  -e REST_VUS=50 \
  -e WS_VUS=200 \
  -e DURATION=3m \
  grafana/k6 run - < loadtest/k6/smart_bandage_load.js
```

`--network host` isn't available on Docker Desktop for Windows/Mac; use
`-e API_BASE=http://host.docker.internal:8000 -e WS_BASE=ws://host.docker.internal:8000`
instead, with no `--network host` flag, when running there.

If you have a local k6 binary, `k6 run loadtest/k6/smart_bandage_load.js`
with the same env vars works too.

## What it measures

- `sb_rest_*_ms` — per-endpoint REST latency (Trend: p50/p95/p99 come out of
  k6's summary automatically).
- `sb_ws_connect_ms` — WS handshake latency.
- `sb_ws_messages_received` — total live-push messages received across all
  WS subscribers, for throughput (`rate` in the k6 summary = messages/sec).
- `sb_ws_measurement_lag_ms` — wall-clock gap between a measurement's server
  `timestamp` and when this script received it over the socket; a rough
  end-to-end push latency, not a substitute for server-side instrumentation.
- `sb_ws_connection_dropped` — WS socket errors, i.e. fanout/backpressure
  problems under load.

## Notes / limitations

- `setup()` registers all `DEVICE_COUNT` devices and starts their
  simulations serially over plain `http.post` calls before the timed
  scenarios start — at DEVICE_COUNT=200 that ramp-up itself takes real wall
  time and is not part of the measured window (scenarios have a 5s
  `startTime` offset, but a slow 200-device setup can still eat into it;
  watch k6's setup duration in the run output).
- The REST scenario picks a random device per iteration rather than a fixed
  device-per-VU mapping, so it approximates dashboard polling traffic more
  than per-device request patterns.
- `teardown()` best-effort stops every simulation it started; if a run is
  killed mid-flight (Ctrl-C) those `DeviceSimulation` background tasks keep
  running server-side until `POST /simulation/stop` or the backend restarts.
