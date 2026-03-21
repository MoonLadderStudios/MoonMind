#!/usr/bin/env bash
# Populate the cursor_auth_volume Docker volume with Cursor CLI credentials
# so the MoonMind sandbox worker can invoke cursor-agent headless.
#
# Usage:
#   tools/auth-cursor-volume.sh                 # set API key (default)
#   tools/auth-cursor-volume.sh --api-key       # store CURSOR_API_KEY in volume
#   tools/auth-cursor-volume.sh --login         # interactive login inside container
#   tools/auth-cursor-volume.sh --check         # verify volume credentials
#   tools/auth-cursor-volume.sh --register      # explicitly register the profile via API
#   tools/auth-cursor-volume.sh --api-key --no-register  # set API key only, skip profile registration
#
# Environment overrides:
#   CURSOR_API_KEY         API key for Cursor CLI
#   CURSOR_VOLUME_NAME     Docker volume name (default: cursor_auth_volume)
#   CURSOR_VOLUME_PATH     Container-side auth root (default: /home/app/.cursor)
set -euo pipefail

export CURSOR_VOLUME_NAME="${CURSOR_VOLUME_NAME:-cursor_auth_volume}"
VOLUME_NAME="${CURSOR_VOLUME_NAME}"
CURSOR_VOLUME_PATH="${CURSOR_VOLUME_PATH:-/home/app/.cursor}"

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
# --api-key (default): store CURSOR_API_KEY into the Docker volume
# ---------------------------------------------------------------------------
cmd_api_key() {
  _require_docker
  _ensure_volume

  if [ -z "${CURSOR_API_KEY:-}" ]; then
    echo "Error: CURSOR_API_KEY environment variable is not set." >&2
    echo "" >&2
    echo "Set it and re-run:" >&2
    echo "  CURSOR_API_KEY=your-key-here $0 --api-key" >&2
    exit 1
  fi

  echo "Storing Cursor API key into volume '${VOLUME_NAME}'..."

  # Write the API key to a file inside the volume that cursor-agent can use
  docker run --rm \
    -v "${VOLUME_NAME}:${CURSOR_VOLUME_PATH}" \
    -e "CURSOR_API_KEY=${CURSOR_API_KEY}" \
    alpine:3.20 sh -c "
      mkdir -p '${CURSOR_VOLUME_PATH}' &&
      echo \"\${CURSOR_API_KEY}\" > '${CURSOR_VOLUME_PATH}/api_key' &&
      chown -R 1000:1000 '${CURSOR_VOLUME_PATH}' &&
      chmod 0775 '${CURSOR_VOLUME_PATH}' &&
      chmod 0600 '${CURSOR_VOLUME_PATH}/api_key'
    "

  echo ""
  echo "Done. Restart the sandbox worker to pick up the new credentials:"
  echo "  docker compose restart temporal-worker-sandbox"
}

# ---------------------------------------------------------------------------
# --login: interactive cursor-agent login inside a container
# ---------------------------------------------------------------------------
cmd_login() {
  _require_docker

  local NETWORK_NAME="${MOONMIND_DOCKER_NETWORK:-local-network}"
  local AUTH_SERVICE="${CURSOR_AUTH_SERVICE:-temporal-worker-sandbox}"
  local AUTH_PROFILE="${CURSOR_AUTH_PROFILE:-}"
  local CURSOR_TERM="${TERM:-xterm-256color}"

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
    -e CURSOR_API_KEY= \
    -e TERM="${CURSOR_TERM}" \
    ${COMPOSE_NETWORK_ARGS[@]+"${COMPOSE_NETWORK_ARGS[@]}"} \
    "$AUTH_SERVICE" \
    -lc 'unset CURSOR_API_KEY; stty sane 2>/dev/null || true; exec cursor-agent login'
}

