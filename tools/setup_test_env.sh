#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

find_python() {
    if [[ -x "$REPO_ROOT/.venv/bin/python" ]]; then
        echo "$REPO_ROOT/.venv/bin/python"
        return 0
    fi
    if command -v python >/dev/null 2>&1; then
        echo "python"
        return 0
    fi
    if command -v python3 >/dev/null 2>&1; then
        echo "python3"
        return 0
    fi
    echo "Error: neither 'python' nor 'python3' is available." >&2
    exit 127
}

BOOTSTRAP_PYTHON="$(find_python)"

if [[ ! -x "$REPO_ROOT/.venv/bin/python" ]]; then
    "$BOOTSTRAP_PYTHON" -m venv "$REPO_ROOT/.venv"
fi

VENV_PYTHON="$REPO_ROOT/.venv/bin/python"

"$VENV_PYTHON" -m pip install --upgrade pip
"$VENV_PYTHON" -m pip install -e "$REPO_ROOT[tests]"

if command -v npm >/dev/null 2>&1; then
    (
        cd "$REPO_ROOT"
        npm ci
    )
else
    echo "Note: npm is not installed; skipping Node dependency setup." >&2
fi

echo "Test environment ready with $VENV_PYTHON"
