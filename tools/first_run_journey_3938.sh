#!/usr/bin/env bash
# Disposable default Compose product journeys (MoonLadderStudios/MoonMind#3938,
# MoonLadderStudios/MoonMind#4356, MoonLadderStudios/MoonMind#4502).
#
# Boots the default deployment file (docker-compose.yaml) in an isolated
# moonmind-test-* project with no .env, no inherited provider credentials,
# no login caches, and no seeded person, then drives the real application
# through its published API and compiled dashboard:
#
#   ./tools/first_run_journey_3938.sh               # fresh instance
#   ./tools/first_run_journey_3938.sh --upgrade     # eligible upgrade
#   ./tools/first_run_journey_3938.sh --controller  # Settings Operations
#                                                   # through the controller
#
# Fresh: tools/single_user_journey_checks.py reads the settings/preset
# catalogs, submits one task with the dashboard's default repository as a
# deferred start (and redelivers it), attaches an artifact, dispatches a
# recurring definition, and saves a preset; the vector_free phase then
# proves the running candidate is vector-free at its live boundaries
# (MoonLadderStudios/MoonMind#4114: live /healthz and /openapi.json carry
# no vector backend, and one task-envelope request with an explicit
# retired vector requirement is rejected with 422 and creates no
# execution); the browser opens the built
# dashboard on that work; the workflow worker restarts; the browser cancels
# the deferred task from its workflow page and the API must report it
# canceled; a synthetic credential is bound through an instance setting;
# everything saved is read back; the binding is released.
#
# Upgrade: an account-era release from before the guarded single-user
# conversion (FIRST_RUN_3938_UPGRADE_FROM_REVISION, its published image and
# its own docker-compose.yaml) is deployed from a git worktree, and the same
# work is saved and canceled there. The worktree is then checked out at the
# candidate revision in place, as an operator's update would, and the stack
# is recreated on the candidate image (MOONMIND_IMAGE) against the same
# volumes and state directories. The API startup log must show the guarded
# conversion classifying the retained data as one eligible operator. The
# saved work, settings, credential binding, and preset version must be
# readable through the API and dashboard, and a new journey runs on the
# upgraded instance.
#
# Controller: the candidate is deployed from a disposable worktree (so its
# controller state never lands in the operator's checkout) with its own
# controller link network. Before a controller exists, a Settings Operations
# update is refused with the host repair route and no workflow-backed update
# exists. The real deploy/controller bootstrap then
# installs the controller state and the real controller server starts beside
# the stack. The compiled dashboard submits an update to it and sees the
# controller's operation with its requested target, observed installed state,
# original error, logs, and Retry. The API is replaced; a fresh page
# reconnects to the same operation without submitting again and retries it.
# The API must then show one controller-owned operation whose retry kept its
# first failure and no workflow-backed update. The controller has no Docker
# daemon, so every attempt fails at image staging and no stack is mutated.
#
# Any failed, missing, or unobserved step exits non-zero. There is no smoke
# mode. A model-backed step needs a provider credential, which this
# credential-free run does not have, so no step runs to completion: the task
# is deferred and canceled, and the recurring run is observed as dispatched.
# The conversion outcome is printed either way. It publishes only when every
# retained subsystem has a registered transform; until then it refuses
# without mutation and the journey reports it as not published.
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
#                                       (moonmind-test-upgrade-4356 with --upgrade,
#                                       moonmind-test-controller-4502 with --controller)
#   MOONMIND_IMAGE                      candidate image under test
#                                       (default ghcr.io/moonladderstudios/moonmind:latest;
#                                       CI sets this to the checkout build)
#   FIRST_RUN_3938_UPGRADE_FROM_REVISION
#                                       release the upgrade starts from
#                                       (default 2c67aeb959482d17a72d75dea7766e1a8ee8adf0,
#                                       the last published main build before
#                                       the #4346 guarded conversion)
#   FIRST_RUN_3938_UPGRADE_FROM         image the upgrade starts from (default
#                                       ghcr.io/moonladderstudios/moonmind:sha-<revision>)
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
  --controller) MODE="controller" ;;
  *)
    echo "Usage: $0 [--upgrade|--controller]" >&2
    exit 2
    ;;
