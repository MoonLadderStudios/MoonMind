#!/usr/bin/env bash
# Disposable default Compose product journeys (MoonLadderStudios/MoonMind#3938,
# MoonLadderStudios/MoonMind#4356).
#
# Boots the default deployment file (docker-compose.yaml) in an isolated
# moonmind-test-* project with no .env, no inherited provider credentials,
# no login caches, and no seeded person, then drives the real application
# through its published API and compiled dashboard:
#
#   ./tools/first_run_journey_3938.sh            # fresh instance
#   ./tools/first_run_journey_3938.sh --upgrade  # eligible upgrade
#
# Fresh: tools/single_user_journey_checks.py reads the settings/preset
# catalogs, submits one task with the dashboard's default selections (and
# redelivers it), observes it running, attaches an artifact, and dispatches
# a recurring definition; the browser opens the built dashboard on that work;
# the workflow worker restarts; both executions are canceled and must reach
# the canceled state; a synthetic credential is bound through an instance
# setting; everything saved is read back; the binding is released.
#
# Upgrade: the same work is saved and canceled on the previously published
# image (FIRST_RUN_3938_UPGRADE_FROM), the stack is recreated on the
# candidate image (MOONMIND_IMAGE) against the same volumes, the saved work
# must be readable through the API and dashboard, and a new journey runs on
# the upgraded instance.
#
# Any failed, missing, or unobserved step exits non-zero. There is no smoke
# mode. Completing a model-backed task needs a provider credential, which
# this credential-free run does not have; it cancels its work instead.
#
# Teardown is project-owned (down --remove-orphans on this project only; no
# global prune). Credential-bearing named volumes are re-scoped to the
# project so a persistent host never leaks operator credentials into the run.
# Logs are bounded and redacted.
#
# Requirements: docker compose, python3, node with the repository's
# Playwright dependency (npm ci && npx playwright install chromium).
#
# Environment:
#   MOONMIND_TEST_COMPOSE_PROJECT_NAME  default moonmind-test-first-run-3938
#                                       (moonmind-test-upgrade-4356 with --upgrade)
#   MOONMIND_IMAGE                      candidate image under test
#                                       (default ghcr.io/moonladderstudios/moonmind:latest;
#                                       CI sets this to the checkout build)
#   FIRST_RUN_3938_UPGRADE_FROM         image the upgrade starts from
#                                       (default ghcr.io/moonladderstudios/moonmind:latest)
#   FIRST_RUN_3938_API_BASE             default derived from the Compose
#                                       binding (MOONMIND_API_PUBLISH_HOST /
#                                       MOONMIND_API_HOST_PORT)
#   FIRST_RUN_3938_LOG_DIR              default var/artifacts/first-run-3938
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
COMPOSE_FILE="$REPO_ROOT/docker-compose.yaml"

MODE="fresh"
case "${1:-}" in
  "") ;;
  --upgrade) MODE="upgrade" ;;
  *)
    echo "Usage: $0 [--upgrade]" >&2
    exit 2
    ;;
esac

if [[ "$MODE" == "upgrade" ]]; then
  PROJECT_NAME="${MOONMIND_TEST_COMPOSE_PROJECT_NAME:-moonmind-test-upgrade-4356}"
else
  PROJECT_NAME="${MOONMIND_TEST_COMPOSE_PROJECT_NAME:-moonmind-test-first-run-3938}"
fi
project_name_regex='^moonmind-test(-[a-z0-9][a-z0-9_-]*)?$'
if [[ ! "$PROJECT_NAME" =~ $project_name_regex ]]; then
  echo "Error: MOONMIND_TEST_COMPOSE_PROJECT_NAME must be 'moonmind-test' or start with 'moonmind-test-' (got '$PROJECT_NAME')." >&2
  exit 2
fi

