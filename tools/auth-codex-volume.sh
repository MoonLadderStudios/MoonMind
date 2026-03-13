#!/usr/bin/env bash
set -euo pipefail

NETWORK_NAME="${MOONMIND_DOCKER_NETWORK:-local-network}"
AUTH_SERVICE="${CODEX_AUTH_SERVICE:-temporal-worker-sandbox}"
AUTH_PROFILE="${CODEX_AUTH_PROFILE:-}"
AUTH_COMMAND="${CODEX_AUTH_COMMAND:-codex login --device-auth && codex login status}"
AUTH_COMMAND_TOKEN_RE='^[A-Za-z0-9._/:=?&%+#@!,-]+$'
CODEX_TERM="${TERM:-xterm-256color}"

run_auth_command() {
  local raw_command="$1"
  local -a command_parts=()
  local -a forbidden_patterns=(
    '$'
    '`'
  )

  for pattern in "${forbidden_patterns[@]}"; do
  if [[ "$raw_command" == *"$pattern"* ]]; then
    echo "Error: CODEX_AUTH_COMMAND contains unsupported characters." >&2
    return 1
  fi
  done

  set -f
  if ! eval "command_parts=( $raw_command )"; then
    set +f
    echo "Error: CODEX_AUTH_COMMAND could not be parsed as shell arguments." >&2
    return 1
  fi
  set +f
  if [[ "${#command_parts[@]}" -eq 0 ]]; then
    echo "Error: CODEX_AUTH_COMMAND contains an empty command." >&2
    return 1
  fi

  if [[ "${command_parts[0]}" != "codex" ]]; then
    echo "Error: CODEX_AUTH_COMMAND must invoke the codex CLI." >&2
    return 1
  fi

  for token in "${command_parts[@]}"; do
    if [[ ! "$token" =~ $AUTH_COMMAND_TOKEN_RE ]]; then
      echo "Error: CODEX_AUTH_COMMAND contains invalid argument values." >&2
      return 1
    fi
  done

  docker compose run --rm -it \
    ${COMPOSE_PROFILE_ARGS[@]+"${COMPOSE_PROFILE_ARGS[@]}"} \
    "${COMPOSE_NETWORK_ARGS[@]}" \
    --user app \
    --entrypoint /bin/bash \
    -e TERM="${CODEX_TERM}" \
    "$AUTH_SERVICE" \
    -lc 'stty sane 2>/dev/null || true; exec "$@"' -- \
    "${command_parts[@]}"
}

if ! command -v docker >/dev/null 2>&1; then
  echo "Error: docker CLI is not available." >&2
  exit 127
fi

if ! command -v sed >/dev/null 2>&1; then
  echo "Error: sed is not available." >&2
  exit 127
fi

if ! command -v grep >/dev/null 2>&1; then
  echo "Error: grep is not available." >&2
  exit 127
fi

if ! docker compose version >/dev/null 2>&1; then
  echo "Error: docker compose is not available." >&2
  echo "Install Docker Compose v2 and retry." >&2
  exit 127
fi

if ! docker network inspect "$NETWORK_NAME" >/dev/null 2>&1; then
  docker network create "$NETWORK_NAME" >/dev/null
fi

if ! [ -t 0 ] || ! [ -t 1 ]; then
  echo "Error: Codex login requires an interactive terminal."
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

AUTH_COMMAND_LINES=()
while IFS= read -r auth_command; do
  auth_command="${auth_command#"${auth_command%%[![:space:]]*}"}"
  auth_command="${auth_command%"${auth_command##*[![:space:]]}"}"
  [[ -z "$auth_command" ]] && continue
  AUTH_COMMAND_LINES+=("$auth_command")
done < <(
  printf '%s\n' "$AUTH_COMMAND" | sed -E 's/[[:space:]]*&&[[:space:]]*/\n/g'
)

if [[ "${#AUTH_COMMAND_LINES[@]}" -eq 0 ]]; then
  echo "Error: CODEX_AUTH_COMMAND was empty after parsing." >&2
  exit 1
fi

for AUTH_COMMAND_LINE in "${AUTH_COMMAND_LINES[@]}"; do
  run_auth_command "$AUTH_COMMAND_LINE"
done
