#!/bin/sh
set -eu

until /opt/moonmind/check-runner-projections.sh; do
  echo "Omnigent host waiting for a resolved Skill projection" >&2
  sleep 5
done
/opt/moonmind/clear-stale-host-daemons.sh
exec omnigent host --server "${OMNIGENT_SERVER_URL:-http://omnigent:8000}" --non-interactive
