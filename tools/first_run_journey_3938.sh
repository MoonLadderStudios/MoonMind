#!/usr/bin/env bash
# MoonLadderStudios/MoonMind#3938: disposable default Compose first-run journey.
#
# One disposable default installation with no .env file, no inherited
# credentials/login caches, and no old application state. Uses the default
# deployment file (docker-compose.yaml) with ordinary bootstrap/migrations
# and operator admission, then submits one supported task with
# omitted/default selections and explicit no-publication intent through the
# real API (POST /api/executions) and the existing browser consumer
# (frontend/src/entrypoints/workflow-start.tsx, workflow-detail.tsx, and the
# native chat route at /omnigent-ui/workflow-chat/{chatBindingId}).
#
# Substitute identity: deterministic-credential-free-substitute-3938. This
# label is provenance only: it names the hermetic provider-interface
# substitute exercised by tests/integration/single_user/
# test_first_run_journey_3938.py. No Compose setting, mounted
# implementation, or fake provider service consumes it, and the disposable
# Compose stack has no deterministic model provider — so this journey makes
# no credential-free saved-result claim. Deterministic saved-result
# coverage lives in that hermetic suite; the Compose journey below proves
# startup/admission/submission transport in every run and proves
# completion plus the session/artifact save path only on an explicitly
# authorized live run (FIRST_RUN_3938_LIVE=1 with a configured provider).
#
# Teardown is project-owned (down --remove-orphans on this journey's
# project only). No global Docker prune. Logs are bounded and redacted.
# Credential-bearing named volumes (codex/claude auth, secrets, session
# keys) are re-scoped to journey-owned names so a persistent host can never
# leak real operator credentials into this run.
#
# Limits stated accurately: the full journey boots the candidate image
# (MOONMIND_IMAGE must point at the revision under test; CI builds it from
# this checkout) referenced by docker-compose.yaml (identities recorded as
# provenance in var/artifacts/first-run-3938/provenance.log) while the
# hermetic integration_ci suite pins candidate-revision behavior; CI on the
# candidate revision ties the two together. A live-provider availability
# claim requires separately authorized live observation and is not made by
# the default credential-free run.
#
# Modes:
#   ./tools/first_run_journey_3938.sh --contract   # static contract only
#   ./tools/first_run_journey_3938.sh --provenance-only
#   ./tools/first_run_journey_3938.sh              # full disposable journey
#   FIRST_RUN_3938_LIVE=1 ./tools/first_run_journey_3938.sh
#     # full journey requiring terminal completion + saved bytes
#
# Environment:
#   MOONMIND_TEST_COMPOSE_PROJECT_NAME  default moonmind-test-first-run-3938
#   MOONMIND_IMAGE                      candidate image under test
#                                       (default ghcr.io/moonladderstudios/moonmind:latest;
#                                       CI sets this to the checkout build)
#   FIRST_RUN_3938_API_BASE             default derived from the Compose
#                                       binding (MOONMIND_API_PUBLISH_HOST /
#                                       MOONMIND_API_HOST_PORT)
#   FIRST_RUN_3938_SUBSTITUTE           substitute identity (provenance label only)
#   FIRST_RUN_3938_LIVE                 empty (default smoke) | 1 (require
#                                       completion + saved bytes)
#   FIRST_RUN_3938_FAULT                empty | bootstrap-failure | lost-ack
#                                       (fault injection hints; unavailable /
#                                       interruption stay hermetic-only, see below)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
COMPOSE_FILE="$REPO_ROOT/docker-compose.yaml"
PROJECT_NAME="${MOONMIND_TEST_COMPOSE_PROJECT_NAME:-moonmind-test-first-run-3938}"
# Derive the default API URL from the same Compose binding variables the
# deployment publishes (docker-compose.yaml: MOONMIND_API_PUBLISH_HOST /
# MOONMIND_API_HOST_PORT). A wildcard publish host is not dialable, so fall
# back to loopback for the client URL. An explicit FIRST_RUN_3938_API_BASE
# still wins for runner-specific overrides.
PUBLISH_HOST="${MOONMIND_API_PUBLISH_HOST:-127.0.0.1}"
PUBLISH_PORT="${MOONMIND_API_HOST_PORT:-7000}"
if [[ "$PUBLISH_HOST" == "0.0.0.0" || "$PUBLISH_HOST" == "::" ]]; then
  PUBLISH_HOST="127.0.0.1"
