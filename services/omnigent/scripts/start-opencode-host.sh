#!/bin/sh
set -eu

state_root=${OMNIGENT_STATE_PATH:-/home/app/.omnigent}
opencode_auth=${OPENCODE_AUTH_PATH:-/home/app/.local/share/opencode/auth.json}
expected_generation=${OPENCODE_CREDENTIAL_GENERATION:-}
server=${OMNIGENT_SERVER_URL:-http://omnigent:8000}

[ -n "$expected_generation" ] || { echo "OpenCode credential generation is required" >&2; exit 64; }
mkdir -p "$state_root"
printf '%s\n' "$expected_generation" > "$state_root/credential-generation"
chmod 0600 "$state_root/credential-generation"
chown 1000:1000 "$state_root/credential-generation" 2>/dev/null || true

# Clear conflicting ambient credentials before host launch (issue §5)
unset OPENCODE_AUTH_CONTENT OPENCODE_CONFIG OPENCODE_CONFIG_CONTENT OPENAI_API_KEY ANTHROPIC_API_KEY 2>/dev/null || true

# Materialize GitHub token similarly to Codex host (non-secret path selector)
github_token=${GH_TOKEN:-}
if [ -n "$github_token" ]; then
  case "$github_token" in
    *[!A-Za-z0-9_]*)
      echo "GitHub credential contains unsupported characters" >&2
      exit 64
      ;;
  esac
  github_config_home=${XDG_CONFIG_HOME:-/home/app/.cache/moonmind-xdg}
  github_config_dir=$github_config_home/gh
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
unset github_token GH_TOKEN GIT_TOKEN GITHUB_TOKEN

# Wait for credential file to be materialized by trusted worker
until /opt/moonmind/check-opencode-host.sh; do
  echo "OpenCode host waiting for authenticated credentials at $opencode_auth" >&2
  sleep 5
done

until /opt/moonmind/check-runner-projections.sh; do
  echo "OpenCode host waiting for a resolved Skill projection" >&2
  sleep 5
done
/opt/moonmind/clear-stale-host-daemons.sh

# Verify opencode is ready before handing to Omnigent
command -v opencode >/dev/null || { echo "opencode binary missing" >&2; exit 70; }
opencode --version

exec omnigent host --server "$server" --non-interactive
