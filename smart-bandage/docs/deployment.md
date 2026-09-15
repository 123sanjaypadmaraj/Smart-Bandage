# Deployment

Two paths, staged by how much external setup they need. Both are described
here with their real, current, verified status as of this writeup
(2026-09-07) — nothing below is aspirational.

## Path A — Self-hosted Docker (baseline, no external accounts needed)

**Status: verified end to end, live, on this machine.**

```bash
mkdir certs && openssl req -x509 -nodes -newkey rsa:2048 -days 365 \
  -keyout certs/privkey.pem -out certs/fullchain.pem -subj "/CN=localhost"
docker compose -f docker-compose.yml -f docker-compose.tls.yml up --build
```

This brings up three containers — Postgres (`db`), the FastAPI backend
(`backend`), and the dashboard behind nginx (`frontend`) — wired together
on Compose's default network, with TLS terminated by nginx (dashboard) and
uvicorn's built-in `--ssl-certfile`/`--ssl-keyfile` (backend, no extra
reverse-proxy container needed for the API).

Verified this session, live, against a fresh build:

| Check | Result |
| --- | --- |
| `docker compose ... up --build` | all three services build and reach healthy/running |
| `GET https://localhost:8443/health` | `200 {"status":"ok"}` |
| `GET https://localhost:8443/docs` | `200` (Swagger UI) |
| `GET https://localhost:443/` | `200`, dashboard `index.html` served |
| `GET http://localhost:80/` | `301` → `https://localhost/`, followed through to `200` |
| `POST /auth/login` (seeded `admin`/`admin`) | `200`, real JWT access + refresh tokens |
| `POST /devices/register` (authenticated) | `201`, row persisted in Postgres |
| `GET /devices` | `200`, returns the just-registered device back out of Postgres |

That last three rows matter beyond a bare health check: they prove the
whole chain — nginx/uvicorn TLS termination → FastAPI auth → Postgres
read/write — actually works, not just that the containers start.

### Bug found and fixed during this verification

