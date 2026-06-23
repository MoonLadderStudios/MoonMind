#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

RUN_PYTHON_TESTS=1
RUN_DASHBOARD_TESTS=1
USE_XDIST=1
PYTEST_TARGETS=()
UI_TEST_ARGS=()

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
        --ui-args)
            shift
            while [[ $# -gt 0 ]]; do
                UI_TEST_ARGS+=("$1")
                shift
            done
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

# Detect whether we are running inside a container (Docker, containerd, etc.).
# This guard prevents misclassifying a container on a WSL2 host as a "human
# WSL shell" and redirecting to nested Docker, which fails in managed-agent
# workers that have no Docker socket.
is_in_container() {
    # /.dockerenv is created by Docker daemon for containers.
    if [[ -f /.dockerenv ]]; then
        return 0
    fi
    # /proc/1/cgroup contains "docker" or "containerd" for most container runtimes.
    if [[ -f /proc/1/cgroup ]] && grep -qiE "docker|containerd" /proc/1/cgroup 2>/dev/null; then
        return 0
    fi
    return 1
}

is_wsl() {
    if [[ -n "${WSL_DISTRO_NAME:-}" ]]; then
        return 0
    fi
    grep -qiE "(microsoft|wsl)" /proc/version 2>/dev/null
}

# Redirect to Docker-backed tests only for human WSL development sessions that
# lack local tooling parity. Containers on WSL2 hosts are NOT redirected.
# Set MOONMIND_FORCE_LOCAL_TESTS=1 to bypass this behavior entirely.
if ! is_in_container && is_wsl && [[ "${MOONMIND_FORCE_LOCAL_TESTS:-0}" != "1" ]]; then
    # Only redirect if local Python *and* Node are both unavailable. This keeps
    # fully-tooled WSL environments (like the user's dev box) on the local path.
    local_has_python=false
    local_has_node=false
    [[ -x ".venv/bin/python" ]] || command -v python >/dev/null 2>&1 || command -v python3 >/dev/null 2>&1 && local_has_python=true
    command -v node >/dev/null 2>&1 && command -v npm >/dev/null 2>&1 && local_has_node=true
    if [[ "$local_has_python" == "false" ]] || [[ "$local_has_node" == "false" ]]; then
        exec "$SCRIPT_DIR/test_unit_docker.sh"
    fi
fi

export MOONMIND_DISABLE_DEFAULT_USER_DB_LOOKUP=1
export MOONMIND_ALLOW_LIVE_TEMPORAL_IN_TESTS=0

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

uses_temporal_test_server=0
if [[ "$RUN_PYTHON_TESTS" == "1" && "$USE_XDIST" == "1" ]]; then
    for target in "${PYTEST_TARGETS[@]}"; do
        case "$target" in
            tests/unit|tests/unit/workflows/temporal|tests/unit/workflows/temporal/*)
                uses_temporal_test_server=1
                break
                ;;
        esac
    done
fi

if [[ "$uses_temporal_test_server" == "1" ]]; then
    temporal_test_tmpdir="${TMPDIR:-/tmp}"
    rm -f "$temporal_test_tmpdir"/temporal-test-server-sdk-python-*.downloading
    "$PYTHON_BIN" - <<'PY'
import asyncio

from temporalio.testing import WorkflowEnvironment


async def main() -> None:
    env = await WorkflowEnvironment.start_time_skipping()
    await env.shutdown()


asyncio.run(main())
PY
fi

if [[ "$RUN_PYTHON_TESTS" == "1" ]]; then
    PYTEST_DURATIONS="${MOONMIND_PYTEST_DURATIONS:-25}"
    PYTEST_REPORT_ARGS=(--durations="$PYTEST_DURATIONS")
    if [[ -n "${MOONMIND_PYTEST_JUNITXML:-}" ]]; then
        mkdir -p "$(dirname "$MOONMIND_PYTEST_JUNITXML")"
        PYTEST_REPORT_ARGS+=(--junitxml="$MOONMIND_PYTEST_JUNITXML")
    elif [[ "${CI:-}" == "true" ]]; then
        mkdir -p artifacts
        PYTEST_REPORT_ARGS+=(--junitxml=artifacts/pytest-unit.xml)
    fi

    "$PYTHON_BIN" -m pytest -q ${PYTEST_PARALLEL_ARGS[@]+"${PYTEST_PARALLEL_ARGS[@]}"} "${PYTEST_REPORT_ARGS[@]}" "${PYTEST_TARGETS[@]}"
fi

if [[ "$RUN_DASHBOARD_TESTS" == "1" ]]; then
    if ! command -v npm >/dev/null 2>&1; then
        echo "Error: npm is required to run frontend unit tests." >&2
        exit 127
    fi

    # Bootstrap JS dependencies when node_modules is missing or stale.
    REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
    if [[ ! -d "$REPO_ROOT/node_modules" ]] || \
       [[ "$REPO_ROOT/package-lock.json" -nt "$REPO_ROOT/node_modules/.package-lock.json" ]]; then
        echo "Preparing frontend dependencies (npm ci)..."
        (cd "$REPO_ROOT" && npm ci --no-fund --no-audit)
    fi

    # Invoke the local binary directly. Managed agent workspaces may include
    # colons in their absolute path, which breaks npm's PATH-based bin lookup.
    VITEST_BIN="$REPO_ROOT/node_modules/.bin/vitest"
    if [[ ! -x "$VITEST_BIN" ]]; then
        echo "Error: Vitest binary not found at $VITEST_BIN after npm dependency preparation." >&2
        exit 127
    fi

    UI_REPO_ROOT="$REPO_ROOT"
    UI_VITEST_BIN="$VITEST_BIN"
    UI_TEST_ARGS_EFFECTIVE=()
    if [[ ${#UI_TEST_ARGS[@]} -gt 0 ]]; then
        UI_TEST_ARGS_EFFECTIVE=("${UI_TEST_ARGS[@]}")
    fi
    UI_MIRROR_ROOT=""
    if [[ "$REPO_ROOT" == *:* ]]; then
        UI_MIRROR_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/moonmind-ui-tests.XXXXXX")"
        cleanup_ui_mirror() {
            rm -rf "$UI_MIRROR_ROOT"
        }
        trap cleanup_ui_mirror EXIT
        cp "$REPO_ROOT/package.json" "$REPO_ROOT/package-lock.json" "$UI_MIRROR_ROOT/"
        for config_file in tailwind.config.cjs postcss.config.cjs; do
            if [[ -f "$REPO_ROOT/$config_file" ]]; then
                cp "$REPO_ROOT/$config_file" "$UI_MIRROR_ROOT/"
            fi
        done
        cp -a "$REPO_ROOT/frontend" "$UI_MIRROR_ROOT/frontend"
        if [[ -d "$REPO_ROOT/api_service/templates" ]]; then
            mkdir -p "$UI_MIRROR_ROOT/api_service"
            cp -a "$REPO_ROOT/api_service/templates" "$UI_MIRROR_ROOT/api_service/templates"
        fi
        # --reflink is a GNU coreutils extension; fall back to a plain copy on
        # platforms whose cp (e.g. BSD/macOS) does not support it.
        if cp --help 2>&1 | grep -q "reflink"; then
            cp -a --reflink=auto "$REPO_ROOT/node_modules" "$UI_MIRROR_ROOT/node_modules"
        else
            cp -a "$REPO_ROOT/node_modules" "$UI_MIRROR_ROOT/node_modules"
        fi
        UI_REPO_ROOT="$UI_MIRROR_ROOT"
        UI_VITEST_BIN="$UI_MIRROR_ROOT/node_modules/.bin/vitest"
        if [[ ${#UI_TEST_ARGS_EFFECTIVE[@]} -gt 0 ]]; then
            for i in "${!UI_TEST_ARGS_EFFECTIVE[@]}"; do
                case "${UI_TEST_ARGS_EFFECTIVE[$i]}" in
                    "$REPO_ROOT"/*)
                        UI_TEST_ARGS_EFFECTIVE[$i]="$UI_MIRROR_ROOT/${UI_TEST_ARGS_EFFECTIVE[$i]#"$REPO_ROOT"/}"
                        ;;
                esac
            done
        fi
    fi

    # Allow targeted frontend test runs via --ui-args (e.g. --ui-args src/components/App.tsx).
    if [[ ${#UI_TEST_ARGS[@]} -gt 0 ]]; then
        (cd "$UI_REPO_ROOT" && "$UI_VITEST_BIN" run --config frontend/vite.config.ts "${UI_TEST_ARGS_EFFECTIVE[@]}")
    else
        (cd "$UI_REPO_ROOT" && "$UI_VITEST_BIN" run --config frontend/vite.config.ts)
    fi
fi
