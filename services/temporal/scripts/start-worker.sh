#!/usr/bin/env sh
set -eu

FLEET="${TEMPORAL_WORKER_FLEET:-}"
if [ -z "$FLEET" ]; then
  echo "TEMPORAL_WORKER_FLEET is required" >&2
  exit 64
fi

python - "$FLEET" <<'PY'
from moonmind.workflows.temporal.workers import main
import sys

raise SystemExit(main(["--fleet", sys.argv[1], "--describe-json"]))
PY

if [ "$#" -gt 0 ]; then
  exec "$@"
fi

exec python -m moonmind.workflows.temporal.workers --fleet "$FLEET"
