"""Minimal in-process rate limiting for the auth endpoints
(backend/app/routers/auth.py).

Deliberately not a new dependency (slowapi/limits) for what this needs to
be: a fixed-window counter per client key, enough to blunt scripted
credential-stuffing/brute-force against POST /auth/login and token-guessing
against POST /auth/refresh. Tradeoffs that follow from that:

  - In-process only. A multi-worker/multi-replica deploy enforces the limit
    per process, not globally -- N workers means effectively N times the
    limit. Fine for blunting a single scripted client hammering one login
    form; not a substitute for a shared store (Redis etc.) if that matters
    for your deployment.
  - Keyed on `request.client.host` (the TCP peer address FastAPI/Starlette
    sees). Behind a reverse proxy (docker/nginx.tls.conf) that's the proxy's
    address unless the proxy is configured to pass and this app trusts
    X-Forwarded-For -- deliberately not done here, since trusting that
    header from an untrusted client lets an attacker spoof their way around
    the limit entirely. Effect: behind nginx, all requests key together
    (the whole deploy shares one bucket) -- still blunts a single attacker
    hammering the endpoint, just not by distinct client IP.
"""
from __future__ import annotations

import time
from collections import defaultdict
from threading import Lock
from typing import Dict, List


class FixedWindowRateLimiter:
    def __init__(self, max_requests: int, window_seconds: float) -> None:
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._hits: Dict[str, List[float]] = defaultdict(list)
        self._lock = Lock()

    def reset(self) -> None:
        """Clear all tracked state. Called once at app startup (see
        backend/app/main.py:lifespan) -- gives every fresh process a clean
        slate, and (not incidentally) gives every test a clean slate too,
        since backend/tests/test_api.py's `client` fixture opens a new
        `TestClient(app)` context -- and therefore re-runs this app's
        lifespan startup -- for each test function."""
        with self._lock:
            self._hits.clear()

    def check(self, key: str) -> bool:
        """Record one attempt for `key` and return whether it's still
        within the limit. Call once per incoming request; a False result
        means the caller should reject the request (e.g. HTTP 429)."""
        now = time.monotonic()
        cutoff = now - self.window_seconds
        with self._lock:
            hits = self._hits[key]
            while hits and hits[0] < cutoff:
                hits.pop(0)
            if len(hits) >= self.max_requests:
                return False
            hits.append(now)
            return True


def _int_env(name: str, default: str) -> int:
    import os

    return int(os.environ.get(name, default))


# Defaults: 10 login attempts / 60s and 30 refresh calls / 60s per client
# key. Overridable per deployment without a code change.
login_rate_limiter = FixedWindowRateLimiter(
    max_requests=_int_env("RATE_LIMIT_LOGIN_MAX", "10"),
    window_seconds=_int_env("RATE_LIMIT_LOGIN_WINDOW_SECONDS", "60"),
)
refresh_rate_limiter = FixedWindowRateLimiter(
    max_requests=_int_env("RATE_LIMIT_REFRESH_MAX", "30"),
    window_seconds=_int_env("RATE_LIMIT_REFRESH_WINDOW_SECONDS", "60"),
)
