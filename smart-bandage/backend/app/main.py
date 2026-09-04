"""
Phase 4 deliverable: FastAPI + PostgreSQL(-or-SQLite) implementing
docs/api/openapi.yaml, wired to the Phase 2 simulator and Phase 3
processing pipeline.

Run it:

    uvicorn backend.app.main:app --reload --app-dir smart-bandage

(or `cd smart-bandage && uvicorn backend.app.main:app --reload`)
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.app.config import settings
from backend.app.database import SessionLocal, init_db
from backend.app.routers import ai, alerts, auth, biomarkers, devices, measurements, simulation, ws
from backend.app.security import ensure_seed_user


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.validate()  # raises ConfigurationError if ENVIRONMENT=production with dev-only defaults
    init_db()
    if not settings.is_production:
        db = SessionLocal()
        try:
            ensure_seed_user(db)  # dev-only default: admin/admin -- see backend/app/security.py
        finally:
            db.close()
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

app.include_router(auth.router)
app.include_router(devices.router)
app.include_router(measurements.router)
app.include_router(biomarkers.router)
app.include_router(alerts.router)
app.include_router(simulation.router)
app.include_router(ws.router)
app.include_router(ai.router)


@app.get("/health", tags=["meta"])
def health() -> dict:
    return {"status": "ok"}
