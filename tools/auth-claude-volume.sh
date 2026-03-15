#!/usr/bin/env bash
set -euo pipefail

NETWORK_NAME="${MOONMIND_DOCKER_NETWORK:-local-network}"
AUTH_SERVICE="${CLAUDE_AUTH_SERVICE:-temporal-worker-sandbox}"
AUTH_PROFILE="${CLAUDE_AUTH_PROFILE:-}"
export CLAUDE_VOLUME_NAME="${CLAUDE_VOLUME_NAME:-claude_auth_volume}"

CLAUDE_HOME="${CLAUDE_HOME:-/home/app/.claude}"
CLAUDE_TERM="${TERM:-xterm-256color}"

if ! command -v docker >/dev/null 2>&1; then
  echo "Error: docker CLI is not available." >&2
  exit 127
fi

if ! docker network inspect "$NETWORK_NAME" >/dev/null 2>&1; then
  docker network create "$NETWORK_NAME" >/dev/null
fi

if ! [ -t 0 ] || ! [ -t 1 ]; then
  echo "Error: Claude login requires an interactive terminal."
  echo "Run this command from an interactive shell."
  exit 1
fi

COMPOSE_NETWORK_ARGS=()
if docker compose run --help 2>/dev/null | grep -Eq '(^|[[:space:]])--network([[:space:]]|=|$)'; then
  COMPOSE_NETWORK_ARGS+=(--network "$NETWORK_NAME")
fi

COMPOSE_PROFILE_ARGS=()
if [[ -n "$AUTH_PROFILE" ]]; then
  COMPOSE_PROFILE_ARGS+=(--profile "$AUTH_PROFILE")
fi

docker compose run --rm -it \
  ${COMPOSE_PROFILE_ARGS[@]+"${COMPOSE_PROFILE_ARGS[@]}"} \
  --entrypoint /bin/bash \
  -e MOONMIND_CLAUDE_CLI_AUTH_MODE=oauth \
  -e ANTHROPIC_API_KEY= \
  -e CLAUDE_API_KEY= \
  -e TERM="${CLAUDE_TERM}" \
  -e CLAUDE_HOME="${CLAUDE_HOME}" \
  -e CLAUDE_VOLUME_NAME="${CLAUDE_VOLUME_NAME}" \
  "${COMPOSE_NETWORK_ARGS[@]}" \
  "$AUTH_SERVICE" \
  -lc 'unset ANTHROPIC_API_KEY CLAUDE_API_KEY; stty sane 2>/dev/null || true; mkdir -p "${CLAUDE_HOME:-/home/app/.claude}"; exec claude login'
