#!/usr/bin/env bash
# Starts the backend (uvicorn --reload) and dashboard (vite dev) together
# for local, non-Docker development. Ctrl+C stops both.
#
#   ./scripts/dev_up.sh
#
# Assumes `python -m venv .venv && pip install -r requirements-dev.txt`
# (backend) and `cd frontend && npm install` (dashboard) have already been
# run once -- see the repo README's "Getting started".
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

VENV_ACTIVATE=".venv/Scripts/activate"        # Windows (git-bash/MSYS)
[ -f "$VENV_ACTIVATE" ] || VENV_ACTIVATE=".venv/bin/activate"  # macOS/Linux
if [ ! -f "$VENV_ACTIVATE" ]; then
  echo "No .venv found -- run: python -m venv .venv && . $VENV_ACTIVATE && pip install -r requirements-dev.txt" >&2
  exit 1
fi
# shellcheck disable=SC1090
source "$VENV_ACTIVATE"

if [ ! -f frontend/.env.local ]; then
  echo "frontend/.env.local missing -- copying frontend/.env.example (defaults to backend on :8000)"
  cp frontend/.env.example frontend/.env.local
fi

pids=()
cleanup() {
  echo
  echo "Stopping..."
  for pid in "${pids[@]}"; do
    kill "$pid" 2>/dev/null || true
  done
}
trap cleanup EXIT INT TERM

echo "Backend  -> http://localhost:8000 (docs at /docs)"
uvicorn backend.app.main:app --reload &
pids+=("$!")

echo "Dashboard -> starting (vite picks a free port -- watch the log line below)"
(cd frontend && npm run dev) &
pids+=("$!")

wait
