#!/usr/bin/env sh
set -eu

log() {
  printf '[temporal-bootstrap] %s\n' "$*"
}

TEMPORAL_ADDRESS="${TEMPORAL_ADDRESS:-temporal:7233}"
TEMPORAL_NAMESPACE="${TEMPORAL_NAMESPACE:-moonmind}"
TEMPORAL_NAMESPACE_RETENTION_DAYS="${TEMPORAL_NAMESPACE_RETENTION_DAYS:-36500}"
TEMPORAL_RETENTION_MAX_STORAGE_GB="${TEMPORAL_RETENTION_MAX_STORAGE_GB:-100}"

case "${TEMPORAL_NAMESPACE_RETENTION_DAYS}" in
  ''|*[!0-9]*)
    log "TEMPORAL_NAMESPACE_RETENTION_DAYS must be a positive integer."
    exit 1
    ;;
esac

if [ "${TEMPORAL_NAMESPACE_RETENTION_DAYS}" -le 0 ]; then
  log "TEMPORAL_NAMESPACE_RETENTION_DAYS must be greater than zero."
  exit 1
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
    if temporal operator cluster health --address "$TEMPORAL_ADDRESS" >/dev/null 2>&1; then
      break
    fi
  else
    if tctl --address "$TEMPORAL_ADDRESS" cluster health >/dev/null 2>&1; then
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

if [ "$CLI_KIND" = "temporal" ]; then
  retention_hours=$((TEMPORAL_NAMESPACE_RETENTION_DAYS * 24))

  if temporal operator namespace describe \
    --address "$TEMPORAL_ADDRESS" \
    --namespace "$TEMPORAL_NAMESPACE" >/dev/null 2>&1; then
    log "Namespace exists; updating retention to ${retention_hours}h."
    temporal operator namespace update \
      --address "$TEMPORAL_ADDRESS" \
      --namespace "$TEMPORAL_NAMESPACE" \
      --retention "${retention_hours}h"
  else
    log "Namespace does not exist; creating with retention ${retention_hours}h."
    temporal operator namespace create \
      --address "$TEMPORAL_ADDRESS" \
      --namespace "$TEMPORAL_NAMESPACE" \
      --description "MoonMind runtime workflows" \
      --retention "${retention_hours}h"
  fi
else
  if tctl --address "$TEMPORAL_ADDRESS" namespace describe --namespace "$TEMPORAL_NAMESPACE" >/dev/null 2>&1 \
    || tctl --address "$TEMPORAL_ADDRESS" namespace describe "$TEMPORAL_NAMESPACE" >/dev/null 2>&1; then
    log "Namespace exists; updating retention to ${TEMPORAL_NAMESPACE_RETENTION_DAYS} days."
    tctl --address "$TEMPORAL_ADDRESS" namespace update \
      --namespace "$TEMPORAL_NAMESPACE" \
      --rd "${TEMPORAL_NAMESPACE_RETENTION_DAYS}" \
      >/dev/null 2>&1 \
      || tctl --address "$TEMPORAL_ADDRESS" namespace update \
        --namespace "$TEMPORAL_NAMESPACE" \
        --retention "${TEMPORAL_NAMESPACE_RETENTION_DAYS}"
  else
    log "Namespace does not exist; creating with retention ${TEMPORAL_NAMESPACE_RETENTION_DAYS} days."
    tctl --address "$TEMPORAL_ADDRESS" namespace register \
      --rd "${TEMPORAL_NAMESPACE_RETENTION_DAYS}" \
      --description "MoonMind runtime workflows" \
      --namespace "$TEMPORAL_NAMESPACE"
  fi
fi

log "Namespace policy applied. Storage cap guardrail is ${TEMPORAL_RETENTION_MAX_STORAGE_GB} GB."
log "Temporal foundation bootstrap complete."