`docker-compose.tls.yml` (the TLS overlay) mapped `443:443` for the
frontend but never mapped host port `80` anywhere — so the plain-HTTP→HTTPS
redirect nginx does internally on container port 80 was unreachable from
the host on the documented `:80`. (The base `docker-compose.yml`'s
`5175:80` mapping does reach that same container port, so `:80` behavior
was reachable via `:5175` — but not on the actual port 80 the README and
this deploy's acceptance criteria describe.) Fixed by adding `"80:80"` to
the frontend service's `ports` in `docker-compose.tls.yml`. Verified after
the fix: `curl http://localhost/` now returns `301` to `https://localhost/`
directly, matching the documented behavior.

### Known limitation

This is `-subj "/CN=localhost"` self-signed, fine for local verification
(`curl -k` / browser click-through) but not for a real public deploy — see
Path B and `docs/deployment/vercel.md` §"What's out of scope" for what a
real domain + CA-issued cert (`certbot`) + a real backend host adds on top.
`docker-compose.tls.yml`'s own header comments and the README's "Deploy
behind TLS" section already walk through swapping the self-signed pair for
a real `certbot` one and editing `docker/nginx.tls.conf`'s `server_name`
— nothing about that process needed to change here.

### A note on running this alongside other sessions

Docker Compose derives its project name from the directory basename
(`smart-bandage`) by default, and the Docker daemon is shared across every
git worktree on a given machine — it is **not** scoped per worktree. If two
concurrent sessions each run `docker compose up` from their own
`smart-bandage/` worktree without an explicit `-p <unique-name>`, they will
silently fight over the same container/network/volume names and one will
recreate (steal) the other's containers out from under it. This happened
live during this verification (a concurrent session's plain `docker compose
up` recreated this session's TLS backend container, dropping the `:8443`
mapping). The fix used here was `-p <unique-name>` plus alternate host
ports for the re-verification pass, not touching the other session's
containers at all. Anyone running this stack alongside other concurrent
smart-bandage sessions should pass `-p <something-unique>` (or set
`COMPOSE_PROJECT_NAME`) to avoid the same collision.

## Path B — Real cloud deploy (stretch goal)

**Status: frontend config finished and confirmed correct; not deployed —
blocked on credentials that were not available in this environment.**

The split (from `docs/deployment/vercel.md`, already on this branch before
this session started):

- **Frontend → Vercel.** `frontend/vercel.json` pins the Vite framework
  preset, build command, and output directory. Confirmed this session that
  the build reads `VITE_API_BASE_URL` / `VITE_WS_BASE_URL` at build time
  (`frontend/src/api.ts`), matching what `docs/deployment/vercel.md` §2
  documents to set in the Vercel project's environment variables — so the
  Vercel side of this is complete and internally consistent, just never
  actually deployed (no Vercel account/token in this environment).
- **Backend → a host that runs a long-lived process** (Railway, Render,
  Fly.io, ...) — Vercel's serverless functions can't hold the backend's
  per-device WebSocket (`/ws/devices/{id}`) or a persistent Postgres
  connection. Uses the existing `docker/backend.Dockerfile` unchanged.
- **Managed Postgres** on whichever host is chosen for the backend.

None of this was deployed for real. This session asked the coordinator
("main") whether the user has or wants to create the necessary accounts
(Vercel; a backend host; managed Postgres) before doing anything
outward-facing, per the project's escalation policy — no credentials were
fabricated or assumed, and a real deploy did not proceed without them.

### Exact remaining steps, if/when credentials are provided

1. **Vercel**: `npx vercel` (or the Vercel dashboard) importing this repo
   with Root Directory `smart-bandage/frontend` — framework
   auto-detects/pins to Vite via `frontend/vercel.json`. Set
   `VITE_API_BASE_URL=https://<backend-host>` and
   `VITE_WS_BASE_URL=wss://<backend-host>` in Vercel's Production **and**
   Preview environment variables (build-time, so a redeploy is needed after
   changing them).
2. **Backend host** (Railway/Render/Fly/other): deploy
   `docker/backend.Dockerfile` from the repo root as build context (it
   `COPY`s `common/`, `processing/`, `simulator/` alongside `backend/`, so
   the build context must stay the repo root, not `backend/`). Provision a
   managed Postgres instance on the same host/account and set:
   - `DATABASE_URL=postgresql+psycopg2://...` (the managed Postgres
     connection string)
   - `JWT_SECRET=$(python scripts/generate_secret.py)` — generate on your
     own machine and paste the value directly into that platform's secret
     store (Railway/Render/Fly all have one); `generate_secret.py` never
     writes to disk on purpose, so this value should never pass through a
     committed file, `.env`, or a script's stdout redirected to a file.
   - `ENVIRONMENT=production` — `backend/app/config.py:Settings.validate()`
     then refuses to start with a dev-default `JWT_SECRET` or a SQLite
     `DATABASE_URL`, so a misconfigured secret fails fast at boot instead
     of shipping.
   - `CORS_ORIGINS=https://<vercel-project>.vercel.app,https://<custom-domain>`
     (see `docs/deployment/vercel.md` §3 for why preview-deploy subdomains
     need their own entry or should just be pointed at a local/dev backend
     instead).
   - `GEMINI_API_KEY` (optional — Phase 10 AI endpoints 503 cleanly without
     it).
3. Get the backend host's public HTTPS URL (every one of Railway/Render/Fly
   terminates TLS for you — no `certs/`/nginx step needed there, that's
   only for the fully self-hosted Path A).
4. Point Vercel's `VITE_API_BASE_URL`/`VITE_WS_BASE_URL` (step 1) at that
   URL and redeploy the frontend.
5. **Custom domain** (optional): attach it in the Vercel project settings
   (frontend) and/or the backend host's settings; update `CORS_ORIGINS` on
   the backend to match.

Each step above needs an account this session doesn't have — nothing was
skipped or approximated; this is the literal sequence to run once those
accounts exist.
