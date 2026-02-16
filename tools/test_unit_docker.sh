#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

COMPOSE_FILE="$REPO_ROOT/docker-compose.test.yaml"
TEST_SERVICE="pytest"
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

if [[ ! -f "$REPO_ROOT/.env" ]]; then
    if [[ -f "$REPO_ROOT/.env-template" ]]; then
        cp "$REPO_ROOT/.env-template" "$REPO_ROOT/.env"
        echo "Created $REPO_ROOT/.env from .env-template for docker compose tests."
    else
        echo "Error: missing $REPO_ROOT/.env and $REPO_ROOT/.env-template." >&2
        exit 1
    fi
fi

if command -v docker >/dev/null 2>&1; then
    if ! docker network inspect "$NETWORK_NAME" >/dev/null 2>&1; then
        docker network create "$NETWORK_NAME" >/dev/null
    fi
fi

"${COMPOSE_CMD[@]}" -f "$COMPOSE_FILE" --project-directory "$REPO_ROOT" build "$TEST_SERVICE"
"${COMPOSE_CMD[@]}" -f "$COMPOSE_FILE" --project-directory "$REPO_ROOT" run --rm "$TEST_SERVICE"
