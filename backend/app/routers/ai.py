"""
Phase 10 AI analysis: GET /devices/{id}/ai/insight and POST
/devices/{id}/ai/chat, both grounded in the device's real Phase 3/6
pipeline output -- never invented data.

Prompt construction lives in processing/intelligence/ai_insight.py (pure,
unit-tested without network); the Gemini transport lives in
backend/app/gemini_client.py (mockable). This router just wires the two
together: load a device's recent measurements + active alerts, build the
prompt, call Gemini, shape the response. See
backend/tests/test_ai_analysis.py for the mocked end-to-end tests.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Tuple

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.app import crud
from backend.app.config import settings
from backend.app.database import get_db
from backend.app.gemini_client import ChatTurn, GeminiClient, GeminiError
from backend.app.models import DeviceORM
from backend.app.schemas import AIChatRequest, AIChatResponse, AIInsightResponse
from backend.app.security import get_current_username
from common.schemas.measurement import MeasurementRecord
from processing.intelligence.ai_insight import build_chat_system_prompt, build_insight_prompt

router = APIRouter(prefix="/devices/{device_id}/ai", tags=["ai"])

# Recent readings (across all of a device's channels) fed to the model --
# enough for the trend context build_context_block formats, without
# growing the prompt unbounded on a long-running device.
HISTORY_LIMIT = 60


def get_gemini_client() -> GeminiClient:
    """FastAPI dependency -- overridden in tests with a fake client
    (app.dependency_overrides) so the suite never makes a real network
    call. Reads `settings` fresh on every call rather than caching a client,
    so flipping GEMINI_API_KEY doesn't need a process restart."""
    if not settings.gemini_api_key:
        raise HTTPException(
            status_code=503,
            detail="AI analysis is not configured -- set the GEMINI_API_KEY environment variable",
        )
    return GeminiClient(api_key=settings.gemini_api_key, model=settings.gemini_model)


def _device_and_context(db: Session, device_id: str) -> Tuple[DeviceORM, List[MeasurementRecord]]:
    device_row = crud.get_device(db, device_id)
    if device_row is None:
        raise HTTPException(status_code=404, detail="device not found")

    rows = crud.query_measurements(db, device_id=device_id, limit=HISTORY_LIMIT)
    records = [crud.measurement_to_schema(r) for r in rows]
    if not records:
        raise HTTPException(status_code=404, detail="no measurement history for this device yet")
    return device_row, records


@router.get("/insight", response_model=AIInsightResponse)
def get_ai_insight(
    device_id: str,
    db: Session = Depends(get_db),
    client: GeminiClient = Depends(get_gemini_client),
    _user: str = Depends(get_current_username),
) -> AIInsightResponse:
    device_row, records = _device_and_context(db, device_id)
    alerts = [crud.alert_to_schema(row) for row in crud.list_active_alerts(db, device_id=device_id)]

    prompt = build_insight_prompt(device_id, device_row.name, records, alerts)
    try:
        summary = client.generate(prompt)
    except GeminiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return AIInsightResponse(
        device_id=device_id,
        summary=summary,
        model=settings.gemini_model,
        generated_at=datetime.now(timezone.utc),
    )


@router.post("/chat", response_model=AIChatResponse)
def post_ai_chat(
    device_id: str,
    body: AIChatRequest,
    db: Session = Depends(get_db),
    client: GeminiClient = Depends(get_gemini_client),
    _user: str = Depends(get_current_username),
) -> AIChatResponse:
    device_row, records = _device_and_context(db, device_id)
    alerts = [crud.alert_to_schema(row) for row in crud.list_active_alerts(db, device_id=device_id)]

    system_prompt = build_chat_system_prompt(device_id, device_row.name, records, alerts)
    history = [ChatTurn(role=turn.role, text=turn.text) for turn in body.history]

    try:
        reply = client.generate(body.message, history=history, system_instruction=system_prompt)
    except GeminiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return AIChatResponse(device_id=device_id, reply=reply, model=settings.gemini_model)
