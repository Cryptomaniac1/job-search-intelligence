#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
PYTHON="${PYTHON:-$ROOT/backend/.venv/bin/python}"

if [ ! -x "$PYTHON" ]; then
  echo "Missing backend environment: $PYTHON" >&2
  echo "Create it and install backend/requirements.txt before starting." >&2
  exit 1
fi

export PYTHONPATH="${PYTHONPATH:-$ROOT/backend}"
export JOBS_DB_PATH="${JOBS_DB_PATH:-$ROOT/data/jobs.db}"

exec "$PYTHON" -m uvicorn backend.main:app \
  --host "${HOST:-127.0.0.1}" \
  --port "${PORT:-8000}" \
  "$@"
