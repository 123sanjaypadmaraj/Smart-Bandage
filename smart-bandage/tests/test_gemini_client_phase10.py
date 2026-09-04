"""Phase 10: backend/app/gemini_client.py against a fake transport --
no network, no FastAPI app, no DB. See backend/tests/test_ai_analysis.py
for the endpoint-level tests that exercise the real router."""
from __future__ import annotations

import pytest

from backend.app.gemini_client import ChatTurn, GeminiClient, GeminiError


def test_requires_an_api_key():
    with pytest.raises(GeminiError):
        GeminiClient(api_key="")


def test_generate_builds_expected_payload_and_parses_reply():
    captured = {}

    def fake_transport(url: str, payload: dict) -> dict:
        captured["url"] = url
        captured["payload"] = payload
        return {"candidates": [{"content": {"parts": [{"text": "hello there"}]}}]}

    client = GeminiClient(api_key="test-key", model="gemini-test", transport=fake_transport)
    result = client.generate(
        "hi",
        history=[ChatTurn(role="user", text="prev turn")],
        system_instruction="be nice",
    )

    assert result == "hello there"
    assert "test-key" in captured["url"]
    assert "gemini-test" in captured["url"]
    assert captured["payload"]["systemInstruction"] == {"parts": [{"text": "be nice"}]}
    assert captured["payload"]["contents"][0] == {"role": "user", "parts": [{"text": "prev turn"}]}
    assert captured["payload"]["contents"][-1] == {"role": "user", "parts": [{"text": "hi"}]}


def test_generate_without_history_or_system_instruction_omits_them():
    captured = {}

    def fake_transport(url: str, payload: dict) -> dict:
        captured["payload"] = payload
        return {"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}

    client = GeminiClient(api_key="k", transport=fake_transport)
    client.generate("hi")

    assert "systemInstruction" not in captured["payload"]
    assert captured["payload"]["contents"] == [{"role": "user", "parts": [{"text": "hi"}]}]


def test_generate_joins_multiple_text_parts():
    def fake_transport(url: str, payload: dict) -> dict:
        return {"candidates": [{"content": {"parts": [{"text": "part one. "}, {"text": "part two."}]}}]}

    client = GeminiClient(api_key="k", transport=fake_transport)
    assert client.generate("hi") == "part one. part two."


@pytest.mark.parametrize(
    "response",
    [
        {},
        {"candidates": []},
        {"candidates": [{"content": {"parts": []}}]},
        {"candidates": [{"content": {"parts": [{"text": ""}]}}]},
        {"promptFeedback": {"blockReason": "SAFETY"}},
    ],
)
def test_generate_raises_on_malformed_or_empty_response(response):
    client = GeminiClient(api_key="k", transport=lambda url, payload: response)
    with pytest.raises(GeminiError):
        client.generate("hi")


def test_generate_propagates_transport_errors():
    def boom(url: str, payload: dict) -> dict:
        raise GeminiError("could not reach Gemini API: network down")

    client = GeminiClient(api_key="k", transport=boom)
    with pytest.raises(GeminiError, match="network down"):
        client.generate("hi")