esac

if [[ "$MODE" == "upgrade" ]]; then
  PROJECT_NAME="${MOONMIND_TEST_COMPOSE_PROJECT_NAME:-moonmind-test-upgrade-4356}"
elif [[ "$MODE" == "controller" ]]; then
  PROJECT_NAME="${MOONMIND_TEST_COMPOSE_PROJECT_NAME:-moonmind-test-controller-4502}"
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
UPGRADE_FROM_REVISION="${FIRST_RUN_3938_UPGRADE_FROM_REVISION:-2c67aeb959482d17a72d75dea7766e1a8ee8adf0}"
UPGRADE_FROM="${FIRST_RUN_3938_UPGRADE_FROM:-ghcr.io/moonladderstudios/moonmind:sha-$UPGRADE_FROM_REVISION}"
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

# The deployment directory. The fresh journey deploys this checkout; the
# upgrade journey deploys a worktree that starts at the old release.
DEPLOY_DIR="$REPO_ROOT"
COMPOSE_OVERRIDES=()

compose() {
  "${COMPOSE_CMD[@]}" --project-name "$PROJECT_NAME" \
    -f "$DEPLOY_DIR/docker-compose.yaml" ${COMPOSE_OVERRIDES[@]+"${COMPOSE_OVERRIDES[@]}"} \
    --project-directory "$DEPLOY_DIR" "$@"
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
  if [[ "$DEPLOY_DIR" != "$REPO_ROOT" ]]; then
    # Containers may leave root-owned state behind; remove what this user
    # can and let git forget the worktree.
    git -C "$REPO_ROOT" worktree remove --force "$DEPLOY_DIR" >/dev/null 2>&1 \
      || rm -rf "$DEPLOY_DIR" 2>/dev/null || true
    git -C "$REPO_ROOT" worktree prune >/dev/null 2>&1 || true
  fi
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
    echo "revision=$(git -c safe.directory='*' -C "$DEPLOY_DIR" rev-parse HEAD 2>/dev/null || echo unknown)"
    echo "moonmind_image=$MOONMIND_IMAGE"
    echo "image identities (provenance, not an equality gate):"
    compose images 2>/dev/null || true
  } | redact | tee -a "$LOG_DIR/provenance.log"
}

bring_up() {
  local attempts="${1:-1}" attempt=1
  echo "Bringing up $PROJECT_NAME on $MOONMIND_IMAGE..." | redact
  until compose up -d --wait --wait-timeout 600 2>&1 | redact | tail -n 40; do
    if (( attempt >= attempts )); then
      echo "Error: compose up failed for $MOONMIND_IMAGE." >&2
      exit 1
    fi
    compose ps 2>&1 | redact > "$LOG_DIR/compose-ps-wait-$attempt.log"
    echo "compose up --wait reported an unhealthy service on $MOONMIND_IMAGE; waiting again ($attempt/$attempts)..." | redact
    attempt=$((attempt + 1))
  done
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
    "${@:3}" 2>&1 | redact
}

browser() {
  node "$SCRIPT_DIR/single_user_journey_browser.mjs" "$API_BASE" "$STATE_DIR/$1.json" "${2:-view}"
}

# MoonLadderStudios/MoonMind#4502: deploy committed HEAD from a disposable
# worktree, so the controller state this journey installs (bearer secret,
# identity, operation records under deploy/state/controller) never lands in
# the operator's checkout, and give the controller link its own network so
# the journey never joins a live deployment's.
prepare_controller_source() {
  CANDIDATE_REVISION="$(git -C "$REPO_ROOT" rev-parse HEAD)"
  if [[ -n "$(git -C "$REPO_ROOT" status --porcelain --untracked-files=no)" ]]; then
    echo "Note: the controller journey deploys committed HEAD ($CANDIDATE_REVISION); uncommitted changes are not deployed." >&2
  fi
  DEPLOY_DIR="$(mktemp -d "${TMPDIR:-/tmp}/moonmind-controller-4502.XXXXXX")"
  git -C "$REPO_ROOT" worktree add --detach --quiet "$DEPLOY_DIR" "$CANDIDATE_REVISION"
  export MOONMIND_DEPLOYMENT_CONTROLLER_NETWORK="${PROJECT_NAME}_deployment-controller-network"
}

