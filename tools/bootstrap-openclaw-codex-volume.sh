#!/usr/bin/env bash
set -euo pipefail

NETWORK_NAME="${MOONMIND_DOCKER_NETWORK:-local-network}"
SRC_VOLUME="${CODEX_VOLUME_NAME:-codex_auth_volume}"
DEST_VOLUME="${OPENCLAW_CODEX_VOLUME_NAME:-openclaw_codex_auth_volume}"
PROFILE_NAME="openclaw"
SERVICE_NAME="openclaw"
SERVICE_USER="${OPENCLAW_DOCKER_USER:-app}"
OPENCLAW_VOLUME_PATH="${OPENCLAW_CODEX_VOLUME_PATH:-/home/app/.codex}"

log() {
  printf '[bootstrap-openclaw] %s\n' "$*"
}

fatal() {
  printf '[bootstrap-openclaw][error] %s\n' "$*" >&2
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

if ! docker volume inspect "$DEST_VOLUME" >/dev/null 2>&1; then
  log "Creating destination volume '$DEST_VOLUME'"
  docker volume create "$DEST_VOLUME" >/dev/null
fi

if docker ps --filter volume="$DEST_VOLUME" --format '{{.ID}}' | grep -q '.'; then
  fatal "Destination volume '$DEST_VOLUME' is currently attached to a running container. Stop OpenClaw before bootstrap."
fi

log "Cloning Codex credentials from '$SRC_VOLUME' into '$DEST_VOLUME'"
docker run --rm \
  -v "$SRC_VOLUME":/src \
  -v "$DEST_VOLUME":/dst \
  alpine:3.19 sh -c 'set -euo pipefail; cd /src && tar -cpf - . | (cd /dst && tar -xpf -)'

log "Validating Codex auth inside OpenClaw container"
docker compose --profile "$PROFILE_NAME" run --rm --user "$SERVICE_USER" "$SERVICE_NAME" \
  bash -lc 'set -euo pipefail; cd "'"${OPENCLAW_VOLUME_PATH}"'" && ls >/dev/null; codex login status'

log "OpenClaw Codex auth volume synced successfully"
