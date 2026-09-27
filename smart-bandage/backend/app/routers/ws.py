"""WS /ws/devices/{device_id} (Blueprint API §6 "Realtime").

Server -> client only; every message is a JSON object with a `type`
(mirrored by frontend/src/types.ts DeviceMessage):

  measurement         {"measurement": MeasurementRecord}
  alert               {"alert": Alert}
  twin_ground_truth   {"ground_truth": TwinGroundTruth}                 DT-6, dev only
  twin_bandage_truth  schemas.TwinBandageTruthMessage                   bandage twin, dev only
                      {device_id, bandage_design, timestamp, truth}
  twin_eis            schemas.TwinEisMessage                            bandage twin, one per
                      {device_id, channel_id, timestamp, eis}           aptamer channel per tick

All are sent by backend/app/simulation.py's DeviceSimulation._tick; this
router only holds the socket open. Clients should ignore unknown types."""
from __future__ import annotations

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from backend.app.ws_manager import manager

router = APIRouter()


@router.websocket("/ws/devices/{device_id}")
async def device_updates(websocket: WebSocket, device_id: str) -> None:
    await manager.connect(device_id, websocket)
    try:
        while True:
            # dashboard doesn't need to send anything back yet; just keep
            # the socket open so disconnects are detected promptly
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(device_id, websocket)
