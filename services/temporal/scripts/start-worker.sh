#!/usr/bin/env sh
set -eu

if [ -z "${TEMPORAL_WORKER_FLEET:-}" ] && [ -z "${TEMPORAL_WORKER_FLEETS:-}" ]; then
  echo "TEMPORAL_WORKER_FLEET or TEMPORAL_WORKER_FLEETS is required" >&2
  exit 64
fi

python - <<'PYTHON'
from moonmind.workflows.temporal.workers import main

raise SystemExit(main(["--describe-json"]))
PYTHON

if [ -n "${TEMPORAL_WORKER_COMMAND:-}" ] && [ "$TEMPORAL_WORKER_COMMAND" != "sleep infinity" ]; then
  exec sh -lc "$TEMPORAL_WORKER_COMMAND"
fi

if [ "$#" -gt 0 ]; then
  exec "$@"
fi

exec python -m moonmind.workflows.temporal.worker_runtime
