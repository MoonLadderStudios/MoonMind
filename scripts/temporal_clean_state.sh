#!/bin/bash
# script to clean Temporal environment state between test runs

set -e

echo "Stopping Temporal services..."
docker compose stop temporal temporal-db temporal-namespace-init temporal-worker-workflow temporal-worker-artifacts temporal-worker-llm temporal-worker-sandbox temporal-worker-integrations

echo "Removing Temporal database volume..."
# We remove the container and its associated volumes if possible, but for a named volume we use docker volume rm.
# Find the actual volume name
PROJECT_NAME=$(docker compose config | grep "name:" | head -n 1 | awk '{print $2}')
if [ -z "$PROJECT_NAME" ]; then
    # fallback to basename of current dir
    PROJECT_NAME=$(basename "$PWD")
fi
# Remove hyphen and lowercase for default docker compose volume prefix
PREFIX=$(echo "$PROJECT_NAME" | tr -d '-' | tr '[:upper:]' '[:lower:]')
VOLUME_NAME="${PREFIX}_temporal-db-data"

# if that fails, try exact compose name
if ! docker volume rm "$VOLUME_NAME" 2>/dev/null; then
    docker volume rm "${PROJECT_NAME}_temporal-db-data" 2>/dev/null || echo "Volume might already be removed or named differently."
fi

# To be completely safe and generic across docker compose versions, we can also use docker compose down with specific volumes if needed, 
# but compose down -v removes ALL volumes including api-db.
# A simpler generic way:
docker compose rm -f -v temporal-db

echo "Restarting Temporal services..."
docker compose up -d temporal-db temporal temporal-namespace-init temporal-worker-workflow temporal-worker-artifacts temporal-worker-llm temporal-worker-sandbox temporal-worker-integrations

echo "Waiting for Temporal to be ready..."
sleep 10
echo "Temporal environment state has been reset."
