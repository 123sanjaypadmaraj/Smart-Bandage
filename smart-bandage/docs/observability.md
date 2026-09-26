# Observability (DISPATCH-7)

What the backend gives you for running it in production: structured logs,
a health check that actually checks something, request/error metrics, and
optional error tracking. All of it is either zero-config or opt-in the
same way `GEMINI_API_KEY` already is — nothing here changes how you run
the backend locally unless you ask it to.

## Structured (JSON) logging

`backend/app/logging_config.py` installs a JSON `Formatter` on the root
logger at import time (`backend/app/main.py`), unconditionally — there's
no dev-mode reason to want unparseable logs. Every log line, including
uvicorn's own startup/shutdown messages, comes out as one JSON object on
stdout:

```json
{"timestamp": "2026-09-07T12:00:00.123456+00:00", "level": "INFO", "logger": "smart_bandage.access", "message": "http_request", "http_method": "GET", "http_path": "/health", "http_status": 200, "duration_ms": 1.42}
```

- `LOG_LEVEL` (env var, default `INFO`) tunes verbosity — set `DEBUG`
  locally, `WARNING` if `INFO` is too chatty in prod.
- uvicorn's default plain-text access logger is silenced
  (`uvicorn.access`) since `MetricsMiddleware` (below) already logs one
  structured `http_request` line per request with more detail.
- Any code can add structured fields with `logger.info("event_name",
  extra={"key": value})` — anything under `extra` is folded into the JSON
  payload verbatim (see `backend/app/metrics.py` for the pattern).
- Point any log shipper that reads stdout/`docker logs` (Promtail, a
  CloudWatch agent, `journald`, ...) at the container and it's already
  parseable — no regex log scraping needed. `docker-compose.observability.yml`
  ships a ready-to-run Prometheus + Grafana stack for metrics (not logs);
  see "What this doesn't include" below for the log-aggregation half.

## Health check — `GET /health`

Previously a bare `{"status": "ok"}` regardless of whether anything
downstream actually worked. Now:

```bash
curl http://localhost:8000/health
```

```json
{
  "status": "ok",
  "checks": {
    "database": "ok",
    "gemini_configured": true
  }
}
```

- **`database`** — runs `SELECT 1` against the real configured
  `DATABASE_URL` (Postgres or SQLite) on every call. If that fails,
  `checks.database` becomes `"error: <the exception>"`, top-level
  `status` becomes `"degraded"`, and the endpoint returns **HTTP 503**
  instead of 200 — so a load balancer / orchestrator health check
  correctly stops routing traffic to a backend that can't reach its DB,
  instead of seeing a green 200 from a process that's up but useless.
- **`gemini_configured`** — `true`/`false` for whether `GEMINI_API_KEY`
  is set, so you can tell "Phase 10 AI analysis is intentionally
  unconfigured" apart from "someone forgot to set it" without digging
  into env vars on the box.
- No separate `/ready` endpoint — this one endpoint serves both liveness
  (is the process up) and readiness (can it actually serve real
  requests) checks; point both your liveness and readiness probes at it.

## Metrics — `GET /metrics`

Prometheus text exposition format, via `backend/app/metrics.py`
(`prometheus_client`, the reference Python client). Zero config —
scrapeable the moment the backend is up, `GEMINI_API_KEY`/`SENTRY_DSN`
irrelevant to it.

| Metric | Type | Labels | What it counts |
|---|---|---|---|
| `smart_bandage_http_requests_total` | counter | `method`, `path`, `status` | Every HTTP request handled |
| `smart_bandage_http_errors_total` | counter | `method`, `path` | Requests that raised an unhandled exception or returned a 5xx |
| `smart_bandage_http_request_duration_seconds` | histogram | `method`, `path` | Request latency |

`path` is the matched **route template** (e.g. `/devices/{device_id}`,
not `/devices/abc123`) so per-device/per-id traffic doesn't blow up the
metric's cardinality with one time series per id.

```bash
curl http://localhost:8000/metrics
```

