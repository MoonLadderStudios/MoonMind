#!/usr/bin/env bash
# Populate the codex_auth_volume Docker volume with Codex CLI OAuth
# credentials so the MoonMind sandbox worker inherits the host user's
# account tier.
#
# Usage:
#   tools/auth-codex-volume.sh                 # login interactively and register profile (default)
#   tools/auth-codex-volume.sh --sync          # sync local ~/.codex creds and register
#   tools/auth-codex-volume.sh --login         # same as default (explicit)
#   tools/auth-codex-volume.sh --sync --no-register  # sync only, do not register API profile
#   tools/auth-codex-volume.sh --check         # verify volume credentials
#   tools/auth-codex-volume.sh --register      # explicitly register the profile via API
#
# Environment overrides:
#   CODEX_AUTH_HOST_DIR   Host credential directory (default: ~/.codex)
#   CODEX_VOLUME_NAME     Docker volume name (default: codex_auth_volume)
#   CODEX_VOLUME_PATH     Container-side auth root (default: /home/app/.codex)
#   CODEX_HOME            Legacy alias for CODEX_VOLUME_PATH
set -euo pipefail

export CODEX_VOLUME_NAME="${CODEX_VOLUME_NAME:-codex_auth_volume}"
VOLUME_NAME="${CODEX_VOLUME_NAME}"
HOST_CODEX_DIR="${CODEX_AUTH_HOST_DIR:-${HOME}/.codex}"
CODEX_VOLUME_PATH="${CODEX_VOLUME_PATH:-${CODEX_HOME:-/home/app/.codex}}"
CODEX_HOME="${CODEX_VOLUME_PATH}"

AUTH_COMMAND="${CODEX_AUTH_COMMAND:-codex login --device-auth && codex login status}"
AUTH_COMMAND_TOKEN_RE='^[A-Za-z0-9._/:=?&%+#@!,-]+$'
CODEX_TERM="${TERM:-xterm-256color}"

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

# Security-hardened auth command runner (preserved from original script)
_run_auth_command() {
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
    -e CODEX_HOME="${CODEX_HOME}" \
    "$AUTH_SERVICE" \
    -lc 'stty sane 2>/dev/null || true; exec "$@"' -- \
    "${command_parts[@]}"
}

# ---------------------------------------------------------------------------
# --sync: copy host ~/.codex credentials into the Docker volume
# ---------------------------------------------------------------------------
cmd_sync() {
  _require_docker
  _ensure_volume

  if [ ! -d "${HOST_CODEX_DIR}" ]; then
    echo "Error: No Codex credential directory found at ${HOST_CODEX_DIR}" >&2
    echo "" >&2
    echo "Either:" >&2
    echo "  1. Run 'codex login --device-auth' locally to authenticate, then re-run this script." >&2
    echo "  2. Run '$0 --login' to authenticate interactively inside the container." >&2
    exit 1
  fi

  echo "Syncing Codex credentials from ${HOST_CODEX_DIR} into volume '${VOLUME_NAME}'..."

  docker run --rm \
    -v "${VOLUME_NAME}:${CODEX_HOME}" \
    -v "${HOST_CODEX_DIR}:/host-codex-auth:ro" \
    alpine:3.20 sh -c "
      cp -a /host-codex-auth/. '${CODEX_HOME}/' &&
      chown -R 1000:1000 '${CODEX_HOME}' &&
      chmod 0775 '${CODEX_HOME}' &&
      echo '  Synced the following files:' &&
      find '${CODEX_HOME}' -maxdepth 2 -type f -exec basename {} \;
    "

  echo ""
  echo "Done. Restart the sandbox worker to pick up the new credentials:"
  echo "  docker compose restart temporal-worker-sandbox"
}

# ---------------------------------------------------------------------------
# --login: interactive codex auth login inside a container
# ---------------------------------------------------------------------------
cmd_login() {
  _require_docker

  local NETWORK_NAME="${MOONMIND_DOCKER_NETWORK:-local-network}"
  local AUTH_SERVICE="${CODEX_AUTH_SERVICE:-temporal-worker-sandbox}"
  local AUTH_PROFILE="${CODEX_AUTH_PROFILE:-}"

  if ! docker network inspect "$NETWORK_NAME" >/dev/null 2>&1; then
    docker network create "$NETWORK_NAME" >/dev/null
  fi

  if ! [ -t 0 ] || ! [ -t 1 ]; then
    echo "Error: --login requires an interactive terminal." >&2
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

  # Parse and execute each auth command part
  local -a AUTH_COMMAND_LINES=()
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
    _run_auth_command "$AUTH_COMMAND_LINE"
  done
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
    -v "${VOLUME_NAME}:${CODEX_HOME}:ro" \
    alpine:3.20 sh -c "
      echo 'Files in volume:'
      find '${CODEX_HOME}' -maxdepth 3 -type f -exec basename {} \;
      echo ''
      if find '${CODEX_HOME}' -maxdepth 3 -type f -name '*.json' | head -1 | grep -q .; then
        echo 'STATUS: Credential files found.'
      else
        echo 'WARNING: No credential files found in volume.'
        echo ''
        echo 'Provision credentials with one of:'
        echo '  $0 --sync    (copy from host ~/.codex)'
        echo '  $0 --login   (interactive login in container)'
        exit 1
      fi
    "
}

# ---------------------------------------------------------------------------
# --register: register the volume in the MoonMind provider profile API
# ---------------------------------------------------------------------------
cmd_register() {
  local API_URL="${MOONMIND_API_URL:-http://localhost:5000}"
  local PROFILE_ID="${CODEX_PROFILE_ID:-codex_default}"
  local CREATE_URL="${API_URL}/api/v1/provider-profiles"
  local UPDATE_URL="${API_URL}/api/v1/provider-profiles/${PROFILE_ID}"

  echo "Registering provider profile '${PROFILE_ID}' via ${API_URL}..."

  local create_payload
  create_payload=$(cat <<EOF
{
  "profile_id": "${PROFILE_ID}",
  "runtime_id": "codex_cli",
  "provider_id": "openai",
  "provider_label": "OpenAI",
  "credential_source": "oauth_volume",
  "runtime_materialization_mode": "oauth_home",
  "volume_ref": "${VOLUME_NAME}",
  "volume_mount_path": "${CODEX_VOLUME_PATH}",
  "account_label": "Codex OAuth Profile (${PROFILE_ID})",
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
  "provider_id": "openai",
  "provider_label": "OpenAI",
  "credential_source": "oauth_volume",
  "runtime_materialization_mode": "oauth_home",
  "volume_ref": "${VOLUME_NAME}",
  "volume_mount_path": "${CODEX_VOLUME_PATH}",
  "account_label": "Codex OAuth Profile (${PROFILE_ID})",
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
