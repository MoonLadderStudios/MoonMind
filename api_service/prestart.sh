#!/bin/sh

# Run Alembic migrations
echo "Running Alembic migrations..."
alembic upgrade head

# Start Uvicorn server
echo "Starting Uvicorn server..."
if [ "$FASTAPI_RELOAD" = "True" ]; then
  uvicorn api_service.main:app --host 0.0.0.0 --port 8000 --reload --reload-dir /app/api_service --reload-dir /app/moonmind
else
  uvicorn api_service.main:app --host 0.0.0.0 --port 8000
fi
