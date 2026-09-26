"""
Phase 4 API tests -- auth, devices, measurements, biomarkers, alerts, and
the simulation/WebSocket loop, exercised end to end through FastAPI's
TestClient against a throwaway SQLite file (never the dev DB).

DATABASE_URL must be set before backend.app.config is first imported, so
the env vars below are set at module import time, ahead of the
`from backend.app...` imports.
"""
from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path

_tmp_dir = tempfile.mkdtemp(prefix="smart_bandage_test_")
_db_path = Path(_tmp_dir) / "test.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_db_path.as_posix()}"
os.environ["JWT_SECRET"] = "test-secret"
os.environ["SIMULATION_INTERVAL_SECONDS"] = "0.05"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from backend.app.database import Base, engine  # noqa: E402
from backend.app.main import app  # noqa: E402
from backend.app.simulation import simulation_manager  # noqa: E402


@pytest.fixture()
def client():
    Base.metadata.drop_all(bind=engine)
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def auth_headers(client):
    resp = client.post("/auth/login", json={"username": "admin", "password": "admin"})
    assert resp.status_code == 200, resp.text
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {
        "status": "ok",
        "checks": {"database": "ok", "gemini_configured": False},
    }


def test_login_rejects_bad_password(client):
    resp = client.post("/auth/login", json={"username": "admin", "password": "wrong"})
    assert resp.status_code == 401


