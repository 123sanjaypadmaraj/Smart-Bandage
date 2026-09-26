/**
 * Smart Bandage load/perf test (k6).
 *
 * Exercises the REST surface (auth, devices, measurements, biomarkers,
 * alerts) and the WS live-reading stream (/ws/devices/{device_id}), driven
 * by N simulated devices each running a server-side digital-twin profile --
 * same provisioning pattern as scripts/seed_demo_data.py (login once as
 * admin, POST /devices/register, POST /simulation/start per device).
 *
 * STATUS (see docs/performance.md): this harness was scaffolded against the
 * PRE-MERGE backend (simulator/scenarios/scenarios.py scenario mix) while
 * waiting for the DT-2/3/4/6/7 digital-twin consolidation to land on main.
 * Numbers captured before that merge are not the ones docs/performance.md
 * reports on -- re-run after `git merge main` before trusting any p95/p99
 * here. The REST/WS surface it drives (POST /simulation/start, the scenario
 * names, /ws/devices/{id} payload shape) should be unaffected by the
 * digital-twin swap since it's internal to DeviceSimulation._tick(), but
 * double check `scenario` values still resolve after the merge.
 *
 * Usage:
 *   docker compose up --build            # from repo root, separate shell
 *   docker run --rm -i --network host \
 *     -e API_BASE=http://localhost:8000 \
 *     -e WS_BASE=ws://localhost:8000 \
 *     -e DEVICE_COUNT=50 \
 *     -e REST_VUS=20 -e WS_VUS=50 -e DURATION=2m \
 *     grafana/k6 run - < loadtest/k6/smart_bandage_load.js
 *
 *   # or, with a local k6 install:
 *   k6 run loadtest/k6/smart_bandage_load.js
 *
 * Env vars:
 *   API_BASE      default http://localhost:8000
 *   WS_BASE       default ws://localhost:8000
 *   DEVICE_COUNT  number of simulated devices to register+run (default 50)
 *   REST_VUS      concurrent "dashboard client" VUs polling REST (default 20)
 *   WS_VUS        concurrent WS live-stream subscribers, <= DEVICE_COUNT (default DEVICE_COUNT)
 *   DURATION      how long the load phases run (default 2m)
 */
import http from "k6/http";
import ws from "k6/ws";
import { check, sleep, fail } from "k6";
import { Counter, Trend } from "k6/metrics";
import { SharedArray } from "k6/data";

const API_BASE = __ENV.API_BASE || "http://localhost:8000";
const WS_BASE = __ENV.WS_BASE || "ws://localhost:8000";
const DEVICE_COUNT = parseInt(__ENV.DEVICE_COUNT || "50", 10);
const REST_VUS = parseInt(__ENV.REST_VUS || "20", 10);
const WS_VUS = Math.min(parseInt(__ENV.WS_VUS || String(DEVICE_COUNT), 10), DEVICE_COUNT);
const DURATION = __ENV.DURATION || "2m";
// device_id is a wire-packet field capped at 12 ASCII chars
// (backend/app/schemas.py _ID_MAX_LEN) -- keep the run id short so
// `L<run><0000>` (1 + 4 + 4 = 9 chars) always fits, even at DEVICE_COUNT=200.
const RUN_ID = (__ENV.RUN_ID || String(Date.now())).slice(-4);

// Digital-twin profile mix -- rotated across registered devices so a run
// isn't just N copies of the same trajectory. Pre-merge these are
// simulator/scenarios/scenarios.py names; the DT consolidation is expected
// to keep (or alias) these scenario names on POST /simulation/start.
const SCENARIOS = [
  "normal",
  "rising_concentration",
  "sudden_abnormal",
  "electrode_degradation",
  "high_noise",
  "sensor_disconnect",
  "comms_failure",
];

// ---- custom metrics -------------------------------------------------------

const restLoginTrend = new Trend("sb_rest_login_ms", true);
const restDevicesListTrend = new Trend("sb_rest_devices_list_ms", true);
const restDeviceGetTrend = new Trend("sb_rest_device_get_ms", true);
const restMeasurementsTrend = new Trend("sb_rest_measurements_ms", true);
const restBiomarkersListTrend = new Trend("sb_rest_biomarkers_list_ms", true);
const restBiomarkerGetTrend = new Trend("sb_rest_biomarker_get_ms", true);
const restAlertsTrend = new Trend("sb_rest_alerts_ms", true);

const wsConnectTrend = new Trend("sb_ws_connect_ms", true);
const wsMessagesReceived = new Counter("sb_ws_messages_received");
const wsMeasurementLagTrend = new Trend("sb_ws_measurement_lag_ms", true); // wall-clock: k6 receipt vs. server record timestamp
const wsDropped = new Counter("sb_ws_connection_dropped");

// ---- shared setup -----------------------------------------------------

function deviceId(i) {
  return `L${RUN_ID}${String(i).padStart(4, "0")}`;
}

