#!/bin/sh
# Production entrypoint: apply migrations, then start the API.
# Schema is owned by Alembic (never create_all at startup).
set -e

echo "[entrypoint] applying database migrations..."
alembic upgrade head

echo "[entrypoint] starting uvicorn (workers=${WEB_CONCURRENCY:-2})..."
exec uvicorn app.main:app \
  --host 0.0.0.0 \
  --port "${PORT:-8000}" \
  --workers "${WEB_CONCURRENCY:-2}" \
  --timeout-graceful-shutdown 20 \
  --no-access-log
