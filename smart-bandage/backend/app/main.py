"""
Phase 4 deliverable: FastAPI + PostgreSQL(-or-SQLite) implementing
docs/api/openapi.yaml, wired to the Phase 2 simulator and Phase 3
processing pipeline.

Run it:

    uvicorn backend.app.main:app --reload --app-dir smart-bandage

(or `cd smart-bandage && uvicorn backend.app.main:app --reload`)
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.app.config import settings
from backend.app.database import SessionLocal, get_db, init_db
from backend.app.logging_config import configure_logging
from backend.app.metrics import MetricsMiddleware, metrics_response
from backend.app.observability import init_sentry
from backend.app.rate_limit import login_rate_limiter, refresh_rate_limiter
from backend.app.routers import ai, alerts, auth, biomarkers, devices, measurements, simulation, ws
from backend.app.security import ensure_seed_user

# Both run at import time (module scope, not inside lifespan) so they're
# active for `uvicorn`, `pytest`'s TestClient, and any WSGI/ASGI runner
# alike -- logging before the first log line is emitted, Sentry before the
# FastAPI app (and its ASGI middleware stack) is constructed below.
configure_logging(settings.log_level)
init_sentry()

logger = logging.getLogger("smart_bandage")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.validate()  # raises ConfigurationError if ENVIRONMENT=production with dev-only defaults
    # Clean slate per process start -- also what makes each test's fresh
    # `TestClient(app)` (which re-runs this lifespan) not see rate-limit
    # state left over from an earlier test.
    login_rate_limiter.reset()
    refresh_rate_limiter.reset()
    init_db()
    if not settings.is_production:
        db = SessionLocal()
        try:
            ensure_seed_user(db)  # dev-only default: admin/admin -- see backend/app/security.py
        finally:
            db.close()
    logger.info(
        "startup_complete",
        extra={"environment": settings.environment, "gemini_configured": bool(settings.gemini_api_key)},
    )
    yield


app = FastAPI(
    title="Smart Bandage API",
    version="0.1.0",
    description="Backend for the Smart Bandage platform (Blueprint Phase 4).",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.cors_origins),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
# Outermost of our own middleware (added last = runs first/wraps
# everything else) so it times and counts the full request, CORS included.
app.add_middleware(MetricsMiddleware)

app.include_router(auth.router)
app.include_router(devices.router)
app.include_router(measurements.router)
app.include_router(biomarkers.router)
app.include_router(alerts.router)
app.include_router(simulation.router)
app.include_router(ws.router)
app.include_router(ai.router)


@app.get("/health", tags=["meta"])
def health(db: Session = Depends(get_db)) -> JSONResponse:
    """Liveness *and* readiness in one endpoint (Blueprint has no separate
    /ready): confirms the DB is actually reachable (not just that the
    process is up) and surfaces whether Phase 10 AI analysis is
    configured, so `docker compose ps` / an uptime check / a human hitting
    this URL can tell those apart from a generic "the process didn't
    crash" 200. See docs/observability.md."""
    db_error: str | None = None
    try:
        db.execute(text("SELECT 1"))
    except Exception as exc:  # see backend/tests/test_observability.py for the DB-down case
        db_error = str(exc)

    body = {
        "status": "ok" if db_error is None else "degraded",
        "checks": {
            "database": "ok" if db_error is None else f"error: {db_error}",
            "gemini_configured": bool(settings.gemini_api_key),
        },
    }
    return JSONResponse(status_code=200 if db_error is None else 503, content=body)


@app.get("/metrics", tags=["meta"])
def metrics():
    """Prometheus text exposition format -- see docker-compose.observability.yml."""
    return metrics_response()
