#!/usr/bin/env bash
set -euo pipefail

WORKSPACE_ROOT=${WORKSPACE_ROOT:-/workspace}

if [[ ! -d "${WORKSPACE_ROOT}" ]]; then
  echo "[orchestrator] Workspace root '${WORKSPACE_ROOT}' does not exist" >&2
  exit 1
fi

cd "${WORKSPACE_ROOT}"

if [[ -n "${ORCHESTRATOR_STATSD_HOST:-}" ]]; then
  export STATSD_HOST="${ORCHESTRATOR_STATSD_HOST}"
  export STATSD_PORT="${ORCHESTRATOR_STATSD_PORT:-8125}"
fi

if [[ "${ORCHESTRATOR_AUTO_INSTALL:-1}" == "1" && -f "${WORKSPACE_ROOT}/pyproject.toml" ]]; then
  if ! python - <<'PY'
import importlib.metadata
import sys

try:
    importlib.metadata.distribution("moonmind")
except importlib.metadata.PackageNotFoundError:
    sys.exit(1)
else:
    sys.exit(0)
PY
  then
    echo "[orchestrator] Installing MoonMind package into worker environment" >&2
    python -m pip install --upgrade pip setuptools wheel
    python -m pip install -e "${WORKSPACE_ROOT}"
  fi
fi

if [[ -n "${PYTHONPATH:-}" ]]; then
  case ":${PYTHONPATH}:" in
    *:"${WORKSPACE_ROOT}":*) ;; # already present
    *) export PYTHONPATH="${PYTHONPATH}:${WORKSPACE_ROOT}" ;;
  esac
else
  export PYTHONPATH="${WORKSPACE_ROOT}"
fi

CELERY_APP=${ORCHESTRATOR_CELERY_APP:-moonmind.workflows.orchestrator.tasks}
CELERY_QUEUE=${ORCHESTRATOR_CELERY_QUEUE:-orchestrator.run}
CELERY_LOG_LEVEL=${ORCHESTRATOR_LOG_LEVEL:-info}
CELERY_CONCURRENCY=${ORCHESTRATOR_CONCURRENCY:-1}
CELERY_HOSTNAME=${ORCHESTRATOR_HOSTNAME:-orchestrator@%h}

cmd=(celery)

cmd+=(
  -A "${CELERY_APP}"
  worker
  "--loglevel=${CELERY_LOG_LEVEL}"
  "--queues=${CELERY_QUEUE}"
  "--concurrency=${CELERY_CONCURRENCY}"
  "--hostname=${CELERY_HOSTNAME}"
)

# Surface orchestrator metrics configuration for downstream processes.
export ORCHESTRATOR_METRICS_PREFIX="${ORCHESTRATOR_METRICS_PREFIX:-moonmind.orchestrator}"

exec "${cmd[@]}"
