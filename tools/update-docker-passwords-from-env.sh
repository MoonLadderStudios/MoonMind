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
# MinIO: uses a one-shot minio/mc container with --entrypoint /bin/sh (the mc image defaults
# to entrypoint "mc", so a bare /bin/sh would be treated as an mc subcommand).
# If MinIO already accepts the credentials in .env, nothing is changed. Otherwise the script
# tries MINIO_ROOT_PASSWORD_PREVIOUS (env), then default minioadmin/minioadmin when applicable,
# then prompts interactively for the secret MinIO currently accepts (TTY only).
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

MinIO rotation: if .env is ahead of the server, use MINIO_ROOT_PASSWORD_PREVIOUS or answer
the interactive prompt with the secret MinIO still accepts.

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
    -c 'mc alias set mm http://127.0.0.1:9000 "$MC_AUTH_USER" "$MC_AUTH_SECRET" && mc admin info mm' >/dev/null
}

minio_rotate_secret() {
  local cid="$1"
  local auth_user="$2"
  local auth_secret="$3"
  local new_secret="$4"
  docker run --rm \
    --entrypoint /bin/sh \
    --network "container:${cid}" \
    -e MC_AUTH_USER="$auth_user" \
    -e MC_AUTH_SECRET="$auth_secret" \
    -e MC_NEW_SECRET="$new_secret" \
    "$MINIO_MC_IMAGE" \
    -c 'mc alias set mm http://127.0.0.1:9000 "$MC_AUTH_USER" "$MC_AUTH_SECRET" && mc admin accesskey edit mm "$MC_AUTH_USER" --secret-key "$MC_NEW_SECRET"'
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

  if [[ -n "${MINIO_ROOT_PASSWORD_PREVIOUS:-}" ]]; then
    if minio_try_admin_info "$cid" "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD_PREVIOUS"; then
      echo "[update-docker-passwords] MinIO: rotating secret using MINIO_ROOT_PASSWORD_PREVIOUS..."
      minio_rotate_secret "$cid" "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD_PREVIOUS" "$MINIO_ROOT_PASSWORD"
      echo "[update-docker-passwords] MinIO: done."
      return 0
    fi
  fi

  if [[ "$MINIO_ROOT_USER" == "minioadmin" && "$MINIO_ROOT_PASSWORD" != "minioadmin" ]]; then
    echo "[update-docker-passwords] MinIO: trying default secret minioadmin (dev convenience)..." >&2
    if minio_try_admin_info "$cid" "$MINIO_ROOT_USER" "minioadmin"; then
      echo "[update-docker-passwords] MinIO: rotating from default credentials to .env..."
      minio_rotate_secret "$cid" "$MINIO_ROOT_USER" "minioadmin" "$MINIO_ROOT_PASSWORD"
      echo "[update-docker-passwords] MinIO: done."
      return 0
    fi
  fi

  local prompted=""
  if [[ -t 0 ]]; then
    echo "[update-docker-passwords] MinIO: credentials in .env did not authenticate." >&2
    printf '%s' "Enter the root secret MinIO currently uses (input hidden): " >&2
    read -rs prompted || true
    echo >&2
    if [[ -n "$prompted" ]] && minio_try_admin_info "$cid" "$MINIO_ROOT_USER" "$prompted"; then
      echo "[update-docker-passwords] MinIO: rotating to MINIO_ROOT_PASSWORD from .env..."
      minio_rotate_secret "$cid" "$MINIO_ROOT_USER" "$prompted" "$MINIO_ROOT_PASSWORD"
      echo "[update-docker-passwords] MinIO: done."
      return 0
    fi
  fi

  echo "Error: could not authenticate to MinIO with MINIO_ROOT_PASSWORD from .env." >&2
  echo "Set MINIO_ROOT_PASSWORD_PREVIOUS, re-run on a TTY to be prompted, or fix .env." >&2
  exit 1
}

update_postgres_password
update_minio_password

echo "[update-docker-passwords] All requested updates completed."
