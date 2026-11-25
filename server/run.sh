#!/bin/sh
# Start the quick-farm backend (binds 0.0.0.0:8790 by default).
cd "$(dirname "$0")/.." || exit 1
exec python3 -m uvicorn server.app:app --host "${HOST:-0.0.0.0}" --port "${PORT:-8790}"
