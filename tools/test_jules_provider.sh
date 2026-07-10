#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
COMPOSE_FILE="$REPO_ROOT/docker-compose.test.yaml"
NETWORK_NAME="${MOONMIND_DOCKER_NETWORK:-local-network}"
TEST_COMPOSE_PROJECT_NAME="${MOONMIND_TEST_COMPOSE_PROJECT_NAME:-moonmind-test}"
COMPOSE_CMD=()

project_name_regex='^moonmind-test(-[a-z0-9][a-z0-9_-]*)?$'
if [[ ! "$TEST_COMPOSE_PROJECT_NAME" =~ $project_name_regex ]]; then
  echo "Error: MOONMIND_TEST_COMPOSE_PROJECT_NAME must be 'moonmind-test' or start with 'moonmind-test-'." >&2
  exit 2
fi

cleanup() {
  if (( ${#COMPOSE_CMD[@]} )); then
    "${COMPOSE_CMD[@]}" --project-name "$TEST_COMPOSE_PROJECT_NAME" -f "$COMPOSE_FILE" --project-directory "$REPO_ROOT" down --remove-orphans >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

if [[ -z "${JULES_API_KEY:-}" ]]; then
  echo "Error: JULES_API_KEY must be set to run live Jules provider verification." >&2
  exit 1
fi

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

"${COMPOSE_CMD[@]}" --project-name "$TEST_COMPOSE_PROJECT_NAME" -f "$COMPOSE_FILE" --project-directory "$REPO_ROOT" build pytest
"${COMPOSE_CMD[@]}" --project-name "$TEST_COMPOSE_PROJECT_NAME" -f "$COMPOSE_FILE" --project-directory "$REPO_ROOT" run --rm \
  -e JULES_API_KEY \
  -e JULES_API_URL \
  pytest bash -lc \
  "pytest tests/provider/jules -m 'provider_verification and jules' -q --tb=short -s"
