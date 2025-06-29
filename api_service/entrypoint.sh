#!/bin/bash

# Set environment variables to suppress git warnings
export GIT_PYTHON_REFRESH=quiet

echo "Starting Uvicorn server..."
if [ "$FASTAPI_RELOAD" = "true" ]; then
    exec uvicorn api_service.main:app --host 0.0.0.0 --port 8000 --reload
else
    exec uvicorn api_service.main:app --host 0.0.0.0 --port 8000
fi