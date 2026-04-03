#!/usr/bin/env bash
# Populate the gemini_auth_volume Docker volume with Gemini CLI OAuth
# credentials so the MoonMind sandbox worker inherits the host user's
# account tier (e.g. Gemini Ultra).
#
# Usage:
#   tools/auth-gemini-volume.sh                 # login interactively and register profile (default)
#   tools/auth-gemini-volume.sh --sync          # sync local ~/.gemini creds and register
#   tools/auth-gemini-volume.sh --login         # same as above (explicit)
#   tools/auth-gemini-volume.sh --sync --no-register  # sync only, do not register API profile
#   tools/auth-gemini-volume.sh --check         # verify volume credentials
#   tools/auth-gemini-volume.sh --register      # explicitly register the profile via API
#
# Environment overrides:
#   GEMINI_AUTH_HOST_DIR  Host credential directory (default: ~/.gemini)
#   GEMINI_VOLUME_NAME    Docker volume name (default: moonmind_gemini_auth_volume)
#   GEMINI_VOLUME_PATH    Container-side auth root (default: /var/lib/gemini-auth)
#   GEMINI_HOME           Legacy alias for GEMINI_VOLUME_PATH
#   GEMINI_CLI_HOME       Container-side Gemini CLI home (defaults to GEMINI_VOLUME_PATH)
set -euo pipefail

export GEMINI_VOLUME_NAME="${GEMINI_VOLUME_NAME:-gemini_auth_volume}"
VOLUME_NAME="${GEMINI_VOLUME_NAME}"
HOST_GEMINI_DIR="${GEMINI_AUTH_HOST_DIR:-${HOME}/.gemini}"
GEMINI_VOLUME_PATH="${GEMINI_VOLUME_PATH:-${GEMINI_HOME:-/var/lib/gemini-auth}}"
GEMINI_HOME="${GEMINI_VOLUME_PATH}"
GEMINI_CLI_HOME="${GEMINI_CLI_HOME:-${GEMINI_VOLUME_PATH}}"

AUTH_FILES=(oauth_creds.json settings.json google_accounts.json installation_id)

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
# --sync (default): copy host ~/.gemini credentials into the Docker volume
# ---------------------------------------------------------------------------
cmd_sync() {
  _require_docker
  _ensure_volume

  if [ ! -f "${HOST_GEMINI_DIR}/oauth_creds.json" ]; then
    echo "Error: No Gemini OAuth credentials found at ${HOST_GEMINI_DIR}/oauth_creds.json" >&2
    echo "" >&2
    echo "Either:" >&2
    echo "  1. Run 'gemini' locally to authenticate, then re-run this script." >&2
    echo "  2. Run '$0 --login' to authenticate interactively inside the container." >&2
    exit 1
  fi

  echo "Syncing Gemini credentials from ${HOST_GEMINI_DIR} into volume '${VOLUME_NAME}'..."

  # Build a list of files that actually exist on the host
  local files_to_copy=()
  for f in "${AUTH_FILES[@]}"; do
    if [ -f "${HOST_GEMINI_DIR}/${f}" ]; then
      files_to_copy+=("/host-gemini-auth/${f}")
    fi
  done

  docker run --rm \
    -v "${VOLUME_NAME}:${GEMINI_HOME}" \
    -v "${HOST_GEMINI_DIR}:/host-gemini-auth:ro" \
    alpine:3.20 sh -c "
      mkdir -p '${GEMINI_HOME}/.gemini' &&
      for f in ${files_to_copy[*]}; do
        cp \"\$f\" '${GEMINI_HOME}/.gemini/' &&
        echo \"  Copied \$(basename \"\$f\")\";
      done &&
      chown -R 1000:1000 '${GEMINI_HOME}' &&
      chmod 0775 '${GEMINI_HOME}'
    "

  echo ""
  echo "Done. Restart the sandbox worker to pick up the new credentials:"
  echo "  docker compose restart temporal-worker-sandbox"
}

