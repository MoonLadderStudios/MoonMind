#!/usr/bin/env bash
set -euo pipefail

docker compose run --rm --user app codex-worker \
  bash -lc 'codex login --device-auth && codex login status'
