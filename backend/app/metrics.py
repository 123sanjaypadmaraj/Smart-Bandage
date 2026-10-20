"""
Request/error metrics (DISPATCH-7, see docs/observability.md).

`MetricsMiddleware` wraps every request and:
  1. increments Prometheus counters/histograms (exposed at GET /metrics,
     scraped by the optional docker-compose.observability.yml overlay),
  2. emits one structured JSON access-log line per request (via
     backend/app/logging_config.py's JSONFormatter).

Uses `prometheus_client` (the reference Python client, pure Python, no
compiled extensions) rather than hand-rolled counters so /metrics speaks
the standard Prometheus text exposition format out of the box -- no
custom scrape config needed.
"""
from __future__ import annotations

import logging
import time
from typing import Awaitable, Callable

from fastapi import Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger("smart_bandage.access")

REQUEST_COUNT = Counter(
    "smart_bandage_http_requests_total",
    "Total HTTP requests handled, by method/route/status",
    ["method", "path", "status"],
)
ERROR_COUNT = Counter(
    "smart_bandage_http_errors_total",
    "Total HTTP requests that returned a 5xx status or raised an unhandled exception",
    ["method", "path"],
)
REQUEST_LATENCY = Histogram(
    "smart_bandage_http_request_duration_seconds",
    "HTTP request duration in seconds, by method/route",
    ["method", "path"],
)


def _route_path(request: Request) -> str:
    """The matched route *template* (e.g. `/devices/{device_id}`) when
    routing has completed, falling back to the raw URL path for requests
    that never matched a route (404s) -- keeps the `path` label's
    cardinality bounded instead of growing one series per device id."""
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    return path or request.url.path


class MetricsMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        start = time.perf_counter()
        method = request.method
        status_code = 500
        raised: BaseException | None = None
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        except BaseException as exc:
            raised = exc
            raise
        finally:
            duration = time.perf_counter() - start
            path = _route_path(request)
            REQUEST_COUNT.labels(method=method, path=path, status=str(status_code)).inc()
            REQUEST_LATENCY.labels(method=method, path=path).observe(duration)
            if raised is not None or status_code >= 500:
                ERROR_COUNT.labels(method=method, path=path).inc()
            logger.info(
                "http_request",
                extra={
                    "http_method": method,
                    "http_path": path,
                    "http_status": status_code,
                    "duration_ms": round(duration * 1000, 2),
                },
            )


def metrics_response() -> Response:
    """Prometheus text exposition format -- GET /metrics in main.py."""
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
