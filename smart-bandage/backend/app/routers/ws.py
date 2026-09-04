"""WS /ws/devices/{device_id} (Blueprint API §6 "Realtime")."""
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
