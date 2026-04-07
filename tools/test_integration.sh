#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
COMPOSE_FILE="$REPO_ROOT/docker-compose.test.yaml"
NETWORK_NAME="${MOONMIND_DOCKER_NETWORK:-local-network}"

if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
  COMPOSE_CMD=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE_CMD=(docker-compose)
else
  echo "Error: docker compose CLI is not available." >&2
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

if ! docker network inspect "$NETWORK_NAME" >/dev/null 2>&1; then
  docker network create "$NETWORK_NAME" >/dev/null
  echo "Created Docker network: $NETWORK_NAME"
fi

# Build pytest service
"${COMPOSE_CMD[@]}" -f "$COMPOSE_FILE" --project-directory "$REPO_ROOT" build pytest

# Run integration tests (always cleaned up via trap)
# --timeout 60: kill any single test that runs longer than 60 seconds
# --timeout-method=thread: use thread-based timeout (works with async tests)
run_tests() {
  "${COMPOSE_CMD[@]}" -f "$COMPOSE_FILE" --project-directory "$REPO_ROOT" run --rm pytest \
    bash -lc "pytest tests/integration -m 'integration_ci' -q --tb=short --timeout 60 --timeout-method=thread"
}

# Ensure compose stack is always torn down, even on failure or interrupt
cleanup() {
  "${COMPOSE_CMD[@]}" -f "$COMPOSE_FILE" --project-directory "$REPO_ROOT" down --remove-orphans >/dev/null 2>&1 || true
}
trap cleanup EXIT

run_tests
