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
TEMPORAL_NAMESPACE="${TEMPORAL_NAMESPACE:-default}"
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
NAMESPACE_RETRY_SLEEP_SECONDS="${TEMPORAL_NAMESPACE_RETRY_SLEEP_SECONDS:-3}"
retention_hours=$((EFFECTIVE_RETENTION_DAYS * 24))
ns_attempt=0
while :; do
  ns_attempt=$((ns_attempt + 1))
  ns_success=0

  if [ "$CLI_KIND" = "temporal" ]; then
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
    elif [ "$TEMPORAL_NAMESPACE" = "default" ]; then
      if [ "$ns_attempt" -ge "$MAX_NS_ATTEMPTS" ]; then
        log "Built-in default namespace was not visible after ${MAX_NS_ATTEMPTS} attempts; skipping namespace create/update and retention policy."
        ns_success=1
      else
        log "Built-in default namespace is not visible yet; retrying retention policy."
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
    elif [ "$TEMPORAL_NAMESPACE" = "default" ]; then
      if [ "$ns_attempt" -ge "$MAX_NS_ATTEMPTS" ]; then
        log "Built-in default namespace was not visible after ${MAX_NS_ATTEMPTS} attempts; skipping namespace create/update and retention policy."
        ns_success=1
      else
        log "Built-in default namespace is not visible yet; retrying retention policy."
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

  log "Namespace operation failed, retrying in ${NAMESPACE_RETRY_SLEEP_SECONDS} seconds (attempt $ns_attempt/$MAX_NS_ATTEMPTS)..."
  sleep "$NAMESPACE_RETRY_SLEEP_SECONDS"
done

log "Namespace policy applied. Storage cap guardrail is ${TEMPORAL_RETENTION_MAX_STORAGE_GB} GB with retention ${EFFECTIVE_RETENTION_DAYS} day(s)."

log "Registering custom search attributes..."
REQUIRED_SEARCH_ATTRIBUTES=$(cat <<'EOF'
mm_entry:Keyword
mm_owner_id:Keyword
mm_owner_type:Keyword
mm_state:Keyword
mm_updated_at:Datetime
mm_repo:Keyword
mm_integration:Keyword
mm_scheduled_for:Datetime
mm_has_dependencies:Bool
mm_dependency_count:Int
TaskRunId:Keyword
RuntimeId:Keyword
SessionId:Keyword
SessionEpoch:Int
SessionStatus:Keyword
IsDegraded:Bool
EOF
)

RETIRED_SEARCH_ATTRIBUTES=$(cat <<'EOF'
CustomKeywordField
CustomStringField
CustomTextField
CustomIntField
CustomDatetimeField
CustomDoubleField
CustomBoolField
mm_continue_as_new_cause
mm_dependency_state
EOF
)

list_search_attributes() {
  if [ "$CLI_KIND" = "temporal" ]; then
    run_temporal_cli operator search-attribute list \
      --address "$TEMPORAL_ADDRESS" \
      --namespace "$TEMPORAL_NAMESPACE"
  else
    tctl --address "$TEMPORAL_ADDRESS" --context_timeout 30 cluster get-search-attributes
  fi
}

create_search_attribute() {
  name="$1"
  attr_type="$2"
  if [ "$CLI_KIND" = "temporal" ]; then
    run_temporal_cli operator search-attribute create \
      --address "$TEMPORAL_ADDRESS" \
      --namespace "$TEMPORAL_NAMESPACE" \
      --name "$name" --type "$attr_type" >/dev/null 2>&1
  else
    tctl --address "$TEMPORAL_ADDRESS" --context_timeout 30 admin cluster add-search-attributes \
      --name "$name" --type "$attr_type" >/dev/null 2>&1
  fi
}

remove_search_attribute() {
  name="$1"
  if [ "$CLI_KIND" = "temporal" ]; then
    run_temporal_cli operator search-attribute remove \
      --address "$TEMPORAL_ADDRESS" \
      --namespace "$TEMPORAL_NAMESPACE" \
      --name "$name" \
      --yes >/dev/null 2>&1
  else
    tctl --address "$TEMPORAL_ADDRESS" --context_timeout 30 admin cluster remove-search-attributes \
      --name "$name" >/dev/null 2>&1
  fi
}

search_attribute_registered() {
  name="$1"
  output="$2"
  printf '%s\n' "$output" | grep -Eq "(^|[[:space:]])${name}([[:space:]]|$)"
}

retired_removed=""
list_output="$(list_search_attributes 2>/dev/null || true)"
if [ -n "$list_output" ]; then
  old_ifs=$IFS
  IFS='
'
  for name in $RETIRED_SEARCH_ATTRIBUTES; do
    [ -n "$name" ] || continue
    if ! search_attribute_registered "$name" "$list_output"; then
      continue
    fi
    if remove_search_attribute "$name"; then
      if [ -n "$retired_removed" ]; then
        retired_removed="${retired_removed}, "
      fi
      retired_removed="${retired_removed}${name}"
    else
      log "Failed to remove retired search attribute ${name}."
      exit 1
    fi
  done
  IFS=$old_ifs
fi
if [ -n "$retired_removed" ]; then
  log "Removed retired search attributes: ${retired_removed}."
fi

MAX_ATTEMPTS=30
attempt=0
while :; do
  attempt=$((attempt + 1))
  success=0
  all_registered=0
  registered_missing=""
  list_output="$(list_search_attributes 2>/dev/null || true)"
  if [ -n "$list_output" ]; then
    success=1
    all_registered=1
    old_ifs=$IFS
    IFS='
'
    for spec in $REQUIRED_SEARCH_ATTRIBUTES; do
      [ -n "$spec" ] || continue
      name=${spec%%:*}
      attr_type=${spec#*:}
      if search_attribute_registered "$name" "$list_output"; then
        continue
      fi
      all_registered=0
      if create_search_attribute "$name" "$attr_type"; then
        if [ -n "$registered_missing" ]; then
          registered_missing="${registered_missing}, "
        fi
        registered_missing="${registered_missing}${name}"
      else
        success=0
        break
      fi
    done
    IFS=$old_ifs
    if [ "$success" -eq 1 ] && [ "$all_registered" -eq 1 ]; then
      log "Search attributes registered successfully."
      break
    fi
    if [ "$success" -eq 1 ] && [ -n "$registered_missing" ]; then
      log "Registered missing search attributes: ${registered_missing}. Verifying..."
    fi
  fi

  if [ "$attempt" -ge "$MAX_ATTEMPTS" ]; then
    log "Failed to register search attributes after ${MAX_ATTEMPTS} attempts."
    exit 1
  fi
  
  log "Namespace not ready or search attribute registration failed, retrying in 2 seconds (attempt $attempt/$MAX_ATTEMPTS)..."
  sleep 2
done

log "Temporal foundation bootstrap complete."
