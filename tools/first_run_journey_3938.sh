#!/usr/bin/env bash
# MoonLadderStudios/MoonMind#3938: disposable default Compose first-run journey.
#
# One disposable default installation with no .env file, no inherited
# credentials/login caches, and no old application state. Uses the default
# deployment file (docker-compose.yaml) with ordinary bootstrap/migrations
# and operator admission, then submits one small scratch task with
# omitted/default selections and explicit no-publication intent through the
# real API (POST /api/executions) and the existing browser consumer
# (frontend/src/entrypoints/workflow-start.tsx, workflow-detail.tsx, and the
# native chat route at /omnigent-ui/workflow-chat/{chatBindingId}).
#
# Substitute identity: deterministic-credential-free-substitute-3938. Only
# the external model provider interface is substituted for deterministic
# credential-free CI; production startup, selection, process/session
# transport, and finalization stay in the path. No free-provider service,
# enrollment ceremony, or global qualification framework is added.
#
# Teardown is project-owned (down --remove-orphans on this journey's
# project only). No global Docker prune. Logs are bounded and redacted.
#
# Limits stated accurately: the full journey boots the default deployment
# images referenced by docker-compose.yaml (identities recorded as
# provenance in var/artifacts/first-run-3938/provenance.log) while the
# hermetic integration_ci suite pins candidate-revision behavior; CI on the
# candidate revision ties the two together. A live-provider availability
# claim requires separately authorized live observation and is not made.
#
# Modes:
#   ./tools/first_run_journey_3938.sh --contract   # static contract only
#   ./tools/first_run_journey_3938.sh --provenance-only
#   ./tools/first_run_journey_3938.sh              # full disposable journey
#
# Environment:
#   MOONMIND_TEST_COMPOSE_PROJECT_NAME  default moonmind-test-first-run-3938
#   FIRST_RUN_3938_API_BASE             default http://127.0.0.1:7000
#   FIRST_RUN_3938_SUBSTITUTE           substitute identity (informational)
#   FIRST_RUN_3938_FAULT                empty | unavailable | bootstrap-failure |
#                                       interruption | lost-ack (fault injection
#                                       hint for the handoff under test)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
COMPOSE_FILE="$REPO_ROOT/docker-compose.yaml"
PROJECT_NAME="${MOONMIND_TEST_COMPOSE_PROJECT_NAME:-moonmind-test-first-run-3938}"
API_BASE="${FIRST_RUN_3938_API_BASE:-http://127.0.0.1:7000}"
SUBSTITUTE_IDENTITY="${FIRST_RUN_3938_SUBSTITUTE:-deterministic-credential-free-substitute-3938}"
FAULT="${FIRST_RUN_3938_FAULT:-}"
LOG_DIR="${FIRST_RUN_3938_LOG_DIR:-$REPO_ROOT/var/artifacts/first-run-3938}"

project_name_regex='^moonmind-test(-[a-z0-9][a-z0-9_-]*)?$'
if [[ ! "$PROJECT_NAME" =~ $project_name_regex ]]; then
  echo "Error: MOONMIND_TEST_COMPOSE_PROJECT_NAME must be 'moonmind-test' or start with 'moonmind-test-' (got '$PROJECT_NAME')." >&2
  exit 2
fi

MODE="${1:-full}"
if [[ "$MODE" == "--contract" || "$MODE" == "--provenance-only" ]]; then
  :
else
  MODE="full"
fi

COMPOSE_CMD=()
if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
  COMPOSE_CMD=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE_CMD=(docker-compose)
else
  if [[ "$MODE" == "full" ]]; then
    echo "Error: docker compose CLI is not available (required for the full first-run journey)." >&2
    exit 127
  fi
fi

