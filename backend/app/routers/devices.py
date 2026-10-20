"""GET/POST /devices*, GET /device-status (Blueprint API §6)."""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.app import crud
from backend.app.database import get_db
from backend.app.schemas import DeviceRegisterRequest
from backend.app.security import get_current_username
from backend.app.simulation import simulation_manager
from common.schemas.device import Device, DeviceStatus

router = APIRouter(tags=["devices"])


@router.post("/devices/register", response_model=Device, status_code=201)
def register_device(
    body: DeviceRegisterRequest,
    db: Session = Depends(get_db),
    _user: str = Depends(get_current_username),
) -> Device:
    device = Device(
        device_id=body.device_id,
        name=body.name,
        firmware_version=body.firmware_version,
        channels=body.channels,
        status="unknown",
    )
    row = crud.upsert_device(db, device)
    return crud.device_to_schema(row)


@router.get("/devices", response_model=List[Device])
def list_devices(db: Session = Depends(get_db)) -> List[Device]:
    return [crud.device_to_schema(row) for row in crud.list_devices(db)]


@router.get("/devices/{device_id}", response_model=Device)
def get_device(device_id: str, db: Session = Depends(get_db)) -> Device:
    row = crud.get_device(db, device_id)
    if row is None:
        raise HTTPException(status_code=404, detail="device not found")
    return crud.device_to_schema(row)


@router.get("/device-status", response_model=DeviceStatus)
def device_status(
    device_id: str,
    channel_id: Optional[str] = None,
    db: Session = Depends(get_db),
) -> DeviceStatus:
    """Prefers the live status of a running simulation over the last
    persisted snapshot -- see Blueprint §6, "battery, connectivity, quality"."""
    live = simulation_manager.live_status(device_id, channel_id)
    if live is not None:
        if isinstance(live, dict):
            statuses = list(live.values())
            batteries = [s.battery for s in statuses if s.battery is not None]
            qualities = [s.signal_quality for s in statuses if s.signal_quality is not None]
            errors = [s.last_error for s in statuses if s.last_error]
            return DeviceStatus(
                connected=all(s.connected for s in statuses),
                battery=min(batteries) if batteries else None,
                signal_quality=min(qualities) if qualities else None,
                last_error="; ".join(errors) if errors else None,
            )
        return live

    row = crud.get_device(db, device_id)
    if row is None:
        raise HTTPException(status_code=404, detail="device not found")
    return DeviceStatus(connected=row.status == "online", battery=None, signal_quality=None, last_error=None)
