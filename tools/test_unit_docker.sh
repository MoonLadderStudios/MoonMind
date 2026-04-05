#!/usr/bin/env bash
# Run unit tests inside a Docker container using docker-compose.test.yaml.
# This is the canonical way to run tests on macOS and WSL.
#
# Usage:
#   ./tools/test_unit_docker.sh [options] [args...]
#
# Options:
#   --no-build   Skip re-building the Docker image (much faster if image is current)
#
# All other arguments are forwarded to tools/test_unit.sh inside the container.
# Examples:
#   ./tools/test_unit_docker.sh                            # full run (builds image first)
#   ./tools/test_unit_docker.sh --no-build                 # skip build, run all tests
#   ./tools/test_unit_docker.sh --no-build --python-only   # skip build, Python tests only
#   ./tools/test_unit_docker.sh --no-build -- tests/unit/agents  # run one subdirectory
#   ./tools/test_unit_docker.sh --no-build --no-xdist      # disable parallel workers
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

COMPOSE_FILE="$REPO_ROOT/docker-compose.test.yaml"
TEST_SERVICE="pytest"
NETWORK_NAME="local-network"
SKIP_BUILD=0

# Parse --no-build before forwarding remaining args.
FORWARD_ARGS=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        --no-build)
            SKIP_BUILD=1
            shift
            ;;
        *)
            FORWARD_ARGS+=("$1")
            shift
            ;;
    esac
done

if command -v docker > /dev/null 2>&1 && docker compose version > /dev/null 2>&1; then
    COMPOSE_CMD=(docker compose)
elif command -v docker-compose > /dev/null 2>&1; then
    COMPOSE_CMD=(docker-compose)
else
    echo "Error: docker compose CLI is not available." >&2
    echo "Install Docker Desktop (includes Compose V2) from https://www.docker.com/products/docker-desktop/" >&2
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

# Ensure the shared Docker network exists (required by docker-compose.test.yaml).
if ! docker network inspect "$NETWORK_NAME" > /dev/null 2>&1; then
    docker network create "$NETWORK_NAME" > /dev/null
    echo "Created Docker network: $NETWORK_NAME"
fi

# Build the test image (cached after first build; only rebuilds when Dockerfile/pyproject.toml changes).
if [[ "$SKIP_BUILD" == "0" ]]; then
    echo "Building test image (use --no-build to skip)..."
    "${COMPOSE_CMD[@]}" -f "$COMPOSE_FILE" --project-directory "$REPO_ROOT" build "$TEST_SERVICE"
else
    echo "Skipping image build (--no-build)."
fi

# Pass all remaining arguments through to tools/test_unit.sh inside the container.
# The container mounts the repo at /app, so test_unit.sh is at /app/tools/test_unit.sh.
# MOONMIND_FORCE_LOCAL_TESTS=1 prevents test_unit.sh from recursing back into Docker.
echo "Running tests inside container..."
INNER_ARGS=""
if [[ ${#FORWARD_ARGS[@]} -gt 0 ]]; then
    INNER_ARGS="$(printf '%q ' "${FORWARD_ARGS[@]}")"
fi

# Ensure compose stack is torn down after tests, including dependency containers (e.g. MinIO).
cleanup() {
    echo "Tearing down test compose stack..."
    "${COMPOSE_CMD[@]}" -f "$COMPOSE_FILE" --project-directory "$REPO_ROOT" down --remove-orphans || true
}
trap cleanup EXIT

"${COMPOSE_CMD[@]}" -f "$COMPOSE_FILE" --project-directory "$REPO_ROOT" run --rm \
    -e MOONMIND_FORCE_LOCAL_TESTS=1 \
    "$TEST_SERVICE" \
    bash -lc "exec /app/tools/test_unit.sh ${INNER_ARGS}"
