#!/bin/sh
set -eu

alembic upgrade head
exec uvicorn flowmate.api.app:create_app --factory \
    --host "${APP_HOST:-0.0.0.0}" \
    --port "${APP_PORT:-8000}"
