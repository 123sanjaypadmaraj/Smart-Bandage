"""
DISPATCH-7 observability tests -- structured logging, the deepened
/health check, /metrics, and the SENTRY_DSN opt-in. See
docs/observability.md.

Same DATABASE_URL/JWT_SECRET bootstrapping as backend/tests/test_api.py --
`setdefault` so whichever test module pytest imports first wins and every
module shares one throwaway DB safely (each test's `client` fixture drops
and recreates every table).
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from io import StringIO
from pathlib import Path

_tmp_dir = tempfile.mkdtemp(prefix="smart_bandage_observability_test_")
_db_path = Path(_tmp_dir) / "test.db"
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_db_path.as_posix()}")
os.environ.setdefault("JWT_SECRET", "test-secret")
os.environ.setdefault("SIMULATION_INTERVAL_SECONDS", "0.05")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from backend.app.config import Settings  # noqa: E402
from backend.app.database import Base, engine, get_db  # noqa: E402
from backend.app.logging_config import JSONFormatter  # noqa: E402
from backend.app.main import app  # noqa: E402
from backend.app.observability import init_sentry  # noqa: E402


@pytest.fixture()
def client():
    Base.metadata.drop_all(bind=engine)
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def test_health_reports_database_and_gemini_checks(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["checks"]["database"] == "ok"
    assert body["checks"]["gemini_configured"] is False


def test_health_degrades_when_database_unreachable(client):
    def _broken_db():
        class _Broken:
            def execute(self, *_args, **_kwargs):
                raise RuntimeError("connection refused")

        yield _Broken()

    app.dependency_overrides[get_db] = _broken_db
    resp = client.get("/health")
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "degraded"
    assert "error" in body["checks"]["database"]


def test_metrics_endpoint_exposes_prometheus_format(client):
    # Generate at least one request so the counters have a sample.
    client.get("/health")
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert "text/plain" in resp.headers["content-type"]
    text = resp.text
    assert "smart_bandage_http_requests_total" in text
    assert "smart_bandage_http_request_duration_seconds" in text
    # The /health hit above should show up labeled with the route
    # template, not a raw/duplicated path.
    assert 'path="/health"' in text


def test_json_formatter_produces_one_parseable_json_line_per_record():
    stream = StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JSONFormatter())
    logger = logging.getLogger("smart_bandage.test_json_formatter")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.INFO)

    logger.info("something_happened", extra={"device_id": "abc123", "count": 3})

    line = stream.getvalue().strip()
    payload = json.loads(line)  # raises if it's not valid JSON
    assert payload["message"] == "something_happened"
    assert payload["level"] == "INFO"
    assert payload["logger"] == "smart_bandage.test_json_formatter"
    assert payload["device_id"] == "abc123"
    assert payload["count"] == 3
    assert "timestamp" in payload


def test_sentry_stays_off_when_dsn_unset():
    # The real backend/app/config.py `settings` singleton in this test
    # process never had SENTRY_DSN set, so init_sentry() must be a no-op --
    # confirms the opt-in guard is live end to end, not just in isolation.
    assert init_sentry() is False


def test_sentry_initializes_when_dsn_set(monkeypatch):
    import backend.app.observability as observability_module

    # Settings is a frozen dataclass (backend/app/config.py) -- swap the
    # module-level `settings` name observability.py resolves at call time,
    # rather than mutating a field on the shared instance.
    monkeypatch.setattr(observability_module, "settings", Settings(sentry_dsn="https://public@localhost/1"))

    # A fake-but-well-formed DSN: sentry_sdk.init() parses/validates the
    # DSN locally and does not make a network call at init time, so this
    # stays offline like the rest of the suite.
    try:
        initialized = observability_module.init_sentry()
    finally:
        import sentry_sdk

        sentry_sdk.init(dsn=None)  # reset the global SDK client for later tests
    assert initialized is True
