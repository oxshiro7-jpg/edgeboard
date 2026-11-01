#!/bin/sh
set -e
cd /app/backend
/venv/bin/uvicorn app.api.main:app --host 127.0.0.1 --port 8000 &
cd /app/frontend
exec node node_modules/next/dist/bin/next start -H 0.0.0.0 -p "${PORT:-3000}"
