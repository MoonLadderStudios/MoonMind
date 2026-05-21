#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
COMPOSE_PROJECT_DIR="$REPO_ROOT"
COMPOSE_FILE="$COMPOSE_PROJECT_DIR/docker-compose.test.yaml"
TEMP_COMPOSE_PROJECT_DIR=""
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

if [[ "$REPO_ROOT" == *:* ]]; then
  TEMP_COMPOSE_PROJECT_DIR="/work/agent_jobs/moonmind-integration-repo-$(printf '%s' "$REPO_ROOT" | sha256sum | cut -d' ' -f1 | cut -c1-12)-$$"
  mkdir -p "$TEMP_COMPOSE_PROJECT_DIR"
  tar -C "$REPO_ROOT" \
    --exclude='./.git' \
    --exclude='./.pytest_cache' \
    --exclude='./.mypy_cache' \
    --exclude='./.ruff_cache' \
    --exclude='./.venv' \
    --exclude='./artifacts' \
    --exclude='./live_streams.spool' \
    --exclude='./node_modules' \
    --exclude='./var' \
    --exclude='*/__pycache__' \
    --exclude='*/node_modules' \
    -cf - . | tar -C "$TEMP_COMPOSE_PROJECT_DIR" -xf -
  COMPOSE_PROJECT_DIR="$TEMP_COMPOSE_PROJECT_DIR"
  COMPOSE_FILE="$COMPOSE_PROJECT_DIR/docker-compose.test.yaml"
fi

if ! docker network inspect "$NETWORK_NAME" >/dev/null 2>&1; then
  docker network create "$NETWORK_NAME" >/dev/null
  echo "Created Docker network: $NETWORK_NAME"
fi

export MOONMIND_ALLOW_LIVE_TEMPORAL_IN_TESTS=1

# Build pytest service
"${COMPOSE_CMD[@]}" -f "$COMPOSE_FILE" --project-directory "$COMPOSE_PROJECT_DIR" build pytest

# Run integration tests (always cleaned up via trap)
# --timeout 120: kill any single test that runs longer than 120 seconds
# --timeout-method=thread: use thread-based timeout (works with async tests)
# --durations=10: print the 10 slowest tests at the end
run_tests() {
  "${COMPOSE_CMD[@]}" -f "$COMPOSE_FILE" --project-directory "$COMPOSE_PROJECT_DIR" run --rm \
    -e MOONMIND_ALLOW_LIVE_TEMPORAL_IN_TESTS=1 \
    pytest \
    bash -lc "pytest tests/integration -m 'integration_ci' --tb=short --timeout 120 --timeout-method=thread --durations=10"
}

# Ensure compose stack is always torn down, even on failure or interrupt
cleanup() {
  "${COMPOSE_CMD[@]}" -f "$COMPOSE_FILE" --project-directory "$COMPOSE_PROJECT_DIR" down --remove-orphans >/dev/null 2>&1 || true
  if [[ -n "$TEMP_COMPOSE_PROJECT_DIR" ]]; then
    rm -rf "$TEMP_COMPOSE_PROJECT_DIR" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

run_tests
