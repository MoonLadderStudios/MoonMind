#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
COMPOSE_FILE="$REPO_ROOT/docker-compose.yaml"
PROJECT_NAME="${MOONMIND_CONTROL_PLANE_TEST_PROJECT:-moonmind-test-control-plane}"
NETWORK_NAME="${MOONMIND_CONTROL_PLANE_TEST_NETWORK:-moonmind-test_control-plane-network}"
SESSION_PROJECT_NAME="${PROJECT_NAME//_/-}"
PROBE_SESSION_ID="${SESSION_PROJECT_NAME}-managed-session-probe"
PROBE_NAME="mm-codex-session-${PROBE_SESSION_ID}"
INTERRUPTED_SESSION_ID="${SESSION_PROJECT_NAME}-interrupted-session"
INTERRUPTED_NAME="mm-codex-session-${INTERRUPTED_SESSION_ID}"
COMPOSE_CMD=()

if [[ "${MOONMIND_RUN_CONTROL_PLANE_NETWORK_CONFORMANCE:-}" != "1" ]]; then
  echo "Error: set MOONMIND_RUN_CONTROL_PLANE_NETWORK_CONFORMANCE=1 to run this destructive local lifecycle test." >&2
  exit 2
fi

if [[ ! "$PROJECT_NAME" =~ ^moonmind-test-[a-z0-9][a-z0-9_-]*$ ]]; then
  echo "Error: MOONMIND_CONTROL_PLANE_TEST_PROJECT must start with 'moonmind-test-'." >&2
  exit 2
fi

if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
  COMPOSE_CMD=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE_CMD=(docker-compose)
else
  echo "Error: docker compose CLI is not available." >&2
  exit 127
fi

compose() {
  "${COMPOSE_CMD[@]}" --project-name "$PROJECT_NAME" -f "$COMPOSE_FILE" --project-directory "$REPO_ROOT" "$@"
}

cleanup() {
  docker rm -f "$PROBE_NAME" "$INTERRUPTED_NAME" >/dev/null 2>&1 || true
  compose down --volumes --remove-orphans >/dev/null 2>&1 || true
}
trap cleanup EXIT

if docker container inspect moonmind-sandbox-egress-proxy >/dev/null 2>&1; then
  echo "Error: stop the normal MoonMind deployment before running the isolated lifecycle test." >&2
  exit 2
fi

if docker network inspect "$NETWORK_NAME" >/dev/null 2>&1; then
  echo "Error: test network '$NETWORK_NAME' already exists; fresh-start proof requires it to be absent." >&2
  exit 2
fi

export MOONMIND_CONTROL_PLANE_NETWORK="$NETWORK_NAME"
export MOONMIND_DOCKER_PROXY_NETWORK="${PROJECT_NAME}_docker-proxy-network"
export MOONMIND_SANDBOX_EGRESS_NETWORK="${PROJECT_NAME}_sandbox-egress-network"
export MOONMIND_RESTRICTED_EGRESS_NETWORK="${PROJECT_NAME}_restricted-egress-network"
export MOONMIND_OMNIGENT_EGRESS_NETWORK="${PROJECT_NAME}_omnigent-egress-network"
export MOONMIND_URL="http://api:8000"
export MOONMIND_AGENT_WORKSPACES_VOLUME_NAME="${PROJECT_NAME}_agent-workspaces"
export MOONMIND_SECRETS_VOLUME_NAME="${PROJECT_NAME}_secrets"
export MOONMIND_RETRIEVAL_STATE_VOLUME_NAME="${PROJECT_NAME}_retrieval-state"
export MOONMIND_UNREAL_CCACHE_VOLUME_NAME="${PROJECT_NAME}_unreal-ccache"
export MOONMIND_UNREAL_UBT_VOLUME_NAME="${PROJECT_NAME}_unreal-ubt"
export CODEX_VOLUME_NAME="${PROJECT_NAME}_codex-auth"
export CLAUDE_VOLUME_NAME="${PROJECT_NAME}_claude-auth"
export MOONMIND_API_HOST_PORT="${MOONMIND_CONTROL_PLANE_TEST_API_PORT:-17000}"
export MINIO_API_PORT="${MOONMIND_CONTROL_PLANE_TEST_MINIO_PORT:-19000}"
export MINIO_CONSOLE_PORT="${MOONMIND_CONTROL_PLANE_TEST_MINIO_CONSOLE_PORT:-19001}"
export OMNIGENT_PORT="${MOONMIND_CONTROL_PLANE_TEST_OMNIGENT_PORT:-18000}"

wait_for_runtime() {
  local attempt
  for attempt in $(seq 1 90); do
    if compose exec -T temporal-worker-agent-runtime python -c \
      'import json, urllib.request; payload=json.load(urllib.request.urlopen("http://localhost:8080/readyz", timeout=2)); backend=payload["containerBackend"]; assert backend["ready"] and len(backend["egressAttestations"]) == 2' \
      >/dev/null 2>&1; then
      return 0
    fi
    sleep 2
  done
  echo "Error: agent-runtime worker did not publish passing egress attestations." >&2
  return 1
}

probe_api() {
  local session_id="$1"
  local keep_running="${2:-false}"
  local runtime_image_ref
  runtime_image_ref="$(compose images -q temporal-worker-agent-runtime)"
  if [[ -z "$runtime_image_ref" ]]; then
    echo "Error: could not resolve the managed-session runtime image." >&2
    return 1
  fi
  local probe_args=(
    python /workspace/host_project/tools/probe_managed_session_control_plane.py
    --session-id "$session_id"
    --image-ref "$runtime_image_ref"
    --expected-network "$NETWORK_NAME"
  )
  if [[ "$keep_running" == "true" ]]; then
    probe_args+=(--keep-running)
  fi
  compose exec -T temporal-worker-agent-runtime "${probe_args[@]}"
}

# Fresh startup: Compose must create the stable network without bootstrap.
compose up -d
wait_for_runtime
docker network inspect "$NETWORK_NAME" >/dev/null
probe_api "$PROBE_SESSION_ID"

# Normal owned-container cleanup must allow Compose to remove the network.
compose down --remove-orphans
if docker network inspect "$NETWORK_NAME" >/dev/null 2>&1; then
  echo "Error: normal Compose shutdown left the control-plane network behind." >&2
  exit 1
fi

# An interrupted owned session may temporarily block network deletion. A new
# startup must reuse that authoritative network until reconciliation removes it.
compose up -d
wait_for_runtime
probe_api "$INTERRUPTED_SESSION_ID" true
compose down --remove-orphans >/dev/null 2>&1 || true
if ! docker network inspect "$NETWORK_NAME" >/dev/null 2>&1; then
  echo "Error: Compose removed a network that still had an attached owned container." >&2
  exit 1
fi

compose up -d
wait_for_runtime
docker exec "$INTERRUPTED_NAME" python3 -c \
  'import urllib.request; response=urllib.request.urlopen("http://api:8000/healthz", timeout=10); assert 200 <= response.status < 300'
docker rm -f "$INTERRUPTED_NAME" >/dev/null
compose down --remove-orphans
if docker network inspect "$NETWORK_NAME" >/dev/null 2>&1; then
  echo "Error: reconciled Compose shutdown left the control-plane network behind." >&2
  exit 1
fi

echo "Control-plane network lifecycle conformance passed."
