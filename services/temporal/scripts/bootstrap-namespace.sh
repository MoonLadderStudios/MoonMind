#!/usr/bin/env sh
set -eu

log() {
  printf '[temporal-bootstrap] %s\n' "$*"
}

run_temporal_cli() {
  temporal "$@" \
    --client-connect-timeout 15s \
    --command-timeout 30s
}

TEMPORAL_ADDRESS="${TEMPORAL_ADDRESS:-temporal:7233}"
TEMPORAL_NAMESPACE="${TEMPORAL_NAMESPACE:-moonmind}"
TEMPORAL_NAMESPACE_RETENTION_DAYS="${TEMPORAL_NAMESPACE_RETENTION_DAYS:-}"
TEMPORAL_RETENTION_MAX_STORAGE_GB="${TEMPORAL_RETENTION_MAX_STORAGE_GB:-100}"
TEMPORAL_RETENTION_ESTIMATED_GB_PER_DAY="${TEMPORAL_RETENTION_ESTIMATED_GB_PER_DAY:-1}"

case "${TEMPORAL_RETENTION_MAX_STORAGE_GB}" in
  ''|*[!0-9]*)
    log "TEMPORAL_RETENTION_MAX_STORAGE_GB must be a positive integer."
    exit 1
    ;;
esac

if [ "${TEMPORAL_RETENTION_MAX_STORAGE_GB}" -le 0 ]; then
  log "TEMPORAL_RETENTION_MAX_STORAGE_GB must be greater than zero."
  exit 1
fi

if [ -n "${TEMPORAL_NAMESPACE_RETENTION_DAYS}" ]; then
  case "${TEMPORAL_NAMESPACE_RETENTION_DAYS}" in
    ''|*[!0-9]*)
      log "TEMPORAL_NAMESPACE_RETENTION_DAYS must be a positive integer when provided."
      exit 1
      ;;
  esac

  if [ "${TEMPORAL_NAMESPACE_RETENTION_DAYS}" -le 0 ]; then
    log "TEMPORAL_NAMESPACE_RETENTION_DAYS must be greater than zero."
    exit 1
  fi

  EFFECTIVE_RETENTION_DAYS="${TEMPORAL_NAMESPACE_RETENTION_DAYS}"
  log "Using explicit namespace retention: ${EFFECTIVE_RETENTION_DAYS} day(s)."
else
  case "${TEMPORAL_RETENTION_ESTIMATED_GB_PER_DAY}" in
    ''|*[!0-9]*)
      log "TEMPORAL_RETENTION_ESTIMATED_GB_PER_DAY must be a positive integer."
      exit 1
      ;;
  esac

  if [ "${TEMPORAL_RETENTION_ESTIMATED_GB_PER_DAY}" -le 0 ]; then
    log "TEMPORAL_RETENTION_ESTIMATED_GB_PER_DAY must be greater than zero."
    exit 1
  fi

  EFFECTIVE_RETENTION_DAYS=$((TEMPORAL_RETENTION_MAX_STORAGE_GB / TEMPORAL_RETENTION_ESTIMATED_GB_PER_DAY))
  if [ "${EFFECTIVE_RETENTION_DAYS}" -le 0 ]; then
    EFFECTIVE_RETENTION_DAYS=1
  fi

  log "Derived namespace retention ${EFFECTIVE_RETENTION_DAYS} day(s) from storage cap ${TEMPORAL_RETENTION_MAX_STORAGE_GB} GB at ${TEMPORAL_RETENTION_ESTIMATED_GB_PER_DAY} GB/day."
fi

if command -v temporal >/dev/null 2>&1; then
  CLI_KIND="temporal"
elif command -v tctl >/dev/null 2>&1; then
  CLI_KIND="tctl"
else
  log "Temporal CLI is not available in this container."
  exit 1
fi

log "Using CLI: ${CLI_KIND}"
log "Waiting for Temporal at ${TEMPORAL_ADDRESS}"

attempt=0
while :; do
  attempt=$((attempt + 1))
  if [ "$CLI_KIND" = "temporal" ]; then
    if run_temporal_cli operator cluster health --address "$TEMPORAL_ADDRESS" >/dev/null 2>&1; then
      break
    fi
  else
    if tctl --address "$TEMPORAL_ADDRESS" --context_timeout 30 cluster health >/dev/null 2>&1; then
      break
    fi
  fi

  if [ "$attempt" -ge 90 ]; then
    log "Temporal did not become healthy after ${attempt} attempts."
    exit 1
  fi

  sleep 2
done

log "Temporal is healthy. Ensuring namespace ${TEMPORAL_NAMESPACE}."

