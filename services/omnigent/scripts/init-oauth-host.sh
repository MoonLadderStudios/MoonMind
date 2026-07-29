#!/bin/sh
set -eu

state_root=${OMNIGENT_STATE_PATH:-/home/app/.omnigent}
oauth_root=${OAUTH_HOME:-}
artifacts_root=${OMNIGENT_ARTIFACTS_PATH:-/artifacts}
cache_root=${XDG_CACHE_HOME:-/home/app/.cache}

case "$oauth_root" in
  /home/app/.codex|/home/app/.claude) ;;
  *) echo "unexpected OAuth host path" >&2; exit 64 ;;
esac

for path in "$state_root" "$oauth_root" "$artifacts_root" "$cache_root"; do
  [ ! -L "$path" ] || { echo "refusing symlinked OAuth host path" >&2; exit 65; }
done

for path in "$state_root" "$artifacts_root" "$cache_root"; do
  mkdir -p "$path"
  chown 1000:1000 "$path"
  chmod 700 "$path"
done

[ -d "$oauth_root" ] || { echo "OAuth volume is missing" >&2; exit 66; }
[ "$(stat -c '%u:%g' "$oauth_root")" = "1000:1000" ] ||
  { echo "OAuth volume root must be owned by 1000:1000" >&2; exit 67; }
