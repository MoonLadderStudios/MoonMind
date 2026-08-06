#!/bin/sh
set -eu

state_root=${OMNIGENT_STATE_PATH:-/home/app/.omnigent}
expected_generation=${CODEX_CREDENTIAL_GENERATION:-}
server=${OMNIGENT_SERVER_URL:-http://omnigent:8000}

[ -n "$expected_generation" ] || { echo "credential generation is required" >&2; exit 64; }
printf '%s\n' "$expected_generation" > "$state_root/credential-generation"

# The native Codex app-server intentionally filters token-shaped environment
# variables before it starts.  Materialize gh's standard config in the
# lease-owned cache volume instead, then remove the raw token before Omnigent
# can spawn a runner.  XDG_CONFIG_HOME is a non-secret path selector that the
# stock host and Codex both preserve; the cache volume is destroyed with this
# on-demand host and is outside the captured repository workspace.
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

until /opt/moonmind/check-codex-oauth-host.sh; do
  echo "Codex OAuth host waiting for authenticated credentials" >&2
  sleep 5
done

until /opt/moonmind/check-runner-projections.sh; do
  echo "Codex OAuth host waiting for a resolved Skill projection" >&2
  sleep 5
done
/opt/moonmind/clear-stale-host-daemons.sh

exec omnigent host --server "$server" --non-interactive