# Before a controller exists Settings Operations refuses updates with the
# host repair route and never constructs a workflow-backed updater.
require_controller_absent() {
  checks controller_absent "$1"
}

# The real deploy/controller bootstrap writes the deployment-owned state and
# the real controller server runs beside the stack under its link alias. Its
# own image is not published yet (MoonLadderStudios/MoonMind#4500), so it
# runs from the candidate image, which carries Python and the Docker CLI,
# pinned by the image ID bootstrap records. It has no Docker daemon: it
# derives its Compose target and records each attempt, image staging fails,
# and no stack can be mutated.
install_controller() {
  local state_dir="$DEPLOY_DIR/deploy/state/controller" image_id port
  image_id="$(docker image inspect "$MOONMIND_IMAGE" --format '{{.Id}}')"
  python3 "$DEPLOY_DIR/deploy/controller/bootstrap.py" install --repo "$DEPLOY_DIR" \
    --image "${MOONMIND_IMAGE%%[:@]*}@$image_id" \
    --target-network "$MOONMIND_DEPLOYMENT_CONTROLLER_NETWORK" \
    --target-project "$PROJECT_NAME" 2>&1 | redact
  port="$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["port"])' \
    "$state_dir/controller-identity.json")"
  mkdir -p "$STATE_DIR"
  cat > "$STATE_DIR/controller-override.yaml" <<EOF
services:
  moonmind-controller-journey:
    image: "$MOONMIND_IMAGE"
    entrypoint: ["python", "/opt/moonmind-controller/server.py", "--no-legacy-probe"]
    environment:
      MOONMIND_CONTROLLER_MANAGED: "1"
      MOONMIND_CONTROLLER_STATE_DIR: /var/lib/moonmind-controller
      MOONMIND_CONTROLLER_PORT: "$port"
      MOONMIND_CONTROLLER_TARGET_REPO: "$DEPLOY_DIR"
      DOCKER_HOST: unix:///var/run/moonmind-journey-has-no-docker.sock
    volumes:
      - ./deploy/controller:/opt/moonmind-controller:ro
      - ./deploy/state/controller:/var/lib/moonmind-controller
      - .:$DEPLOY_DIR:ro
    networks:
      deployment-controller-network:
        aliases:
          - moonmind-controller
    restart: "no"
EOF
  COMPOSE_OVERRIDES=(-f "$STATE_DIR/controller-override.yaml")
  compose up -d moonmind-controller-journey 2>&1 | redact | tail -n 5
  checks controller_ready "$1"
}

replace_api() {
  echo "Replacing the API container with the controller operation recorded..."
  compose up -d --no-deps --force-recreate --wait --wait-timeout 300 api 2>&1 | redact | tail -n 5
}

controller_journey() {
  local label="$1"
  require_controller_absent "$label"
  install_controller "$label"
  browser "$label" controller-submit
  replace_api
  browser "$label" controller-reconnect
  checks controller "$label"
}

cancel_from_dashboard() {
  browser "$1" cancel
  checks canceled "$1"
}

