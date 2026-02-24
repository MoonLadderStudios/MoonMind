#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: run-update-moonmind.sh [options]

Options:
  --repo <path>              Target repository path (default: current directory)
  --branch <name>            Branch to checkout before pulling (default: main)
  -h, --help                 Show this help
EOF
}

say() {
  printf '[update-moonmind] %s\n' "$*"
}

die() {
  printf '[update-moonmind] ERROR: %s\n' "$*" >&2
  exit 1
}

run_cmd() {
  if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
    :
  elif command -v docker-compose >/dev/null 2>&1; then
    :
  else
    die "Docker compose command not found."
  fi

  printf '[update-moonmind] $'
  for arg in "$@"; do
    printf ' %q' "$arg"
  done
  printf '\n'
  "$@"
}

compose_cmd() {
  if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
    echo docker compose
  elif command -v docker-compose >/dev/null 2>&1; then
    echo docker-compose
  else
    die "Docker compose command not found."
  fi
}

COMPOSE_CMD=()
read -r -a COMPOSE_CMD <<<"$(compose_cmd)"

REPO_PATH="."
BRANCH="main"

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
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "Unknown argument: $1"
      ;;
  esac
done

if [[ ! -d "$REPO_PATH" ]]; then
  die "Repository path does not exist: $REPO_PATH"
fi

cd "$REPO_PATH"

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  die "Path is not a git repository: $REPO_PATH"
fi

say "Fetching remote branch '$BRANCH' from origin"
run_cmd git fetch --prune origin "${BRANCH}"

if ! git rev-parse --verify "origin/$BRANCH" >/dev/null 2>&1; then
  die "Remote branch 'origin/$BRANCH' does not exist."
fi

BASE_REMOTE_COMMIT="$(git rev-parse "origin/$BRANCH")"

say "Checking out branch: $BRANCH"
run_cmd git checkout "$BRANCH"

say "Pulling latest git changes"
run_cmd git pull --ff-only

say "Pulling updated compose images"
run_cmd "${COMPOSE_CMD[@]}" pull

if [[ "$BASE_REMOTE_COMMIT" == "$(git rev-parse HEAD)" ]]; then
  say "No new commit received from origin/$BRANCH; skipping container updates."
  exit 0
fi

mapfile -t CHANGED_FILES < <(git diff --name-only "${BASE_REMOTE_COMMIT}..HEAD" --)

if [[ ${#CHANGED_FILES[@]} -eq 0 ]]; then
  say "No changed files detected; skipping container updates."
  exit 0
fi

mapfile -t COMPOSE_SERVICES < <("${COMPOSE_CMD[@]}" config --services)

declare -A available_services=()
for service in "${COMPOSE_SERVICES[@]}"; do
  available_services["$service"]=1
done

declare -A target_services=()

add_target() {
  local service="$1"
  if [[ -z "$service" ]]; then
    return
  fi
  if [[ "$service" == "orchestrator" ]]; then
    return
  fi
  if [[ -v available_services["$service"] ]]; then
    target_services["$service"]=1
  fi
}

add_all_non_orchestrator_services() {
  for service in "${COMPOSE_SERVICES[@]}"; do
    if [[ "$service" != "orchestrator" ]]; then
      target_services["$service"]=1
    fi
  done
}

for changed_file in "${CHANGED_FILES[@]}"; do
  case "$changed_file" in
    docker-compose*.y*ml | .env* | AGENTS.md | .env-template* | .env.vllm-template* | .gitmodules )
      add_all_non_orchestrator_services
      ;;
    services/*)
      service="${changed_file#services/}"
      service="${service%%/*}"
      add_target "$service"
      ;;
    moonmind/*|tools/*|.agents/*|.gemini/*|specs/*|.specify/*|docs/*|README*|LICENSE|.gitignore )
      add_all_non_orchestrator_services
      ;;
    api_service/*)
      add_all_non_orchestrator_services
      ;;
    init_db/*)
      add_target "init-db"
      ;;
    celery_worker/*)
      add_target "celery-worker"
      add_target "celery-codex-0"
      add_target "celery-codex-1"
      add_target "celery-codex-2"
      ;;
    keycloak/*)
      add_target "keycloak"
      add_target "keycloak-db"
      ;;
    *)
      :
      ;;
  esac
done

readarray -t SERVICES_TO_RESTART < <(
  for target in "${!target_services[@]}"; do
    case "$target" in
      docker-proxy | agent-workspaces-init | codex-auth-init | gemini-auth-init )
        continue
        ;;
      *)
        printf '%s\n' "$target"
        ;;
    esac
  done | sort
)

if [[ ${#SERVICES_TO_RESTART[@]} -eq 0 ]]; then
  say "No restartable changed services detected after filtering."
  exit 0
fi

say "Restarting changed services (excluding orchestrator): ${SERVICES_TO_RESTART[*]}"
run_cmd "${COMPOSE_CMD[@]}" up -d --remove-orphans --no-deps --force-recreate "${SERVICES_TO_RESTART[@]}"

say "MoonMind update complete"
