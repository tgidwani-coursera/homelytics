#!/usr/bin/env bash
# Start the Homelytics dashboard (FastAPI + static SPA).
# Reads DB credentials from .env. Run from the repo root.
set -euo pipefail
cd "$(dirname "$0")"

HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8000}"

echo "Homelytics dashboard → http://${HOST}:${PORT}"
exec python3 -m uvicorn dashboard.app:app --host "$HOST" --port "$PORT" "$@"
