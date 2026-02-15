#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: update-moonmind.sh [service...]

Refresh MoonMind by pulling latest container images and relaunching Compose services.

If no services are specified, all services in the current compose configuration
are pulled and relaunched.
USAGE
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "$ROOT_DIR"

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
  COMPOSE_CMD=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE_CMD=(docker-compose)
else
  echo "Error: docker compose CLI is not available." >&2
  echo "Install Docker with Compose V2 plugin or legacy docker-compose." >&2
  exit 1
fi

run_compose() {
  "${COMPOSE_CMD[@]}" "$@"
}

SERVICES=("$@")

run_compose pull "${SERVICES[@]}"
run_compose up -d --remove-orphans --force-recreate "${SERVICES[@]}"
