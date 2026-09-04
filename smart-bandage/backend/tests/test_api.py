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
    assert client.get("/health").json() == {"status": "ok"}


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


def test_ingest_and_query_measurement(client):
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
    ingested = client.post("/measurements", json=payload)
    assert ingested.status_code == 201, ingested.text

    listed = client.get("/measurements", params={"device_id": "SB-200"})
    assert listed.status_code == 200
    assert len(listed.json()) == 1
    assert listed.json()[0]["estimated_value"] == 123.4


def test_biomarker_trend_needs_enough_history_before_committing(client):
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
    client.post("/measurements", json={**base, "timestamp": "2026-08-30T14:00:00Z", "raw_signal": 1.0, "estimated_value": 100.0})
    client.post("/measurements", json={**base, "timestamp": "2026-08-30T14:01:00Z", "raw_signal": 1.05, "estimated_value": 105.0})

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


def test_biomarker_trend_and_anomaly_over_a_sustained_rise(client):
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
        )

    body = client.get("/biomarkers/CH-01").json()
    assert body["estimated_value"] == 145.0
    assert body["trend"] == "rising"
    assert body["anomaly"] is True
    assert 0.0 <= body["confidence"] <= 1.0


def test_alerts_start_empty(client):
    assert client.get("/alerts").json() == []


def test_stored_measurement_timestamp_round_trips_with_utc_offset(client):
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
