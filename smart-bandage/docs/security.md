# Security posture

Written up from a production security pass on `backend/`, `frontend/`,
`mobile/`, and the Docker/TLS deploy path (branch `security-hardening`,
commit `fdf71c0` + the ingest-auth follow-up in this same branch). Two
things live here: what's actually fixed, and what's a known, deliberately
un-fixed gap that anyone deploying this needs to see before they do —
this is not a "we checked, it's fine" document. If you're assembling
`docs/production_readiness.md`, the "Known gaps" section below is the
part that has to make it into that doc; don't summarize it away.

## Auth model as it exists today

- User login is `POST /auth/login` (bcrypt-hashed password, one seeded
  `admin`/`admin` account in development — see `backend/app/security.py`,
  skipped entirely when `ENVIRONMENT=production`) issuing a short-lived JWT
  access token (`JWT_EXPIRES_MINUTES`, 180min default) plus a longer-lived
  refresh token (`JWT_REFRESH_EXPIRES_DAYS`, 30 days default) via
  `POST /auth/refresh`. Both are stateless HS256 JWTs signed with
  `JWT_SECRET` — no server-side revocation list, so a token is valid until
  it expires, full stop.
- `get_current_username` (`backend/app/security.py`) is the one place that
  validates a Bearer token and is what individual routes pull in via
  `Depends(...)` to require login. **A route not depending on it has no
  auth at all** — see below, this is exactly the mechanism that left most
  of the read endpoints open.
- `POST /auth/login` and `POST /auth/refresh` are now rate-limited
  (`backend/app/rate_limit.py`, in-process fixed window, 10/60s and
  30/60s respectively, keyed by client IP) — blunts scripted
  credential-stuffing against the login form. Not a distributed limiter;
  see the module docstring for the multi-worker/reverse-proxy caveats.

## Known gap: most read endpoints (and the live WebSocket) have no auth

**Not fixed in this pass — deliberately left as-is per product decision,
documented here instead so it isn't silently shipped as if it were fine.**

The following endpoints have **zero authentication** — no `Depends(get_current_username)`,
nothing:

- `GET /devices`, `GET /devices/{device_id}`, `GET /device-status`
- `GET /measurements`, `GET /measurements/{measurement_id}`
- `GET /biomarkers`, `GET /biomarkers/{name}`
- `GET /alerts`
- `WS /ws/devices/{device_id}` — the live push feed of readings + alerts

Compare with what *does* require a Bearer token today: `POST /devices/register`,
`POST /measurements` (see next section), `POST/GET /simulation/*`, and
`GET/POST /devices/{id}/ai/*`.

**Why this matters concretely:** anyone who can reach the API — no login,
no token, just the base URL — can read every device's full measurement
history, biomarker trends, active alerts, and the live WebSocket stream of
readings/alerts as they happen. For a health-monitoring device this is
patient physiological data (biomarker concentrations, device/channel IDs,
alert history) exposed with no access control whatsoever.

**Why this looks like a bug, not a design choice:** `frontend/src/App.tsx`
gates the entire dashboard behind `LoginScreen` (`if (!token) return
<LoginScreen .../>`) and only calls `api.listDevices()` once a token
exists. But `frontend/src/api.ts`'s `listDevices()`, the measurements
calls, and the alerts call never actually attach an `Authorization` header
— and the backend never required one either, so nothing ever surfaced the
mismatch. The login screen currently provides zero actual access control
over this data; it's a UI gate the API doesn't enforce.

**Why it wasn't fixed here anyway:** flipping these to
`Depends(get_current_username)` is a real auth-behavior change, and per
this repo's escalation policy that's exactly the kind of change that needs
a product decision first rather than a silent fix — it risks breaking
whatever currently relies on the open access (the simulator, `scripts/seed_demo_data.py`,
any other unauthenticated caller) and the mobile app's own auth wiring
hasn't been audited against this same gap. **This decision was made
explicitly by the product owner: leave it unauthenticated for now, but
document it clearly rather than ship it silently.**

