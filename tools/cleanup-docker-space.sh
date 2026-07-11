#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: cleanup-docker-space.sh [--dry-run] [--aggressive] [--help]

Run Docker-space cleanup tasks to reclaim local disk.

Default mode is safe/recovering:
  - Remove stopped containers
  - Remove dangling images
  - Prune builder cache

Use --aggressive to also remove all unused images and unused volumes.
Use --dry-run to print what would be removed without deleting it.
USAGE
}

DRY_RUN=0
AGGRESSIVE=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    --aggressive)
      AGGRESSIVE=1
      shift
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      usage
      exit 1
      ;;
  esac
done

if ! command -v docker >/dev/null 2>&1; then
  echo "Error: docker not found in PATH." >&2
  exit 1
fi

echo "== Docker disk check for MoonMind postgres volume (best-effort) =="
if docker volume inspect moonmind_postgres-data >/dev/null 2>&1; then
  docker run --rm -v moonmind_postgres-data:/data alpine sh -c 'df -h /data'
else
  echo "Note: moonmind_postgres-data volume is not present yet."
fi

prune_arg=()
if [[ "$DRY_RUN" -eq 1 ]]; then
  prune_arg+=(--dry-run)
  echo "Dry-run mode: no data will be removed."
fi

echo "== Stopping cleanup plan =="
if [[ "$AGGRESSIVE" -eq 1 ]]; then
  echo "Mode: aggressive"
else
  echo "Mode: safe (non-destructive by default)"
fi

if [[ "$DRY_RUN" -eq 1 ]]; then
  echo "Would run: docker container prune -f ${prune_arg[*]}"
  echo "Would run: docker image prune -f ${prune_arg[*]}"
  echo "Would run: docker builder prune -f --all ${prune_arg[*]}"
  if [[ "$AGGRESSIVE" -eq 1 ]]; then
    echo "Would run: docker image prune -f -a ${prune_arg[*]}"
    echo "Would run: docker volume prune -f ${prune_arg[*]}"
  fi
else
  echo "Running docker container prune -f"
  docker container prune -f
  echo "Running docker image prune -f"
  docker image prune -f
  echo "Running docker builder prune -af"
  docker builder prune -af
  if [[ "$AGGRESSIVE" -eq 1 ]]; then
    echo "Running docker image prune -af"
    docker image prune -af
    echo "Running docker volume prune -f"
    docker volume prune -f
  fi
fi

echo "Cleanup complete."