# Derive the API URL from the binding the deployment publishes. A wildcard
# publish host is not dialable, so the client uses loopback.
PUBLISH_HOST="${MOONMIND_API_PUBLISH_HOST:-127.0.0.1}"
PUBLISH_PORT="${MOONMIND_API_HOST_PORT:-7000}"
if [[ "$PUBLISH_HOST" == "0.0.0.0" || "$PUBLISH_HOST" == "::" ]]; then
  PUBLISH_HOST="127.0.0.1"
fi
API_BASE="${FIRST_RUN_3938_API_BASE:-http://$PUBLISH_HOST:$PUBLISH_PORT}"
CANDIDATE_IMAGE="${MOONMIND_IMAGE:-ghcr.io/moonladderstudios/moonmind:latest}"
UPGRADE_FROM="${FIRST_RUN_3938_UPGRADE_FROM:-ghcr.io/moonladderstudios/moonmind:latest}"
LOG_DIR="${FIRST_RUN_3938_LOG_DIR:-$REPO_ROOT/var/artifacts/first-run-3938}/$MODE"

export CODEX_VOLUME_NAME="${CODEX_VOLUME_NAME:-$PROJECT_NAME-codex-auth}"
export CLAUDE_VOLUME_NAME="${CLAUDE_VOLUME_NAME:-$PROJECT_NAME-claude-auth}"
export MOONMIND_SECRETS_VOLUME_NAME="${MOONMIND_SECRETS_VOLUME_NAME:-$PROJECT_NAME-secrets}"
export MOONMIND_SESSION_KEYS_VOLUME_NAME="${MOONMIND_SESSION_KEYS_VOLUME_NAME:-$PROJECT_NAME-session-keys}"

if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
  COMPOSE_CMD=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE_CMD=(docker-compose)
else
  echo "Error: docker compose CLI is not available." >&2
  exit 127
fi
for tool in python3 node curl; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    echo "Error: $tool is required for the journey." >&2
    exit 127
  fi
done

compose() {
  "${COMPOSE_CMD[@]}" --project-name "$PROJECT_NAME" -f "$COMPOSE_FILE" \
    --project-directory "$REPO_ROOT" "$@"
}

redact() {
  sed -E -e 's/sk-[A-Za-z0-9_.-]+/***/g' -e 's/ghp_[A-Za-z0-9_]+/***/g' \
    -e 's/(key=)[^[:space:];&]+/\1***/g'
}

JOURNEY_FAILED=1
ENV_STASH_DIR=""
cleanup() {
  if [[ "$JOURNEY_FAILED" != "0" ]]; then
    mkdir -p "$LOG_DIR"
    compose logs --no-color --tail 300 2>/dev/null | redact \
      > "$LOG_DIR/compose-logs-tail.log" 2>/dev/null || true
    compose ps -a 2>/dev/null > "$LOG_DIR/compose-ps.log" || true
    echo "Journey failed; bounded redacted logs are in $LOG_DIR." >&2
  fi
  compose down --remove-orphans >/dev/null 2>&1 || true
  if [[ -n "$ENV_STASH_DIR" && -f "$ENV_STASH_DIR/.env" ]]; then
    mv "$ENV_STASH_DIR/.env" "$REPO_ROOT/.env"
    rmdir "$ENV_STASH_DIR" 2>/dev/null || true
  fi
}
trap cleanup EXIT

# The default install reads no .env. A preceding helper
# (./tools/test_integration.sh) may have generated one; stash it for the run
# and restore it on exit without deleting the caller's file.
if [[ -f "$REPO_ROOT/.env" ]]; then
  ENV_STASH_DIR="$(mktemp -d "${TMPDIR:-/tmp}/first-run-3938-env-stash.XXXXXX")"
  mv "$REPO_ROOT/.env" "$ENV_STASH_DIR/.env"
  echo "Note: stashed $REPO_ROOT/.env for the disposable default install; it is restored on exit." >&2
