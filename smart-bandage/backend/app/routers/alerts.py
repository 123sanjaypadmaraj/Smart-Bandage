"""GET /alerts (Blueprint API §6 "Health & alerts")."""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.app import crud
from backend.app.database import get_db
from backend.app.schemas import Alert

router = APIRouter(prefix="/alerts", tags=["alerts"])


@router.get("", response_model=List[Alert])
def list_alerts(device_id: Optional[str] = None, db: Session = Depends(get_db)) -> List[Alert]:
    return [crud.alert_to_schema(row) for row in crud.list_active_alerts(db, device_id)]
