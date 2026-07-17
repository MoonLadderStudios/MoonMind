#!/bin/sh
set -eu

state_root=${OMNIGENT_STATE_PATH:-${HOME:?HOME is required}/.omnigent}
daemon_root=$state_root/daemons

[ -d "$daemon_root" ] || exit 0

# Daemon PIDs are scoped to the previous container. In a replacement container,
# a persisted PID 1 marker would incorrectly identify the new entrypoint as the
# old Omnigent daemon and prevent the host from starting.
for marker in "$daemon_root"/*.json; do
  [ -e "$marker" ] || continue
  rm -f -- "$marker"
done
