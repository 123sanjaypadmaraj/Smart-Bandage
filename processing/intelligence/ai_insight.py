"""
Phase 10 AI analysis: turns a device's recent Phase 3/6 pipeline output
(measurement history + active alerts) into the text prompt Gemini needs to
produce a grounded, plain-language insight or chat answer.

Kept separate from backend/app/gemini_client.py deliberately -- this module
touches no network and imports no HTTP client, so every prompt-shape
decision here (what data goes in, how it's worded, what the model is told
not to do) is unit-testable in isolation. See
tests/test_ai_insight_phase10.py.
"""
from __future__ import annotations

from typing import Dict, List, Sequence

from backend.app.schemas import Alert
from common.schemas.measurement import MeasurementRecord

_INSIGHT_INSTRUCTIONS = (
    "You are a monitoring assistant summarizing data from a wearable wound-monitoring "
    "biosensor bandage. You are not a doctor and must not issue a diagnosis. Using only "
    "the data below, write a short, plain-language summary (3-5 sentences) for a "
    "caregiver: what the recent readings show, whether any channel looks concerning, "
    "and one practical suggestion (e.g. 'keep monitoring' or 'contact a clinician') if "
    "warranted."
)

_CHAT_INSTRUCTIONS = (
    "You are a monitoring assistant answering a caregiver's questions about a wearable "
    "wound-monitoring biosensor bandage. You are not a doctor and must not issue a "
    "diagnosis -- for anything clinical, suggest contacting a clinician instead. Base "
    "your answers only on the data below; say so plainly if a question asks about "
    "something the data doesn't cover. Keep answers concise."
)


def _fmt(value: float | None, unit: str = "", precision: int = 3) -> str:
    if value is None:
        return "n/a"
    suffix = f" {unit}" if unit else ""
    return f"{value:.{precision}g}{suffix}"


def build_context_block(
    device_id: str,
    device_name: str,
    records: Sequence[MeasurementRecord],
    alerts: Sequence[Alert],
) -> str:
    """`records` may be in any order (grouped by channel here); `alerts`
    should be the device's currently-unresolved alerts. Raises ValueError
    when there's no measurement history at all -- callers should 404 before
    ever reaching this (see backend/app/routers/ai.py)."""
    if not records:
        raise ValueError("no measurement history to build a context block from")

    by_channel: Dict[str, List[MeasurementRecord]] = {}
    for record in records:
        by_channel.setdefault(record.channel_id, []).append(record)

    channel_lines: List[str] = []
    for channel_id, channel_records in by_channel.items():
        # newest first is how crud.query_measurements returns rows; keep that
        # order for "latest" but show history oldest-to-newest, matching how
        # a person reads a trend left-to-right
        latest = channel_records[0]
        history = ", ".join(_fmt(r.estimated_value) for r in reversed(channel_records))
        channel_lines.append(
            f"- {channel_id}: latest reading {_fmt(latest.estimated_value, latest.unit or '')} "
            f"(signal quality {_fmt(latest.signal_quality, precision=2)}, status {latest.status}); "
            f"recent history oldest-to-newest: {history}"
        )

    alert_lines = (
        [f"- [{alert.severity.upper()}] {alert.channel or 'device'}: {alert.message}" for alert in alerts]
        if alerts
        else ["- none"]
    )

    return (
        f"Device: {device_name} ({device_id})\n\n"
        "Recent channel readings:\n" + "\n".join(channel_lines) + "\n\n"
        "Active alerts:\n" + "\n".join(alert_lines)
    )


def build_insight_prompt(
    device_id: str,
    device_name: str,
    records: Sequence[MeasurementRecord],
    alerts: Sequence[Alert],
) -> str:
    """The full one-shot prompt for GET /devices/{id}/ai/insight."""
    context = build_context_block(device_id, device_name, records, alerts)
    return f"{_INSIGHT_INSTRUCTIONS}\n\n{context}"


def build_chat_system_prompt(
    device_id: str,
    device_name: str,
    records: Sequence[MeasurementRecord],
    alerts: Sequence[Alert],
) -> str:
    """A `system_instruction` (backend/app/gemini_client.py) for
    POST /devices/{id}/ai/chat -- grounds every turn in the same data
    without it counting as a visible message in the conversation."""
    context = build_context_block(device_id, device_name, records, alerts)
    return f"{_CHAT_INSTRUCTIONS}\n\n{context}"
