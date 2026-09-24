#!/bin/sh
set -e

echo "[Startup] Running database migrations..."
alembic upgrade head

echo "[Startup] Starting Uvicorn web server on port ${PORT:-8080}..."
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8080}"