# Deploy the old release from its own tree so its docker-compose.yaml and
# bind-mounted files match its image. CI checks out one commit, so fetch the
# release revision when it is missing.
prepare_upgrade_source() {
  if ! git -C "$REPO_ROOT" cat-file -e "$UPGRADE_FROM_REVISION^{commit}" 2>/dev/null; then
    git -C "$REPO_ROOT" fetch --no-tags --depth 1 origin "$UPGRADE_FROM_REVISION"
  fi
  CANDIDATE_REVISION="$(git -C "$REPO_ROOT" rev-parse HEAD)"
  if [[ -n "$(git -C "$REPO_ROOT" status --porcelain --untracked-files=no)" ]]; then
    echo "Note: the upgrade checks out committed HEAD ($CANDIDATE_REVISION); uncommitted changes are not deployed." >&2
  fi
  DEPLOY_DIR="$(mktemp -d "${TMPDIR:-/tmp}/moonmind-upgrade-4356.XXXXXX")"
  git -C "$REPO_ROOT" worktree add --detach --quiet "$DEPLOY_DIR" "$UPGRADE_FROM_REVISION"
  # The old release pins quay.io/minio/minio, which is no longer publicly
  # pullable. Substitute the MinIO image the candidate qualifies; the
  # MoonMind services stay on the old release.
  local minio_image
  minio_image="$(compose_candidate_config | python3 -c \
    'import json, sys; print(json.load(sys.stdin)["services"]["minio"]["image"])')"
  mkdir -p "$STATE_DIR"
  printf 'services:\n  minio:\n    image: %s\n' "$minio_image" > "$STATE_DIR/upgrade-source-override.yaml"
  COMPOSE_OVERRIDES=(-f "$STATE_DIR/upgrade-source-override.yaml")
  echo "Upgrade source: $UPGRADE_FROM_REVISION on $UPGRADE_FROM (MinIO $minio_image)."
}

compose_candidate_config() {
  "${COMPOSE_CMD[@]}" --project-name "$PROJECT_NAME" -f "$COMPOSE_FILE" \
    --project-directory "$REPO_ROOT" config --format json
}

# Update the deployment directory in place, as an operator's checkout would:
# untracked state (var/, deploy/state) and the named volumes stay.
upgrade_source_to_candidate() {
  git -C "$DEPLOY_DIR" checkout --detach --quiet "$CANDIDATE_REVISION"
  COMPOSE_OVERRIDES=()
}

require_conversion_outcome() {
  compose logs --no-color api 2>&1 | redact > "$STATE_DIR/api-after-upgrade.log"
  checks conversion "$1" --api-log "$STATE_DIR/api-after-upgrade.log"
}

restart_workflow_worker() {
  echo "Restarting the workflow worker with work in flight..."
  compose restart temporal-worker-workflow 2>&1 | redact | tail -n 5
  compose up -d --wait --wait-timeout 300 temporal-worker-workflow 2>&1 | redact | tail -n 5
}

fresh_journey() {
  local label="$1"
  checks populate "$label"
  # MoonLadderStudios/MoonMind#4114: vector-free boundary proof on the
  # candidate instance (fresh installs and the post-upgrade candidate;
  # never on the pre-upgrade old release, which predates retirement).
  checks vector_free "$label"
  browser "$label"
  restart_workflow_worker
  cancel_from_dashboard "$label"
  checks credential "$label"
  checks verify "$label"
  checks release "$label"
}

if [[ "$MODE" == "fresh" ]]; then
  export MOONMIND_IMAGE="$CANDIDATE_IMAGE"
  bring_up
  fresh_journey fresh
elif [[ "$MODE" == "controller" ]]; then
  prepare_controller_source
  export MOONMIND_IMAGE="$CANDIDATE_IMAGE"
  bring_up
  controller_journey controller
else
  prepare_upgrade_source
  export MOONMIND_IMAGE="$UPGRADE_FROM"
  # The old release predates #4483: a transient Temporal RESOURCE_EXHAUSTED
  # during release-routing startup restarts its workflow worker group once,
  # and its 30s healthcheck interval can report unhealthy before the restarted
  # workers are observed ready. Wait again rather than fail on a release that
  # recovers on its own; the candidate still gets a single wait.
  bring_up 3
  checks populate before-upgrade
  cancel_from_dashboard before-upgrade
  checks credential before-upgrade
  checks verify before-upgrade

  echo "Upgrading $PROJECT_NAME in place from $UPGRADE_FROM_REVISION to $CANDIDATE_REVISION ($CANDIDATE_IMAGE)..." | redact
  upgrade_source_to_candidate
  export MOONMIND_IMAGE="$CANDIDATE_IMAGE"
  bring_up
  require_conversion_outcome before-upgrade
  checks verify before-upgrade
  browser before-upgrade
  checks release before-upgrade
  fresh_journey after-upgrade
fi

JOURNEY_FAILED=0
echo "Single-user $MODE journey passed on $CANDIDATE_IMAGE; evidence in $LOG_DIR." | redact
