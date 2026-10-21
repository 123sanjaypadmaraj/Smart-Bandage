"""Emulated EIS analyzer -- the dataset in datasets/ecoli_eis/ replayed as
hardware output (see backend/app/hardware_eis.py). Public and read-only like
GET /api/twin/eis: nothing here is per-user and no state is stored.

  GET /api/hardware/eis/samples          the stored runs (filter: ?sample_class=)
  GET /api/hardware/eis/capture          every frame of one assay, as a list
  WS  /ws/hardware/eis                   the same frames, paced like an instrument

Both capture endpoints take either `run_id` (replay a stored run) or
`sample_class` + `cfu` + `seed` (synthesise a new one)."""
from __future__ import annotations

import asyncio
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query, WebSocket, WebSocketDisconnect

from backend.app import hardware_eis

router = APIRouter(tags=["hardware-eis"])

_CLASSES = "^(target|nonspecific|blank)$"


@router.get("/api/hardware/eis/samples")
def list_samples(sample_class: Optional[str] = Query(None, pattern=_CLASSES)) -> List[dict]:
    return hardware_eis.list_samples(sample_class)


@router.get("/api/hardware/eis/capture")
def get_capture(
    run_id: Optional[str] = Query(None, max_length=32),
    sample_class: str = Query("target", pattern=_CLASSES),
    cfu: float = Query(0.0, ge=0.0, le=1e9),
    seed: int = Query(0, ge=0, le=2**31 - 1),
) -> dict:
    try:
        frames = hardware_eis.capture(run_id, sample_class=sample_class, cfu=cfu, seed=seed)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc.args[0])) from exc
    return {"frames": frames}


@router.websocket("/ws/hardware/eis")
async def stream_capture(
    websocket: WebSocket,
    run_id: Optional[str] = Query(None, max_length=32),
    sample_class: str = Query("target", pattern=_CLASSES),
    cfu: float = Query(0.0, ge=0.0, le=1e9),
    seed: int = Query(0, ge=0, le=2**31 - 1),
    point_interval_s: float = Query(0.05, ge=0.0, le=10.0, description="wall-clock gap between sweep points"),
) -> None:
    await websocket.accept()
    try:
        frames = await asyncio.to_thread(
            hardware_eis.capture, run_id, sample_class=sample_class, cfu=cfu, seed=seed
        )
    except KeyError as exc:
        await websocket.close(code=4404, reason=str(exc.args[0]))
        return
    try:
        for frame in frames:
            await websocket.send_json(frame)
            await asyncio.sleep(point_interval_s if frame["type"] == "point" else point_interval_s * 4)
        await websocket.close()
    except (WebSocketDisconnect, RuntimeError):
        return
