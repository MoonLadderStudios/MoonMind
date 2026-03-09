#!/usr/bin/env bash
set -euo pipefail

NETWORK_NAME="${MOONMIND_DOCKER_NETWORK:-local-network}"

GEMINI_HOME="${GEMINI_HOME:-/var/lib/gemini-auth}"
GEMINI_CLI_HOME="${GEMINI_CLI_HOME:-${GEMINI_HOME}}"
GEMINI_TERM="${TERM:-xterm-256color}"

if ! command -v docker >/dev/null 2>&1; then
  echo "Error: docker CLI is not available." >&2
  exit 127
fi

if ! docker network inspect "$NETWORK_NAME" >/dev/null 2>&1; then
  docker network create "$NETWORK_NAME" >/dev/null
fi

if ! [ -t 0 ] || ! [ -t 1 ]; then
  echo "Error: Gemini login requires an interactive terminal."
  echo "Run this command from an interactive shell."
  exit 1
fi

COMPOSE_NETWORK_ARGS=()
if docker compose run --help 2>/dev/null | grep -Eq '(^|[[:space:]])--network([[:space:]]|=|$)'; then
  COMPOSE_NETWORK_ARGS+=(--network "$NETWORK_NAME")
fi

docker compose run --rm -it \
  -e MOONMIND_GEMINI_CLI_AUTH_MODE=oauth \
  -e GOOGLE_API_KEY= \
  -e GEMINI_API_KEY= \
  -e TERM="${GEMINI_TERM}" \
  -e GEMINI_HOME="${GEMINI_HOME}" \
  -e GEMINI_CLI_HOME="${GEMINI_CLI_HOME}" \
  ${COMPOSE_NETWORK_ARGS[@]+"${COMPOSE_NETWORK_ARGS[@]}"} \
  gemini-worker \
  bash -lc 'unset GOOGLE_API_KEY GEMINI_API_KEY; stty sane 2>/dev/null || true; mkdir -p "${GEMINI_CLI_HOME:-/var/lib/gemini-auth}/.gemini"; exec gemini'
