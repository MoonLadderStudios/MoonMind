#!/usr/bin/env bash
set -euo pipefail

if [[ $# -eq 0 ]]; then
    echo "Usage: $0 <script-or-module> [args...]" >&2
    exit 64
fi

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

exec "$PYTHON_BIN" "$@"
