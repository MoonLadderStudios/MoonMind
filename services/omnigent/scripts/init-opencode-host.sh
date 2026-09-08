#!/bin/sh
set -eu

state_root=${OMNIGENT_STATE_PATH:-/home/app/.omnigent}
opencode_auth_home=${OPENCODE_AUTH_HOME:-/home/app/.local/share/opencode}

mkdir -p "$state_root" "$opencode_auth_home"
chown 1000:1000 "$state_root" "$opencode_auth_home" 2>/dev/null || true
chmod 0700 "$opencode_auth_home"
# Ensure parent .local/share exists with correct perms
mkdir -p "$(dirname "$opencode_auth_home")"
chmod 0700 "$(dirname "$opencode_auth_home")" 2>/dev/null || true
chmod 0700 "$state_root" 2>/dev/null || true

echo "OpenCode host init complete: $opencode_auth_home (0700, 1000:1000)"
