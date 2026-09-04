"""POST /simulation/start · /stop · /scenario (Blueprint API §6)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.app import crud
from backend.app.config import settings
from backend.app.database import get_db
from backend.app.schemas import SimulationScenarioRequest, SimulationStartRequest
from backend.app.security import get_current_username
from backend.app.simulation import simulation_manager
from common.schemas.device import Device

router = APIRouter(prefix="/simulation", tags=["simulation"])


@router.post("/start", status_code=202)
async def start_simulation(
    body: SimulationStartRequest,
    db: Session = Depends(get_db),
    _user: str = Depends(get_current_username),
) -> dict:
    if crud.get_device(db, body.device_id) is None:
        crud.upsert_device(
            db,
            Device(
                device_id=body.device_id,
                name=f"Simulated {body.device_id}",
                firmware_version="sim-0.1.0",
                channels=body.channels,
                status="online",
            ),
        )
    await simulation_manager.start(
        body.device_id,
        body.channels,
        body.scenario,
        duration=body.duration,
        interval_seconds=settings.simulation_interval_seconds,
    )
    return {"status": "started", "device_id": body.device_id, "scenario": body.scenario}


@router.post("/stop", status_code=202)
async def stop_simulation(device_id: str, _user: str = Depends(get_current_username)) -> dict:
    stopped = await simulation_manager.stop(device_id)
    if not stopped:
        raise HTTPException(status_code=404, detail="no running simulation for that device")
    return {"status": "stopped", "device_id": device_id}


@router.post("/scenario", status_code=202)
def inject_scenario(body: SimulationScenarioRequest, _user: str = Depends(get_current_username)) -> dict:
    try:
        simulation_manager.set_scenario(body.device_id, body.scenario, body.channel_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"status": "scenario set", "device_id": body.device_id, "scenario": body.scenario}
