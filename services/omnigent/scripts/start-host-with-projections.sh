#!/bin/sh
set -eu

/opt/moonmind/check-runner-projections.sh
exec omnigent host --server "${OMNIGENT_SERVER_URL:-http://omnigent:8000}" --non-interactive
