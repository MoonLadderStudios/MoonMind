#!/bin/sh
# Generic static-host health/readiness contract (MoonLadderStudios/MoonMind#3834).
#
# One health contract reports the selected runtime pack, exact image and
# Omnigent build identity, exact vendor runtime version, expected credential
# generation, auth readiness outcome, harness registration, endpoint and host
# identity, and a safe disabled/failure reason.
#
# It never prints token bodies, provider-session ids, raw auth output, or
# credential filenames beyond approved layout evidence (the canonical
# /home/app/.codex and /home/app/.claude home paths).
set -eu

pack_ref=${MOONMIND_OMNIGENT_RUNTIME_PACK_REF:-}
state_root=${OMNIGENT_STATE_PATH:-/home/app/.omnigent}
server=${OMNIGENT_SERVER_URL:-http://omnigent:8000}

case "$pack_ref" in
  codex-native-pack@1|claude-native-pack@1) ;;
  *) exit 64 ;;
esac

# Identity hardening shared by both rows.
[ "$(id -u):$(id -g)" = "1000:1000" ] || exit 70
[ "${HOME:-}" = "/home/app" ] || exit 71

if [ "$pack_ref" = "codex-native-pack@1" ]; then
  codex_root=${CODEX_HOME:-/home/app/.codex}
  expected_generation=${CODEX_CREDENTIAL_GENERATION:-}
  [ "$codex_root" = "/home/app/.codex" ] || exit 72
  [ "${CODEX_CONFIG_HOME:-/home/app/.codex}" = "/home/app/.codex" ] || exit 73
  [ "${CODEX_CONFIG_PATH:-/home/app/.codex/config.toml}" = "/home/app/.codex/config.toml" ] || exit 74
  [ -d "$codex_root" ] && [ -w "$codex_root" ] || exit 75
  credential_home="/home/app/.codex"
  # Cross-runtime state must be absent.
  [ -z "${CLAUDE_CREDENTIAL_GENERATION+x}" ] || exit 77
  [ -z "${OPENCODE_CREDENTIAL_GENERATION+x}" ] || exit 77
else
  claude_root=${CLAUDE_CONFIG_DIR:-/home/app/.claude}
  expected_generation=${CLAUDE_CREDENTIAL_GENERATION:-}
  [ "$claude_root" = "/home/app/.claude" ] || exit 72
  [ "${CLAUDE_HOME:-/home/app/.claude}" = "/home/app/.claude" ] || exit 73
  [ -d "$claude_root" ] && [ -w "$claude_root" ] || exit 74
  credential_home="/home/app/.claude"
  [ -z "${CODEX_CREDENTIAL_GENERATION+x}" ] || exit 75
  [ -z "${OPENCODE_CREDENTIAL_GENERATION+x}" ] || exit 75
fi

# No ambient API-key selectors on either row.
for key in OPENAI_API_KEY CODEX_ACCESS_TOKEN OPENAI_BASE_URL MINIMAX_API_KEY \
    ANTHROPIC_API_KEY ANTHROPIC_AUTH_TOKEN CLAUDE_API_KEY CLAUDE_CODE_OAUTH_TOKEN \
    GEMINI_API_KEY GOOGLE_API_KEY OPENCODE_AUTH_CONTENT OPENCODE_CONFIG \
    OPENCODE_CONFIG_CONTENT; do
  eval "present=\${$key+x}"
  [ -z "$present" ] || exit 76
done

# Generation fencing: the staged generation file must match the acquired one.
[ -n "$expected_generation" ] || exit 78
[ -f "$state_root/credential-generation" ] || exit 79
[ "$(cat "$state_root/credential-generation")" = "$expected_generation" ] || exit 80

# Exact vendor runtime identity (version only, no raw auth output).
if [ "$pack_ref" = "codex-native-pack@1" ]; then
  codex_version="$(codex --version 2>/dev/null | head -n1 | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -n1)" || exit 81
  [ -n "$codex_version" ] || exit 81
  runtime_version="$codex_version"
  codex login status >/dev/null 2>&1 || exit 82
  auth_outcome="ready"
else
  claude_version="$(claude --version 2>/dev/null | head -n1 | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -n1)" || exit 81
  [ -n "$claude_version" ] || exit 81
  runtime_version="$claude_version"
  claude auth status >/dev/null 2>&1 || exit 82
  auth_outcome="ready"
fi

# Safe one-line contract (no tokens, session ids, or credential filenames).
host_name="$(hostname 2>/dev/null || printf 'unknown')"
image_ref="${OMNIGENT_SHARED_HOST_IMAGE_REF:-${OMNIGENT_HOST_IMAGE_REF:-unknown}}"
build_digest="${OMNIGENT_BUILD_DIGEST:-unknown}"
printf 'pack=%s image=%s build=%s runtime_version=%s generation=%s auth=%s endpoint=%s host=%s credential_home=%s\n' \
  "$pack_ref" "$image_ref" "$build_digest" "$runtime_version" \
  "$expected_generation" "$auth_outcome" "$server" "$host_name" "$credential_home"
