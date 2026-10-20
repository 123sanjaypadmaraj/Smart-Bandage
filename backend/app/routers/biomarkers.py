"""GET /biomarkers, GET /biomarkers/{name} (Blueprint API §6)."""
from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.app import crud
from backend.app.database import get_db
from backend.app.schemas import BiomarkerWithTrend
from processing.intelligence.anomaly import ChannelAnomalyDetector
from processing.intelligence.confidence import estimate_confidence

router = APIRouter(prefix="/biomarkers", tags=["biomarkers"])

# how much history to replay through the Phase 6 detector per request --
# matches ChannelAnomalyDetector's own default window, no point fetching more
HISTORY_WINDOW = 12


@router.get("", response_model=List[str])
def list_biomarkers(db: Session = Depends(get_db)) -> List[str]:
    return crud.distinct_channel_ids(db)


@router.get("/{name}", response_model=BiomarkerWithTrend)
def get_biomarker(name: str, db: Session = Depends(get_db)) -> BiomarkerWithTrend:
    """`name` is a channel_id -- latest converted value plus a Phase 6
    trend/anomaly read derived from up to HISTORY_WINDOW prior readings on
    that same channel, not just the single previous one."""
    rows = crud.latest_measurements_for_channel(db, name, limit=HISTORY_WINDOW)
    if not rows:
        raise HTTPException(status_code=404, detail="no biomarker data for that channel")

    latest = rows[0]
    ascending = list(reversed(rows))  # crud returns newest-first; the detector wants oldest-first

    detector = ChannelAnomalyDetector(window=HISTORY_WINDOW)
    result = None
    for row in ascending:
        result = detector.update(crud.as_utc(row.timestamp), row.estimated_value, row.signal_quality)

    trend = result.value_trend.label if result and result.value_trend else "unknown"
    r_squared = result.value_trend.r_squared if result and result.value_trend else None
    anomaly_score = result.score if result else 0.0
    confidence = estimate_confidence(
        signal_quality=latest.signal_quality,
        trend_r_squared=r_squared,
        anomaly_score=anomaly_score,
    )

    return BiomarkerWithTrend(
        channel_id=latest.channel_id,
        estimated_value=latest.estimated_value,
        unit=latest.unit or "a.u.",
        confidence=confidence,
        trend=trend,
        anomaly=bool(result.is_anomaly) if result else False,
        timestamp=crud.as_utc(latest.timestamp),
    )