function authHeaders(token) {
  return { headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" } };
}

export const options = {
  scenarios: {
    rest_load: {
      executor: "constant-vus",
      exec: "restLoad",
      vus: REST_VUS,
      duration: DURATION,
      startTime: "5s", // give setup()'s registrations a beat to settle
    },
    ws_load: {
      executor: "per-vu-iterations",
      exec: "wsLoad",
      vus: WS_VUS,
      iterations: 1,
      maxDuration: DURATION,
      startTime: "5s",
    },
  },
  thresholds: {
    // Informational guardrails, not hard pass/fail gates for this ticket --
    // real p50/p95/p99 numbers get written up in docs/performance.md.
    sb_rest_devices_list_ms: ["p(95)<2000"],
    sb_rest_measurements_ms: ["p(95)<2000"],
    sb_ws_connect_ms: ["p(95)<3000"],
  },
};

export function setup() {
  const loginRes = http.post(
    `${API_BASE}/auth/login`,
    JSON.stringify({ username: "admin", password: "admin" }),
    { headers: { "Content-Type": "application/json" } }
  );
  check(loginRes, { "login succeeded": (r) => r.status === 200 });
  if (loginRes.status !== 200) {
    fail(`setup: login failed with status ${loginRes.status}: ${loginRes.body}`);
  }
  const token = loginRes.json("access_token");

  const deviceIds = [];
  for (let i = 0; i < DEVICE_COUNT; i++) {
    const id = deviceId(i);
    const scenario = SCENARIOS[i % SCENARIOS.length];

    const registerRes = http.post(
      `${API_BASE}/devices/register`,
      JSON.stringify({
        device_id: id,
        name: `Load Test Device ${i}`,
        firmware_version: "loadtest-0.1.0",
        channels: ["CH-01"],
      }),
      authHeaders(token)
    );
    check(registerRes, { "device register succeeded": (r) => r.status === 201 });

    const startRes = http.post(
      `${API_BASE}/simulation/start`,
      JSON.stringify({ device_id: id, channels: ["CH-01"], scenario }),
      authHeaders(token)
    );
    check(startRes, { "simulation start succeeded": (r) => r.status === 202 });

    deviceIds.push(id);
  }

  return { token, deviceIds };
}

// ---- REST scenario: mimics N dashboard clients polling live state -----

export function restLoad(data) {
  const { token, deviceIds } = data;
  const id = deviceIds[Math.floor(Math.random() * deviceIds.length)];
  const opts = authHeaders(token);

  let res = http.get(`${API_BASE}/devices`, opts);
  restDevicesListTrend.add(res.timings.duration);
  check(res, { "GET /devices 200": (r) => r.status === 200 });

  res = http.get(`${API_BASE}/devices/${id}`, opts);
  restDeviceGetTrend.add(res.timings.duration);
  check(res, { "GET /devices/{id} 200": (r) => r.status === 200 });

  res = http.get(`${API_BASE}/measurements?device_id=${id}&channel_id=CH-01&limit=50`, opts);
  restMeasurementsTrend.add(res.timings.duration);
  check(res, { "GET /measurements 200": (r) => r.status === 200 });

  res = http.get(`${API_BASE}/biomarkers`, opts);
  restBiomarkersListTrend.add(res.timings.duration);
  check(res, { "GET /biomarkers 200": (r) => r.status === 200 });

  res = http.get(`${API_BASE}/biomarkers/CH-01`, opts);
  restBiomarkerGetTrend.add(res.timings.duration);
  check(res, { "GET /biomarkers/CH-01 200 or 404": (r) => r.status === 200 || r.status === 404 });

  res = http.get(`${API_BASE}/alerts?device_id=${id}`, opts);
  restAlertsTrend.add(res.timings.duration);
  check(res, { "GET /alerts 200": (r) => r.status === 200 });

  sleep(1);
}

// ---- WS scenario: one live-reading subscriber per simulated device ----

export function wsLoad(data) {
  const { deviceIds } = data;
  // spread WS_VUS across DEVICE_COUNT devices deterministically so every VU
  // gets a distinct (or, if WS_VUS < DEVICE_COUNT, still spread) device
  const id = deviceIds[(__VU - 1) % deviceIds.length];
  const url = `${WS_BASE}/ws/devices/${id}`;

  const connectStart = Date.now();
  const res = ws.connect(url, {}, function (socket) {
    wsConnectTrend.add(Date.now() - connectStart);

    socket.on("message", function (msg) {
      wsMessagesReceived.add(1);
      try {
        const payload = JSON.parse(msg);
        if (payload.type === "measurement" && payload.measurement && payload.measurement.timestamp) {
          const serverTs = new Date(payload.measurement.timestamp).getTime();
          if (!Number.isNaN(serverTs)) {
            wsMeasurementLagTrend.add(Date.now() - serverTs);
          }
        }
      } catch (e) {
        // non-JSON or unexpected shape; still counted above, just not lag-tracked
      }
    });

    socket.on("error", function () {
      wsDropped.add(1);
    });

    // Stay connected for most of the run duration so throughput/latency is
    // measured over a realistic live-viewing window, not a single frame.
    socket.setTimeout(function () {
      socket.close();
    }, durationToMs(DURATION) - 8000);
  });

  check(res, { "ws handshake succeeded": (r) => r && r.status === 101 });
}

function durationToMs(d) {
  // minimal "2m" / "30s" / "90s" parser -- good enough for this harness's DURATION env var
  const match = /^(\d+)(ms|s|m|h)$/.exec(d);
  if (!match) return 120000;
  const n = parseInt(match[1], 10);
  switch (match[2]) {
    case "ms":
      return n;
    case "s":
      return n * 1000;
    case "m":
      return n * 60 * 1000;
    case "h":
      return n * 60 * 60 * 1000;
    default:
      return 120000;
  }
}

export function teardown(data) {
  // Best-effort: stop every simulation we started so a run doesn't leave
  // background asyncio tasks (and their DB writes) running against the
  // backend forever. Not load-bearing for the measured numbers.
  const { token, deviceIds } = data;
  const opts = authHeaders(token);
  for (const id of deviceIds) {
    http.post(`${API_BASE}/simulation/stop?device_id=${id}`, null, opts);
  }
}
