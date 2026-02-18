#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

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

"$PYTHON_BIN" -m pytest -q tests/unit
