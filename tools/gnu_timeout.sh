#!/usr/bin/env bash
# Ubuntu's default timeout may be uutils, whose SIGINT/grace behavior differs.
# Keep one selector for host test deadlines; never silently run unbounded.
set -euo pipefail
for name in gnutimeout timeout gtimeout; do
  if executable="$(type -P "$name")" && version="$("$executable" --version 2>/dev/null)"; then
    first_line="${version%%$'\n'*}"
    if [[ "$first_line" == *"(GNU coreutils)"* ]]; then
      printf 'Deadline command: %s (%s)\n' "$executable" "$first_line" >&2
      exec "$executable" "$@"
    fi
  fi
done
printf 'Error: GNU coreutils timeout is required (gnutimeout, timeout, or gtimeout).\n' >&2
exit 125
