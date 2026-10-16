#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
COMPOSE_PROJECT_DIR="$REPO_ROOT"
COMPOSE_FILE="$COMPOSE_PROJECT_DIR/docker-compose.test.yaml"
TEMP_COMPOSE_PROJECT_DIR=""
TEST_COMPOSE_PROJECT_NAME="${MOONMIND_TEST_COMPOSE_PROJECT_NAME:-moonmind-test}"
COMPOSE_CMD=()
# The JUnit report, streamed log and failure diagnostics land on the host
# here, also when Compose runs from a temporary copy of the checkout.
REPORT_PATH="artifacts/integration-ci"
REPORT_DIR="$REPO_ROOT/$REPORT_PATH"
JUNIT_NAME="pytest-integration-ci.xml"
# Each teardown or diagnostics command gets this many seconds before it is
# terminated, so a stuck daemon cannot hold the run open after pytest ends.
CLEANUP_TIMEOUT="${MOONMIND_TEST_CLEANUP_TIMEOUT:-120}"
TEST_PID=""
TEST_STATUS=0

project_name_regex='^moonmind-test(-[a-z0-9][a-z0-9_-]*)?$'
if [[ ! "$TEST_COMPOSE_PROJECT_NAME" =~ $project_name_regex ]]; then
  echo "Error: MOONMIND_TEST_COMPOSE_PROJECT_NAME must be 'moonmind-test' or start with 'moonmind-test-'." >&2
  exit 2
fi

# Compose against this wrapper's own project. A leading time bound in seconds
# applies when coreutils timeout exists; stock macOS lacks it, so there the
# command stays unbounded.
compose() {
  local -a bound=()
  if [[ "$1" =~ ^[0-9]+$ ]]; then
    if command -v timeout >/dev/null 2>&1; then
      bound=(timeout --kill-after=10s "$1s")
    fi
    shift
  fi
  ${bound[@]+"${bound[@]}"} "${COMPOSE_CMD[@]}" --project-name "$TEST_COMPOSE_PROJECT_NAME" -f "$COMPOSE_FILE" --project-directory "$COMPOSE_PROJECT_DIR" "$@"
}

