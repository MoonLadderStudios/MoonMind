#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

PROVIDER_VERIFICATION=0
TEST_FILE=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --provider-verification)
            PROVIDER_VERIFICATION=1
            shift
            ;;
        --test-file)
            shift
            TEST_FILE="$1"
            shift
            ;;
        *)
            echo "Unknown option: $1"
            echo "Usage: $0 [--provider-verification] [--test-file <path>]"
            exit 1
            ;;
    esac
done

# Run pre-commit checks first
echo "Running pre-commit checks..."
if ! pre-commit run --all-files; then
    echo "Pre-commit checks failed. Please fix formatting issues and commit changes." >&2
    exit 1
fi
echo "Pre-commit checks passed!"
echo ""

if [[ -n "$TEST_FILE" ]]; then
    docker-compose -f docker-compose.test.yaml run --rm -e TEST_TYPE="integration/$TEST_FILE" pytest
elif [[ "$PROVIDER_VERIFICATION" == "1" ]]; then
    # Run provider verification tests (real credentials required)
    echo "Running provider verification tests (requires credentials)..." >&2
    docker-compose -f docker-compose.test.yaml build pytest
    docker-compose -f docker-compose.test.yaml run --rm pytest bash -lc "pytest tests/integration -m 'provider_verification and jules' -v --tb=short"
else
    # Run hermetic integration CI tests only (no external credentials)
    echo "Running hermetic integration CI tests..." >&2
    docker-compose -f docker-compose.test.yaml build pytest
    docker-compose -f docker-compose.test.yaml run --rm pytest bash -lc "pytest tests/integration -m 'integration_ci' -q --tb=short"
fi