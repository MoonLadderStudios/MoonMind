#!/bin/bash

echo "Starting Uvicorn server..."
if [ "$FASTAPI_RELOAD" = "true" ]; then
    exec uvicorn api_service.main:app --host 0.0.0.0 --port 8000 --no-proxy-headers --reload
else
    exec uvicorn api_service.main:app --host 0.0.0.0 --port 8000 --no-proxy-headers
fi
