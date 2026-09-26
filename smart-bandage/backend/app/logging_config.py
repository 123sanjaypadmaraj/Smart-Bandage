"""
Structured JSON logging (DISPATCH-7, see docs/observability.md).

Plain stdlib `logging` with a custom `Formatter`, not a third-party JSON
logging library, to keep the dependency list small -- consistent with
backend/app/config.py's "plain env-var driven config" philosophy. Every
log record emitted anywhere in the process becomes one JSON line on
stdout:

    {"timestamp": "...", "level": "INFO", "logger": "...", "message": "...", ...}

which any log aggregator (Grafana Loki/Promtail, CloudWatch, Datadog,
`docker logs`+`jq`, ...) can parse without a custom pipeline. See
docker-compose.observability.yml for a ready-to-run Loki+Promtail stack
that tails the backend container's stdout.

Structured (rather than plain-text) logging is unconditional -- there's no
dev-mode reason to want unparseable logs -- but LOG_LEVEL (config.py)
still tunes verbosity.
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any

# Attributes every stdlib LogRecord carries. Anything else on the record
# came from a caller's `logger.info(..., extra={...})` and is folded into
# the JSON payload as an application-specific field (e.g. the request
# method/path/status/duration_ms fields backend/app/metrics.py logs).
_STANDARD_RECORD_ATTRS = frozenset(
    logging.LogRecord(
        name="", level=0, pathname="", lineno=0, msg="", args=(), exc_info=None
    ).__dict__.keys()
) | {"message", "asctime", "taskName"}


class JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _STANDARD_RECORD_ATTRS:
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        # default=str so an unexpected non-JSON-serializable `extra` value
        # (a dataclass, an exception object, ...) never crashes logging
        # itself -- it just gets stringified instead of dropping the line.
        return json.dumps(payload, default=str)


def configure_logging(level: str | None = None) -> None:
    """Call once at process startup (backend/app/main.py, module level so
    it also runs under `uvicorn --reload` and in tests that import the
    app). Idempotent -- safe to call more than once; it just replaces the
    root logger's handlers rather than accumulating duplicates."""
    root = logging.getLogger()
    root.setLevel((level or "INFO").upper())
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JSONFormatter())
    root.handlers = [handler]

    # uvicorn installs its own plain-text access logger; our request
    # logging (backend/app/metrics.py:MetricsMiddleware) already emits one
    # structured JSON line per request with more detail (status,
    # duration_ms), so silence the duplicate rather than emitting both.
    access_logger = logging.getLogger("uvicorn.access")
    access_logger.handlers = []
    access_logger.propagate = False
