#!/usr/bin/env bash
# Apply POSTGRES_PASSWORD and MINIO_ROOT_PASSWORD from .env to running Docker Compose
# Postgres and MinIO services (so DB and object store match your .env).
#
# Requirements:
#   - docker compose (or docker-compose) and a project .env at the repo root
#   - postgres and minio services up (same compose project as this repo)
#
# PostgreSQL: uses docker compose exec + local psql (password not required for peer/trust
# inside the container) to ALTER USER to match POSTGRES_PASSWORD.
#
# MinIO: root credentials are configured from container environment, so the reliable way to
# apply a changed MINIO_ROOT_PASSWORD is to recreate the MinIO container with the updated
# .env, then verify the new credentials work. A one-shot minio/mc container is used only for
# verification against the running MinIO API.
#
# After Postgres password changes, restart app services that cache connections if needed
# (for example: docker compose restart api init-db).
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: update-docker-passwords-from-env.sh [--help]

Reads repo-root .env and updates:
  - Postgres role POSTGRES_USER (default postgres) password -> POSTGRES_PASSWORD
  - MinIO root user MINIO_ROOT_USER (default minioadmin) secret -> MINIO_ROOT_PASSWORD

If .env is ahead of the running MinIO container, the script recreates MinIO so it picks up
the new MINIO_ROOT_PASSWORD, then verifies the updated credentials.

Options:
  --help, -h   Show this help.
USAGE
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "$ROOT_DIR"

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi
if [[ "${1:-}" != "" ]]; then
  echo "Unknown argument: $1" >&2
  usage >&2
  exit 1
fi

ENV_FILE="${ROOT_DIR}/.env"
if [[ ! -f "$ENV_FILE" ]]; then
  echo "Error: missing ${ENV_FILE}" >&2
  exit 1
fi

if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
  COMPOSE_CMD=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE_CMD=(docker-compose)
else
  echo "Error: docker compose CLI is not available." >&2
  exit 1
fi

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

POSTGRES_USER="${POSTGRES_USER:-postgres}"
POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-}"
MINIO_ROOT_USER="${MINIO_ROOT_USER:-minioadmin}"
MINIO_ROOT_PASSWORD="${MINIO_ROOT_PASSWORD:-minioadmin}"
MINIO_MC_IMAGE="${MINIO_MC_IMAGE:-minio/mc:latest}"
TEMPORAL_ARTIFACT_S3_ACCESS_KEY_ID="${TEMPORAL_ARTIFACT_S3_ACCESS_KEY_ID:-minioadmin}"
TEMPORAL_ARTIFACT_S3_SECRET_ACCESS_KEY="${TEMPORAL_ARTIFACT_S3_SECRET_ACCESS_KEY:-minioadmin}"

if [[ -z "$POSTGRES_PASSWORD" ]]; then
  echo "Error: POSTGRES_PASSWORD is empty in .env (required)." >&2
  exit 1
fi

escape_sql_literal() {
  # Double single-quotes for PostgreSQL string literal.
  printf '%s' "$1" | sed "s/'/''/g"
}

update_postgres_password() {
  local escaped
  escaped="$(escape_sql_literal "$POSTGRES_PASSWORD")"
  echo "[update-docker-passwords] PostgreSQL: setting password for role ${POSTGRES_USER}..."
  "${COMPOSE_CMD[@]}" exec -T postgres psql -U "$POSTGRES_USER" -d postgres -v ON_ERROR_STOP=1 \
    -c "ALTER USER \"${POSTGRES_USER}\" WITH PASSWORD '${escaped}';"
  echo "[update-docker-passwords] PostgreSQL: done."
}

minio_container_id() {
  "${COMPOSE_CMD[@]}" ps -q minio 2>/dev/null | head -n1
}

minio_try_admin_info() {
  local cid="$1"
  local user="$2"
  local secret="$3"
  docker run --rm \
    --entrypoint /bin/sh \
    --network "container:${cid}" \
    -e MC_AUTH_USER="$user" \
    -e MC_AUTH_SECRET="$secret" \
    "$MINIO_MC_IMAGE" \
    -c 'mc alias set mm http://127.0.0.1:9000 "$MC_AUTH_USER" "$MC_AUTH_SECRET" && mc admin info mm' >/dev/null 2>&1
}

update_minio_password() {
  local cid
  cid="$(minio_container_id)"
  if [[ -z "$cid" ]]; then
    echo "[update-docker-passwords] MinIO: container not running; skip." >&2
    return 0
  fi

  echo "[update-docker-passwords] MinIO: checking credentials for user ${MINIO_ROOT_USER}..."

  if minio_try_admin_info "$cid" "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD"; then
    echo "[update-docker-passwords] MinIO: already matches MINIO_ROOT_PASSWORD in .env; nothing to do."
    return 0
  fi

  echo "[update-docker-passwords] MinIO: recreating container so it reloads .env credentials..."
  "${COMPOSE_CMD[@]}" up -d --no-deps --force-recreate minio >/dev/null

  cid="$(minio_container_id)"
  if [[ -z "$cid" ]]; then
    echo "Error: MinIO container was not found after recreation." >&2
    exit 1
  fi

  local attempt
  echo "[update-docker-passwords] MinIO: waiting for MinIO to accept updated credentials..."
  for attempt in $(seq 1 30); do
    if minio_try_admin_info "$cid" "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD"; then
      echo "[update-docker-passwords] MinIO: done (ready after ${attempt} check(s))."
      if [[ "$TEMPORAL_ARTIFACT_S3_ACCESS_KEY_ID" == "$MINIO_ROOT_USER" ]] && [[ "$TEMPORAL_ARTIFACT_S3_SECRET_ACCESS_KEY" != "$MINIO_ROOT_PASSWORD" ]]; then
        echo "[update-docker-passwords] Warning: TEMPORAL_ARTIFACT_S3_SECRET_ACCESS_KEY in .env does not match MINIO_ROOT_PASSWORD." >&2
        echo "[update-docker-passwords] Warning: artifact clients using ${TEMPORAL_ARTIFACT_S3_ACCESS_KEY_ID} may fail until their secret is updated too." >&2
      fi
      return 0
    fi
    sleep 2
  done

  echo "Error: MinIO did not accept MINIO_ROOT_PASSWORD from .env after recreation." >&2
  echo "Check the MinIO container logs and verify the .env values are valid." >&2
  exit 1
}

update_postgres_password
update_minio_password

echo "[update-docker-passwords] All requested updates completed."
