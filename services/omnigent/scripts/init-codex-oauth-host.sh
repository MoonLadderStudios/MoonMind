#!/bin/sh
set -eu

state_root=${OMNIGENT_STATE_PATH:-/home/app/.omnigent}
codex_root=${CODEX_HOME:-/home/app/.codex}
artifacts_root=${OMNIGENT_ARTIFACTS_PATH:-/artifacts}
cache_root=${XDG_CACHE_HOME:-/home/app/.cache}

case "$state_root:$codex_root" in
  /home/app/.omnigent:/home/app/.codex) ;;
  *) echo "unexpected Codex OAuth host paths" >&2; exit 64 ;;
esac

case "$artifacts_root:$cache_root" in
  /artifacts:/home/app/.cache) ;;
  *) echo "unexpected Codex host data paths" >&2; exit 64 ;;
esac

for path in "$state_root" "$codex_root" "$artifacts_root" "$cache_root"; do
  if [ -L "$path" ]; then
    echo "refusing symlinked OAuth host path" >&2
    exit 65
  fi
done

mkdir -p "$state_root"
chown 1000:1000 "$state_root"
chmod 700 "$state_root"

for path in "$artifacts_root" "$cache_root"; do
  mkdir -p "$path"
  chown 1000:1000 "$path"
  chmod 700 "$path"
done

if [ ! -d "$codex_root" ]; then
  echo "Codex OAuth volume is missing" >&2
  exit 66
fi
if [ "$(stat -c '%u:%g' "$codex_root")" != "1000:1000" ]; then
  echo "Codex OAuth volume root must be owned by 1000:1000" >&2
  exit 67
fi
