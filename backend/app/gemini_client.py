"""
Phase 10 AI analysis: a thin client for the Gemini API (Google's
"Generative Language API"), used by backend/app/routers/ai.py to turn a
device's Phase 3/6 pipeline output into a plain-language insight and to
back a follow-up chat.

Deliberately a raw REST wrapper (stdlib `urllib`, no `google-genai` SDK
dependency) so the whole integration is exactly one small, readable file
and -- more importantly -- so it's trivial to unit-test: `transport` is
swappable, mirroring the `Transport` abstraction `common/interfaces/` uses
for Phase 7/8 (see common/interfaces/transport.py). Production code never
constructs a `GeminiClient` directly; it goes through
`backend/app/routers/ai.py:get_gemini_client`, which is what tests override.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import List, Literal, Optional, Protocol

_ENDPOINT_TEMPLATE = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


class GeminiError(RuntimeError):
    """Anything that stops a Gemini call from producing text: no API key,
    a network/HTTP failure, or a response that doesn't have the shape we
    expect. Routers catch this and turn it into an HTTP 502/503 -- see
    backend/app/routers/ai.py."""


@dataclass(frozen=True)
class ChatTurn:
    role: Literal["user", "model"]
    text: str


class Transport(Protocol):
    """`url` is the fully-formed request URL (API key included as a query
    param, matching how the Gemini REST API expects it); `payload` is the
    JSON request body. Returns the parsed JSON response body."""

    def __call__(self, url: str, payload: dict) -> dict: ...


def _urllib_post(url: str, payload: dict) -> dict:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise GeminiError(f"Gemini API returned HTTP {exc.code}: {detail[:500]}") from exc
    except urllib.error.URLError as exc:
        raise GeminiError(f"could not reach Gemini API: {exc.reason}") from exc
    except (TimeoutError, json.JSONDecodeError) as exc:
        raise GeminiError(f"Gemini API call failed: {exc}") from exc


class GeminiClient:
    """One instance per request (see backend/app/routers/ai.py) -- cheap to
    construct, holds no state beyond the key/model/transport."""

    def __init__(self, api_key: str, model: str = "gemini-3.6-flash", transport: Transport = _urllib_post) -> None:
        if not api_key:
            raise GeminiError("no Gemini API key configured -- set the GEMINI_API_KEY environment variable")
        self._api_key = api_key
        self._model = model
        self._transport = transport

    def generate(
        self,
        prompt: str,
        history: Optional[List[ChatTurn]] = None,
        system_instruction: Optional[str] = None,
    ) -> str:
        """`history` is prior chat turns (oldest first), `prompt` is the new
        user message. `system_instruction`, when given, steers the model
        without counting as a chat turn -- how the insight/chat instructions
        + device-data context (processing/intelligence/ai_insight.py) get in
        without polluting the visible conversation."""
        contents = [{"role": turn.role, "parts": [{"text": turn.text}]} for turn in (history or [])]
        contents.append({"role": "user", "parts": [{"text": prompt}]})

        payload: dict = {"contents": contents}
        if system_instruction:
            payload["systemInstruction"] = {"parts": [{"text": system_instruction}]}

        url = f"{_ENDPOINT_TEMPLATE.format(model=self._model)}?key={self._api_key}"
        response = self._transport(url, payload)
        return _extract_text(response)


def _extract_text(response: dict) -> str:
    try:
        parts = response["candidates"][0]["content"]["parts"]
        text = "".join(part.get("text", "") for part in parts)
    except (KeyError, IndexError, TypeError) as exc:
        raise GeminiError(f"unexpected Gemini response shape: {response!r}"[:500]) from exc
    if not text:
        raise GeminiError("Gemini returned an empty response (it may have been blocked by a safety filter)")
    return text
