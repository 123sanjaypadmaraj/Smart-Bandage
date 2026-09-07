"""POST /simulation/start · /stop · /scenario (Blueprint API §6), plus the
DT-6 twin-backed-mode reads: GET /simulation/patient-profiles and
GET /simulation/twin/{device_id}."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.app import crud
from backend.app.config import settings
from backend.app.database import get_db
from backend.app.schemas import (
    PatientProfileInfo,
    SimulationScenarioRequest,
    SimulationStartRequest,
    TwinGroundTruth,
)
from backend.app.security import get_current_username
from backend.app.simulation import list_patient_profiles as _list_patient_profiles
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
    try:
        await simulation_manager.start(
            body.device_id,
            body.channels,
            body.scenario,
            duration=body.duration,
            interval_seconds=settings.simulation_interval_seconds,
            patient_profile=body.patient_profile,
            time_scale=body.time_scale,
            twin_channel_id=body.twin_channel_id,
        )
    except KeyError as exc:
        # unknown patient_profile -- digital_twin.patient_profile.get_patient_profile
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValueError as exc:
        # bad twin-backed request shape -- empty channels or a twin_channel_id
        # that isn't one of `channels` (backend/app/simulation.py:DeviceSimulation.__init__)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "status": "started",
        "device_id": body.device_id,
        "scenario": body.scenario,
        "twin": body.patient_profile is not None,
    }


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
    except ValueError as exc:
        # device is twin-backed -- see SimulationManager.set_scenario
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"status": "scenario set", "device_id": body.device_id, "scenario": body.scenario}


@router.get("/patient-profiles", response_model=list[PatientProfileInfo])
def list_patient_profiles() -> list[PatientProfileInfo]:
    """Lets the dashboard/mobile twin control panel populate its picker
    without hardcoding backend/app/simulation.py's registry. No auth, same
    as GET /devices -- nothing here is per-user."""
    return [PatientProfileInfo(**info) for info in _list_patient_profiles()]


@router.get("/twin/{device_id}", response_model=TwinGroundTruth)
def get_twin_ground_truth(device_id: str) -> TwinGroundTruth:
    """DT-6 dev-only "ground truth overlay" data -- 404 in production
    (Settings.is_production, same gate as the seeded dev login) since this
    exposes the twin's hidden state, and 404 whenever there isn't one yet
    (not twin-backed, not running, or hasn't ticked)."""
    if settings.is_production:
        raise HTTPException(status_code=404, detail="not available in production")
    snapshot = simulation_manager.twin_ground_truth(device_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="no twin-backed simulation running for that device yet")
    return TwinGroundTruth(
        device_id=device_id,
        channel_id=snapshot.channel_id,
        patient_profile=snapshot.patient_profile,
        time_scale=snapshot.time_scale,
        inflammation=snapshot.inflammation,
        bacterial_load=snapshot.bacterial_load,
        moisture=snapshot.moisture,
        perfusion=snapshot.perfusion,
        true_signal=snapshot.true_signal,
        estimated_signal=snapshot.estimated_signal,
        timestamp=snapshot.timestamp,
    )
