#!/bin/sh
set -eu

state_root=${OMNIGENT_STATE_PATH:-/home/app/.omnigent}
codex_root=${CODEX_HOME:-/home/app/.codex}

case "$state_root:$codex_root" in
  /home/app/.omnigent:/home/app/.codex) ;;
  *) echo "unexpected Codex OAuth host paths" >&2; exit 64 ;;
esac

for path in "$state_root" "$codex_root"; do
  if [ -L "$path" ]; then
    echo "refusing symlinked OAuth host path" >&2
    exit 65
  fi
done

mkdir -p "$state_root"
chown 1000:1000 "$state_root"
chmod 700 "$state_root"

if [ ! -d "$codex_root" ]; then
  echo "Codex OAuth volume is missing" >&2
  exit 66
fi
if [ "$(stat -c '%u:%g' "$codex_root")" != "1000:1000" ]; then
  echo "Codex OAuth volume root must be owned by 1000:1000" >&2
  exit 67
fi
