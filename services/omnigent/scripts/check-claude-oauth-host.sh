#!/bin/sh
set -eu

state_root=${OMNIGENT_STATE_PATH:-/home/app/.omnigent}
claude_root=${CLAUDE_CONFIG_DIR:-/home/app/.claude}
expected_generation=${CLAUDE_CREDENTIAL_GENERATION:-}

[ "$(id -u):$(id -g)" = "1000:1000" ] || exit 70
[ "$HOME" = "/home/app" ] || exit 71
[ "$claude_root" = "/home/app/.claude" ] || exit 72
[ "$CLAUDE_HOME" = "/home/app/.claude" ] || exit 73
[ -d "$claude_root" ] && [ -w "$claude_root" ] || exit 74

for key in OPENAI_API_KEY CODEX_ACCESS_TOKEN OPENAI_BASE_URL MINIMAX_API_KEY ANTHROPIC_API_KEY ANTHROPIC_AUTH_TOKEN CLAUDE_API_KEY CLAUDE_CODE_OAUTH_TOKEN GEMINI_API_KEY GOOGLE_API_KEY; do
  eval "present=\${$key+x}"
  [ -z "$present" ] || exit 75
done

[ -n "$expected_generation" ] || exit 76
[ -f "$state_root/credential-generation" ] || exit 77
[ "$(cat "$state_root/credential-generation")" = "$expected_generation" ] || exit 78
claude auth status >/dev/null 2>&1 || exit 79
