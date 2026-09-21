#!/bin/sh
# Generic static-connected Omnigent host entrypoint (MoonLadderStudios/MoonMind#3834).
#
# One trusted implementation owns shared host preparation and Omnigent startup
# for the static Codex and Claude services, which share the digest-pinned
# `omnigent-host-moonmind` image. Runtime differences resolve only through the
# trusted pack/materializer registries:
#
#   omnigent-host-codex  -> MOONMIND_OMNIGENT_RUNTIME_PACK_REF=codex-native-pack@1
#   omnigent-host-claude -> MOONMIND_OMNIGENT_RUNTIME_PACK_REF=claude-native-pack@1
#
# The entrypoint must not accept arbitrary shell commands, target paths, or
# environment allowlists from Compose variables or workflow input. Any variable
# that would smuggle such authority fails closed here.
set -eu

pack_ref=${MOONMIND_OMNIGENT_RUNTIME_PACK_REF:-}
server=${OMNIGENT_SERVER_URL:-http://omnigent:8000}
state_root=${OMNIGENT_STATE_PATH:-/home/app/.omnigent}
skills=${MOONMIND_ACTIVE_SKILLS_DIR:-/opt/moonmind-skills}

# --- 1. Trusted pack/materializer selection (exact match only) ---------------
case "$pack_ref" in
  codex-native-pack@1|claude-native-pack@1) ;;
  *) echo "unsupported runtime pack ref: ${pack_ref:-<unset>}" >&2; exit 64 ;;
esac
materializer_ref=${MOONMIND_OMNIGENT_CREDENTIAL_MATERIALIZER_REF:-}
case "$pack_ref:$materializer_ref" in
  "codex-native-pack@1:codex-oauth-home@1"|"claude-native-pack@1:claude-oauth-home@1") ;;
  *) echo "unsupported runtime pack/materializer combination: ${pack_ref:-<unset>}/${materializer_ref:-<unset>}" >&2; exit 64 ;;
esac

# --- 2. Reject unapproved ambient authority -----------------------------------
# These names would let Compose variables or workflow input smuggle arbitrary
# commands, target paths, or environment allowlists into the trusted host.
# They are never read; their mere presence fails closed.
for key in MOONMIND_OMNIGENT_ENTRYPOINT_CMD MOONMIND_OMNIGENT_EXTRA_ARGS \
    MOONMIND_OMNIGENT_ENV_ALLOWLIST MOONMIND_OMNIGENT_TARGET_PATH \
    MOONMIND_OMNIGENT_MOUNT_PATH OMNIGENT_HOST_CMD OMNIGENT_HOST_ENTRYPOINT_ARGS; do
  eval "present=\${$key+x}"
  if [ -n "$present" ]; then
    echo "unapproved host control variable is set: $key" >&2
    exit 64
  fi
done

# --- 3. Pack-specific generation + layout --------------------------------------
if [ "$pack_ref" = "codex-native-pack@1" ]; then
  expected_generation=${CODEX_CREDENTIAL_GENERATION:-}
  credential_root=${CODEX_HOME:-/home/app/.codex}
  case "$credential_root:${CODEX_CONFIG_HOME:-/home/app/.codex}:${CODEX_CONFIG_PATH:-/home/app/.codex/config.toml}" in
    "/home/app/.codex:/home/app/.codex:/home/app/.codex/config.toml") ;;
    *)
      # Compose pins the canonical layout; anything else fails closed instead
      # of silently staging credentials to an unattested path.
      echo "unexpected Codex credential layout" >&2
      exit 64
      ;;
  esac
else
  expected_generation=${CLAUDE_CREDENTIAL_GENERATION:-}
  credential_root=${CLAUDE_CONFIG_DIR:-/home/app/.claude}
  case "$credential_root:${CLAUDE_HOME:-/home/app/.claude}" in
    "/home/app/.claude:/home/app/.claude") ;;
    *)
      echo "unexpected Claude credential layout" >&2
      exit 64
      ;;
  esac
fi

[ -n "$expected_generation" ] || { echo "credential generation is required" >&2; exit 64; }
[ "$(id -u):$(id -g)" = "1000:1000" ] || { echo "static host must run as 1000:1000" >&2; exit 70; }
[ "${HOME:-}" = "/home/app" ] || { echo "static host HOME must be /home/app" >&2; exit 70; }
[ -d "$credential_root" ] && [ -w "$credential_root" ] || {
  echo "credential home is missing or not writable: $credential_root" >&2
  exit 66
}

