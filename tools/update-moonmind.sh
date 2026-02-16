#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: update-moonmind.sh [--rebuild] [--build-arg NAME=VALUE ...] [service...]

Refresh MoonMind by pulling latest container images and relaunching Compose services.
Use --rebuild when you need the local Dockerfile/tooling changes in the image.
Pass --build-arg to forward compose build arguments (for example,
  CODEX_CLI_VERSION=0.101.0 to avoid known @openai/codex@latest install issues).

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

REBUILD=0
BUILD_ARGS=()

while [[ "${1:-}" == --* ]]; do
  case "$1" in
    --rebuild)
      REBUILD=1
      shift
      ;;
    --build-arg)
      if [[ $# -lt 2 ]]; then
        echo "Error: --build-arg requires NAME=VALUE" >&2
        usage
        exit 1
      fi
      BUILD_ARGS+=("--build-arg" "$2")
      shift 2
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      break
      ;;
  esac
done

run_compose() {
  "${COMPOSE_CMD[@]}" "$@"
}

SERVICES=("$@")

if [[ "$REBUILD" -eq 1 ]]; then
  if [[ ${#BUILD_ARGS[@]} -eq 0 ]]; then
    run_compose build --pull "${SERVICES[@]}"
  else
    run_compose build --pull "${BUILD_ARGS[@]}" "${SERVICES[@]}"
  fi
else
  run_compose pull "${SERVICES[@]}"
fi
run_compose up -d --remove-orphans --force-recreate "${SERVICES[@]}"
