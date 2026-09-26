# Hosting the dashboard on Vercel

The **frontend** (`frontend/`, Vite + React) is a static build and deploys
to Vercel cleanly as-is. The **backend** (`backend/`, FastAPI) does *not* —
it holds a persistent WebSocket per connected device
(`/ws/devices/{id}`, see `backend/app/routers/ws.py`) and needs a real
Postgres connection, neither of which Vercel's serverless functions support
(each invocation is stateless and short-lived, so a long-held socket just
gets dropped). So the split is:

- **Frontend → Vercel** (this doc).
- **Backend → a host that runs a long-lived process** (Railway, Render,
  Fly.io, a VM, ...) using the existing `docker/backend.Dockerfile` +
  `docker-compose.yml` Postgres service, unchanged. Nothing in this change
  touches the backend.

## 1. Import the repo into Vercel

The repo is a monorepo (`smart-bandage/frontend` is two levels down from
the git root). When importing the project in the Vercel dashboard:

- **Root Directory**: `smart-bandage/frontend`
- Framework preset auto-detects as **Vite** (also pinned explicitly in
  `frontend/vercel.json`: `buildCommand: npm run build`,
  `outputDirectory: dist`).

Or via CLI from `smart-bandage/frontend`:

```
npx vercel        # first deploy, links the project
npx vercel --prod # production deploy
```

## 2. Environment variables

Set these in the Vercel project (Settings → Environment Variables), for
Production *and* Preview:

| Variable | Value |
| --- | --- |
| `VITE_API_BASE_URL` | `https://<your-backend-host>` (e.g. your Railway/Render URL) |
| `VITE_WS_BASE_URL` | `wss://<your-backend-host>` |

Use `wss://`, not `ws://` — the dashboard is served over `https://` on
Vercel, and a browser blocks a plain `ws://` connection from an `https://`
page (mixed content). The backend's WebSocket route works unchanged over
`wss://` once it's behind any TLS-terminating host (Railway/Render/Fly all
do this for you; see also `docker-compose.tls.yml` for a self-hosted TLS
option).

These are read at *build* time (`frontend/src/api.ts`:
`import.meta.env.VITE_API_BASE_URL`), so changing them requires a redeploy,
not just a restart.

## 3. Point the backend's CORS at Vercel

The backend rejects cross-origin requests it doesn't recognize
(`CORS_ORIGINS`, see `backend/app/config.py` and
`docs(env)` commit on `CORS_ORIGINS` for the mobile-web equivalent of this).
Add your Vercel domain(s) to it on the backend host:

```
CORS_ORIGINS=https://<your-project>.vercel.app,https://<your-custom-domain>
```

Every Vercel preview deploy gets its own generated subdomain
(`<project>-<hash>-<team>.vercel.app`), which won't match a fixed
`CORS_ORIGINS` list — previews hitting a real backend will 403 on CORS
unless you either add that specific preview URL too, or only rely on
previews against a local/dev backend and treat the production Vercel
domain + custom domain as the ones that need to be in the backend's
`CORS_ORIGINS`.

## 4. What's out of scope here

Deploying the backend itself (Railway/Render/Fly account, managed Postgres,
setting `DATABASE_URL`/`JWT_SECRET`/`ENVIRONMENT=production` there) isn't
part of this change — `backend/app/config.py:Settings.validate()` already
fails fast if `ENVIRONMENT=production` is combined with the dev-only
`JWT_SECRET` or SQLite, so follow that host's own docs for secrets/Postgres
and let that validation confirm you didn't miss one env var.

The **mobile** app (`mobile/`, Expo/React Native) isn't part of this
either — it's a native app, not a Vercel target.
