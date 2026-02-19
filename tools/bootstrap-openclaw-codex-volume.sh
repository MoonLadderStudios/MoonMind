#!/usr/bin/env bash
set -euo pipefail

NETWORK_NAME="${MOONMIND_DOCKER_NETWORK:-local-network}"
SRC_VOLUME="${CODEX_VOLUME_NAME:-codex_auth_volume}"
DEST_VOLUME="${OPENCLAW_CODEX_VOLUME_NAME:-openclaw_codex_auth_volume}"
PROFILE_NAME="openclaw"
SERVICE_NAME="openclaw"
SERVICE_USER="${OPENCLAW_DOCKER_USER:-app}"
OPENCLAW_VOLUME_PATH="${OPENCLAW_CODEX_VOLUME_PATH:-/home/app/.codex}"
AUDIT_ACTOR="${SUDO_USER:-${USER:-$(id -un 2>/dev/null || echo unknown)}}"

ts() {
  date -u +"%Y-%m-%dT%H:%M:%SZ"
}

log() {
  printf '{"ts":"%s","actor":"%s","component":"bootstrap-openclaw","level":"info","message":"%s"}\n' "$(ts)" "$AUDIT_ACTOR" "$*"
}

fatal() {
  printf '{"ts":"%s","actor":"%s","component":"bootstrap-openclaw","level":"error","message":"%s"}\n' "$(ts)" "$AUDIT_ACTOR" "$*" >&2
  exit 1
}

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || fatal "Required command '$1' not found in PATH"
}

require_cmd docker

if ! docker compose version >/dev/null 2>&1; then
  fatal "docker compose CLI plugin is required"
fi

if ! docker network inspect "$NETWORK_NAME" >/dev/null 2>&1; then
  log "Creating Docker network '$NETWORK_NAME'"
  docker network create "$NETWORK_NAME" >/dev/null
fi

if ! docker volume inspect "$SRC_VOLUME" >/dev/null 2>&1; then
  fatal "Source Codex volume '$SRC_VOLUME' not found. Run tools/auth-codex-volume.sh first."
fi

if docker ps -a --filter volume="$SRC_VOLUME" --format '{{.ID}}' | grep -q '.'; then
  fatal "Source volume '$SRC_VOLUME' is attached to a container. Stop services using it before bootstrap to avoid inconsistent auth copies."
fi

if ! docker volume inspect "$DEST_VOLUME" >/dev/null 2>&1; then
  log "Creating destination volume '$DEST_VOLUME'"
  docker volume create "$DEST_VOLUME" >/dev/null
fi

if docker ps -a --filter volume="$DEST_VOLUME" --format '{{.ID}}' | grep -q '.'; then
  fatal "Destination volume '$DEST_VOLUME' is attached to a container. Stop OpenClaw and remove/recreate dependent containers before bootstrap."
fi

if docker run --rm -v "$DEST_VOLUME":/dst alpine:3.19 sh -c 'find /dst -mindepth 1 -print -quit | grep -q .'; then
  if [[ "${OPENCLAW_BOOTSTRAP_OVERWRITE:-}" != "1" ]]; then
    fatal "Destination volume '$DEST_VOLUME' already contains files. Set OPENCLAW_BOOTSTRAP_OVERWRITE=1 to overwrite existing credentials."
  fi
  log "Destination volume '$DEST_VOLUME' is non-empty; overwrite confirmed by OPENCLAW_BOOTSTRAP_OVERWRITE=1"
fi

if ! docker compose --profile "$PROFILE_NAME" config --services | grep -qx "$SERVICE_NAME"; then
  fatal "Compose service '$SERVICE_NAME' is not defined for profile '$PROFILE_NAME'. Add OpenClaw compose wiring before running bootstrap validation."
fi

log "Cloning Codex credentials from '$SRC_VOLUME' into '$DEST_VOLUME'"
docker run --rm \
  -v "$SRC_VOLUME":/src \
  -v "$DEST_VOLUME":/dst \
  alpine:3.19 sh -c 'set -euo pipefail; rm -rf /dst/* /dst/.[!.]* /dst/..?*; cd /src && tar -cpf - . | (cd /dst && tar -xpf -); rm -f /dst/moonmind_worker_token /dst/moonmind_worker_token.policy'

log "Validating Codex auth inside OpenClaw container"
docker compose --profile "$PROFILE_NAME" run --rm --user "$SERVICE_USER" "$SERVICE_NAME" \
  bash -lc 'set -euo pipefail; cd "'"${OPENCLAW_VOLUME_PATH}"'" && ls >/dev/null; codex login status'

log "OpenClaw Codex auth volume synced successfully"