fi
for inherited in GOOGLE_API_KEY GEMINI_API_KEY OPENAI_API_KEY ANTHROPIC_API_KEY \
  OPENROUTER_API_KEY OPENCODE_API_KEY GITHUB_PAT GH_TOKEN GITHUB_TOKEN; do
  unset "$inherited" || true
done

mkdir -p "$LOG_DIR"
STATE_DIR="$LOG_DIR/state"

record_provenance() {
  {
    echo "journey=$MODE project=$PROJECT_NAME compose_file=docker-compose.yaml"
    echo "revision=$(git -c safe.directory='*' -C "$REPO_ROOT" rev-parse HEAD 2>/dev/null || echo unknown)"
    echo "moonmind_image=$MOONMIND_IMAGE"
    echo "image identities (provenance, not an equality gate):"
    compose images 2>/dev/null || true
  } | redact | tee -a "$LOG_DIR/provenance.log"
}

bring_up() {
  echo "Bringing up $PROJECT_NAME on $MOONMIND_IMAGE..." | redact
  if ! compose up -d --wait --wait-timeout 600 2>&1 | redact | tail -n 40; then
    echo "Error: compose up failed for $MOONMIND_IMAGE." >&2
    exit 1
  fi
  local healthy=0
  for _ in $(seq 1 60); do
    if curl -fsS "$API_BASE/healthz" > "$LOG_DIR/healthz.json" 2>/dev/null; then
      healthy=1
      break
    fi
    sleep 5
  done
  if [[ "$healthy" != "1" ]]; then
    echo "Error: API never became healthy at $API_BASE/healthz." >&2
    exit 1
  fi
  python3 - "$LOG_DIR/healthz.json" <<'EOF'
import json, sys
health = json.load(open(sys.argv[1]))
problems = [
    key for key, bad in (
        ("status", health.get("status") != "ok"),
        ("db", health.get("db") != "connected"),
        ("migration_required", bool(health.get("migration_required"))),
        ("setup_required", bool(health.get("setup_required"))),
    ) if bad
]
if problems:
    sys.exit(f"Error: /healthz reports {problems}: {health}")
print(f"healthz ok (uptime {health.get('uptime_seconds')}s)")
EOF
  record_provenance
}

checks() {
  python3 "$SCRIPT_DIR/single_user_journey_checks.py" "$1" \
    --api-base "$API_BASE" --state-file "$STATE_DIR/$2.json" --label "$2" \
    2>&1 | redact
}

browser() {
  node "$SCRIPT_DIR/single_user_journey_browser.mjs" "$API_BASE" "$STATE_DIR/$1.json"
}

restart_workflow_worker() {
  echo "Restarting the workflow worker with work in flight..."
  compose restart temporal-worker-workflow 2>&1 | redact | tail -n 5
  compose up -d --wait --wait-timeout 300 temporal-worker-workflow 2>&1 | redact | tail -n 5
}

fresh_journey() {
  local label="$1"
  checks populate "$label"
  browser "$label"
  restart_workflow_worker
  checks cancel "$label"
  checks credential "$label"
  checks verify "$label"
  checks release "$label"
}

if [[ "$MODE" == "fresh" ]]; then
  export MOONMIND_IMAGE="$CANDIDATE_IMAGE"
  bring_up
  fresh_journey fresh
else
  export MOONMIND_IMAGE="$UPGRADE_FROM"
  bring_up
  checks populate before-upgrade
  checks cancel before-upgrade
  checks credential before-upgrade
  checks verify before-upgrade

  echo "Upgrading $PROJECT_NAME from $UPGRADE_FROM to $CANDIDATE_IMAGE..." | redact
  export MOONMIND_IMAGE="$CANDIDATE_IMAGE"
  bring_up
  checks verify before-upgrade
  browser before-upgrade
  checks release before-upgrade
  fresh_journey after-upgrade
fi

JOURNEY_FAILED=0
echo "Single-user $MODE journey passed on $CANDIDATE_IMAGE; evidence in $LOG_DIR." | redact