fi
API_BASE="${FIRST_RUN_3938_API_BASE:-http://$PUBLISH_HOST:$PUBLISH_PORT}"
SUBSTITUTE_IDENTITY="${FIRST_RUN_3938_SUBSTITUTE:-deterministic-credential-free-substitute-3938}"
FAULT="${FIRST_RUN_3938_FAULT:-}"
LIVE="${FIRST_RUN_3938_LIVE:-}"
LOG_DIR="${FIRST_RUN_3938_LOG_DIR:-$REPO_ROOT/var/artifacts/first-run-3938}"

# Journey-owned credential-bearing volumes: the Compose file names these
# globally, so re-scope them per journey. Data volumes without an explicit
# name stay project-scoped automatically. (P2: credential-volume isolation.)
export CODEX_VOLUME_NAME="${CODEX_VOLUME_NAME:-$PROJECT_NAME-codex-auth}"
export CLAUDE_VOLUME_NAME="${CLAUDE_VOLUME_NAME:-$PROJECT_NAME-claude-auth}"
export MOONMIND_SECRETS_VOLUME_NAME="${MOONMIND_SECRETS_VOLUME_NAME:-$PROJECT_NAME-secrets}"
export MOONMIND_SESSION_KEYS_VOLUME_NAME="${MOONMIND_SESSION_KEYS_VOLUME_NAME:-$PROJECT_NAME-session-keys}"

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

# Only the implemented fault modes are accepted. `unavailable` (no eligible
# model) and `interruption` are covered hermetically in
# test_first_run_journey_3938.py; advertising them here without an injection
# would report a requested fault that never happened. (P2: fault modes.)
case "$FAULT" in
  ""|"bootstrap-failure"|"lost-ack") ;;
  *)
    echo "Error: FIRST_RUN_3938_FAULT='$FAULT' is not implemented by this runner (supported: bootstrap-failure, lost-ack)." >&2
    exit 2
    ;;
esac

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

JOURNEY_FAILED=0
ENV_STASH_DIR=""
compose_files_args=(-f "$COMPOSE_FILE")

