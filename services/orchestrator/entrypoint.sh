#!/usr/bin/env bash
set -euo pipefail

WORKSPACE_ROOT=${WORKSPACE_ROOT:-/workspace}
cd "${WORKSPACE_ROOT}"

export PYTHONPATH="${PYTHONPATH:-${WORKSPACE_ROOT}}"

if [[ -n "${ORCHESTRATOR_STATSD_HOST:-}" ]]; then
  export STATSD_HOST="${ORCHESTRATOR_STATSD_HOST}"
  export STATSD_PORT="${ORCHESTRATOR_STATSD_PORT:-8125}"
fi

if [[ "${ORCHESTRATOR_AUTO_INSTALL:-1}" == "1" && -f "${WORKSPACE_ROOT}/pyproject.toml" ]]; then
  if ! python - <<'PY' >/dev/null 2>&1
import importlib
import sys
sys.exit(0 if importlib.util.find_spec("moonmind") else 1)
PY
  then
    echo "[orchestrator] Installing MoonMind package into worker environment" >&2
    python -m pip install --upgrade pip setuptools wheel >/dev/null 2>&1 || true
    python -m pip install -e "${WORKSPACE_ROOT}" >/dev/null 2>&1
  fi
fi

CELERY_APP=${ORCHESTRATOR_CELERY_APP:-moonmind.workflows.orchestrator.tasks}
CELERY_QUEUE=${ORCHESTRATOR_CELERY_QUEUE:-orchestrator.run}
CELERY_LOG_LEVEL=${ORCHESTRATOR_LOG_LEVEL:-info}
CELERY_CONCURRENCY=${ORCHESTRATOR_CONCURRENCY:-1}
CELERY_CONFIG=${ORCHESTRATOR_CELERY_CONFIG:-}
CELERY_HOSTNAME=${ORCHESTRATOR_HOSTNAME:-orchestrator@%h}

cmd=(
  celery
  -A "${CELERY_APP}"
  worker
  "--loglevel=${CELERY_LOG_LEVEL}"
  "--queues=${CELERY_QUEUE}"
  "--concurrency=${CELERY_CONCURRENCY}"
  "--hostname=${CELERY_HOSTNAME}"
)

if [[ -n "${CELERY_CONFIG}" ]]; then
  cmd+=("--config=${CELERY_CONFIG}")
fi

exec "${cmd[@]}"
