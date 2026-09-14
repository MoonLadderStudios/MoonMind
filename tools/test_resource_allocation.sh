#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
resource_project="${MOONMIND_TEST_COMPOSE_PROJECT_NAME:-moonmind-test-resources}"
if [[ ! "$resource_project" =~ ^moonmind-test(-[a-z0-9][a-z0-9_-]*)?$ ]]; then
    echo 'Resource qualification requires a moonmind-test project name.' >&2
    exit 2
fi
resource_compose=(docker compose -p "$resource_project" -f tests/integration/resource_allocation/compose.yaml)
cleanup() {
    "${resource_compose[@]}" logs --no-color engine > artifacts/resource-engine.log 2>&1 || true
    "${resource_compose[@]}" down -v --remove-orphans
}
trap cleanup EXIT
mkdir -p artifacts
if [[ "${1:-}" != --no-build ]]; then
    "${resource_compose[@]}" build pytest
fi
"${resource_compose[@]}" up -d --wait --wait-timeout 180 engine
"${resource_compose[@]}" run --rm pytest
