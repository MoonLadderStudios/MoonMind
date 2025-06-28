#!/bin/bash
set -e

echo "Running Alembic migrations..."
cd /app/api_service/migrations
alembic upgrade head
cd /app

echo "Starting Uvicorn server..."
if [ "$FASTAPI_RELOAD" = "true" ]; then
    exec uvicorn api_service.main:app --host 0.0.0.0 --port 8000 --reload
else
    exec uvicorn api_service.main:app --host 0.0.0.0 --port 8000
fi