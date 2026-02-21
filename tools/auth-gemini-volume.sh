#!/usr/bin/env bash
set -euo pipefail

NETWORK_NAME="${MOONMIND_DOCKER_NETWORK:-local-network}"

if ! command -v docker >/dev/null 2>&1; then
  echo "Error: docker CLI is not available." >&2
  exit 127
fi

if ! docker network inspect "$NETWORK_NAME" >/dev/null 2>&1; then
  docker network create "$NETWORK_NAME" >/dev/null
fi

docker compose run --rm -it --user app \
  -e MOONMIND_GEMINI_CLI_AUTH_MODE=oauth \
  -e GOOGLE_API_KEY= \
  -e GEMINI_API_KEY= \
  celery_gemini_worker \
  bash -lc 'unset GOOGLE_API_KEY GEMINI_API_KEY; gemini'
