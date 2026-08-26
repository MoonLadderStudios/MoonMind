#!/usr/bin/env bash
# Explicit operator command for the generic NVIDIA container qualification
# journey (MoonLadderStudios/MoonMind#3777).
#
# Run this on a deployment-owned NVIDIA GPU host whose Docker daemon exposes the
# NVIDIA runtime. It is excluded from required CI; CPU-only runners skip the
# journey with an explicit environment reason.
#
# Required:
#   MOONMIND_GPU_QUALIFICATION_IMAGE   caller-supplied image with an explicit tag
#                                      or digest
# Optional:
#   MOONMIND_GPU_QUALIFICATION_COMMAND         JSON array overriding the command
#   MOONMIND_GPU_QUALIFICATION_GPU_COUNT       "all" (default) or a positive int
#   MOONMIND_GPU_QUALIFICATION_WORKSPACE_ROOT  workspace root shared with the daemon
#   MOONMIND_GPU_QUALIFICATION_CACHE_VOLUME    named volume for the warm-reuse leg
#   MOONMIND_GPU_QUALIFICATION_DOCKER_HOST     Docker endpoint for the trusted launch
#   MOONMIND_GPU_QUALIFICATION_TIMEOUT         per-request timeout in seconds
#
# These are qualification test configuration only. No MoonMind module reads
# them, and none of them is a MoonMind product or project integration setting.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

if [[ -z "${MOONMIND_GPU_QUALIFICATION_IMAGE:-}" ]]; then
  echo "Error: MOONMIND_GPU_QUALIFICATION_IMAGE must name the caller-supplied NVIDIA container image." >&2
  exit 1
fi

DOCKER_BINARY="${MOONMIND_GPU_QUALIFICATION_DOCKER_BINARY:-docker}"
if ! command -v "$DOCKER_BINARY" >/dev/null 2>&1; then
  echo "Error: '$DOCKER_BINARY' is not available; this journey needs a reachable Docker boundary." >&2
  exit 127
fi

if ! "$DOCKER_BINARY" info --format '{{json .Runtimes}}' 2>/dev/null | grep -qi nvidia; then
  echo "Error: the configured Docker daemon exposes no NVIDIA runtime; run this on a deployment-owned GPU host." >&2
  exit 1
fi

if [[ -x "$REPO_ROOT/.venv/bin/python" ]]; then
  PYTHON_BIN="$REPO_ROOT/.venv/bin/python"
elif command -v python >/dev/null 2>&1; then
  PYTHON_BIN="python"
elif command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN="python3"
else
  echo "Error: neither '.venv/bin/python', 'python', nor 'python3' is available." >&2
  exit 127
fi

exec "$PYTHON_BIN" -m pytest \
  "$REPO_ROOT/tests/integration/workloads" \
  -m requires_gpu -q --tb=short -rs "$@"
