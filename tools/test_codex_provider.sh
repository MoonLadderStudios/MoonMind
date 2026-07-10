#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
COMPOSE_FILE="$REPO_ROOT/docker-compose.test.yaml"
NETWORK_NAME="${MOONMIND_DOCKER_NETWORK:-local-network}"
TEST_COMPOSE_PROJECT_NAME="${MOONMIND_TEST_COMPOSE_PROJECT_NAME:-moonmind-test}"
RESULT_PATH="${MOONMIND_CODEX_CANARY_RESULT_PATH:-$REPO_ROOT/artifacts/codex-conformance/canary-result.json}"
COMPOSE_CMD=()

project_name_regex='^moonmind-test(-[a-z0-9][a-z0-9_-]*)?$'
if [[ ! "$TEST_COMPOSE_PROJECT_NAME" =~ $project_name_regex ]]; then
  echo "Error: MOONMIND_TEST_COMPOSE_PROJECT_NAME must be 'moonmind-test' or start with 'moonmind-test-'." >&2
  exit 2
fi

if [[ -z "${MOONMIND_CODEX_CANARY_CANDIDATE_DIGEST:-}" ]]; then
  echo "Error: MOONMIND_CODEX_CANARY_CANDIDATE_DIGEST must be set for Codex provider verification." >&2
  exit 1
fi

cleanup() {
  if (( ${#COMPOSE_CMD[@]} )); then
    "${COMPOSE_CMD[@]}" --project-name "$TEST_COMPOSE_PROJECT_NAME" -f "$COMPOSE_FILE" --project-directory "$REPO_ROOT" down --remove-orphans >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

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

if ! docker network inspect "$NETWORK_NAME" >/dev/null 2>&1; then
  docker network create "$NETWORK_NAME" >/dev/null
  echo "Created Docker network: $NETWORK_NAME"
fi

mkdir -p "$(dirname "$RESULT_PATH")"

"${COMPOSE_CMD[@]}" --project-name "$TEST_COMPOSE_PROJECT_NAME" -f "$COMPOSE_FILE" --project-directory "$REPO_ROOT" build pytest
"${COMPOSE_CMD[@]}" --project-name "$TEST_COMPOSE_PROJECT_NAME" -f "$COMPOSE_FILE" --project-directory "$REPO_ROOT" run --rm \
  -e MOONMIND_API_URL \
  -e MOONMIND_API_TOKEN \
  -e MOONMIND_CODEX_CANARY_CANDIDATE_DIGEST \
  -e MOONMIND_CODEX_CANARY_CANDIDATE_REF \
  -e MOONMIND_CODEX_CANARY_PROFILE_REF \
  -e CODEX_CLI_VERSION \
  -e CODEX_APP_SERVER_VERSION \
  -e GITHUB_SHA \
  -v "$REPO_ROOT/artifacts:/app/artifacts" \
  pytest bash -lc \
  "python tools/run_codex_conformance_canary.py --candidate-digest \"\$MOONMIND_CODEX_CANARY_CANDIDATE_DIGEST\" --output artifacts/codex-conformance/canary-result.json && MOONMIND_CODEX_CANARY_RESULT_PATH=artifacts/codex-conformance/canary-result.json pytest tests/provider/codex -m 'provider_verification and codex' -q --tb=short -s"
