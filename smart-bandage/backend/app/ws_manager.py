"""Live push to the dashboard: WS /ws/devices/{device_id} (Blueprint API §6)."""
from __future__ import annotations

from collections import defaultdict
from typing import Dict, List

from fastapi import WebSocket


class ConnectionManager:
    def __init__(self) -> None:
        self._connections: Dict[str, List[WebSocket]] = defaultdict(list)

    async def connect(self, device_id: str, websocket: WebSocket) -> None:
        await websocket.accept()
        self._connections[device_id].append(websocket)

    def disconnect(self, device_id: str, websocket: WebSocket) -> None:
        conns = self._connections.get(device_id, [])
        if websocket in conns:
            conns.remove(websocket)
        if not conns and device_id in self._connections:
            del self._connections[device_id]

    async def broadcast(self, device_id: str, payload: dict) -> None:
        dead: List[WebSocket] = []
        for ws in self._connections.get(device_id, []):
            try:
                await ws.send_json(payload)
            except Exception:  # noqa: BLE001 - a broken client socket shouldn't kill the loop
                dead.append(ws)
        for ws in dead:
            self.disconnect(device_id, ws)


manager = ConnectionManager()
