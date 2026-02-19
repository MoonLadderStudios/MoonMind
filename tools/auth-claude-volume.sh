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

docker compose run --rm --user app codex-worker \
  bash -lc '
set -euo pipefail

status_quiet() {
  claude auth status >/dev/null 2>&1 || claude login status >/dev/null 2>&1
}

status_verbose() {
  claude auth status || claude login status
}

if status_quiet; then
  status_verbose
  exit 0
fi

if claude auth login; then
  :
elif claude login --device-auth; then
  :
else
  claude login
fi

status_verbose
'
