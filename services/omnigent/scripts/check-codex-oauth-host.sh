#!/bin/sh
set -eu

state_root=${OMNIGENT_STATE_PATH:-/home/app/.omnigent}
codex_root=${CODEX_HOME:-/home/app/.codex}
expected_generation=${CODEX_CREDENTIAL_GENERATION:-}

[ "$(id -u):$(id -g)" = "1000:1000" ] || exit 70
[ "$HOME" = "/home/app" ] || exit 71
[ "$codex_root" = "/home/app/.codex" ] || exit 72
[ "$CODEX_CONFIG_HOME" = "/home/app/.codex" ] || exit 73
[ "$CODEX_CONFIG_PATH" = "/home/app/.codex/config.toml" ] || exit 74
[ -d "$codex_root" ] || exit 75
[ -w "$codex_root" ] || exit 76

for key in OPENAI_API_KEY CODEX_ACCESS_TOKEN OPENAI_BASE_URL MINIMAX_API_KEY ANTHROPIC_API_KEY ANTHROPIC_AUTH_TOKEN CLAUDE_API_KEY CLAUDE_CODE_OAUTH_TOKEN GEMINI_API_KEY GOOGLE_API_KEY; do
  eval "present=\${$key+x}"
  [ -z "$present" ] || exit 77
done

[ -n "$expected_generation" ] || exit 78
[ -f "$state_root/credential-generation" ] || exit 79
[ "$(cat "$state_root/credential-generation")" = "$expected_generation" ] || exit 80
codex login status >/dev/null 2>&1 || exit 81
command -v gh >/dev/null 2>&1 || exit 82
gh --version >/dev/null 2>&1 || exit 83
if [ -n "${GH_TOKEN:-}" ]; then
  [ "$GH_CONFIG_DIR" = "/workspaces/run/.config/gh" ] || exit 84
  [ "$(stat -c '%a' "$GH_CONFIG_DIR")" = "700" ] || exit 85
  [ "${GH_PROMPT_DISABLED:-}" = "1" ] || exit 86
  [ "${GH_NO_UPDATE_NOTIFIER:-}" = "1" ] || exit 87
  [ "${GH_NO_EXTENSION_UPDATE_NOTIFIER:-}" = "1" ] || exit 88
  gh auth status >/dev/null 2>&1 || exit 89
fi