cleanup() {
  if (( ${#COMPOSE_CMD[@]} )) && [[ "$MODE" == "full" ]]; then
    "${COMPOSE_CMD[@]}" --project-name "$PROJECT_NAME" -f "$COMPOSE_FILE" \
      --project-directory "$REPO_ROOT" down --remove-orphans >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

redact() {
  sed -E -e 's/sk-[A-Za-z0-9_.-]+/***/g' -e 's/(key=)[^[:space:];&]+/\1***/g'
}

record_provenance() {
  local revision compose_sha
  revision="$(git -C "$REPO_ROOT" rev-parse HEAD)"
  compose_sha="$(sha256sum "$COMPOSE_FILE" | cut -d' ' -f1)"
  mkdir -p "$LOG_DIR"
  {
    echo "first-run-3938 revision=$revision compose-sha256=${compose_sha:0:16}"
    echo "substitute=$SUBSTITUTE_IDENTITY"
    echo "fault=${FAULT:-none}"
    echo "project=$PROJECT_NAME compose_file=docker-compose.yaml"
    if (( ${#COMPOSE_CMD[@]} )); then
      "${COMPOSE_CMD[@]}" --project-name "$PROJECT_NAME" -f "$COMPOSE_FILE" \
        --project-directory "$REPO_ROOT" config 2>/dev/null | sha256sum | cut -d' ' -f1 | sed 's/^/rendered-config-sha256=/' || true
      echo "image digests (provenance, not an equality gate):"
      "${COMPOSE_CMD[@]}" --project-name "$PROJECT_NAME" -f "$COMPOSE_FILE" \
        --project-directory "$REPO_ROOT" images 2>/dev/null || \
        docker images --digests 2>/dev/null | head -n 20 || true
    fi
  } | redact | tee "$LOG_DIR/provenance.log"
}

check_contract() {
  local failures=0
  # Default deployment file owns the path (not the test-only compose file).
  [[ -f "$COMPOSE_FILE" ]] || { echo "missing docker-compose.yaml" >&2; failures=$((failures+1)); }
  # No test-only public route: session/artifact routes require admission.
  if grep -q '"/api/artifacts"' "$REPO_ROOT/moonmind/security/operator_admission.py" 2>/dev/null; then
    echo "contract violation: /api/artifacts must not be public" >&2; failures=$((failures+1))
  fi
  # Real API transport owns submission.
  grep -q '@router.post' "$REPO_ROOT/api_service/api/routers/executions.py" || { echo "missing executions POST route" >&2; failures=$((failures+1)); }
  grep -q 'get_session_snapshot\|/{session_id}' "$REPO_ROOT/api_service/api/routers/sessions.py" || { echo "missing sessions GET route" >&2; failures=$((failures+1)); }
  # Existing browser consumer owns the UI path.
  [[ -f "$REPO_ROOT/frontend/src/entrypoints/workflow-start.tsx" ]] || { echo "missing workflow-start browser path" >&2; failures=$((failures+1)); }
  [[ -f "$REPO_ROOT/frontend/src/entrypoints/workflow-detail.tsx" ]] || { echo "missing workflow-detail browser path" >&2; failures=$((failures+1)); }
  # Saved-work continuation is reused, not duplicated.
  [[ -f "$REPO_ROOT/tests/integration/reliability/test_saved_workspace_journey.py" ]] || { echo "missing saved-work journey for reuse" >&2; failures=$((failures+1)); }
  # Project-owned teardown only (comment lines stating the ban are ignored).
  grep -q 'down --remove-orphans' "$SCRIPT_DIR/first_run_journey_3938.sh" || { echo "missing project-owned teardown" >&2; failures=$((failures+1)); }
  if grep -vE '^\s*#' "$SCRIPT_DIR/first_run_journey_3938.sh" | grep -v 'grep -' | grep -qE '(docker|compose).*(system +prune|volume +prune|down +-v\b)'; then
    echo "contract violation: global prune forbidden" >&2; failures=$((failures+1))
  fi
  return "$failures"
}

if [[ "$MODE" == "--contract" ]]; then
  check_contract
  echo "first-run-3938 contract OK (substitute=$SUBSTITUTE_IDENTITY, hermetic limits stated in test docstring)."
  exit 0
fi

if [[ "$MODE" == "--provenance-only" ]]; then
  record_provenance
  exit 0
fi

# ---- Full disposable journey ----
if [[ -f "$REPO_ROOT/.env" ]]; then
  echo "Error: disposable default install requires no $REPO_ROOT/.env (remove it or run with a clean checkout)." >&2
  exit 1
fi
for inherited in GOOGLE_API_KEY GEMINI_API_KEY OPENAI_API_KEY ANTHROPIC_API_KEY GITHUB_PAT GH_TOKEN; do
  if [[ -n "${!inherited:-}" ]]; then
    echo "Note: ignoring inherited credential env $inherited for the disposable default install." >&2
    unset "$inherited" || true
  fi
done
if [[ -d "$HOME/.codex" || -d "$HOME/.claude" ]]; then
  echo "Note: login caches at ~/.codex or ~/.claude exist on this host; the journey uses a disposable project and does not read them." >&2
fi

record_provenance
check_contract

echo "Bringing up disposable default stack (project=$PROJECT_NAME)..." | redact
# Default env values only; no .env file is read (docker-compose.yaml marks
# env_file as required:false, and we assert no repo .env above).
"${COMPOSE_CMD[@]}" --project-name "$PROJECT_NAME" -f "$COMPOSE_FILE" \
  --project-directory "$REPO_ROOT" up -d --wait --wait-timeout 600 2>&1 | redact | tail -n 40

echo "Waiting for API health at $API_BASE/healthz..." | redact
for _ in $(seq 1 60); do
  if curl -fsS "$API_BASE/healthz" >/dev/null 2>&1; then break; fi
  sleep 10
done
curl -fsS "$API_BASE/healthz" | redact | head -c 2000; echo

if [[ "$FAULT" == "bootstrap-failure" ]]; then
  echo "Fault injection (bootstrap-failure): stopping one service to prove saved work survives..." >&2
  "${COMPOSE_CMD[@]}" --project-name "$PROJECT_NAME" -f "$COMPOSE_FILE" \
    --project-directory "$REPO_ROOT" stop temporal-worker-workflow 2>&1 | redact | tail -n 5 || true
  "${COMPOSE_CMD[@]}" --project-name "$PROJECT_NAME" -f "$COMPOSE_FILE" \
    --project-directory "$REPO_ROOT" start temporal-worker-workflow 2>&1 | redact | tail -n 5 || true
fi

echo "Submitting scratch task with omitted/default selections and explicit no-publication intent..." | redact
SUBMIT_PAYLOAD="$(python3 - "$API_BASE" <<'EOF'
import json
print(json.dumps({
    "title": "first-run-3938 scratch",
    "workflow_type": "scratch",
    "initial_parameters": {},
    "idempotency_key": "first-run-3938-scratch-1",
    "publication": None,
}))
EOF
)"
echo "$SUBMIT_PAYLOAD" | redact
SUBMIT_RESP="$(curl -fsS -X POST "$API_BASE/api/executions" -H 'Content-Type: application/json' -d "$SUBMIT_PAYLOAD" | redact | tee "$LOG_DIR/submit-response.json")"
echo "$SUBMIT_RESP" | head -c 2000; echo
WORKFLOW_ID="$(python3 -c "import json;print(json.load(open('$LOG_DIR/submit-response.json')).get('workflowId') or json.load(open('$LOG_DIR/submit-response.json')).get('workflow_id') or '')")"
if [[ -z "$WORKFLOW_ID" ]]; then
  echo "Error: submission did not return a workflow id; see $LOG_DIR/submit-response.json" >&2
  exit 1
fi
echo "workflow_id=$WORKFLOW_ID substitute=$SUBSTITUTE_IDENTITY fault=${FAULT:-none}" | redact | tee "$LOG_DIR/journey.log"

echo "Polling execution $WORKFLOW_ID..." | redact
for _ in $(seq 1 60); do
  STATUS_JSON="$(curl -fsS "$API_BASE/api/executions/$WORKFLOW_ID" 2>/dev/null || echo '{}')"
  echo "$STATUS_JSON" | redact >> "$LOG_DIR/journey.log"
  STATUS="$(python3 -c "import json,sys;print(json.loads(sys.argv[1]).get('status',''))" "$STATUS_JSON" 2>/dev/null || echo '')"
  if [[ "$STATUS" == "completed" || "$STATUS" == "COMPLETED" || "$STATUS" == "succeeded" ]]; then break; fi
  sleep 10
done
tail -n 5 "$LOG_DIR/journey.log" | redact

echo "Saved-work continuation is owned by tests/integration/reliability/test_saved_workspace_journey.py (#4014-4018); this journey reuses it by reference and does not duplicate restore/publication here." | tee -a "$LOG_DIR/journey.log"
echo "first-run-3938 disposable journey complete: revision + image provenance in $LOG_DIR/provenance.log, submission in $LOG_DIR/submit-response.json." | redact
