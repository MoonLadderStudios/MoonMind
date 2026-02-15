#!/usr/bin/env bash
set -euo pipefail

docker compose run --rm --user app codex-worker \
  bash -lc 'export CODEX_HOME=/home/app/.codex CODEX_CONFIG_HOME=/home/app/.codex CODEX_CONFIG_PATH=/home/app/.codex/config.toml; codex login --device-auth && codex login status'
