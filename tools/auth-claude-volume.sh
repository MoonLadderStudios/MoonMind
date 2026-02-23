#!/usr/bin/env bash
set -euo pipefail

NETWORK_NAME="${MOONMIND_DOCKER_NETWORK:-local-network}"
NO_DEPS="${MOONMIND_CLAUDE_AUTH_NO_DEPS:-1}"
ALLOW_INTERACTIVE="${CLAUDE_AUTH_ALLOW_INTERACTIVE:-0}"
CLAUDE_THEME="${CLAUDE_THEME:-dark}"

if ! command -v docker >/dev/null 2>&1; then
  echo "Error: docker CLI is not available." >&2
  exit 127
fi

if ! docker network inspect "$NETWORK_NAME" >/dev/null 2>&1; then
  docker network create "$NETWORK_NAME" >/dev/null
fi

RUN_OPTS=(run --rm --user app)
if [[ "$NO_DEPS" == "1" ]]; then
  RUN_OPTS+=(--no-deps)
fi

RUN_OPTS+=(
  -e "CLAUDE_AUTH_ALLOW_INTERACTIVE=$ALLOW_INTERACTIVE"
)

if [[ -n "$CLAUDE_THEME" ]]; then
  RUN_OPTS+=(-e "CLAUDE_THEME=$CLAUDE_THEME")
fi

docker compose "${RUN_OPTS[@]}" codex-worker \
  bash -lc '
set -euo pipefail

status_quiet() {
  claude auth status >/dev/null 2>&1 || claude login status >/dev/null 2>&1
}

status_verbose() {
  claude auth status || claude login status
}

set_theme_dark() {
  # Some CLI builds support explicit theme config; ignore failures to stay
  # compatible across versions.
  claude config set theme dark >/dev/null 2>&1 || true
  claude config set -g theme dark >/dev/null 2>&1 || true
}

if status_quiet; then
  status_verbose
  exit 0
fi

set_theme_dark

if [[ "${CLAUDE_AUTH_ALLOW_INTERACTIVE:-0}" == "1" ]]; then
  if claude auth login; then
    :
  elif claude login --device-auth; then
    :
  else
    claude login
  fi
else
  claude login --device-auth
fi

status_verbose
'
