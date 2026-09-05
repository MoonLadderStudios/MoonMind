#!/bin/sh
# Thin compatibility wrapper (MoonLadderStudios/MoonMind#3834).
#
# Codex static-host readiness now lives in the generic contract
# /opt/moonmind/check-omnigent-host.sh. This wrapper selects only the trusted
# pack ref and delegates; it must not retain duplicate probe logic.
# Retirement: remove after no deployment or replay-visible path references
# this name and the generic Codex static row has passed its exact-image and
# lifecycle gates (see STATIC_HOST_STARTUP_INVENTORY.md).
set -eu
export MOONMIND_OMNIGENT_RUNTIME_PACK_REF=codex-native-pack@1
exec /opt/moonmind/check-omnigent-host.sh
