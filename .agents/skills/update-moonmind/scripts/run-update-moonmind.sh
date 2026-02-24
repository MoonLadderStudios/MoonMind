#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: run-update-moonmind.sh [options]

Options:
  --repo <path>              Target repository path (default: current directory)
  --branch <name>            Branch to checkout before pulling (default: main)
  --allow-dirty              Allow running with uncommitted git changes
  --no-compose-pull          Skip docker compose pull step
  --dry-run                  Print commands without executing them
  --restart-orchestrator     Include orchestrator in service restart list
  -h, --help                 Show this help
EOF
}

timestamp_utc() {
  date -u +"%Y-%m-%dT%H:%M:%SZ"
}

escape_json() {
  local value="$1"
  value=${value//\\/\\\\}
  value=${value//\"/\\\"}
  value=${value//$'\n'/\\n}
  value=${value//$'\r'/\\r}
  value=${value//$'\t'/\\t}
  printf '%s' "$value"
}

current_actor() {
  if command -v id >/dev/null 2>&1; then
    id -un 2>/dev/null || whoami 2>/dev/null || printf 'unknown'
  else
    whoami 2>/dev/null || printf 'unknown'
  fi
}

log_json() {
  local level="$1"
  local message="$2"
  printf '{"tool":"update-moonmind","timestamp":"%s","user":"%s","level":"%s","message":"%s"}\n' \
    "$(timestamp_utc)" \
    "$(escape_json "$(current_actor)")" \
    "$(escape_json "$level")" \
    "$(escape_json "$message")"
}

say() {
  log_json "INFO" "$*"
}

die() {
  log_json "ERROR" "$*" >&2
  exit 1
}

run_cmd() {
  local cmd=()
  cmd=("$@")
  if [[ "$DRY_RUN" == "true" ]]; then
    printf '[dry-run]'
    for arg in "${cmd[@]}"; do
      printf ' %q' "$arg"
    done
    printf '\n'
    return 0
  fi

  printf '[update-moonmind] $'
  for arg in "${cmd[@]}"; do
    printf ' %q' "$arg"
  done
  printf '\n'
  set +e
  "${cmd[@]}"
  local exit_code=$?
  set -e
  if [[ $exit_code -ne 0 ]]; then
    die "Command failed with exit code $exit_code: ${cmd[*]}"
  fi
}

load_compose_service_images() {
  if ! command -v jq >/dev/null 2>&1; then
    say "jq unavailable; skipping compose image drift checks."
    return 1
  fi

  local line
  declare -gA COMPOSE_SERVICE_IMAGE=()
  mapfile -t compose_image_lines < <("${COMPOSE_CMD[@]}" config --format json | jq -r '.services | to_entries[] | "\(.key)|\(.value.image // empty)"')
  for line in "${compose_image_lines[@]}"; do
    service="${line%%|*}"
    image="${line#*|}"
    COMPOSE_SERVICE_IMAGE["$service"]="$image"
  done
  return 0
}

mark_stale_services_for_restart() {
  local service image expected_image_id
  local -a container_ids=()
  local container_id running_image_id compose_image_id

  for service in "${COMPOSE_SERVICES[@]}"; do
    if [[ "$service" == "orchestrator" && "$RESTART_ORCHESTRATOR" != "true" ]]; then
      continue
    fi

    image="${COMPOSE_SERVICE_IMAGE[$service]:-}"
    if [[ -z "$image" ]]; then
      continue
    fi

    mapfile -t container_ids < <("${COMPOSE_CMD[@]}" ps -q "$service" 2>/dev/null || true)
    if [[ ${#container_ids[@]} -eq 0 ]]; then
      continue
    fi

    expected_image_id="$(docker image inspect "$image" --format '{{.Id}}' 2>/dev/null || true)"
    if [[ -z "$expected_image_id" ]]; then
      continue
    fi

    for container_id in "${container_ids[@]}"; do
      compose_image_id="$(docker inspect "$container_id" --format '{{index .Config.Labels "com.docker.compose.image"}}' 2>/dev/null || true)"
      running_image_id="$(docker inspect "$container_id" --format '{{.Config.Image}}' 2>/dev/null || true)"

      if [[ "$compose_image_id" == "$expected_image_id" || "$running_image_id" == "$expected_image_id" ]]; then
        continue
      fi

      if [[ -n "$compose_image_id" ]]; then
        say "Marking service '$service' for restart: image drift detected (running ${compose_image_id} vs expected ${expected_image_id})."
      else
        say "Marking service '$service' for restart: image drift detected (running ${running_image_id} vs expected ${expected_image_id})."
      fi
      add_target "$service"
      break
    done
  done
}

mark_not_running_services_for_restart() {
  local service container_id container_status
  local -a container_ids=()
  local all_running=true

  for service in "${COMPOSE_SERVICES[@]}"; do
    if [[ "$service" == "orchestrator" && "$RESTART_ORCHESTRATOR" != "true" ]]; then
      continue
    fi

    case "$service" in
      docker-proxy | agent-workspaces-init | codex-auth-init | gemini-auth-init)
        continue
        ;;
    esac

    mapfile -t container_ids < <("${COMPOSE_CMD[@]}" ps -q "$service" 2>/dev/null || true)
    if [[ ${#container_ids[@]} -eq 0 ]]; then
      continue
    fi

    all_running=true
    for container_id in "${container_ids[@]}"; do
      container_status="$(docker inspect "$container_id" --format '{{.State.Status}}' 2>/dev/null || true)"
      if [[ "$container_status" != "running" ]]; then
        all_running=false
        break
      fi
    done

    if [[ "$all_running" == "false" ]]; then
      say "Marking service '$service' for restart: service is not running."
      add_target "$service"
    fi
  done
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
ALLOW_DIRTY="false"
SKIP_COMPOSE_PULL="false"
DRY_RUN="false"
RESTART_ORCHESTRATOR="false"

validate_branch() {
  local branch="$1"
  [[ -n "$branch" ]] || die "--branch cannot be empty"
  [[ "$branch" != -* ]] || die "--branch must not start with '-'"
  [[ "$branch" != *[[:space:]]* ]] || die "--branch must not include whitespace"
  [[ "$branch" != *$'\n'* ]] || die "--branch must not include control characters"
  git check-ref-format --branch "$branch" >/dev/null 2>&1 || \
    die "Invalid --branch value: $branch"
}

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
    --restart-orchestrator)
      RESTART_ORCHESTRATOR="true"
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

validate_branch "$BRANCH"

if [[ ! -d "$REPO_PATH" ]]; then
  die "Repository path does not exist: $REPO_PATH"
fi

cd "$REPO_PATH"

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  die "Path is not a git repository: $REPO_PATH"
fi

if [[ "$ALLOW_DIRTY" != "true" ]]; then
  if [[ -n "$(git status --porcelain)" ]]; then
    die "Working tree is not clean. Commit/stash changes or rerun with --allow-dirty."
  fi
fi

PRE_PULL_COMMIT="$(git rev-parse HEAD)"

say "Fetching remote branch '$BRANCH' from origin"
run_cmd git fetch --prune origin -- "$BRANCH"

if ! git rev-parse --verify "origin/$BRANCH" >/dev/null 2>&1; then
  die "Remote branch 'origin/$BRANCH' does not exist."
fi

say "Checking out local branch '$BRANCH' tracking origin/$BRANCH"
run_cmd git checkout -B "$BRANCH" "origin/$BRANCH"

say "Pulling latest git changes"
run_cmd git pull --ff-only origin "$BRANCH"

POST_PULL_COMMIT="$(git rev-parse HEAD)"

if [[ "$SKIP_COMPOSE_PULL" != "true" ]]; then
  say "Pulling updated compose images"
  run_cmd "${COMPOSE_CMD[@]}" pull
fi

if [[ "$PRE_PULL_COMMIT" == "$POST_PULL_COMMIT" ]]; then
  say "No new commit received from origin/$BRANCH; skipping file-based restart detection."
fi

mapfile -t CHANGED_FILES < <(
  if [[ "$PRE_PULL_COMMIT" == "$POST_PULL_COMMIT" ]]; then
    printf ''
  else
    git diff --name-only "${PRE_PULL_COMMIT}..${POST_PULL_COMMIT}" --
  fi
)

if [[ "$PRE_PULL_COMMIT" == "$POST_PULL_COMMIT" && "$SKIP_COMPOSE_PULL" == "true" ]]; then
  say "No commit update detected and compose image refresh was skipped; only stale image checks that can be resolved locally were applied."
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
  if [[ "$service" == "orchestrator" && "$RESTART_ORCHESTRATOR" != "true" ]]; then
    return
  fi
  if [[ -v available_services["$service"] ]]; then
    target_services["$service"]=1
  fi
}

add_all_services() {
  for service in "${COMPOSE_SERVICES[@]}"; do
    if [[ "$service" == "orchestrator" && "$RESTART_ORCHESTRATOR" != "true" ]]; then
      continue
    fi
    if [[ -v available_services["$service"] ]]; then
      target_services["$service"]=1
    fi
  done
}

if load_compose_service_images; then
  mark_stale_services_for_restart
fi
mark_not_running_services_for_restart

for changed_file in "${CHANGED_FILES[@]}"; do
  case "$changed_file" in
    docker-compose*.y*ml | .env* | AGENTS.md | .env-template* | .env.vllm-template* | .gitmodules )
      add_all_services
      ;;
    services/*)
      service="${changed_file#services/}"
      service="${service%%/*}"
      add_target "$service"
      ;;
    moonmind/*|tools/*|.agents/*|.gemini/*|specs/*|.specify/*|docs/*|README*|LICENSE|.gitignore )
      add_all_services
      ;;
    api_service/*)
      add_all_services
      ;;
    init_db/*)
      add_target "init-db"
      ;;
    celery_worker/*)
      add_target "celery-worker"
      for service in "${COMPOSE_SERVICES[@]}"; do
        [[ "$service" == celery-codex-* ]] && add_target "$service"
      done
      ;;
    keycloak/*)
      add_target "keycloak"
      add_target "keycloak-db"
      ;;
    *)
      add_all_services
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
  say "No restartable services detected after filtering."
  exit 0
fi

if [[ "$RESTART_ORCHESTRATOR" == "true" ]]; then
  say "Restarting changed services: ${SERVICES_TO_RESTART[*]}"
else
  say "Restarting changed services (excluding orchestrator): ${SERVICES_TO_RESTART[*]}"
fi
run_cmd "${COMPOSE_CMD[@]}" up -d --remove-orphans --no-deps --force-recreate "${SERVICES_TO_RESTART[@]}"

say "MoonMind update complete"