**Recommended remediation** (for whoever picks this up): decide the
intended access model first —
1. Simplest: require `Depends(get_current_username)` on all the routes
   listed above (matches what the frontend UI already implies), plus a
   token-bearing query param or subprotocol for the WebSocket route
   (browsers can't set custom headers on a WS handshake).
2. Or, if some of this is meant to be genuinely public (a status page?),
   say so explicitly per-route instead of leaving it ambiguous.

Either way: audit `frontend/src/api.ts` and `mobile/src/api.ts` at the same
time, since neither currently sends a token on these calls — turning on
backend auth without also fixing the client(s) will just break the app.

## Fixed in this pass: `POST /measurements` ingest auth

`POST /measurements` (the BLE-gateway/CSV-import ingest path,
`backend/app/routers/measurements.py`) previously had no auth either —
anyone could inject fabricated measurement data for any `device_id`, which
is worse than the read-only gaps above since it's a write path that could
trigger false alerts or pollute a device's real history. This one **was**
fixed: it now requires the same user JWT (`Depends(get_current_username)`)
as the rest of the authenticated API.

This is explicitly **not** a real fix for the underlying problem — there's
still no per-device credential model, so *any* logged-in user can still
post data for *any* device_id, not just their own. It closes off
*anonymous* injection, nothing more. A real fix needs a device-level
credential (e.g. a per-device API key issued at `POST /devices/register`
time) rather than reusing the human-user JWT for a machine/gateway
ingest path. Regression test: `backend/tests/test_api.py::test_ingest_measurement_requires_auth`.

## Known gap: `mobile/` dependency tree (npm audit)

`npm audit` in `mobile/` reports 10 moderate-severity CVEs (a `uuid`
buffer-bounds issue via the `xcode` package, and several `@expo/*`
config/build-tooling advisories), all only fixable by a **major** Expo
upgrade (40.x → 46.0.21, flagged `isSemVerMajor` by npm). Left undone,
deliberately:

- It's a breaking upgrade needing its own dedicated testing pass, not a
  drive-by dependency bump during a security review.
- All of the affected packages are **build-time tooling** (Expo CLI,
  config-plugins, prebuild), not runtime code that ships inside the built
  app — lower real-world exploitability than a runtime dependency CVE.
- `mobile/` has another agent (DISPATCH-8) actively working in a separate
  branch right now (icons, splash screen, `app.json`, a local EAS build) —
  a major Expo bump from this branch would very likely conflict with that
  in-flight work regardless of the CVE severity.

`frontend/`'s `npm audit` is clean (0 vulnerabilities).

## Known gap: `ecdsa` transitive dependency (Python)

`pip-audit` flags `ecdsa==0.19.2` (pulled in transitively via
`python-jose`) for `PYSEC-2026-1325` / `CVE-2024-23342` — a Minerva timing
attack on the P-256 curve. No fixed version exists; the upstream `ecdsa`
project has declared side-channel attacks out of scope with no planned
fix. This app's JWT usage is HS256-only (`backend/app/config.py`:
`jwt_algorithm = "HS256"`, hardcoded, not configurable), which is HMAC —
it never touches `ecdsa`'s EC signing code path, so there's no live
exploit path via this app's own usage today. Left as a documented future
improvement rather than swapped now: moving off `python-jose` (e.g. to
`PyJWT`, which doesn't pull in `ecdsa` for HS256) is a dependency swap
that risks new bugs under time pressure for a gap with no current
exploit path — not worth doing for a demo/launch that doesn't need it
today. Worth revisiting once there's slack to do it carefully.

## TLS overlay (`docker/nginx.tls.conf`)

`ssl_ciphers` was tightened from `HIGH:!aNULL:!MD5` (OpenSSL's `HIGH`
class still admits non-forward-secret RSA key exchange and non-AEAD CBC
suites) to an explicit forward-secret AEAD list: ECDHE key exchange only,
paired with AES-GCM or ChaCha20-Poly1305. `ssl_protocols` was already
correctly scoped to TLSv1.2/TLSv1.3.

## JWT secret / CORS / password hashing — reviewed, no changes needed

- `backend/app/config.py:Settings.validate()` refuses to start with
  `ENVIRONMENT=production` if `JWT_SECRET` is the dev default or under 16
  chars, or if `DATABASE_URL` is still SQLite — correct and already
  tested (`backend/tests/test_config_production_safety.py`).
- `CORS_ORIGINS` defaults to `http://localhost:5173` (a specific origin,
  not a wildcard) even with `allow_credentials=True` — safe as configured.
- Password hashing is `bcrypt` directly (not passlib — see the comment in
  `backend/app/security.py` on why), random salt per `bcrypt.gensalt()`,
  correct truncation to bcrypt's 72-byte input cap on both the hash and
  verify paths.
- `GEMINI_API_KEY` is never logged or echoed back to a client — every
  error path in `backend/app/gemini_client.py` surfaces `exc.reason`,
  `exc.code`, or the response body, never the request URL that embeds the
  key as a query param.

## Fixed in this pass: refresh token usable as an access token

`get_current_username` (`backend/app/security.py`) previously accepted
*any* valid JWT signed with `JWT_SECRET`, including a refresh token — only
`decode_refresh_token`'s reverse check (rejecting an access token used as
a refresh token) existed. That meant a refresh token, 30 days by default,
worked on every protected endpoint exactly like a 180-minute access
token, silently defeating the short-lived-token model. Now rejects any
token carrying `"typ": "refresh"`. Regression test:
`backend/tests/test_api.py::test_protected_endpoint_rejects_a_refresh_token_used_as_bearer`.