cleanup() {
  if [[ -n "$TEST_PID" ]]; then
    kill "$TEST_PID" >/dev/null 2>&1 || true
  fi
  if (( ${#COMPOSE_CMD[@]} )); then
    compose "$CLEANUP_TIMEOUT" down --remove-orphans >/dev/null 2>&1 || true
  fi
  if [[ -n "$TEMP_COMPOSE_PROJECT_DIR" ]]; then
    if [[ -f "$TEMP_COMPOSE_PROJECT_DIR/$REPORT_PATH/$JUNIT_NAME" ]]; then
      cp "$TEMP_COMPOSE_PROJECT_DIR/$REPORT_PATH/$JUNIT_NAME" "$REPORT_DIR/" >/dev/null 2>&1 || true
    fi
    rm -rf "$TEMP_COMPOSE_PROJECT_DIR" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT
# Leave through the EXIT trap so teardown and report export still run.
trap 'exit 130' INT
trap 'exit 143' TERM

hash_repo_path() {
  if command -v sha256sum >/dev/null 2>&1; then
    printf '%s' "$1" | sha256sum | cut -d' ' -f1
  elif command -v shasum >/dev/null 2>&1; then
    printf '%s' "$1" | shasum -a 256 | cut -d' ' -f1
  else
    echo "Error: neither sha256sum nor shasum is available." >&2
    exit 127
  fi
}

if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
  COMPOSE_CMD=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE_CMD=(docker-compose)
else
  echo "Error: docker compose CLI is not available." >&2
  exit 127
fi

if [[ ! -f "$REPO_ROOT/.env" ]]; then
  if [[ -f "$REPO_ROOT/.env-template" ]]; then
    cp "$REPO_ROOT/.env-template" "$REPO_ROOT/.env"
    echo "Created $REPO_ROOT/.env from .env-template for docker compose tests."
  else
    echo "Error: missing $REPO_ROOT/.env and $REPO_ROOT/.env-template." >&2
    exit 1
  fi
fi

if [[ "$REPO_ROOT" == *:* ]]; then
  TEMP_COMPOSE_PROJECT_DIR="${MOONMIND_AGENT_RUNTIME_STORE:-/work/agent_jobs}/moonmind-integration-repo-$(hash_repo_path "$REPO_ROOT" | cut -c1-12)-$$"
  mkdir -p "$TEMP_COMPOSE_PROJECT_DIR"
  tar -C "$REPO_ROOT" \
    --exclude='./.git' \
    --exclude='./.pytest_cache' \
    --exclude='./.mypy_cache' \
    --exclude='./.ruff_cache' \
    --exclude='./.venv' \
    --exclude='./artifacts' \
    --exclude='./live_streams.spool' \
    --exclude='./node_modules' \
    --exclude='./var' \
    --exclude='*/__pycache__' \
    --exclude='*/node_modules' \
    -cf - . | tar -C "$TEMP_COMPOSE_PROJECT_DIR" -xf -
  COMPOSE_PROJECT_DIR="$TEMP_COMPOSE_PROJECT_DIR"
  COMPOSE_FILE="$COMPOSE_PROJECT_DIR/docker-compose.test.yaml"
fi

export MOONMIND_ALLOW_LIVE_TEMPORAL_IN_TESTS=1

# Build the pytest service image unless the caller supplied one.
#
# MOONMIND_PYTHON_TEST_IMAGE names the image the compose ``pytest`` service
# runs. When it is set, the caller owns that image (CI prebuilds it with a
# registry-backed layer cache), so it must already be loadable locally; a
# missing image is an actionable setup error rather than a silent rebuild.
if [[ -n "${MOONMIND_PYTHON_TEST_IMAGE:-}" ]]; then
  if ! docker image inspect "$MOONMIND_PYTHON_TEST_IMAGE" >/dev/null 2>&1; then
    echo "Error: MOONMIND_PYTHON_TEST_IMAGE=$MOONMIND_PYTHON_TEST_IMAGE is set but the image is not available locally; build or pull it first, or unset it to build here." >&2
    exit 1
  fi
  echo "Using prebuilt pytest image $MOONMIND_PYTHON_TEST_IMAGE"
else
  compose build pytest
fi

# Run integration tests (always cleaned up via trap)
# --timeout 120: kill any single test that runs longer than 120 seconds
# --timeout-method=thread: use thread-based timeout (works with async tests)
# --durations=10: print the 10 slowest tests at the end
# -n / --dist loadfile: the hermetic suite provisions its own Temporal test
#   servers, Postgres clusters, and sockets on ephemeral ports, so test files
#   run on separate workers; MOONMIND_INTEGRATION_WORKERS overrides the count.
# MOONMIND_INTEGRATION_FAIL_FAST=1 adds --maxfail=1 for ordinary CI runs;
#   interactive and scheduled runs omit it to keep collecting diagnostics.
# MOONMIND_INTEGRATION_TIMEOUT bounds the run in seconds. The deadline sends
#   SIGINT so pytest writes its report, then kills after a 30s grace; the run
#   then exits 124 (137 for the kill).
INTEGRATION_WORKERS="${MOONMIND_INTEGRATION_WORKERS:-2}"
pytest_args="tests/integration -m 'integration_ci' --tb=short --timeout 120 --timeout-method=thread --durations=10 -n $INTEGRATION_WORKERS --dist loadfile --junitxml=$REPORT_PATH/$JUNIT_NAME"
if [[ "${MOONMIND_INTEGRATION_FAIL_FAST:-0}" == "1" ]]; then
  pytest_args+=" --maxfail=1"
fi
run_bound=()
if [[ -n "${MOONMIND_INTEGRATION_TIMEOUT:-}" ]]; then
  run_bound=(timeout --signal=INT --kill-after=30s "${MOONMIND_INTEGRATION_TIMEOUT}s")
fi

# A report left by an earlier run must never stand in for a missing one.
mkdir -p "$REPORT_DIR"
rm -f "$REPORT_DIR/$JUNIT_NAME" "$REPORT_DIR"/compose-*.txt

# Run in the background so a signal reaches the traps while pytest runs.
${run_bound[@]+"${run_bound[@]}"} "${COMPOSE_CMD[@]}" --project-name "$TEST_COMPOSE_PROJECT_NAME" -f "$COMPOSE_FILE" --project-directory "$COMPOSE_PROJECT_DIR" run --rm \
  -e MOONMIND_ALLOW_LIVE_TEMPORAL_IN_TESTS=1 \
  pytest \
  bash -lc "pytest $pytest_args" > >(tee "$REPORT_DIR/pytest-integration-ci.log") 2>&1 &
TEST_PID=$!
wait "$TEST_PID" || TEST_STATUS=$?
TEST_PID=""

if (( TEST_STATUS != 0 )); then
  compose "$CLEANUP_TIMEOUT" ps -a > "$REPORT_DIR/compose-ps.txt" 2>&1 \
    || echo "compose ps failed or timed out (exit=$?)" >> "$REPORT_DIR/compose-ps.txt"
  compose "$CLEANUP_TIMEOUT" logs --no-color --tail 500 > "$REPORT_DIR/compose-logs.txt" 2>&1 \
    || echo "compose logs failed or timed out (exit=$?)" >> "$REPORT_DIR/compose-logs.txt"
fi
exit "$TEST_STATUS"
