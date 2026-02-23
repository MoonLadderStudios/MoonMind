#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: run-moonmind-update.sh [options]

Options:
  --repo <path>              Target repository path (default: current directory)
  --branch <name>            Branch to checkout before pulling (default: main)
  --allow-dirty              Allow running with uncommitted git changes
  --no-compose-pull          Skip docker compose pull step
  --dry-run                  Print commands without executing them
  -h, --help                 Show this help
EOF
}

say() {
  printf '[moonmind-update] %s\n' "$*"
}

die() {
  printf '[moonmind-update] ERROR: %s\n' "$*" >&2
  exit 1
}

run_cmd() {
  if [[ "$DRY_RUN" == "true" ]]; then
    printf '[dry-run]'
    for arg in "$@"; do
      printf ' %q' "$arg"
    done
    printf '\n'
    return 0
  fi
  "$@"
}

REPO_PATH="."
BRANCH="main"
ALLOW_DIRTY="false"
SKIP_COMPOSE_PULL="false"
DRY_RUN="false"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo)
      [[ $# -ge 2 ]] || die "--repo requires a value"
      REPO_PATH="$2"
      shift 2
      ;;
    --branch)
      [[ $# -ge 2 ]] || die "--branch requires a value"
      BRANCH="$2"
      shift 2
      ;;
    --allow-dirty)
      ALLOW_DIRTY="true"
      shift
      ;;
    --no-compose-pull)
      SKIP_COMPOSE_PULL="true"
      shift
      ;;
    --dry-run)
      DRY_RUN="true"
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "Unknown argument: $1"
      ;;
  esac
done

[[ -d "$REPO_PATH" ]] || die "Repository path does not exist: $REPO_PATH"
cd "$REPO_PATH"

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  die "Path is not a git repository: $REPO_PATH"
fi

if [[ "$ALLOW_DIRTY" != "true" ]]; then
  if [[ -n "$(git status --porcelain)" ]]; then
    die "Working tree is not clean. Commit/stash changes or rerun with --allow-dirty."
  fi
fi

say "Checking out branch: $BRANCH"
run_cmd git checkout "$BRANCH"

say "Pulling latest git changes"
run_cmd git pull --ff-only

if [[ "$SKIP_COMPOSE_PULL" != "true" ]]; then
  COMPOSE=()
  if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
    COMPOSE=(docker compose)
  elif command -v docker-compose >/dev/null 2>&1; then
    COMPOSE=(docker-compose)
  else
    die "Docker Compose not found. Install it or rerun with --no-compose-pull."
  fi

  say "Pulling Docker Compose images"
  run_cmd "${COMPOSE[@]}" pull
fi

UPDATE_SCRIPT=""
for candidate in \
  "./scripts/update-moonmind.sh" \
  "./scripts/update_moonmind.sh" \
  "./update-moonmind.sh" \
  "./update_moonmind.sh" \
  "./scripts/update.sh" \
  "./update.sh"
do
  if [[ -f "$candidate" ]]; then
    UPDATE_SCRIPT="$candidate"
    break
  fi
done

if [[ -z "$UPDATE_SCRIPT" ]]; then
  die "No update script detected. Add one of the expected update script paths."
fi

say "Running detected update script: $UPDATE_SCRIPT"
run_cmd bash "$UPDATE_SCRIPT"

say "Workflow completed"
