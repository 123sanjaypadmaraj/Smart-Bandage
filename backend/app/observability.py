"""
Optional error tracking (DISPATCH-7, see docs/observability.md).

Gated by SENTRY_DSN the same opt-in way backend/app/config.py gates
GEMINI_API_KEY: unset it and `sentry_sdk` is never imported and
`sentry_sdk.init()` is never called, so the app runs identically (and
needs no Sentry account, no network calls to sentry.io) with it unset.
Set it and every unhandled exception plus 5xx response is reported.

Not limited to Sentry specifically -- any Sentry-compatible DSN (Sentry
self-hosted, GlitchTip, ...) works, since they all speak the same
`sentry_sdk` client protocol.
"""
from __future__ import annotations

from backend.app.config import settings


def init_sentry() -> bool:
    """Call once at process startup (backend/app/main.py), before the
    `FastAPI()` app is constructed -- sentry_sdk's ASGI/FastAPI
    integration only instruments requests started after `init()` runs.
    Returns whether it actually initialized, mainly so tests/startup logs
    can assert on it without reaching into sentry_sdk internals."""
    if not settings.sentry_dsn:
        return False

    import sentry_sdk
    from sentry_sdk.integrations.fastapi import FastApiIntegration
    from sentry_sdk.integrations.starlette import StarletteIntegration

    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.environment,
        traces_sample_rate=settings.sentry_traces_sample_rate,
        integrations=[StarletteIntegration(), FastApiIntegration()],
    )
    return True
