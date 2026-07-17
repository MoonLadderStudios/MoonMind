#!/bin/sh
set -eu

state_root=${OMNIGENT_STATE_PATH:-/home/app/.omnigent}
expected_generation=${CODEX_CREDENTIAL_GENERATION:-}
server=${OMNIGENT_SERVER_URL:-http://omnigent:8000}

[ -n "$expected_generation" ] || { echo "credential generation is required" >&2; exit 64; }
printf '%s\n' "$expected_generation" > "$state_root/credential-generation"

until /opt/moonmind/check-codex-oauth-host.sh; do
  echo "Codex OAuth host waiting for authenticated credentials" >&2
  sleep 5
done

/opt/moonmind/check-runner-projections.sh

exec omnigent host --server "$server" --non-interactive
