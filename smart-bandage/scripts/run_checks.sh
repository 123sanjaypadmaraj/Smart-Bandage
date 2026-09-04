#!/usr/bin/env bash
# Runs everything CI/a pre-push check should run: the full pytest suite and
# a frontend typecheck+build. Fails fast with a clear message on either.
#
#   ./scripts/run_checks.sh
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

VENV_ACTIVATE=".venv/Scripts/activate"
[ -f "$VENV_ACTIVATE" ] || VENV_ACTIVATE=".venv/bin/activate"
if [ -f "$VENV_ACTIVATE" ]; then
  # shellcheck disable=SC1090
  source "$VENV_ACTIVATE"
fi

echo "== backend: pytest =="
pytest

echo
echo "== frontend: tsc + vite build =="
(cd frontend && npm run build)

echo
echo "== mobile: tsc --noEmit =="
(cd mobile && npm run typecheck)

echo
echo "All checks passed."
