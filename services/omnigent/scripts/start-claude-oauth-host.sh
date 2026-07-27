#!/bin/sh
set -eu

state_root=${OMNIGENT_STATE_PATH:-/home/app/.omnigent}
expected_generation=${CLAUDE_CREDENTIAL_GENERATION:-}
server=${OMNIGENT_SERVER_URL:-http://omnigent:8000}
[ -n "$expected_generation" ] || exit 64
printf '%s\n' "$expected_generation" > "$state_root/credential-generation"
until /opt/moonmind/check-claude-oauth-host.sh; do sleep 5; done
until /opt/moonmind/check-runner-projections.sh; do sleep 5; done
/opt/moonmind/clear-stale-host-daemons.sh
exec omnigent host --server "$server" --non-interactive
