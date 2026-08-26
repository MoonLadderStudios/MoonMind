#!/bin/sh
set -eu

state_root=${OMNIGENT_STATE_PATH:-/home/app/.omnigent}
expected_generation=${CLAUDE_CREDENTIAL_GENERATION:-}

[ -n "$expected_generation" ] || { echo "credential generation is required" >&2; exit 64; }
printf '%s\n' "$expected_generation" > "$state_root/credential-generation"

unset OPENAI_API_KEY CODEX_ACCESS_TOKEN OPENAI_BASE_URL MINIMAX_API_KEY
unset ANTHROPIC_API_KEY ANTHROPIC_AUTH_TOKEN CLAUDE_API_KEY CLAUDE_CODE_OAUTH_TOKEN
unset GEMINI_API_KEY GOOGLE_API_KEY

until /opt/moonmind/check-claude-oauth-host.sh; do
  echo "Claude OAuth host waiting for authenticated credentials" >&2
  sleep 5
done
until /opt/moonmind/check-runner-projections.sh; do
  echo "Claude OAuth host waiting for a resolved Skill projection" >&2
  sleep 5
done
/opt/moonmind/clear-stale-host-daemons.sh
exec omnigent host --server "${OMNIGENT_SERVER_URL:-http://omnigent:8000}" --non-interactive
