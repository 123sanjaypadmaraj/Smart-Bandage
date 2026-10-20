"""GET/POST /measurements, GET /measurements/{id} (Blueprint API §6)."""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.app import crud
from backend.app.database import get_db
from backend.app.security import get_current_username
from common.schemas.measurement import MeasurementRecord

router = APIRouter(prefix="/measurements", tags=["measurements"])


@router.get("", response_model=List[MeasurementRecord])
def query_measurements(
    device_id: Optional[str] = None,
    channel_id: Optional[str] = None,
    since: Optional[datetime] = None,
    limit: int = 100,
    db: Session = Depends(get_db),
) -> List[MeasurementRecord]:
    rows = crud.query_measurements(db, device_id, channel_id, since, limit)
    return [crud.measurement_to_schema(row) for row in rows]


@router.post("", response_model=MeasurementRecord, status_code=201)
def ingest_measurement(
    record: MeasurementRecord,
    db: Session = Depends(get_db),
    _user: str = Depends(get_current_username),
) -> MeasurementRecord:
    """Ingest one already-processed measurement -- from a BLE gateway or CSV
    import that ran its own pipeline. The simulator ingests through
    backend/app/simulation.py directly, reusing processing/ in-process.

    Gated behind the same user JWT login as the rest of the authenticated
    API (see get_current_username) -- not a real per-device credential.
    Anyone who can sign in can post data for any device_id; there's no
    device-level auth model yet. That's a known, documented gap (see
    docs/security.md), not a full fix -- but it closes off unauthenticated
    data injection, which is strictly worse than "any logged-in user can
    write for any device."
    """
    row = crud.store_measurement(db, record)
    return crud.measurement_to_schema(row)


@router.get("/{measurement_id}", response_model=MeasurementRecord)
def get_measurement(measurement_id: int, db: Session = Depends(get_db)) -> MeasurementRecord:
    row = crud.get_measurement(db, measurement_id)
    if row is None:
        raise HTTPException(status_code=404, detail="measurement not found")
    return crud.measurement_to_schema(row)
