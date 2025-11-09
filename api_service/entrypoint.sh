#!/bin/bash

# Set environment variables to suppress git warnings
export GIT_PYTHON_REFRESH=quiet

if ! python -m api_service.scripts.ensure_codex_config; then
    status=$?
    echo "Codex configuration enforcement failed" >&2
    exit $status
fi

echo "Starting Uvicorn server..."
if [ "$FASTAPI_RELOAD" = "true" ]; then
    exec uvicorn api_service.main:app --host 0.0.0.0 --port 5000 --reload
else
    exec uvicorn api_service.main:app --host 0.0.0.0 --port 5000
fi
