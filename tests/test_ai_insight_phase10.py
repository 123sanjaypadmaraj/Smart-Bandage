"""Phase 10: processing/intelligence/ai_insight.py's prompt-building --
pure functions, no network, no DB. See tests/test_gemini_client_phase10.py
for the Gemini transport and backend/tests/test_ai_analysis.py for the
endpoint-level tests."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from backend.app.schemas import Alert
from common.schemas.measurement import MeasurementRecord
from processing.intelligence.ai_insight import (
    build_chat_system_prompt,
    build_context_block,
    build_insight_prompt,
)


def _record(channel_id="CH-01", value=4.5, quality=0.9, status="valid", unit="mM", minute=0) -> MeasurementRecord:
    return MeasurementRecord(
        device_id="SB-001",
        channel_id=channel_id,
        timestamp=datetime(2026, 8, 31, 12, minute, tzinfo=timezone.utc),
        raw_signal=1.0,
        processed_signal=1.0,
        estimated_value=value,
        unit=unit,
        signal_quality=quality,
        status=status,
    )


def _alert(message="value crossed a threshold", severity="warning", channel="CH-01") -> Alert:
    return Alert(
        type="WARNING",
        severity=severity,
        device_id="SB-001",
        channel=channel,
        message=message,
        timestamp=datetime(2026, 8, 31, 12, 5, tzinfo=timezone.utc),
    )


def test_build_context_block_raises_on_no_records():
    with pytest.raises(ValueError):
        build_context_block("SB-001", "Prototype #1", [], [])


def test_build_context_block_includes_device_channel_and_alert_data():
    records = [_record(value=5.0, minute=2), _record(value=4.0, minute=1), _record(value=3.0, minute=0)]
    block = build_context_block("SB-001", "Prototype #1", records, [_alert()])

    assert "SB-001" in block
    assert "Prototype #1" in block
    assert "CH-01" in block
    assert "latest reading 5" in block  # newest record (minute=2) is index 0
    # history is rendered oldest-to-newest, independent of the newest-first input order
    history = block.split("recent history oldest-to-newest:")[1]
    assert history.index("3") < history.index("4") < history.index("5")
    assert "WARNING" in block
    assert "value crossed a threshold" in block


def test_build_context_block_handles_multiple_channels_separately():
    records = [_record(channel_id="CH-01", value=1.0), _record(channel_id="CH-02", value=99.0)]
    block = build_context_block("SB-001", "Prototype #1", records, [])

    assert "CH-01" in block and "CH-02" in block


def test_build_context_block_reports_no_alerts_explicitly():
    block = build_context_block("SB-001", "Prototype #1", [_record()], [])
    assert "none" in block.lower()


def test_build_context_block_handles_missing_estimated_value_and_quality():
    record = MeasurementRecord(
        device_id="SB-001",
        channel_id="CH-01",
        timestamp=datetime(2026, 8, 31, 12, 0, tzinfo=timezone.utc),
        raw_signal=1.0,
        status="error",
    )
    block = build_context_block("SB-001", "Prototype #1", [record], [])
    assert "n/a" in block
    assert "error" in block


def test_insight_prompt_includes_instructions_and_forbids_diagnosis():
    prompt = build_insight_prompt("SB-001", "Prototype #1", [_record()], [])
    assert "not a doctor" in prompt
    assert "diagnosis" in prompt
    assert "SB-001" in prompt


def test_chat_system_prompt_differs_from_insight_prompt_but_shares_context():
    records = [_record()]
    insight = build_insight_prompt("SB-001", "Prototype #1", records, [])
    chat = build_chat_system_prompt("SB-001", "Prototype #1", records, [])

    assert insight != chat
    assert "CH-01" in insight and "CH-01" in chat
    assert "caregiver's questions" in chat
