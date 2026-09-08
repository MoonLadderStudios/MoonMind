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
#   MOONMIND_GPU_QUALIFICATION_COMMAND_WRITES_OUTPUT
#                                              set to 1 when a command override
#                                              writes the declared-output path
#                                              exported as
#                                              MOONMIND_GPU_QUALIFICATION_OUTPUT
#   MOONMIND_GPU_QUALIFICATION_GPU_COUNT       "all" (default) or a positive int
#   MOONMIND_GPU_QUALIFICATION_WORKSPACE_ROOT  workspace root shared with the daemon
#   MOONMIND_GPU_QUALIFICATION_CACHE_VOLUME    named volume for the warm-reuse leg
#   MOONMIND_GPU_QUALIFICATION_DOCKER_HOST     Docker endpoint for the trusted launch
#   MOONMIND_GPU_QUALIFICATION_TIMEOUT         per-request timeout in seconds
#   MOONMIND_GPU_QUALIFICATION_PROBE_TIMEOUT   timeout for the device probe
#   MOONMIND_GPU_QUALIFICATION_RECORD_DIR      durable directory for published records
#                                              (default: var/gpu_qualification)
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

# Preflight must inspect the same daemon the journey launches on, not the CLI
# default endpoint.
DOCKER_ARGS=()
if [[ -n "${MOONMIND_GPU_QUALIFICATION_DOCKER_HOST:-}" ]]; then
  DOCKER_ARGS+=(--host "$MOONMIND_GPU_QUALIFICATION_DOCKER_HOST")
fi

if ! "$DOCKER_BINARY" ${DOCKER_ARGS[@]+"${DOCKER_ARGS[@]}"} info --format '{{json .Runtimes}}' 2>/dev/null | grep -qi nvidia; then
  echo "Error: the configured Docker daemon exposes no NVIDIA runtime; run this on a deployment-owned GPU host." >&2
  exit 1
fi

# Published evidence must name the MoonMind implementation it qualified. On a
# source checkout the immutable identity is the checked-out revision; a
# deployment image supplies its own build identity instead.
if [[ -z "${MOONMIND_BUILD_SHA:-}" && -z "${MOONMIND_IMAGE_DIGEST:-}" ]]; then
  RESOLVED_REVISION="$(git -C "$REPO_ROOT" rev-parse HEAD 2>/dev/null || true)"
  if [[ -n "$RESOLVED_REVISION" ]]; then
    export MOONMIND_BUILD_SHA="$RESOLVED_REVISION"
  else
    echo "Error: no immutable MoonMind revision is available; set MOONMIND_BUILD_SHA or MOONMIND_IMAGE_DIGEST." >&2
    exit 1
  fi
fi

export MOONMIND_GPU_QUALIFICATION_RECORD_DIR="${MOONMIND_GPU_QUALIFICATION_RECORD_DIR:-$REPO_ROOT/var/gpu_qualification}"
mkdir -p "$MOONMIND_GPU_QUALIFICATION_RECORD_DIR"
echo "GPU qualification records will be published to: $MOONMIND_GPU_QUALIFICATION_RECORD_DIR"

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

# ``-s`` keeps the printed durable record paths visible in the operator log.
exec "$PYTHON_BIN" -m pytest \
  "$REPO_ROOT/tests/integration/workloads" \
  -m requires_gpu -q -s --tb=short -rs "$@"