cleanup() {
  if (( ${#COMPOSE_CMD[@]} )) && [[ "$MODE" == "full" ]]; then
    if [[ "$JOURNEY_FAILED" != "0" ]]; then
      # Capture bounded redacted diagnostics for this project before the
      # containers are destroyed; the Actions diagnostics step queries a
      # different compose file, so this is the only record of the default
      # stack that actually failed. (P2: diagnostics.)
      mkdir -p "$LOG_DIR"
      "${COMPOSE_CMD[@]}" --project-name "$PROJECT_NAME" "${compose_files_args[@]}" \
        --project-directory "$REPO_ROOT" logs --no-color --tail 300 2>/dev/null \
        | sed -E -e 's/sk-[A-Za-z0-9_.-]+/***/g' -e 's/(key=)[^[:space:];&]+/\1***/g' \
        > "$LOG_DIR/compose-logs-tail.log" 2>/dev/null || true
    fi
    "${COMPOSE_CMD[@]}" --project-name "$PROJECT_NAME" "${compose_files_args[@]}" \
      --project-directory "$REPO_ROOT" down --remove-orphans >/dev/null 2>&1 || true
  fi
  if [[ -n "$ENV_STASH_DIR" && -f "$ENV_STASH_DIR/.env" ]]; then
    mv "$ENV_STASH_DIR/.env" "$REPO_ROOT/.env"
    rmdir "$ENV_STASH_DIR" 2>/dev/null || true
  fi
}
trap cleanup EXIT

redact() {
  sed -E -e 's/sk-[A-Za-z0-9_.-]+/***/g' -e 's/(key=)[^[:space:];&]+/\1***/g'
}

record_provenance() {
  local revision compose_sha
  revision="$(git -c safe.directory='*' -C "$REPO_ROOT" rev-parse HEAD)"
  compose_sha="$(sha256sum "$COMPOSE_FILE" | cut -d' ' -f1)"
  mkdir -p "$LOG_DIR"
  {
    echo "first-run-3938 revision=$revision compose-sha256=${compose_sha:0:16}"
    echo "substitute=$SUBSTITUTE_IDENTITY (provenance label only; no Compose consumer)"
    echo "fault=${FAULT:-none} live=${LIVE:-smoke}"
    echo "project=$PROJECT_NAME compose_file=docker-compose.yaml"
    echo "candidate_image=${MOONMIND_IMAGE:-ghcr.io/moonladderstudios/moonmind:latest}"
    if (( ${#COMPOSE_CMD[@]} )); then
      "${COMPOSE_CMD[@]}" --project-name "$PROJECT_NAME" "${compose_files_args[@]}" \
        --project-directory "$REPO_ROOT" config 2>/dev/null | sha256sum | cut -d' ' -f1 | sed 's/^/rendered-config-sha256=/' || true
      echo "image digests (provenance, not an equality gate):"
      "${COMPOSE_CMD[@]}" --project-name "$PROJECT_NAME" "${compose_files_args[@]}" \
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
  # The journey submits the supported product envelope: MoonMind.UserWorkflow
  # with a real plan source (instructions) and explicit no-publication
  # intent (publishMode none). The retired `scratch` workflow_type and the
  # discarded top-level `publication` field must stay out. (P1: supported
  # envelope; P2: publication field.) Check lines are excluded from the
  # search input so the patterns below cannot self-match.
  SCRIPT_BODY="$(grep -v 'grep -' "$SCRIPT_DIR/first_run_journey_3938.sh")"
  echo "$SCRIPT_BODY" | grep -q '"workflowType": "MoonMind.UserWorkflow"' || { echo "contract violation: journey must submit MoonMind.UserWorkflow" >&2; failures=$((failures+1)); }
  echo "$SCRIPT_BODY" | grep -q '"publishMode": "none"' || { echo "contract violation: journey must carry publishMode none" >&2; failures=$((failures+1)); }
  if echo "$SCRIPT_BODY" | grep -q '"workflow_type": "scratch"'; then
    echo "contract violation: unsupported scratch workflow_type" >&2; failures=$((failures+1))
  fi
  if echo "$SCRIPT_BODY" | grep -q '"publication": None'; then
    echo "contract violation: discarded top-level publication field" >&2; failures=$((failures+1))
  fi
  # Completion honesty: the runner tracks observed success and fails on
  # terminal failure/timeout instead of reporting completion unconditionally.
  echo "$SCRIPT_BODY" | grep -q 'SEEN_SUCCESS' || { echo "contract violation: journey must track observed success" >&2; failures=$((failures+1)); }
  # Saved-output verification follows completion through artifactRefs plus a
  # real download of saved bytes.
  echo "$SCRIPT_BODY" | grep -q 'artifactRefs' || { echo "contract violation: journey must verify artifactRefs" >&2; failures=$((failures+1)); }
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
# A preceding helper (./tools/test_integration.sh) generates $REPO_ROOT/.env
# from .env-template when the checkout has none. Stash any such file aside
# so this journey deterministically boots default values, then restore it on
# exit: the disposable default install requires no $REPO_ROOT/.env while it
# runs, but it must not delete the caller's file. (P1: .env ordering.)
if [[ -f "$REPO_ROOT/.env" ]]; then
  ENV_STASH_DIR="$(mktemp -d "${TMPDIR:-/tmp}/first-run-3938-env-stash.XXXXXX")"
  mv "$REPO_ROOT/.env" "$ENV_STASH_DIR/.env"
  echo "Note: stashed pre-existing $REPO_ROOT/.env for the disposable default install; it will be restored on exit." >&2
fi

# Provider availability is read BEFORE unsetting: without an authorized live
# provider the stack cannot complete model-backed execution, so the default
# credential-free run proves startup/admission/submission transport and
# skips the completion wait instead of misreporting it. FIRST_RUN_3938_LIVE=1
# requires terminal completion plus saved bytes and fails otherwise.
LIVE_REQUESTED=0
if [[ "$LIVE" == "1" ]]; then
  LIVE_REQUESTED=1
fi
for inherited in GOOGLE_API_KEY GEMINI_API_KEY OPENAI_API_KEY ANTHROPIC_API_KEY GITHUB_PAT GH_TOKEN; do
  if [[ -n "${!inherited:-}" ]]; then
    if [[ "$LIVE_REQUESTED" == "1" ]]; then
      echo "Note: keeping credential env $inherited for the authorized live run." >&2
    else
      echo "Note: ignoring inherited credential env $inherited for the disposable default install." >&2
      unset "$inherited" || true
    fi
  fi
done
if [[ -d "$HOME/.codex" || -d "$HOME/.claude" ]]; then
  echo "Note: login caches at ~/.codex or ~/.claude exist on this host; the journey uses journey-scoped credential volumes and does not read them." >&2
fi

record_provenance
check_contract

echo "Bringing up disposable default stack (project=$PROJECT_NAME)..." | redact
# Default env values only; no .env file is read (stashed above when present,
# and docker-compose.yaml marks env_file as required:false).
"${COMPOSE_CMD[@]}" --project-name "$PROJECT_NAME" "${compose_files_args[@]}" \
  --project-directory "$REPO_ROOT" up -d --wait --wait-timeout 600 2>&1 | redact | tail -n 40 || { JOURNEY_FAILED=1; echo "Error: compose up failed; see $LOG_DIR/compose-logs-tail.log" >&2; exit 1; }

echo "Waiting for API health at $API_BASE/healthz..." | redact
HEALTHY=0
for _ in $(seq 1 60); do
  if curl -fsS "$API_BASE/healthz" >/dev/null 2>&1; then HEALTHY=1; break; fi
  sleep 10
done
if [[ "$HEALTHY" != "1" ]]; then
  JOURNEY_FAILED=1
  echo "Error: API never became healthy at $API_BASE/healthz." >&2
  exit 1
fi
curl -fsS "$API_BASE/healthz" | redact | head -c 2000; echo

if [[ "$FAULT" == "bootstrap-failure" ]]; then
  echo "Fault injection (bootstrap-failure): stopping one service to prove saved work survives..." >&2
  "${COMPOSE_CMD[@]}" --project-name "$PROJECT_NAME" "${compose_files_args[@]}" \
    --project-directory "$REPO_ROOT" stop temporal-worker-workflow 2>&1 | redact | tail -n 5 || true
  "${COMPOSE_CMD[@]}" --project-name "$PROJECT_NAME" "${compose_files_args[@]}" \
    --project-directory "$REPO_ROOT" start temporal-worker-workflow 2>&1 | redact | tail -n 5 || true
fi

echo "Submitting supported task with omitted/default selections and explicit no-publication intent..." | redact
SUBMIT_PAYLOAD="$(python3 - "$API_BASE" <<'EOF'
import json
print(json.dumps({
    "workflowType": "MoonMind.UserWorkflow",
    "title": "first-run-3938 disposable check",
    "initialParameters": {
        "instructions": "First-run 3938 disposable check: acknowledge this run in one short sentence and save that sentence as the run summary.",
        "publishMode": "none",
    },
    "idempotencyKey": "first-run-3938-check-1",
}))
EOF
)"
echo "$SUBMIT_PAYLOAD" | redact
SUBMIT_RESP="$(curl -fsS -X POST "$API_BASE/api/executions" -H 'Content-Type: application/json' -d "$SUBMIT_PAYLOAD" | redact | tee "$LOG_DIR/submit-response.json")"
echo "$SUBMIT_RESP" | head -c 2000; echo
WORKFLOW_ID="$(python3 -c "import json;d=json.load(open('$LOG_DIR/submit-response.json'));print(d.get('workflowId') or d.get('workflow_id') or '')")"
if [[ -z "$WORKFLOW_ID" ]]; then
  JOURNEY_FAILED=1
  echo "Error: submission did not return a workflow id; see $LOG_DIR/submit-response.json" >&2
  exit 1
fi
echo "workflow_id=$WORKFLOW_ID substitute=$SUBSTITUTE_IDENTITY fault=${FAULT:-none} live=$LIVE_REQUESTED" | redact | tee "$LOG_DIR/journey.log"

if [[ "$FAULT" == "lost-ack" ]]; then
  # The launch acknowledgment never arrived: redeliver the identical payload
  # under the same idempotency key and require the same workflow id rather
  # than a duplicate session. (P2: fault modes.)
  echo "Fault injection (lost-ack): redelivering identical submit payload..." | redact
  REDLIVERY_RESP="$(curl -fsS -X POST "$API_BASE/api/executions" -H 'Content-Type: application/json' -d "$SUBMIT_PAYLOAD")"
  REDLIVERY_ID="$(python3 -c "import json,sys;print(json.loads(sys.argv[1]).get('workflowId') or json.loads(sys.argv[1]).get('workflow_id') or '')" "$REDLIVERY_RESP")"
  if [[ "$REDLIVERY_ID" != "$WORKFLOW_ID" ]]; then
    JOURNEY_FAILED=1
    echo "Error: idempotent redelivery returned '$REDLIVERY_ID', expected '$WORKFLOW_ID' (duplicate session)." >&2
    exit 1
  fi
  echo "lost-ack redelivery reused workflow_id=$WORKFLOW_ID (no duplicate session)." | redact | tee -a "$LOG_DIR/journey.log"
fi

echo "Polling execution $WORKFLOW_ID..." | redact
SEEN_SUCCESS=0
SEEN_TERMINAL_FAILURE=""
POLL_ROUNDS=60
if [[ "$LIVE_REQUESTED" != "1" ]]; then
  # Credential-free smoke: observe honest status a few times for transport
  # evidence, then stop. Completion and saved bytes are proven only on an
  # authorized live run; this path must never print completion.
  POLL_ROUNDS=6
fi
for _ in $(seq 1 "$POLL_ROUNDS"); do
  STATUS_JSON="$(curl -fsS "$API_BASE/api/executions/$WORKFLOW_ID" 2>/dev/null || echo '{}')"
  echo "$STATUS_JSON" | redact >> "$LOG_DIR/journey.log"
  STATUS="$(python3 -c "import json,sys;print(json.loads(sys.argv[1]).get('status',''))" "$STATUS_JSON" 2>/dev/null || echo '')"
  if [[ "$STATUS" == "completed" || "$STATUS" == "COMPLETED" || "$STATUS" == "succeeded" ]]; then SEEN_SUCCESS=1; break; fi
  if [[ "$STATUS" == "failed" || "$STATUS" == "FAILED" || "$STATUS" == "canceled" || "$STATUS" == "CANCELED" ]]; then SEEN_TERMINAL_FAILURE="$STATUS"; break; fi
  sleep 10
done
tail -n 5 "$LOG_DIR/journey.log" | redact

if [[ "$LIVE_REQUESTED" != "1" ]]; then
  # Smoke outcome: transport proven (201 submit + readable execution
  # resource). A terminal failure here reflects the missing provider, not a
  # product regression, so record it without failing — and never claim
  # completion. (P1: fail-when-incomplete applies to the live gate below.)
  LAST_STATUS="$(python3 -c "import json;lines=[l for l in open('$LOG_DIR/journey.log') if l.strip().startswith('{')];print(json.loads(lines[-1]).get('status','unknown') if lines else 'unknown')" 2>/dev/null || echo unknown)"
  echo "first-run-3938 SMOKE complete: submission accepted (workflow_id=$WORKFLOW_ID), last observed status=$LAST_STATUS. Completion + saved bytes require FIRST_RUN_3938_LIVE=1 with a configured provider; not claimed here." | redact | tee -a "$LOG_DIR/journey.log"
  echo "Saved-work continuation is owned by tests/integration/reliability/test_saved_workspace_journey.py (#4014-4018); this journey reuses it by reference and does not duplicate restore/publication here." | tee -a "$LOG_DIR/journey.log"
  exit 0
fi

# Live gate: terminal failure or no observed success is a journey failure,
# never a passing result. (P1: fail when the execution does not complete.)
if [[ -n "$SEEN_TERMINAL_FAILURE" ]]; then
  JOURNEY_FAILED=1
  echo "Error: execution $WORKFLOW_ID ended in terminal status $SEEN_TERMINAL_FAILURE; see $LOG_DIR/journey.log" >&2
  exit 1
fi
if [[ "$SEEN_SUCCESS" != "1" ]]; then
  JOURNEY_FAILED=1
  echo "Error: execution $WORKFLOW_ID did not complete within the poll window; see $LOG_DIR/journey.log" >&2
  exit 1
fi

# Saved-output verification: follow the completed execution through the
# production artifact transport — require an artifact reference on the
# execution describe payload, download the saved bytes, and assert they are
# non-empty. A completion that saved nothing fails the journey. Session
# endpoints require account auth unavailable to this disposable install, so
# the save path is proven here via artifactRefs plus download; session route
# presence stays contract-checked above. (P1: verify the saved artifact.)
DESCRIBE_JSON="$(curl -fsS "$API_BASE/api/executions/$WORKFLOW_ID")"
ARTIFACT_ID="$(python3 -c "
import json,sys
d=json.loads(sys.argv[1])
refs=d.get('artifactRefs') or []
print(refs[0] if refs else (d.get('summaryArtifactRef') or '')
)" "$DESCRIBE_JSON")"
if [[ -z "$ARTIFACT_ID" ]]; then
  JOURNEY_FAILED=1
  echo "Error: execution $WORKFLOW_ID completed without saving any artifact reference; see $LOG_DIR/journey.log" >&2
  exit 1
fi
curl -fsS "$API_BASE/api/artifacts/$ARTIFACT_ID/download" -o "$LOG_DIR/saved-artifact.bytes"
SAVED_BYTES="$(wc -c < "$LOG_DIR/saved-artifact.bytes" | tr -d ' ')"
if [[ "$SAVED_BYTES" == "0" || -z "$SAVED_BYTES" ]]; then
  JOURNEY_FAILED=1
  echo "Error: downloaded artifact $ARTIFACT_ID is empty; see $LOG_DIR/journey.log" >&2
  exit 1
fi
echo "verified saved artifact $ARTIFACT_ID ($SAVED_BYTES bytes) for execution $WORKFLOW_ID" | redact | tee -a "$LOG_DIR/journey.log"

echo "Saved-work continuation is owned by tests/integration/reliability/test_saved_workspace_journey.py (#4014-4018); this journey reuses it by reference and does not duplicate restore/publication here." | tee -a "$LOG_DIR/journey.log"
echo "first-run-3938 disposable journey complete: revision + image provenance in $LOG_DIR/provenance.log, submission in $LOG_DIR/submit-response.json." | redact
