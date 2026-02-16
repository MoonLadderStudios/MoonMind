#!/usr/bin/env bash
set -euo pipefail

COMPOSE_FILE="docker-compose.test.yaml"
TEST_SERVICE="pytest"
TEST_TYPE="unit"
NETWORK_NAME="local-network"

if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
    COMPOSE_CMD=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
    COMPOSE_CMD=(docker-compose)
else
    echo "Error: docker compose CLI is not available." >&2
    echo "Install Docker with Compose V2 plugin (or docker-compose)." >&2
    exit 127
fi

if command -v docker >/dev/null 2>&1; then
    if ! docker network inspect "$NETWORK_NAME" >/dev/null 2>&1; then
        docker network create "$NETWORK_NAME" >/dev/null
    fi
fi

TEST_TYPE="$TEST_TYPE" "${COMPOSE_CMD[@]}" -f "$COMPOSE_FILE" build "$TEST_SERVICE"
TEST_TYPE="$TEST_TYPE" "${COMPOSE_CMD[@]}" -f "$COMPOSE_FILE" run --rm "$TEST_SERVICE"
