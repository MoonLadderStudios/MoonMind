#!/usr/bin/env sh
set -eu

log() {
  printf '[temporal-visibility-rehearsal] %s\n' "$*"
}

TEMPORAL_POSTGRES_HOST="${TEMPORAL_POSTGRES_HOST:-temporal-db}"
TEMPORAL_POSTGRES_PORT="${TEMPORAL_POSTGRES_PORT:-5432}"
TEMPORAL_POSTGRES_USER="${TEMPORAL_POSTGRES_USER:-temporal}"
TEMPORAL_POSTGRES_PASSWORD="${TEMPORAL_POSTGRES_PASSWORD:-temporal}"
TEMPORAL_VISIBILITY_DB="${TEMPORAL_VISIBILITY_DB:-temporal_visibility}"
TEMPORAL_SQL_PLUGIN="${TEMPORAL_SQL_PLUGIN:-postgres12}"
TEMPORAL_VISIBILITY_SCHEMA_DIR="${TEMPORAL_VISIBILITY_SCHEMA_DIR:-/etc/temporal/schema/postgresql/v12/visibility/versioned}"
TEMPORAL_NUM_HISTORY_SHARDS="${TEMPORAL_NUM_HISTORY_SHARDS:-1}"
TEMPORAL_SHARD_DECISION_ACK="${TEMPORAL_SHARD_DECISION_ACK:-}"
TEMPORAL_REHEARSAL_DRY_RUN="${TEMPORAL_REHEARSAL_DRY_RUN:-0}"

if [ "${TEMPORAL_NUM_HISTORY_SHARDS}" = "1" ] && [ "${TEMPORAL_SHARD_DECISION_ACK}" != "acknowledged" ]; then
  log "Shard decision gate is not acknowledged for single-shard deployment."
  log "Set TEMPORAL_SHARD_DECISION_ACK=acknowledged to proceed with upgrade rehearsal."
  exit 2
fi

if [ "${TEMPORAL_REHEARSAL_DRY_RUN}" = "1" ]; then
  log "Dry-run mode enabled; shard decision gate passed."
  log "Would rehearse visibility schema update for database ${TEMPORAL_VISIBILITY_DB}."
  exit 0
fi

if command -v temporal-sql-tool >/dev/null 2>&1; then
  SQL_TOOL="temporal-sql-tool"
elif command -v temporal-sql-tool.sh >/dev/null 2>&1; then
  SQL_TOOL="temporal-sql-tool.sh"
else
  log "Unable to locate temporal-sql-tool in this container."
  exit 1
fi

run_sql_tool() {
  # Try legacy flags first, then modern long-form flags for compatibility.
  "${SQL_TOOL}" --plugin "${TEMPORAL_SQL_PLUGIN}" --ep "${TEMPORAL_POSTGRES_HOST}" -p "${TEMPORAL_POSTGRES_PORT}" --user "${TEMPORAL_POSTGRES_USER}" --pw "${TEMPORAL_POSTGRES_PASSWORD}" --db "${TEMPORAL_VISIBILITY_DB}" "$@" \
    || "${SQL_TOOL}" --plugin "${TEMPORAL_SQL_PLUGIN}" --ep "${TEMPORAL_POSTGRES_HOST}" --port "${TEMPORAL_POSTGRES_PORT}" --user "${TEMPORAL_POSTGRES_USER}" --password "${TEMPORAL_POSTGRES_PASSWORD}" --db "${TEMPORAL_VISIBILITY_DB}" "$@"
}

log "Waiting for PostgreSQL at ${TEMPORAL_POSTGRES_HOST}:${TEMPORAL_POSTGRES_PORT}"
if command -v pg_isready >/dev/null 2>&1; then
  attempt=0
  while :; do
    attempt=$((attempt + 1))
    if PGPASSWORD="${TEMPORAL_POSTGRES_PASSWORD}" pg_isready \
      -h "${TEMPORAL_POSTGRES_HOST}" \
      -p "${TEMPORAL_POSTGRES_PORT}" \
      -U "${TEMPORAL_POSTGRES_USER}" \
      -d "${TEMPORAL_VISIBILITY_DB}" >/dev/null 2>&1; then
      break
    fi

    if [ "${attempt}" -ge 60 ]; then
      log "PostgreSQL did not become ready after ${attempt} attempts."
      exit 1
    fi

    sleep 2
  done
fi

log "Ensuring visibility schema baseline exists."
run_sql_tool setup-schema -v 0.0 >/dev/null 2>&1 || true

log "Running visibility schema update rehearsal from ${TEMPORAL_VISIBILITY_SCHEMA_DIR}."
run_sql_tool update-schema -d "${TEMPORAL_VISIBILITY_SCHEMA_DIR}"

log "Visibility schema rehearsal completed successfully."
