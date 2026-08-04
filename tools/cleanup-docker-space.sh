#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: cleanup-docker-space.sh [--dry-run] [--aggressive] [--help]

Run Docker-space cleanup tasks to reclaim local disk.

Default mode is safe/recovering:
  - Remove unused images from labeled pre-cutover MoonMind session sidecars
  - Remove stopped containers
  - Remove dangling images
  - Prune builder cache

Use --aggressive to also remove all unused images and unused volumes.
Use --dry-run to inspect disk usage and print cleanup commands without deleting.
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

cleanup_failed=0

print_command() {
  printf 'Would run:'
  printf ' %q' "$@"
  printf '\n'
}

run_cleanup() {
  if [[ "$DRY_RUN" -eq 1 ]]; then
    print_command "$@"
    return 0
  fi

  printf 'Running:'
  printf ' %q' "$@"
  printf '\n'
  if ! "$@"; then
    echo "Warning: cleanup command failed; continuing with the remaining recovery steps." >&2
    cleanup_failed=1
  fi
}

show_docker_usage() {
  echo "== Docker disk usage (best-effort) =="
  if ! docker system df; then
    echo "Warning: Docker disk usage is unavailable; continuing with cleanup." >&2
  fi
}

prune_pre_cutover_sidecars() {
  local sidecar_output
  local sidecar_id

  echo "== Pre-cutover MoonMind session sidecars =="
  if ! sidecar_output="$(
    docker ps \
      --filter 'label=moonmind.kind=session-docker-sidecar' \
      --format '{{.ID}}'
  )"; then
    echo "Warning: could not discover managed-session sidecars." >&2
    cleanup_failed=1
    return
  fi

  if [[ -z "$sidecar_output" ]]; then
    echo "No active pre-cutover session sidecars found."
    return
  fi

  while IFS= read -r sidecar_id; do
    [[ -n "$sidecar_id" ]] || continue
    if [[ ! "$sidecar_id" =~ ^[0-9a-f]{12,64}$ ]]; then
      echo "Warning: refusing unexpected Docker container id: $sidecar_id" >&2
      cleanup_failed=1
      continue
    fi

    echo "Sidecar $sidecar_id disk usage (best-effort):"
    if ! docker exec "$sidecar_id" docker \
      -H unix:///var/run/moonmind-docker/docker.sock system df; then
      echo "Warning: sidecar $sidecar_id disk usage is unavailable." >&2
    fi
    run_cleanup docker exec "$sidecar_id" docker \
      -H unix:///var/run/moonmind-docker/docker.sock image prune -af
  done <<<"$sidecar_output"
}

show_docker_usage

if [[ "$DRY_RUN" -eq 1 ]]; then
  echo "Dry-run mode: no data will be removed."
fi

prune_pre_cutover_sidecars

echo "== Host cleanup plan =="
if [[ "$AGGRESSIVE" -eq 1 ]]; then
  echo "Mode: aggressive"
else
  echo "Mode: safe recovery"
fi

run_cleanup docker container prune -f
run_cleanup docker image prune -f
run_cleanup docker builder prune -af
if [[ "$AGGRESSIVE" -eq 1 ]]; then
  run_cleanup docker image prune -af
  run_cleanup docker volume prune -f
fi

show_docker_usage

if [[ "$cleanup_failed" -ne 0 ]]; then
  echo "Cleanup completed with one or more failures." >&2
  exit 1
fi

echo "Cleanup complete."
