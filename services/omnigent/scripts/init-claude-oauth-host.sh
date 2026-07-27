#!/bin/sh
set -eu

state_root=${OMNIGENT_STATE_PATH:-/home/app/.omnigent}
claude_root=${CLAUDE_HOME:-/home/app/.claude}
artifacts_root=${OMNIGENT_ARTIFACTS_PATH:-/artifacts}
cache_root=${XDG_CACHE_HOME:-/home/app/.cache}

[ "$state_root:$claude_root" = "/home/app/.omnigent:/home/app/.claude" ] || exit 64
for path in "$state_root" "$claude_root" "$artifacts_root" "$cache_root"; do
  [ ! -L "$path" ] || { echo "refusing symlinked OAuth host path" >&2; exit 65; }
done
for path in "$state_root" "$artifacts_root" "$cache_root"; do
  mkdir -p "$path"
  chown 1000:1000 "$path"
  chmod 700 "$path"
done
[ -d "$claude_root" ] || { echo "Claude OAuth volume is missing" >&2; exit 66; }
[ "$(stat -c '%u:%g' "$claude_root")" = "1000:1000" ] || exit 67