```
smart_bandage_http_requests_total{method="GET",path="/health",status="200"} 42.0
smart_bandage_http_errors_total{method="POST",path="/devices/{device_id}/ai/chat"} 1.0
smart_bandage_http_request_duration_seconds_bucket{method="GET",path="/health",le="0.005"} 42.0
...
```

### Prometheus + Grafana overlay

`docker-compose.observability.yml` follows the same overlay pattern as
`docker-compose.tls.yml` — it *adds* services on top of a base compose
file rather than replacing it, so it works with either
`docker-compose.yml` (Postgres) or `docker-compose.sqlite.yml`:

```bash
docker compose -f docker-compose.yml -f docker-compose.observability.yml up --build
```

- **Prometheus** — `http://localhost:9090`, pre-configured
  (`docker/prometheus.yml`) to scrape the backend's `/metrics` every 15s.
- **Grafana** — `http://localhost:3000`, login `admin`/`admin` (Grafana's
  own out-of-the-box default, forces a password change on first login —
  nothing this repo sets). Its Prometheus datasource is
  auto-provisioned (`docker/grafana-datasource.yml`) so there's no
  click-through setup before you can build a dashboard from the metrics
  above.

Neither container is part of the base `docker compose up` — omit the
`-f docker-compose.observability.yml` and neither image is even pulled.

## Error tracking (optional) — `SENTRY_DSN`

`backend/app/observability.py:init_sentry()` runs once at process
startup, gated by `SENTRY_DSN` the exact same opt-in way
`backend/app/config.py` gates `GEMINI_API_KEY`:

- **Unset** (the default): `sentry_sdk.init()` is never called — the app
  runs identically to before this ticket, no Sentry account needed, no
  network calls to sentry.io.
- **Set**: every unhandled exception and 5xx response is reported, tagged
  with `environment` (`ENVIRONMENT`, same value `Settings.validate()`
  already uses to gate production safety checks).

```bash
# .env or your platform's secret store
SENTRY_DSN=https://<key>@<org>.ingest.sentry.io/<project>
SENTRY_TRACES_SAMPLE_RATE=0.0   # 0.0-1.0; performance tracing is off by default
```

Any Sentry-compatible DSN works (self-hosted Sentry, GlitchTip, ...) since
they speak the same `sentry_sdk` client protocol — this isn't locked to
sentry.io specifically.

## What this doesn't include

- **Log aggregation** — logs go to stdout as structured JSON (see above)
  but nothing here ships a Loki/CloudWatch/ELK stack to collect them.
  `docker logs -f backend | jq .` works today for local debugging; wiring
  a real aggregator is a follow-up (Promtail sidecar reading the
  container's stdout is the natural next overlay to add here, mirroring
  `docker-compose.observability.yml`'s shape).
- **Alerting rules** — Prometheus is scraping metrics but no Alertmanager
  config or Grafana alert rules ship yet; add them against the metrics
  table above once you know what thresholds matter for your deployment.
- **Distributed tracing** — `SENTRY_TRACES_SAMPLE_RATE` enables Sentry's
  own performance tracing if you want a taste of this, but there's no
  OpenTelemetry/Jaeger wiring.

## Files

| File | What |
|---|---|
| `backend/app/logging_config.py` | JSON log formatter + `configure_logging()` |
| `backend/app/metrics.py` | `MetricsMiddleware`, the three metrics above, `GET /metrics` |
| `backend/app/observability.py` | `init_sentry()` |
| `backend/app/main.py` | Wires all three in, deepens `GET /health` |
| `backend/app/config.py` | `LOG_LEVEL`, `SENTRY_DSN`, `SENTRY_TRACES_SAMPLE_RATE` settings |
| `docker-compose.observability.yml` | Prometheus + Grafana overlay |
| `docker/prometheus.yml` | Prometheus scrape config |
| `docker/grafana-datasource.yml` | Grafana's auto-provisioned Prometheus datasource |
| `backend/tests/test_observability.py` | Health/metrics/logging/Sentry-opt-in tests |