MAX_NS_ATTEMPTS=10
ns_attempt=0
while :; do
  ns_attempt=$((ns_attempt + 1))
  ns_success=0

  if [ "$CLI_KIND" = "temporal" ]; then
    retention_hours=$((EFFECTIVE_RETENTION_DAYS * 24))

    if run_temporal_cli operator namespace describe \
      --address "$TEMPORAL_ADDRESS" \
      --namespace "$TEMPORAL_NAMESPACE" >/dev/null 2>&1; then
      log "Namespace exists; updating retention to ${retention_hours}h."
      if run_temporal_cli operator namespace update \
        --address "$TEMPORAL_ADDRESS" \
        --namespace "$TEMPORAL_NAMESPACE" \
        --retention "${retention_hours}h"; then
        ns_success=1
      fi
    else
      log "Namespace does not exist; creating with retention ${retention_hours}h."
      if run_temporal_cli operator namespace create \
        --address "$TEMPORAL_ADDRESS" \
        --namespace "$TEMPORAL_NAMESPACE" \
        --description "MoonMind runtime workflows" \
        --retention "${retention_hours}h"; then
        ns_success=1
      fi
    fi
  else
    if tctl --address "$TEMPORAL_ADDRESS" --context_timeout 30 namespace describe --namespace "$TEMPORAL_NAMESPACE" >/dev/null 2>&1 \
      || tctl --address "$TEMPORAL_ADDRESS" --context_timeout 30 namespace describe "$TEMPORAL_NAMESPACE" >/dev/null 2>&1; then
      log "Namespace exists; updating retention to ${EFFECTIVE_RETENTION_DAYS} days."
      if tctl --address "$TEMPORAL_ADDRESS" --context_timeout 30 namespace update \
        --namespace "$TEMPORAL_NAMESPACE" \
        --rd "${EFFECTIVE_RETENTION_DAYS}" \
        >/dev/null 2>&1 \
        || tctl --address "$TEMPORAL_ADDRESS" --context_timeout 30 namespace update \
          --namespace "$TEMPORAL_NAMESPACE" \
          --retention "${EFFECTIVE_RETENTION_DAYS}"; then
        ns_success=1
      fi
    else
      log "Namespace does not exist; creating with retention ${EFFECTIVE_RETENTION_DAYS} days."
      if tctl --address "$TEMPORAL_ADDRESS" --context_timeout 30 namespace register \
        --rd "${EFFECTIVE_RETENTION_DAYS}" \
        --description "MoonMind runtime workflows" \
        --namespace "$TEMPORAL_NAMESPACE"; then
        ns_success=1
      fi
    fi
  fi

  if [ "$ns_success" -eq 1 ]; then
    log "Namespace policy applied successfully."
    break
  fi

  if [ "$ns_attempt" -ge "$MAX_NS_ATTEMPTS" ]; then
    log "Failed to apply namespace policy after ${MAX_NS_ATTEMPTS} attempts."
    exit 1
  fi

  log "Namespace operation failed, retrying in 3 seconds (attempt $ns_attempt/$MAX_NS_ATTEMPTS)..."
  sleep 3
done

log "Namespace policy applied. Storage cap guardrail is ${TEMPORAL_RETENTION_MAX_STORAGE_GB} GB with retention ${EFFECTIVE_RETENTION_DAYS} day(s)."

log "Registering custom search attributes..."
MAX_ATTEMPTS=30
attempt=0
while :; do
  attempt=$((attempt + 1))
  success=0
  if [ "$CLI_KIND" = "temporal" ]; then
    if run_temporal_cli operator search-attribute create \
      --address "$TEMPORAL_ADDRESS" \
      --namespace "$TEMPORAL_NAMESPACE" \
      --name "mm_entry" --type "Keyword" \
      --name "mm_owner_id" --type "Keyword" \
      --name "mm_owner_type" --type "Keyword" \
      --name "mm_state" --type "Keyword" \
      --name "mm_updated_at" --type "Datetime" \
      --name "mm_repo" --type "Keyword" \
      --name "mm_integration" --type "Keyword" \
      --name "mm_continue_as_new_cause" --type "Keyword" \
      --name "mm_scheduled_for" --type "Datetime" >/dev/null 2>&1; then
      success=1
    elif run_temporal_cli operator search-attribute list --address "$TEMPORAL_ADDRESS" --namespace "$TEMPORAL_NAMESPACE" | grep -q "mm_owner_id"; then
      log "Search attributes already exist."
      success=1
    fi
  else
    if tctl --address "$TEMPORAL_ADDRESS" --context_timeout 30 admin cluster add-search-attributes \
      --name mm_entry --type Keyword \
      --name mm_owner_id --type Keyword \
      --name mm_owner_type --type Keyword \
      --name mm_state --type Keyword \
      --name mm_updated_at --type Datetime \
      --name mm_repo --type Keyword \
      --name mm_integration --type Keyword \
      --name mm_continue_as_new_cause --type Keyword \
      --name mm_scheduled_for --type Datetime >/dev/null 2>&1; then
      success=1
    elif tctl --address "$TEMPORAL_ADDRESS" --context_timeout 30 cluster get-search-attributes | grep -q "mm_owner_id"; then
      log "Search attributes already exist."
      success=1
    fi
  fi

  if [ "$success" -eq 1 ]; then
    log "Search attributes registered successfully."
    break
  fi

  if [ "$attempt" -ge "$MAX_ATTEMPTS" ]; then
    log "Failed to register search attributes after ${MAX_ATTEMPTS} attempts."
    exit 1
  fi
  
  log "Namespace not ready or search attribute registration failed, retrying in 2 seconds (attempt $attempt/$MAX_ATTEMPTS)..."
  sleep 2
done

log "Temporal foundation bootstrap complete."