# ---------------------------------------------------------------------------
# --login: interactive gemini auth login inside a container
# ---------------------------------------------------------------------------
cmd_login() {
  _require_docker

  local NETWORK_NAME="${MOONMIND_DOCKER_NETWORK:-local-network}"
  local AUTH_SERVICE="${GEMINI_AUTH_SERVICE:-temporal-worker-sandbox}"
  local AUTH_PROFILE="${GEMINI_AUTH_PROFILE:-}"
  local GEMINI_TERM="${TERM:-xterm-256color}"

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
    -e MOONMIND_GEMINI_CLI_AUTH_MODE=oauth \
    -e GOOGLE_API_KEY= \
    -e GEMINI_API_KEY= \
    -e TERM="${GEMINI_TERM}" \
    -e GEMINI_HOME="${GEMINI_HOME}" \
    -e GEMINI_CLI_HOME="${GEMINI_CLI_HOME}" \
    ${COMPOSE_NETWORK_ARGS[@]+"${COMPOSE_NETWORK_ARGS[@]}"} \
    "$AUTH_SERVICE" \
    -lc 'unset GOOGLE_API_KEY GEMINI_API_KEY; stty sane 2>/dev/null || true; mkdir -p "${GEMINI_CLI_HOME:-${GEMINI_HOME:-/var/lib/gemini-auth}}/.gemini"; exec gemini'
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
    -v "${VOLUME_NAME}:${GEMINI_HOME}:ro" \
    alpine:3.20 sh -c "
      creds='${GEMINI_HOME}/.gemini/oauth_creds.json'
      settings='${GEMINI_HOME}/.gemini/settings.json'
      accounts='${GEMINI_HOME}/.gemini/google_accounts.json'

      if [ ! -f \"\$creds\" ]; then
        echo 'MISSING: oauth_creds.json — no credentials in volume'
        exit 1
      fi
      echo 'FOUND: oauth_creds.json'

      if [ -f \"\$settings\" ]; then
        echo \"AUTH TYPE: \$(cat \"\$settings\")\"
      fi

      if [ -f \"\$accounts\" ]; then
        echo \"ACCOUNT: \$(cat \"\$accounts\")\"
      fi
    "
}

# ---------------------------------------------------------------------------
# --register: register the volume in the MoonMind provider profile API
# ---------------------------------------------------------------------------
cmd_register() {
  local API_URL="${MOONMIND_API_URL:-http://localhost:5000}"
  local PROFILE_ID="${GEMINI_PROFILE_ID:-gemini_default}"
  local CREATE_URL="${API_URL}/api/v1/provider-profiles"
  local UPDATE_URL="${API_URL}/api/v1/provider-profiles/${PROFILE_ID}"

  echo "Registering provider profile '${PROFILE_ID}' via ${API_URL}..."

  local create_payload
  create_payload=$(cat <<EOF
{
  "profile_id": "${PROFILE_ID}",
  "runtime_id": "gemini_cli",
  "provider_id": "google",
  "provider_label": "Google",
  "credential_source": "oauth_volume",
  "runtime_materialization_mode": "oauth_home",
  "volume_ref": "${VOLUME_NAME}",
  "volume_mount_path": "${GEMINI_VOLUME_PATH}",
  "account_label": "Gemini OAuth Profile (${PROFILE_ID})",
  "max_parallel_runs": 1,
  "cooldown_after_429_seconds": 900,
  "rate_limit_policy": "backoff",
  "enabled": true
}
EOF
)

  local update_payload
  update_payload=$(cat <<EOF
{
  "provider_id": "google",
  "provider_label": "Google",
  "credential_source": "oauth_volume",
  "runtime_materialization_mode": "oauth_home",
  "volume_ref": "${VOLUME_NAME}",
  "volume_mount_path": "${GEMINI_VOLUME_PATH}",
  "account_label": "Gemini OAuth Profile (${PROFILE_ID})",
  "max_parallel_runs": 1,
  "cooldown_after_429_seconds": 900,
  "rate_limit_policy": "backoff",
  "enabled": true
}
EOF
)

  local response
  response=$(curl -sS -w "\n%{http_code}" -X POST "${CREATE_URL}" \
    -H "Content-Type: application/json" \
    -d "${create_payload}")

  local status_code=$(echo "$response" | tail -n1)
  local body=$(echo "$response" | sed '$d')

  if [ "$status_code" -eq 201 ]; then
    echo "Successfully created provider profile '${PROFILE_ID}'."
  elif [ "$status_code" -eq 409 ]; then
    echo "Provider profile '${PROFILE_ID}' already exists; updating it..."

    response=$(curl -sS -w "\n%{http_code}" -X PATCH "${UPDATE_URL}" \
      -H "Content-Type: application/json" \
      -d "${update_payload}")

    status_code=$(echo "$response" | tail -n1)
    body=$(echo "$response" | sed '$d')

    if [ "$status_code" -eq 200 ]; then
      echo "Successfully updated provider profile '${PROFILE_ID}'."
    else
      echo "Error updating provider profile (HTTP $status_code): $body" >&2
      exit 1
    fi
  else
    echo "Error registering provider profile (HTTP $status_code): $body" >&2
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
    --login) COMMAND="login" ;;
    --check) COMMAND="check" ;;
    --sync) COMMAND="sync" ;;
    --register) COMMAND="register" ;;
    -h|--help)
      sed -n '2,/^[^#]/{ /^#/s/^# \?//p }' "$0"
      exit 0
      ;;
    *)
      echo "Unknown option: $arg" >&2
      echo "Usage: $0 [--sync|--login|--check|--register|--no-register|--help]" >&2
      exit 1
      ;;
  esac
done

if [ -z "$COMMAND" ]; then
  COMMAND="login"
fi

case "$COMMAND" in
  login)
    cmd_login
    if [ "$DO_REGISTER" -eq 1 ]; then
      echo ""
      cmd_register
    fi
    ;;
  check)
    cmd_check
    ;;
  sync)
    cmd_sync
    if [ "$DO_REGISTER" -eq 1 ]; then
      echo ""
      cmd_register
    fi
    ;;
  register)
    cmd_register
    ;;
esac