# --- 4. Reject unapproved ambient credential selectors -------------------------
# A static service must never become a multi-profile host merely because the
# shared image contains several CLIs. Any API-key-shaped ambient credential or
# cross-runtime OAuth selector fails closed before staging.
for key in OPENAI_API_KEY CODEX_ACCESS_TOKEN OPENAI_BASE_URL MINIMAX_API_KEY \
    ANTHROPIC_API_KEY ANTHROPIC_AUTH_TOKEN CLAUDE_API_KEY CLAUDE_CODE_OAUTH_TOKEN \
    GEMINI_API_KEY GOOGLE_API_KEY OPENCODE_AUTH_CONTENT OPENCODE_CONFIG \
    OPENCODE_CONFIG_CONTENT; do
  eval "present=\${$key+x}"
  if [ -n "$present" ]; then
    echo "unapproved ambient credential selector is set: $key" >&2
    exit 64
  fi
done

# Cross-runtime generation markers must be absent: the Codex row carries only
# CODEX_CREDENTIAL_GENERATION and the Claude row only CLAUDE_CREDENTIAL_GENERATION.
if [ "$pack_ref" = "codex-native-pack@1" ]; then
  if [ -n "${CLAUDE_CREDENTIAL_GENERATION+x}" ] || [ -n "${OPENCODE_CREDENTIAL_GENERATION+x}" ]; then
    echo "cross-runtime credential generation is set for codex pack" >&2
    exit 64
  fi
else
  if [ -n "${CODEX_CREDENTIAL_GENERATION+x}" ] || [ -n "${OPENCODE_CREDENTIAL_GENERATION+x}" ]; then
    echo "cross-runtime credential generation is set for claude pack" >&2
    exit 64
  fi
fi

# --- 5. Skill + mounted-tool validation -----------------------------------------
[ -d "$skills" ] || { echo "active Skill dir is missing: $skills" >&2; exit 69; }
[ -r "$skills/_manifest.json" ] || { echo "active Skill manifest is missing" >&2; exit 69; }
grep -q '"snapshot_id"' "$skills/_manifest.json" || {
  echo "active Skill manifest has no snapshot_id" >&2
  exit 69
}
[ -r /opt/moonmind-tools/manifest.json ] || { echo "mounted-tool manifest is missing" >&2; exit 69; }
[ -x /opt/moonmind-tools/bin/gh ] || { echo "mounted gh tool is missing" >&2; exit 69; }

