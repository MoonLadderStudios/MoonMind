#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

RUN_PYTHON_TESTS=1
RUN_DASHBOARD_TESTS=1
USE_XDIST=1
PYTEST_TARGETS=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        --python-only)
            RUN_DASHBOARD_TESTS=0
            shift
            ;;
        --dashboard-only)
            RUN_PYTHON_TESTS=0
            shift
            ;;
        --no-xdist)
            USE_XDIST=0
            shift
            ;;
        --)
            shift
            while [[ $# -gt 0 ]]; do
                PYTEST_TARGETS+=("$1")
                shift
            done
            ;;
        *)
            PYTEST_TARGETS+=("$1")
            shift
            ;;
    esac
done

is_wsl() {
    if [[ -n "${WSL_DISTRO_NAME:-}" ]]; then
        return 0
    fi
    grep -qiE "(microsoft|wsl)" /proc/version 2>/dev/null
}

# In WSL environments, use Docker-backed tests by default to avoid host Python
# dependency drift. Set MOONMIND_FORCE_LOCAL_TESTS=1 to bypass this behavior.
if is_wsl && [[ "${MOONMIND_FORCE_LOCAL_TESTS:-0}" != "1" ]]; then
    exec "$SCRIPT_DIR/test_unit_docker.sh"
fi

export MOONMIND_DISABLE_DEFAULT_USER_DB_LOOKUP=1

# Clear worker-specific overrides so WorkflowSettings-based tests use
# repository defaults instead of container env hints.
unset WORKFLOW_GITHUB_REPOSITORY
unset WORKFLOW_REPO_ROOT
unset WORKFLOW_SKILLS_LOCAL_MIRROR_ROOT
unset WORKFLOW_SKILLS_LEGACY_MIRROR_ROOT
unset SPEC_SKILLS_LOCAL_MIRROR_ROOT
unset SPEC_SKILLS_LEGACY_MIRROR_ROOT
unset MOONMIND_CODEX_MODEL
unset MOONMIND_CODEX_EFFORT

# Run only unit tests locally.
# Prefer repository-local virtualenv when present to avoid system Python drift.
if [[ -x ".venv/bin/python" ]]; then
    PYTHON_BIN=".venv/bin/python"
elif command -v python >/dev/null 2>&1; then
    PYTHON_BIN="python"
elif command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="python3"
else
    echo "Error: neither '.venv/bin/python', 'python', nor 'python3' is available." >&2
    exit 127
fi

PYTEST_PARALLEL_ARGS=()
if [[ "$USE_XDIST" == "1" ]] && "$PYTHON_BIN" -c "import xdist" >/dev/null 2>&1; then
    PYTEST_PARALLEL_ARGS=(-n auto --dist loadfile)
elif [[ "$USE_XDIST" == "1" ]]; then
    echo "Warning: pytest-xdist is not installed; running unit tests without parallel workers." >&2
fi

if [[ ${#PYTEST_TARGETS[@]} -eq 0 ]]; then
    PYTEST_TARGETS=(tests/unit)
fi

if [[ "$RUN_PYTHON_TESTS" == "1" ]]; then
    "$PYTHON_BIN" -m pytest -q ${PYTEST_PARALLEL_ARGS[@]+"${PYTEST_PARALLEL_ARGS[@]}"} "${PYTEST_TARGETS[@]}"
fi

if [[ "$RUN_DASHBOARD_TESTS" == "1" ]]; then
if command -v npm >/dev/null 2>&1; then
    npm run ui:test
else
    echo "Error: npm is required to run frontend unit tests." >&2
    exit 127
fi
fi
