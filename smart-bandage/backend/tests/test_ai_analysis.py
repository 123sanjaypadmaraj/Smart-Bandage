"""
Phase 10 API tests -- GET/POST /devices/{id}/ai/{insight,chat}, exercised
through FastAPI's TestClient with a fake GeminiClient injected via
`app.dependency_overrides` so the suite never makes a real network call.
See backend/app/routers/ai.py, backend/app/gemini_client.py.

Same DATABASE_URL/JWT_SECRET bootstrapping as backend/tests/test_api.py --
`setdefault` rather than a plain assignment so whichever test module
pytest imports first wins and both share one throwaway DB safely (each
test's `client` fixture drops and recreates every table).
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from types import SimpleNamespace

_tmp_dir = tempfile.mkdtemp(prefix="smart_bandage_ai_test_")
_db_path = Path(_tmp_dir) / "test.db"
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_db_path.as_posix()}")
os.environ.setdefault("JWT_SECRET", "test-secret")
os.environ.setdefault("SIMULATION_INTERVAL_SECONDS", "0.05")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from backend.app.database import Base, engine  # noqa: E402
from backend.app.gemini_client import GeminiError  # noqa: E402
from backend.app.main import app  # noqa: E402
from backend.app.routers import ai as ai_router  # noqa: E402


class FakeGeminiClient:
    """Stands in for backend.app.gemini_client.GeminiClient -- records every
    call so tests can assert on the prompt/history/system_instruction the
    router built, without any of it touching the network."""

    def __init__(self, reply: str = "Readings look stable.", error: Exception | None = None):
        self.reply = reply
        self.error = error
        self.calls: list[dict] = []

    def generate(self, prompt, history=None, system_instruction=None):
        self.calls.append({"prompt": prompt, "history": history, "system_instruction": system_instruction})
        if self.error is not None:
            raise self.error
        return self.reply


@pytest.fixture()
def client():
    Base.metadata.drop_all(bind=engine)
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.pop(ai_router.get_gemini_client, None)


@pytest.fixture()
def auth_headers(client):
    resp = client.post("/auth/login", json={"username": "admin", "password": "admin"})
    assert resp.status_code == 200, resp.text
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _register_device(client, auth_headers, device_id="SB-AI-1", channels=("CH-01",)):
    resp = client.post(
        "/devices/register",
        json={"device_id": device_id, "name": "AI Test Bandage", "firmware_version": "0.1.0", "channels": list(channels)},
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    return device_id


def _ingest_measurement(client, device_id, channel_id="CH-01", estimated_value=4.5, timestamp="2026-08-31T12:00:00Z"):
    resp = client.post(
        "/measurements",
        json={
            "device_id": device_id,
            "channel_id": channel_id,
            "timestamp": timestamp,
            "raw_signal": 0.83,
            "processed_signal": 0.79,
            "estimated_value": estimated_value,
            "unit": "mM",
            "signal_quality": 0.9,
            "status": "valid",
        },
    )
    assert resp.status_code == 201, resp.text


def _seed_device_with_measurement(client, auth_headers, device_id="SB-AI-1"):
    _register_device(client, auth_headers, device_id)
    _ingest_measurement(client, device_id)
    return device_id


# ---- GET /devices/{id}/ai/insight ----


def test_insight_requires_auth(client, auth_headers):
    # Overridden so this exercises the auth check, not the (also-real)
    # 503 a missing GEMINI_API_KEY would otherwise raise first.
    app.dependency_overrides[ai_router.get_gemini_client] = lambda: FakeGeminiClient()
    device_id = _seed_device_with_measurement(client, auth_headers)
    resp = client.get(f"/devices/{device_id}/ai/insight")
    assert resp.status_code == 401


def test_insight_404_for_unknown_device(client, auth_headers):
    app.dependency_overrides[ai_router.get_gemini_client] = lambda: FakeGeminiClient()
    resp = client.get("/devices/does-not-exist/ai/insight", headers=auth_headers)
    assert resp.status_code == 404


def test_insight_404_for_device_with_no_measurements(client, auth_headers):
    app.dependency_overrides[ai_router.get_gemini_client] = lambda: FakeGeminiClient()
    device_id = _register_device(client, auth_headers, "SB-AI-empty")
    resp = client.get(f"/devices/{device_id}/ai/insight", headers=auth_headers)
    assert resp.status_code == 404


def test_insight_returns_summary_from_gemini(client, auth_headers):
    device_id = _seed_device_with_measurement(client, auth_headers)
    fake = FakeGeminiClient(reply="Readings are stable; no action needed.")
    app.dependency_overrides[ai_router.get_gemini_client] = lambda: fake

    resp = client.get(f"/devices/{device_id}/ai/insight", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["device_id"] == device_id
    assert body["summary"] == "Readings are stable; no action needed."
    assert body["model"]
    assert body["generated_at"]

    assert len(fake.calls) == 1
    assert "CH-01" in fake.calls[0]["prompt"]
    assert fake.calls[0]["history"] is None


def test_insight_surfaces_gemini_errors_as_502(client, auth_headers):
    device_id = _seed_device_with_measurement(client, auth_headers)
    app.dependency_overrides[ai_router.get_gemini_client] = lambda: FakeGeminiClient(error=GeminiError("boom"))

    resp = client.get(f"/devices/{device_id}/ai/insight", headers=auth_headers)
    assert resp.status_code == 502


def test_insight_503_when_no_api_key_configured(client, auth_headers, monkeypatch):
    device_id = _seed_device_with_measurement(client, auth_headers)
    monkeypatch.setattr(
        ai_router,
        "settings",
        SimpleNamespace(gemini_api_key="", gemini_model="gemini-3.6-flash"),
    )

    resp = client.get(f"/devices/{device_id}/ai/insight", headers=auth_headers)
    assert resp.status_code == 503


# ---- POST /devices/{id}/ai/chat ----


def test_chat_requires_auth(client, auth_headers):
    app.dependency_overrides[ai_router.get_gemini_client] = lambda: FakeGeminiClient()
    device_id = _seed_device_with_measurement(client, auth_headers)
    resp = client.post(f"/devices/{device_id}/ai/chat", json={"message": "hi"})
    assert resp.status_code == 401


def test_chat_uses_message_and_history_and_grounds_system_prompt(client, auth_headers):
    device_id = _seed_device_with_measurement(client, auth_headers)
    fake = FakeGeminiClient(reply="Yes, CH-01 is within its usual range.")
    app.dependency_overrides[ai_router.get_gemini_client] = lambda: fake

    resp = client.post(
        f"/devices/{device_id}/ai/chat",
        headers=auth_headers,
        json={
            "message": "Is CH-01 normal?",
            "history": [{"role": "user", "text": "hi"}, {"role": "model", "text": "hello"}],
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["reply"] == "Yes, CH-01 is within its usual range."

    call = fake.calls[0]
    assert call["prompt"] == "Is CH-01 normal?"
    assert call["system_instruction"] is not None and "CH-01" in call["system_instruction"]
    assert [(t.role, t.text) for t in call["history"]] == [("user", "hi"), ("model", "hello")]


def test_chat_rejects_empty_message(client, auth_headers):
    device_id = _seed_device_with_measurement(client, auth_headers)
    app.dependency_overrides[ai_router.get_gemini_client] = lambda: FakeGeminiClient()

    resp = client.post(f"/devices/{device_id}/ai/chat", headers=auth_headers, json={"message": ""})
    assert resp.status_code == 422


def test_chat_404_without_measurement_history(client, auth_headers):
    app.dependency_overrides[ai_router.get_gemini_client] = lambda: FakeGeminiClient()
    device_id = _register_device(client, auth_headers, "SB-AI-empty2")
    resp = client.post(f"/devices/{device_id}/ai/chat", headers=auth_headers, json={"message": "hi"})
    assert resp.status_code == 404
