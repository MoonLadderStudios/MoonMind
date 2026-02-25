#!/usr/bin/env bash
set -euo pipefail

NETWORK_NAME="${MOONMIND_DOCKER_NETWORK:-local-network}"
AUTH_SERVICE="${CODEX_AUTH_SERVICE:-codex-worker}"
AUTH_COMMAND="${CODEX_AUTH_COMMAND:-codex login --device-auth && codex login status}"

if ! command -v docker >/dev/null 2>&1; then
  echo "Error: docker CLI is not available." >&2
  exit 127
fi

if ! docker network inspect "$NETWORK_NAME" >/dev/null 2>&1; then
  docker network create "$NETWORK_NAME" >/dev/null
fi

docker compose run --rm --user app \
  "$AUTH_SERVICE" \
  bash -lc "$AUTH_COMMAND"
