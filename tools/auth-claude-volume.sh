#!/usr/bin/env bash
# Populate the claude_auth_volume Docker volume with Claude Code OAuth
# credentials so the MoonMind sandbox worker inherits the host user's
# account tier.
#
# Usage:
#   tools/auth-claude-volume.sh            # sync local ~/.claude creds
#   tools/auth-claude-volume.sh --sync     # same as above (explicit)
#   tools/auth-claude-volume.sh --login    # interactive claude login
#   tools/auth-claude-volume.sh --check    # verify volume credentials
#
# Environment overrides:
#   CLAUDE_AUTH_HOST_DIR  Host credential directory (default: ~/.claude)
#   CLAUDE_VOLUME_NAME   Docker volume name (default: claude_auth_volume)
#   CLAUDE_VOLUME_PATH   Container-side auth root (default: /home/app/.claude)
#   CLAUDE_HOME          Legacy alias for CLAUDE_VOLUME_PATH
set -euo pipefail

export CLAUDE_VOLUME_NAME="${CLAUDE_VOLUME_NAME:-claude_auth_volume}"
VOLUME_NAME="${CLAUDE_VOLUME_NAME}"
HOST_CLAUDE_DIR="${CLAUDE_AUTH_HOST_DIR:-${HOME}/.claude}"
CLAUDE_VOLUME_PATH="${CLAUDE_VOLUME_PATH:-${CLAUDE_HOME:-/home/app/.claude}}"
CLAUDE_HOME="${CLAUDE_VOLUME_PATH}"

# ---------------------------------------------------------------------------
_require_docker() {
  if ! command -v docker >/dev/null 2>&1; then
    echo "Error: docker CLI is not available." >&2
    exit 127
  fi
}

_ensure_volume() {
  if ! docker volume inspect "$VOLUME_NAME" >/dev/null 2>&1; then
    echo "Creating Docker volume '$VOLUME_NAME'..."
    docker volume create "$VOLUME_NAME" >/dev/null
  fi
}

# ---------------------------------------------------------------------------
# --sync (default): copy host ~/.claude credentials into the Docker volume
# ---------------------------------------------------------------------------
cmd_sync() {
  _require_docker
  _ensure_volume

  if [ ! -d "${HOST_CLAUDE_DIR}" ]; then
    echo "Error: No Claude credential directory found at ${HOST_CLAUDE_DIR}" >&2
    echo "" >&2
    echo "Either:" >&2
    echo "  1. Run 'claude login' locally to authenticate, then re-run this script." >&2
    echo "  2. Run '$0 --login' to authenticate interactively inside the container." >&2
    exit 1
  fi

  echo "Syncing Claude credentials from ${HOST_CLAUDE_DIR} into volume '${VOLUME_NAME}'..."

  # Copy the entire host credential directory into the volume.
  # Claude's file layout can vary (credentials.json, settings.json, cache/, etc.)
  # so we sync everything present rather than a fixed list.
  docker run --rm \
    -v "${VOLUME_NAME}:${CLAUDE_HOME}" \
    -v "${HOST_CLAUDE_DIR}:/host-claude-auth:ro" \
    alpine:3.20 sh -c "
      cp -a /host-claude-auth/. '${CLAUDE_HOME}/' &&
      chown -R 1000:1000 '${CLAUDE_HOME}' &&
      chmod 0775 '${CLAUDE_HOME}' &&
      echo '  Synced the following files:' &&
      find '${CLAUDE_HOME}' -maxdepth 2 -type f -exec basename {} \;
    "

  echo ""
  echo "Done. Restart the sandbox worker to pick up the new credentials:"
  echo "  docker compose restart temporal-worker-sandbox"
}

# ---------------------------------------------------------------------------
# --login: interactive claude login inside a container
# ---------------------------------------------------------------------------
cmd_login() {
  _require_docker

  local NETWORK_NAME="${MOONMIND_DOCKER_NETWORK:-local-network}"
  local AUTH_SERVICE="${CLAUDE_AUTH_SERVICE:-temporal-worker-sandbox}"
  local AUTH_PROFILE="${CLAUDE_AUTH_PROFILE:-}"
  local CLAUDE_TERM="${TERM:-xterm-256color}"

  if ! docker network inspect "$NETWORK_NAME" >/dev/null 2>&1; then
    docker network create "$NETWORK_NAME" >/dev/null
  fi

  if ! [ -t 0 ] || ! [ -t 1 ]; then
    echo "Error: --login requires an interactive terminal." >&2
    exit 1
  fi

  local COMPOSE_NETWORK_ARGS=()
  if docker compose run --help 2>/dev/null | grep -Eq '(^|[[:space:]])--network([[:space:]]|=|$)'; then
    COMPOSE_NETWORK_ARGS+=(--network "$NETWORK_NAME")
  fi

  local COMPOSE_PROFILE_ARGS=()
  if [ -n "$AUTH_PROFILE" ]; then
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
    ${COMPOSE_NETWORK_ARGS[@]+"${COMPOSE_NETWORK_ARGS[@]}"} \
    "$AUTH_SERVICE" \
    -lc 'unset ANTHROPIC_API_KEY CLAUDE_API_KEY; stty sane 2>/dev/null || true; mkdir -p "${CLAUDE_HOME:-/home/app/.claude}"; exec claude login'
}

# ---------------------------------------------------------------------------
# --check: verify the volume has valid credentials
# ---------------------------------------------------------------------------
cmd_check() {
  _require_docker
  _ensure_volume

  echo "Checking credentials in volume '${VOLUME_NAME}'..."
  echo ""

  docker run --rm \
    -v "${VOLUME_NAME}:${CLAUDE_HOME}:ro" \
    alpine:3.20 sh -c "
      creds='${CLAUDE_HOME}/credentials.json'
      settings='${CLAUDE_HOME}/settings.json'

      if [ ! -f \"\$creds\" ]; then
        echo 'MISSING: credentials.json — no credentials in volume'
        echo ''
        echo 'Provision credentials with one of:'
        echo '  $0 --sync    (copy from host ~/.claude)'
        echo '  $0 --login   (interactive login in container)'
        exit 1
      fi
      echo 'FOUND: credentials.json'

      if [ -f \"\$settings\" ]; then
        echo \"SETTINGS: \$(cat \"\$settings\")\"
      fi

      echo ''
      echo 'Files in volume:'
      find '${CLAUDE_HOME}' -maxdepth 2 -type f -exec basename {} \;
    "
}

# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------
case "${1:-}" in
  --login)  cmd_login ;;
  --check)  cmd_check ;;
  --sync|"") cmd_sync ;;
  -h|--help)
    sed -nE '2,/^[^#]/{ /^#/s/^# ?//p; }' "$0"
    exit 0
    ;;
  *)
    echo "Unknown option: $1" >&2
    echo "Usage: $0 [--sync|--login|--check|--help]" >&2
    exit 1
    ;;
esac