def test_login_returns_refresh_token(client):
    resp = client.post("/auth/login", json={"username": "admin", "password": "admin"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["refresh_token"]


def test_refresh_issues_new_access_token(client):
    login = client.post("/auth/login", json={"username": "admin", "password": "admin"})
    refresh_token = login.json()["refresh_token"]

    resp = client.post("/auth/refresh", json={"refresh_token": refresh_token})
    assert resp.status_code == 200, resp.text
    new_access = resp.json()["access_token"]

    # the freshly refreshed access token actually works
    devices = client.get("/devices", headers={"Authorization": f"Bearer {new_access}"})
    assert devices.status_code == 200


def test_refresh_rejects_an_access_token(client):
    """An access token isn't a refresh token (no "typ": "refresh" claim) --
    make sure /auth/refresh doesn't accept one anyway."""
    login = client.post("/auth/login", json={"username": "admin", "password": "admin"})
    access_token = login.json()["access_token"]

    resp = client.post("/auth/refresh", json={"refresh_token": access_token})
    assert resp.status_code == 401


def test_refresh_rejects_garbage_token(client):
    resp = client.post("/auth/refresh", json={"refresh_token": "not-a-real-token"})
    assert resp.status_code == 401


def test_protected_endpoint_rejects_a_refresh_token_used_as_bearer(client):
    """A refresh token is longer-lived (JWT_REFRESH_EXPIRES_DAYS, 30 days by
    default) than an access token (JWT_EXPIRES_MINUTES, 180 minutes) and is
    told apart only by its "typ" claim -- make sure it can't be used
    directly as a Bearer access token on a protected endpoint, which would
    otherwise let anyone holding a refresh token skip the shorter-lived
    access-token model entirely."""
    login = client.post("/auth/login", json={"username": "admin", "password": "admin"})
    refresh_token = login.json()["refresh_token"]

    resp = client.post(
        "/devices/register",
        json={"device_id": "SB-999", "name": "x", "firmware_version": "0.1.0"},
        headers={"Authorization": f"Bearer {refresh_token}"},
    )
    assert resp.status_code == 401


def test_login_rate_limited_after_repeated_failures(client):
    for _ in range(10):
        resp = client.post("/auth/login", json={"username": "admin", "password": "wrong"})
        assert resp.status_code == 401
    resp = client.post("/auth/login", json={"username": "admin", "password": "wrong"})
    assert resp.status_code == 429

    # A correct password doesn't bypass the limiter once it's tripped.
    resp = client.post("/auth/login", json={"username": "admin", "password": "admin"})
    assert resp.status_code == 429


def test_register_requires_auth(client):
    resp = client.post(
        "/devices/register",
        json={"device_id": "SB-100", "name": "No auth", "firmware_version": "0.1.0"},
    )
    assert resp.status_code == 401


def test_register_and_fetch_device(client, auth_headers):
    resp = client.post(
        "/devices/register",
        json={"device_id": "SB-100", "name": "Prototype #1", "firmware_version": "0.1.0", "channels": ["CH-01"]},
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["device_id"] == "SB-100"

    listed = client.get("/devices").json()
    assert any(d["device_id"] == "SB-100" for d in listed)

    fetched = client.get("/devices/SB-100")
    assert fetched.status_code == 200
    assert fetched.json()["channels"] == ["CH-01"]

    missing = client.get("/devices/does-not-exist")
    assert missing.status_code == 404


def test_ingest_measurement_requires_auth(client):
    """POST /measurements used to accept data for any device_id with no
    auth at all (unauthenticated data injection). Gated behind the same
    user JWT login as the rest of the authenticated API now -- not a real
    per-device credential (see docs/security.md), but it closes off
    anonymous writes."""
    resp = client.post(
        "/measurements",
        json={
            "device_id": "SB-200",
            "channel_id": "CH-01",
            "timestamp": "2026-08-30T14:52:00Z",
            "raw_signal": 0.832,
            "status": "valid",
        },
    )
    assert resp.status_code == 401


def test_ingest_and_query_measurement(client, auth_headers):
    payload = {
        "device_id": "SB-200",
        "channel_id": "CH-01",
        "timestamp": "2026-08-30T14:52:00Z",
        "raw_signal": 0.832,
        "processed_signal": 0.791,
        "estimated_value": 123.4,
        "unit": "ng/mL",
        "signal_quality": 0.94,
        "temperature": 36.7,
        "battery": 87,
        "status": "valid",
    }
    ingested = client.post("/measurements", json=payload, headers=auth_headers)
    assert ingested.status_code == 201, ingested.text

    listed = client.get("/measurements", params={"device_id": "SB-200"})
    assert listed.status_code == 200
    assert len(listed.json()) == 1
    assert listed.json()[0]["estimated_value"] == 123.4


def test_biomarker_trend_needs_enough_history_before_committing(client, auth_headers):
    """Phase 6: two readings isn't enough for the least-squares detector
    (processing/intelligence/trend.py, min_samples=5 by default) to call a
    direction -- it honestly reports "unknown" rather than overfit noise
    from a single two-point diff, which is what the pre-Phase-6 endpoint
    used to do."""
    base = {
        "device_id": "SB-300",
        "channel_id": "CH-01",
        "unit": "ng/mL",
        "status": "valid",
    }
    # <15% apart so this alone doesn't also trip the rapid_change anomaly
    # check -- isolates "not enough history for a trend" from "anomalous"
    client.post(
        "/measurements",
        json={**base, "timestamp": "2026-08-30T14:00:00Z", "raw_signal": 1.0, "estimated_value": 100.0},
        headers=auth_headers,
    )
    client.post(
        "/measurements",
        json={**base, "timestamp": "2026-08-30T14:01:00Z", "raw_signal": 1.05, "estimated_value": 105.0},
        headers=auth_headers,
    )

    resp = client.get("/biomarkers/CH-01")
    assert resp.status_code == 200
    body = resp.json()
    assert body["estimated_value"] == 105.0
    assert body["trend"] == "unknown"
    assert body["anomaly"] is False

    names = client.get("/biomarkers").json()
    assert "CH-01" in names

    missing = client.get("/biomarkers/does-not-exist")
    assert missing.status_code == 404


def test_biomarker_trend_and_anomaly_over_a_sustained_rise(client, auth_headers):
    """Once there's enough history, a clean sustained rise is exactly what
    Phase 6's ChannelAnomalyDetector is meant to flag."""
    base = {
        "device_id": "SB-300",
        "channel_id": "CH-01",
        "unit": "ng/mL",
        "status": "valid",
    }
    # one-second spacing -- matches the wearable's actual streaming cadence
    # (Blueprint: ~1 reading/second), unlike the minute-apart timestamps
    # above; the detector's slope threshold is tuned to that cadence, not
    # to sparse manual entries
    for i, value in enumerate([100.0, 108.0, 118.0, 130.0, 145.0]):
        client.post(
            "/measurements",
            json={**base, "timestamp": f"2026-08-30T14:00:0{i}Z", "raw_signal": 1.0, "estimated_value": value},
            headers=auth_headers,
        )

    body = client.get("/biomarkers/CH-01").json()
    assert body["estimated_value"] == 145.0
    assert body["trend"] == "rising"
    assert body["anomaly"] is True
    assert 0.0 <= body["confidence"] <= 1.0


def test_alerts_start_empty(client):
    assert client.get("/alerts").json() == []


def test_stored_measurement_timestamp_round_trips_with_utc_offset(client, auth_headers):
    """Regression test: SQLite drops tzinfo on a datetime column, so a naive
    read-back must be re-tagged UTC (backend/app/crud.py: as_utc) -- otherwise
    the dashboard's `new Date(iso_string)` renders historical points shifted
    by the browser's local UTC offset relative to live WebSocket data."""
    client.post(
        "/measurements",
        json={
            "device_id": "SB-800",
            "channel_id": "CH-01",
            "timestamp": "2026-08-30T10:00:00+00:00",
            "raw_signal": 1.0,
            "status": "valid",
        },
        headers=auth_headers,
    )
    stored = client.get("/measurements", params={"device_id": "SB-800"}).json()[0]
    assert stored["timestamp"].endswith("+00:00") or stored["timestamp"].endswith("Z")


def test_simulation_scenario_requires_auth(client):
    resp = client.post("/simulation/scenario", json={"device_id": "SB-400", "scenario": "normal"})
    assert resp.status_code == 401


def test_simulation_start_produces_measurements_and_live_status(client, auth_headers):
    device_id = "SB-500"
    start = client.post(
        "/simulation/start",
        json={"device_id": device_id, "channels": ["CH-01"], "scenario": "normal"},
        headers=auth_headers,
    )
    assert start.status_code == 202, start.text

    try:
        # give the background loop (0.05s tick interval) a few rounds to run
        deadline = time.monotonic() + 3.0
        measurements = []
        while time.monotonic() < deadline:
            measurements = client.get("/measurements", params={"device_id": device_id}).json()
            if measurements:
                break
            time.sleep(0.1)
        assert measurements, "simulation never produced a stored measurement"

        status = client.get("/device-status", params={"device_id": device_id})
        assert status.status_code == 200
        assert status.json()["connected"] is True

        registered = client.get(f"/devices/{device_id}")
        assert registered.status_code == 200  # auto-registered by /simulation/start
    finally:
        stop = client.post("/simulation/stop", params={"device_id": device_id}, headers=auth_headers)
        assert stop.status_code == 202

    assert simulation_manager.is_running(device_id) is False


def test_simulation_stop_unknown_device_returns_404(client, auth_headers):
    resp = client.post("/simulation/stop", params={"device_id": "no-such-device"}, headers=auth_headers)
    assert resp.status_code == 404


def test_simulation_scenario_injection_raises_alerts_for_disconnect(client, auth_headers):
    device_id = "SB-600"
    client.post(
        "/simulation/start",
        json={"device_id": device_id, "channels": ["CH-01"], "scenario": "sensor_disconnect"},
        headers=auth_headers,
    )
    try:
        deadline = time.monotonic() + 3.0
        alerts = []
        while time.monotonic() < deadline:
            alerts = client.get("/alerts", params={"device_id": device_id}).json()
            if any(a["type"] == "DEVICE_ERROR" for a in alerts):
                break
            time.sleep(0.1)
        assert any(a["type"] == "DEVICE_ERROR" for a in alerts)
    finally:
        client.post("/simulation/stop", params={"device_id": device_id}, headers=auth_headers)


def test_simulation_rising_concentration_raises_sustained_trend_alert(client, auth_headers):
    """Phase 6: backend/app/intelligence.py should catch the
    rising_concentration scenario as a sustained trend, on top of whatever
    backend/app/alerts.py's single-reading thresholds already do."""
    device_id = "SB-900"
    client.post(
        "/simulation/start",
        json={"device_id": device_id, "channels": ["CH-01"], "scenario": "rising_concentration"},
        headers=auth_headers,
    )
    try:
        deadline = time.monotonic() + 5.0
        alerts = []
        while time.monotonic() < deadline:
            alerts = client.get("/alerts", params={"device_id": device_id}).json()
            if any(a["type"] == "SUSTAINED_TREND" for a in alerts):
                break
            time.sleep(0.1)
        assert any(a["type"] == "SUSTAINED_TREND" for a in alerts)
    finally:
        client.post("/simulation/stop", params={"device_id": device_id}, headers=auth_headers)


def test_simulation_electrode_degradation_raises_quality_degrading_alert(client, auth_headers):
    """Phase 6: a declining signal_quality trend (Blueprint scenario 4)
    should surface as QUALITY_DEGRADING even though each individual
    reading's quality is, on its own, still plausible."""
    device_id = "SB-901"
    client.post(
        "/simulation/start",
        json={"device_id": device_id, "channels": ["CH-01"], "scenario": "electrode_degradation"},
        headers=auth_headers,
    )
    try:
        deadline = time.monotonic() + 5.0
        alerts = []
        while time.monotonic() < deadline:
            alerts = client.get("/alerts", params={"device_id": device_id}).json()
            if any(a["type"] == "QUALITY_DEGRADING" for a in alerts):
                break
            time.sleep(0.1)
        assert any(a["type"] == "QUALITY_DEGRADING" for a in alerts)
    finally:
        client.post("/simulation/stop", params={"device_id": device_id}, headers=auth_headers)


def test_websocket_receives_live_measurements(client, auth_headers):
    device_id = "SB-700"
    client.post(
        "/simulation/start",
        json={"device_id": device_id, "channels": ["CH-01"], "scenario": "normal"},
        headers=auth_headers,
    )
    try:
        with client.websocket_connect(f"/ws/devices/{device_id}") as ws:
            message = ws.receive_json()
            assert message["type"] in ("measurement", "alert")
    finally:
        client.post("/simulation/stop", params={"device_id": device_id}, headers=auth_headers)


# ---- DT-6: twin-backed simulation mode ----


def test_patient_profiles_are_listed_without_auth(client):
    resp = client.get("/simulation/patient-profiles")
    assert resp.status_code == 200
    names = {p["name"] for p in resp.json()}
    assert {"healthy_baseline", "diabetic_slow_healing", "immunocompromised_high_risk"} <= names
    assert all(p["description"] for p in resp.json())


def test_twin_ground_truth_404s_before_any_simulation_runs(client):
    resp = client.get("/simulation/twin/no-such-device")
    assert resp.status_code == 404


def test_twin_backed_simulation_produces_measurements_and_ground_truth(client, auth_headers):
    """POST /simulation/start with a patient_profile should drive
    DigitalTwinDevice (digital_twin/observation.py) instead of the scenario
    script, still through the exact same measurement/alert/WS pipeline."""
    device_id = "SB-800"
    start = client.post(
        "/simulation/start",
        json={
            "device_id": device_id,
            "channels": ["CH-01"],
            "patient_profile": "diabetic_slow_healing",
            "time_scale": 200.0,
        },
        headers=auth_headers,
    )
    assert start.status_code == 202, start.text
    assert start.json()["twin"] is True

    try:
        deadline = time.monotonic() + 3.0
        ground_truth = None
        while time.monotonic() < deadline:
            resp = client.get(f"/simulation/twin/{device_id}")
            if resp.status_code == 200:
                ground_truth = resp.json()
                break
            time.sleep(0.1)
        assert ground_truth is not None, "twin never produced a ground-truth snapshot"
        assert ground_truth["patient_profile"] == "diabetic_slow_healing"
        assert ground_truth["time_scale"] == 200.0
        assert ground_truth["channel_id"] == "CH-01"
        assert 0.0 <= ground_truth["bacterial_load"] <= 1.5

        measurements = client.get("/measurements", params={"device_id": device_id}).json()
        assert measurements, "twin-backed simulation never produced a stored measurement"
    finally:
        client.post("/simulation/stop", params={"device_id": device_id}, headers=auth_headers)


def test_twin_backed_simulation_reports_real_units_not_arbitrary_units(client, auth_headers):
    """A twin-backed channel's estimated_value/unit should read like
    something a real bandage electrode could report (e.g. "6.8 mg/L") --
    not the scenario-backed path's identity calibration / "a.u." -- and a
    multi-channel device should get distinct sensor types (pathogen/pH/
    glucose), not the same assay cloned across every channel. See
    backend/app/simulation.py's _twin_pipeline / _assign_channel_sensor_types."""
    device_id = "SB-810"
    start = client.post(
        "/simulation/start",
        json={
            "device_id": device_id,
            "channels": ["CH-01", "CH-02", "CH-03"],
            "patient_profile": "immunocompromised_high_risk",
            "time_scale": 200.0,
        },
        headers=auth_headers,
    )
    assert start.status_code == 202, start.text

    try:
        deadline = time.monotonic() + 3.0
        units_seen: set[str] = set()
        while time.monotonic() < deadline and len(units_seen) < 3:
            measurements = client.get("/measurements", params={"device_id": device_id}).json()
            units_seen = {m["unit"] for m in measurements if m.get("unit") is not None}
            if len(units_seen) < 3:
                time.sleep(0.1)
        assert "a.u." not in units_seen
        assert units_seen == {"mg/L", "pH", "mg/dL"}
    finally:
        client.post("/simulation/stop", params={"device_id": device_id}, headers=auth_headers)


def test_twin_backed_simulation_rejects_scenario_injection(client, auth_headers):
    device_id = "SB-801"
    client.post(
        "/simulation/start",
        json={"device_id": device_id, "channels": ["CH-01"], "patient_profile": "healthy_baseline"},
        headers=auth_headers,
    )
    try:
        resp = client.post(
            "/simulation/scenario",
            json={"device_id": device_id, "scenario": "normal"},
            headers=auth_headers,
        )
        assert resp.status_code == 409
    finally:
        client.post("/simulation/stop", params={"device_id": device_id}, headers=auth_headers)


def test_twin_backed_simulation_start_rejects_unknown_patient_profile(client, auth_headers):
    resp = client.post(
        "/simulation/start",
        json={"device_id": "SB-802", "channels": ["CH-01"], "patient_profile": "no-such-profile"},
        headers=auth_headers,
    )
    assert resp.status_code == 400


def test_twin_backed_simulation_start_rejects_empty_channels(client, auth_headers):
    """A twin needs at least one channel to drive -- previously an empty
    `channels` list reached `channel_ids[0]` in DeviceSimulation.__init__
    and raised an unhandled IndexError (500) instead of a clean 400."""
    resp = client.post(
        "/simulation/start",
        json={"device_id": "SB-804", "channels": [], "patient_profile": "healthy_baseline"},
        headers=auth_headers,
    )
    assert resp.status_code == 400


def test_twin_backed_simulation_start_rejects_unknown_twin_channel_id(client, auth_headers):
    """A twin_channel_id outside `channels` previously passed validation
    silently -- the ground-truth channel condition in DeviceSimulation._tick
    would then never match, so GET /simulation/twin/{id} 404'd forever
    instead of the request being rejected up front."""
    resp = client.post(
        "/simulation/start",
        json={
            "device_id": "SB-805",
            "channels": ["CH-01"],
            "patient_profile": "healthy_baseline",
            "twin_channel_id": "CH-99",
        },
        headers=auth_headers,
    )
    assert resp.status_code == 400


def test_twin_backed_simulation_honors_explicit_twin_channel_id(client, auth_headers):
    """With multiple channels, twin_channel_id picks which one the
    ground-truth overlay tracks instead of always defaulting to the first."""
    device_id = "SB-806"
    start = client.post(
        "/simulation/start",
        json={
            "device_id": device_id,
            "channels": ["CH-01", "CH-02"],
            "patient_profile": "healthy_baseline",
            "twin_channel_id": "CH-02",
        },
        headers=auth_headers,
    )
    assert start.status_code == 202, start.text

    try:
        deadline = time.monotonic() + 3.0
        ground_truth = None
        while time.monotonic() < deadline:
            resp = client.get(f"/simulation/twin/{device_id}")
            if resp.status_code == 200:
                ground_truth = resp.json()
                break
            time.sleep(0.1)
        assert ground_truth is not None, "twin never produced a ground-truth snapshot"
        assert ground_truth["channel_id"] == "CH-02"
    finally:
        client.post("/simulation/stop", params={"device_id": device_id}, headers=auth_headers)


def test_websocket_receives_twin_ground_truth_broadcast(client, auth_headers):
    device_id = "SB-803"
    client.post(
        "/simulation/start",
        json={"device_id": device_id, "channels": ["CH-01"], "patient_profile": "healthy_baseline"},
        headers=auth_headers,
    )
    try:
        with client.websocket_connect(f"/ws/devices/{device_id}") as ws:
            seen_types = set()
            for _ in range(6):
                message = ws.receive_json()
                seen_types.add(message["type"])
                if "twin_ground_truth" in seen_types:
                    break
            assert "twin_ground_truth" in seen_types
    finally:
        client.post("/simulation/stop", params={"device_id": device_id}, headers=auth_headers)
