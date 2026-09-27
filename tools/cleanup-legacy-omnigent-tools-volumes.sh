#!/usr/bin/env bash
# Bounded legacy Omnigent tools-volume retirement (MoonLadderStudios/MoonMind#4558).
#
# Default (no flags) is inspect/dry-run: prints the removal plan as JSON and
# changes nothing. Pass --apply to remove approved unused legacy volumes.
# Cleanup failures are reported, never fatal to the caller.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

PYTHON_BIN="${PYTHON_BIN:-}"
if [[ -z "${PYTHON_BIN}" ]]; then
    if [[ -x "${REPO_ROOT}/.venv/bin/python" ]]; then
        PYTHON_BIN="${REPO_ROOT}/.venv/bin/python"
    elif command -v python3 >/dev/null 2>&1; then
        PYTHON_BIN="python3"
    else
        echo "error: no python3 available" >&2
        exit 127
    fi
fi

exec "${PYTHON_BIN}" "${REPO_ROOT}/moonmind/omnigent/host_services/legacy_tools_cleanup.py" "$@"
