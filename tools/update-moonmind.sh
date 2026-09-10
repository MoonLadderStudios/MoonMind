#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: update-moonmind.sh [--rebuild] [--build-arg NAME=VALUE ...] [service...]

Refresh MoonMind by pulling latest container images and relaunching Compose services.
Use --rebuild when you need the local Dockerfile/tooling changes in the image.
Pass --build-arg to forward compose build arguments (for example,
  CODEX_CLI_VERSION=0.104.0 to pin @openai/codex to a known-good release).

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

if ! command -v python3 >/dev/null 2>&1 || \
  ! python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))'; then
  echo "Error: Python 3.10 or newer is required on the host for deployment access checks; install python3 before updating." >&2
  exit 1
fi
if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
  COMPOSE_CMD=(docker compose)
else
  echo "Error: Docker Compose V2 is required; install the Docker Compose plugin before updating." >&2
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
ACCESS_ARGS=()
for service in "${SERVICES[@]}"; do
  ACCESS_ARGS+=(--service "$service")
done

python3 "$ROOT_DIR/moonmind/deployment_access.py" "${ACCESS_ARGS[@]}" -- "${COMPOSE_CMD[@]}"

if [[ "$REBUILD" -eq 1 ]]; then
  if [[ ${#BUILD_ARGS[@]} -eq 0 ]]; then
    run_compose build --pull "${SERVICES[@]}"
  else
    run_compose build --pull "${BUILD_ARGS[@]}" "${SERVICES[@]}"
  fi
else
  run_compose pull "${SERVICES[@]}"
fi
python3 "$ROOT_DIR/moonmind/deployment_access.py" "${ACCESS_ARGS[@]}" -- "${COMPOSE_CMD[@]}"
run_compose up -d --remove-orphans --force-recreate "${SERVICES[@]}"