# --- 6. Host control credentials (approved mounted material only) ----------------
control_file=${MOONMIND_OMNIGENT_CONTROL_CREDENTIAL_FILE:-}
if [ -n "$control_file" ]; then
  case "$control_file" in
    /run/moonmind-host-auth/*|/home/app/.omnigent/*) ;;
    *) echo "unapproved control credential path" >&2; exit 64 ;;
  esac
  [ -f "$control_file" ] && [ -r "$control_file" ] || {
    echo "control credential file is missing" >&2
    exit 69
  }
fi

# --- 7. Generic GitHub CLI setup (bounded, non-secret path selector) -------------
# Restart semantics (MoonLadderStudios/MoonMind#3936, coordinated with #4011):
# a restart without a new GH_TOKEN preserves the previously persisted
# hosts.yml untouched (no deletion path exists below); unsetting the
# environment selectors after the write removes them from this process only
# and does not revoke cached credential authority.
github_token=${GH_TOKEN:-}
github_config_home=${XDG_CONFIG_HOME:-/home/app/.cache/moonmind-xdg}
case "$github_config_home" in
  /home/app/.cache/*) ;;
  *) echo "unapproved GitHub config home" >&2; exit 64 ;;
esac
github_config_dir=$github_config_home/gh
if [ -n "$github_token" ]; then
  case "$github_token" in
    *[!A-Za-z0-9_]*)
      echo "GitHub credential contains unsupported characters" >&2
      exit 64
      ;;
  esac
  github_config_tmp=$github_config_dir/hosts.yml.tmp.$$
  umask 077
  mkdir -p "$github_config_dir"
  {
    printf 'github.com:\n'
    printf '    oauth_token: %s\n' "$github_token"
    printf '    git_protocol: https\n'
  } > "$github_config_tmp"
  mv "$github_config_tmp" "$github_config_dir/hosts.yml"
fi
# gh migrates an unversioned config on every invocation and reads the account
# name from api.github.com to do it, so one blocked or throttled provider call
# fails every gh command in the host -- including the --version build probe.
# Declare the schema version the projection above already satisfies. This runs
# for a preserved restart too, repairing a config written before the marker.
if [ -f "$github_config_dir/hosts.yml" ]; then
  umask 077
  github_version_tmp=$github_config_dir/config.yml.tmp.$$
  printf 'version: "1"\n' > "$github_version_tmp"
  mv "$github_version_tmp" "$github_config_dir/config.yml"
fi
unset github_token GH_TOKEN GIT_TOKEN GITHUB_TOKEN

# --- 8. Bounded pack staging ------------------------------------------------------
mkdir -p "$state_root"
printf '%s\n' "$expected_generation" > "$state_root/credential-generation"
printf '%s\n' "$pack_ref" > "$state_root/runtime-pack"
# The staged marker records which generation this container staged; it is not
# admission evidence by itself. Admission authority stays with the Provider
# Profile lease/generation, host binding/lease, and attestation owners named
# in moonmind.omnigent.harness_platform.static_hosts.static_host_authority_notes.
# Verify the staging write read it back before gating on it.
[ "$(cat "$state_root/credential-generation")" = "$expected_generation" ] || {
  echo "credential generation staging verification failed" >&2
  exit 69
}

# --- 9. Bounded readiness gates --------------------------------------------------
# An optional static service may wait for operator enrollment, but a workflow
# must not wait indefinitely and process existence is never admitted usable
# capacity: the workflow admission/retry deadline and the capacity, lease,
# attestation, and session owners decide admitted capacity. These loops bound
# the operator waiting state with distinct waiting vs failed outcomes: expiry
# exits nonzero (waiting-for-enrollment / missing-projection) so the
# waiting state stays distinguishable from ready, and Docker restart policy
# re-enters the wait instead of hanging a workflow forever.
credential_timeout=${MOONMIND_OMNIGENT_STATIC_CREDENTIAL_TIMEOUT_SECONDS:-1800}
skill_timeout=${MOONMIND_OMNIGENT_STATIC_SKILL_TIMEOUT_SECONDS:-600}
readiness_interval=5
# Validate each timeout independently: the joined "1800:600" value always
# contains the ":" separator, which itself matches a digits-only rejection
# pattern applied to the pair. Checking the pair jointly exits 64 even for
# the defaults, before either readiness probe can run.
case "$credential_timeout" in
  ""|*[!0-9]*) echo "static readiness timeouts must be positive integers" >&2; exit 64 ;;
esac
case "$skill_timeout" in
  ""|*[!0-9]*) echo "static readiness timeouts must be positive integers" >&2; exit 64 ;;
esac
[ "$credential_timeout" -ge "$readiness_interval" ] || { echo "credential readiness timeout is below the probe interval" >&2; exit 64; }
[ "$skill_timeout" -ge "$readiness_interval" ] || { echo "skill readiness timeout is below the probe interval" >&2; exit 64; }
if [ "$pack_ref" = "codex-native-pack@1" ]; then
  waiting_label="Codex static host waiting for authenticated credentials"
else
  waiting_label="Claude static host waiting for authenticated credentials"
fi
elapsed=0
until /opt/moonmind/check-omnigent-host.sh; do
  if [ "$elapsed" -ge "$credential_timeout" ]; then
    echo "static host waiting-for-enrollment deadline exceeded after ${credential_timeout}s: authenticated credentials not ready (operator enrollment pending, not admitted capacity)" >&2
    exit 69
  fi
  echo "$waiting_label" >&2
  sleep "$readiness_interval"
  elapsed=$((elapsed + readiness_interval))
done

elapsed=0
until /opt/moonmind/check-runner-projections.sh; do
  if [ "$elapsed" -ge "$skill_timeout" ]; then
    echo "static host Skill-projection deadline exceeded after ${skill_timeout}s: no resolved Skill projection (missing projection, not admitted capacity)" >&2
    exit 69
  fi
  echo "Static host waiting for a resolved Skill projection" >&2
  sleep "$readiness_interval"
  elapsed=$((elapsed + readiness_interval))
done
/opt/moonmind/clear-stale-host-daemons.sh

exec omnigent host --server "$server" --non-interactive
