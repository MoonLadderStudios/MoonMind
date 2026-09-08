#!/bin/bash
# script to clean Temporal environment state between test runs

set -e

echo "Stopping Temporal services..."
docker compose stop temporal temporal-db temporal-namespace-init temporal-worker-workflow temporal-worker-artifacts temporal-worker-llm temporal-worker-sandbox temporal-worker-integrations

echo "Removing Temporal database and volume..."
# Using `down --volumes` on a specific service is the cleanest way to
# remove the service container and its associated named volumes.
docker compose down --volumes temporal-db

echo "Restarting Temporal services..."
docker compose up -d temporal-db temporal temporal-namespace-init temporal-worker-workflow temporal-worker-artifacts temporal-worker-llm temporal-worker-sandbox temporal-worker-integrations

echo "Waiting for Temporal namespace to be initialized..."
docker compose wait temporal-namespace-init
echo "Temporal environment state has been reset."
