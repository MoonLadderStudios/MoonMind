#!/bin/sh
# Thin compatibility wrapper (MoonLadderStudios/MoonMind#3834).
#
# The Claude static-host lifecycle now lives in the generic entrypoint
# /opt/moonmind/start-omnigent-host.sh. This wrapper selects only the trusted
# pack ref and delegates; it must not retain a duplicate lifecycle
# implementation. Retirement: remove after no deployment or replay-visible
# path references this name and the generic Claude static row has passed its
# exact-image and lifecycle gates (see STATIC_HOST_STARTUP_INVENTORY.md).
set -eu
export MOONMIND_OMNIGENT_RUNTIME_PACK_REF=claude-native-pack@1
exec /opt/moonmind/start-omnigent-host.sh
