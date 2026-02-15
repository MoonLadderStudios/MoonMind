#!/usr/bin/env bash
set -euo pipefail

# Run only unit tests
if command -v python >/dev/null 2>&1; then
    PYTHON_BIN="python"
elif command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="python3"
else
    echo "Error: neither 'python' nor 'python3' is available on PATH." >&2
    exit 127
fi

"$PYTHON_BIN" -m pytest -q tests/unit