# ---------------------------------------------------------------------------
# --check: verify the volume has valid credentials
# ---------------------------------------------------------------------------
cmd_check() {
  _require_docker
  _ensure_volume

  echo "Checking Cursor CLI credentials in volume '${VOLUME_NAME}'..."
  echo ""

  docker run --rm \
    -v "${VOLUME_NAME}:${CURSOR_VOLUME_PATH}:ro" \
    alpine:3.20 sh -c "
      api_key='${CURSOR_VOLUME_PATH}/api_key'

      if [ -f \"\$api_key\" ]; then
        key_preview=\$(head -c 8 \"\$api_key\")
        echo \"FOUND: api_key (starts with: \${key_preview}...)\"
        echo 'AUTH MODE: api_key'
      else
        echo 'MISSING: api_key — no API key credentials in volume'
        echo ''
        echo 'To set up:'
        echo '  CURSOR_API_KEY=your-key-here $0 --api-key'
        echo '  or: $0 --login'
        exit 1
      fi
    "
}

# ---------------------------------------------------------------------------
# --register: register the volume in the MoonMind auth profile API
# ---------------------------------------------------------------------------
cmd_register() {
  local API_URL="${MOONMIND_API_URL:-http://localhost:5000}"
  local PROFILE_ID="${CURSOR_PROFILE_ID:-cursor_default}"
  local AUTH_MODE="${REGISTER_AUTH_MODE:-oauth}"
  local ACCOUNT_LABEL="Cursor CLI OAuth profile ('"${PROFILE_ID}"')"
  if [ "$AUTH_MODE" = "api_key" ]; then
    ACCOUNT_LABEL="Cursor CLI API key profile ('"${PROFILE_ID}"')"
  fi

  echo "Registering auth profile '${PROFILE_ID}' (${AUTH_MODE}) via ${API_URL}..."

  local response
  response=$(curl -sS -w "\n%{http_code}" -X POST "${API_URL}/api/v1/auth-profiles" \
    -H "Content-Type: application/json" \
    -d '{
      "profile_id": "'"${PROFILE_ID}"'",
      "runtime_id": "cursor_cli",
      "auth_mode": "'"${AUTH_MODE}"'",
      "volume_ref": "'"${VOLUME_NAME}"'",
      "volume_mount_path": "'"${CURSOR_VOLUME_PATH}"'",
      "account_label": "'"${ACCOUNT_LABEL}"'",
      "max_parallel_runs": 1
    }')

  local status_code=$(echo "$response" | tail -n1)
  local body=$(echo "$response" | sed '$d')

  if [ "$status_code" -eq 201 ]; then
    echo "Successfully registered profile '${PROFILE_ID}'."
  elif [ "$status_code" -eq 409 ]; then
    echo "Profile '${PROFILE_ID}' already exists."
  else
    echo "Error registering profile (HTTP $status_code): $body" >&2
    exit 1
  fi
}

# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------
DO_REGISTER=1
COMMAND=""

for arg in "$@"; do
  case "$arg" in
    --no-register) DO_REGISTER=0 ;;
    --api-key) COMMAND="api-key" ;;
    --login) COMMAND="login" ;;
    --check) COMMAND="check" ;;
    --register) COMMAND="register" ;;
    -h|--help)
      sed -n '2,/^[^#]/{ /^#/s/^# \?//p }' "$0"
      exit 0
      ;;
    *)
      echo "Unknown option: $arg" >&2
      echo "Usage: $0 [--api-key|--login|--check|--register|--no-register|--help]" >&2
      exit 1
      ;;
  esac
done

if [ -z "$COMMAND" ]; then
  COMMAND="api-key"
fi

case "$COMMAND" in
  api-key)
    cmd_api_key
    if [ "$DO_REGISTER" -eq 1 ]; then
      echo ""
      REGISTER_AUTH_MODE=api_key cmd_register
    fi
    ;;
  check)
    cmd_check
    ;;
  login)
    cmd_login
    if [ "$DO_REGISTER" -eq 1 ]; then
      echo ""
      REGISTER_AUTH_MODE=oauth cmd_register
    fi
    ;;
  register)
    cmd_register
    ;;
esac
