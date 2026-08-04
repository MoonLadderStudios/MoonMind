#!/usr/bin/env bash
set -euo pipefail

if [[ -n "${MOONMIND_AGENT_RUN_ID:-}" && -n "${MOONMIND_RUNTIME_ID:-}" && -n "${MOONMIND_URL:-}" ]]; then
  exec python3 "$(dirname "${BASH_SOURCE[0]}")/run_moonmind_unreal_tactics.py" "$@"
fi

usage() {
  cat <<'EOF'
Run Unreal Tactics build/tests from a MoonMind worker via Docker-outside-Docker.

Usage:
  run_dood_unreal_tactics.sh [options]

Options:
  --repo <path>               Absolute repo path (default: /mnt/d/Unreal/Tactics)
  --image <image>             Unreal runner image
                              (default: ghcr.io/epicgames/unreal-engine:dev-5.5@sha256:3f7b292cda7f6066aeaea46fa95a520a0d26811810e0d082cfbf5dc85018bd82)
  --platform <value>          Docker platform (default: linux/amd64)
  --phase <value>             all|build|test (default: all)
  --target <name>             UBT target (default: TacticsEditor)
  --build-platform <name>     UBT platform arg (default: Linux)
  --configuration <name>      UBT configuration arg (default: Development)
  --uproject <path>           Uproject path relative to repo (default: Tactics.uproject)
  --test-filter <value>       Automation filter (default: Tactics.Unit.PlayerReadyNotification)
  --ccache-dir <path>         Host ccache dir (default: ~/.ccache)
  --ubt-dir <path>            Host UnrealBuildTool metadata dir
                              (default: ~/.config/Epic/UnrealBuildTool)
  --workspace-volume <name>   Docker volume name for workspace (overrides bind mount)
  --workspace-target <path>   Workspace mount target (default: /work/agent_jobs)
  --ccache-volume <name>      Docker volume name for ccache (overrides --ccache-dir bind mount)
  --ubt-volume <name>         Docker volume name for UBT metadata (overrides --ubt-dir bind mount)
  --results-subdir <path>     Relative artifact dir in repo
                              (default: .artifacts/dood-unreal-tactics)
  --gate-file <path>          Gate JSON path (repo-relative)
                              (default: <results-subdir>/latest/gate.json)
  --pull <policy>             if-missing|always|never (default: if-missing)
  --dry-run                   Print docker commands and exit
  -h, --help                  Show this help

Examples:
  run_dood_unreal_tactics.sh
  run_dood_unreal_tactics.sh --phase build
  run_dood_unreal_tactics.sh --phase test --test-filter "Tactics.Unit.HostRuntime.MatrixSystems"
EOF
}

fail() {
  echo "[ERROR] $*" >&2
  exit 1
}

json_escape() {
  local value="${1-}"
  value="${value//\\/\\\\}"
  value="${value//\"/\\\"}"
  value="${value//$'\n'/\\n}"
  value="${value//$'\r'/\\r}"
  value="${value//$'\t'/\\t}"
  printf '%s' "$value"
}

print_cmd() {
  printf '%q ' "$@"
  echo
}

run_with_log() {
  local log_file="$1"
  shift
  local cmd=("$@")

  print_cmd "${cmd[@]}"
  set +e
  "${cmd[@]}" 2>&1 | tee "$log_file"
  local status=${PIPESTATUS[0]}
  set -e
  return "$status"
}

validate_volume_name() {
  local name="$1"
  local field="$2"
  if [[ ! "$name" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$ ]]; then
    fail "$field must be a safe Docker volume name: [A-Za-z0-9._-]"
  fi
}

validate_mount_target() {
  local target="$1"
  local field="$2"
  if [[ ! "$target" =~ ^/[A-Za-z0-9/_.-]+$ ]] || [[ "$target" == *".."* ]]; then
    fail "$field must be an absolute, slash-delimited path without traversal"
  fi
}

resolve_and_validate_gate_file() {
  local input="$1"
  local normalized
  if [[ -z "$input" ]]; then
    fail "--gate-file requires a value"
  fi
  if [[ "$input" == /* ]]; then
    fail "--gate-file must be repo-relative"
  fi
  normalized="$(realpath -m "$REPO_DIR/$input")"
  if [[ "$normalized" != "$REPO_DIR"/* ]]; then
    fail "--gate-file must resolve within repo: $input"
  fi
  if [[ "$normalized" =~ [^/A-Za-z0-9._-] ]]; then
    fail "--gate-file contains unsupported characters: $input"
  fi
  printf '%s' "$normalized"
}

REPO_DIR="/mnt/d/Unreal/Tactics"
IMAGE="ghcr.io/epicgames/unreal-engine:dev-5.5@sha256:3f7b292cda7f6066aeaea46fa95a520a0d26811810e0d082cfbf5dc85018bd82"
PLATFORM="linux/amd64"
PHASE="all"
TARGET="TacticsEditor"
BUILD_PLATFORM="Linux"
CONFIGURATION="Development"
UPROJECT_PATH="Tactics.uproject"
TEST_FILTER="Tactics.Unit.PlayerReadyNotification"
CCACHE_DIR="${HOME}/.ccache"
UBT_DIR="${HOME}/.config/Epic/UnrealBuildTool"
WORKSPACE_VOLUME=""
WORKSPACE_TARGET="/work/agent_jobs"
CCACHE_VOLUME=""
UBT_VOLUME=""
RESULTS_SUBDIR=".artifacts/dood-unreal-tactics"
GATE_FILE_INPUT=""
PULL_POLICY="if-missing"
DRY_RUN=0

results_host_dir=""
build_log=""
test_log=""

GATE_FILE=""
GATE_STATUS="FAIL"
GATE_REASON="Workflow did not complete"
GATE_DOCKER_STATUS="not_checked"
GATE_BUILD_STATUS="not_run"
GATE_TEST_STATUS="not_run"
GATE_SOURCE="run_dood_unreal_tactics.sh"
GATE_TIMESTAMP=""

write_gate_result() {
  [[ -n "${GATE_FILE:-}" ]] || return 0
  mkdir -p "$(dirname "$GATE_FILE")"
  GATE_TIMESTAMP="${GATE_TIMESTAMP:-$(date -u +%Y-%m-%dT%H:%M:%SZ)}"
  cat >"$GATE_FILE" <<EOF
{
  "status": "$(json_escape "$GATE_STATUS")",
  "reason": "$(json_escape "$GATE_REASON")",
  "timestamp": "$(json_escape "$GATE_TIMESTAMP")",
  "source": "$(json_escape "$GATE_SOURCE")",
  "repo": "$(json_escape "$REPO_DIR")",
  "phase": "$(json_escape "$PHASE")",
  "dockerStatus": "$(json_escape "$GATE_DOCKER_STATUS")",
  "buildStatus": "$(json_escape "$GATE_BUILD_STATUS")",
  "testStatus": "$(json_escape "$GATE_TEST_STATUS")",
  "resultsDir": "$(json_escape "$results_host_dir")",
  "buildLog": "$(json_escape "$build_log")",
  "testLog": "$(json_escape "$test_log")"
}
EOF
}

finalize_gate() {
  local exit_code=$?
  if [[ "$exit_code" -ne 0 && "$GATE_STATUS" == "PASS" ]]; then
    GATE_STATUS="FAIL"
    GATE_REASON="Workflow exited non-zero ($exit_code)"
  fi
  write_gate_result || true
}

trap finalize_gate EXIT

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo)
      [[ $# -ge 2 ]] || fail "--repo requires a value"
      REPO_DIR="$2"
      shift 2
      ;;
    --image)
      [[ $# -ge 2 ]] || fail "--image requires a value"
      IMAGE="$2"
      shift 2
      ;;
    --platform)
      [[ $# -ge 2 ]] || fail "--platform requires a value"
      PLATFORM="$2"
      shift 2
      ;;
    --phase)
      [[ $# -ge 2 ]] || fail "--phase requires a value"
      PHASE="$2"
      shift 2
      ;;
    --target)
      [[ $# -ge 2 ]] || fail "--target requires a value"
      TARGET="$2"
      shift 2
      ;;
    --build-platform)
      [[ $# -ge 2 ]] || fail "--build-platform requires a value"
      BUILD_PLATFORM="$2"
      shift 2
      ;;
    --configuration)
      [[ $# -ge 2 ]] || fail "--configuration requires a value"
      CONFIGURATION="$2"
      shift 2
      ;;
    --uproject)
      [[ $# -ge 2 ]] || fail "--uproject requires a value"
      UPROJECT_PATH="$2"
      shift 2
      ;;
    --test-filter)
      [[ $# -ge 2 ]] || fail "--test-filter requires a value"
      TEST_FILTER="$2"
      shift 2
      ;;
    --ccache-dir)
      [[ $# -ge 2 ]] || fail "--ccache-dir requires a value"
      CCACHE_DIR="$2"
      shift 2
      ;;
    --ubt-dir)
      [[ $# -ge 2 ]] || fail "--ubt-dir requires a value"
      UBT_DIR="$2"
      shift 2
      ;;
    --workspace-volume)
      [[ $# -ge 2 ]] || fail "--workspace-volume requires a value"
      validate_volume_name "$2" "--workspace-volume"
      WORKSPACE_VOLUME="$2"
      shift 2
      ;;
    --workspace-target)
      [[ $# -ge 2 ]] || fail "--workspace-target requires a value"
      WORKSPACE_TARGET="$2"
      shift 2
      ;;
    --ccache-volume)
      [[ $# -ge 2 ]] || fail "--ccache-volume requires a value"
      validate_volume_name "$2" "--ccache-volume"
      CCACHE_VOLUME="$2"
      shift 2
      ;;
    --ubt-volume)
      [[ $# -ge 2 ]] || fail "--ubt-volume requires a value"
      validate_volume_name "$2" "--ubt-volume"
      UBT_VOLUME="$2"
      shift 2
      ;;
    --results-subdir)
      [[ $# -ge 2 ]] || fail "--results-subdir requires a value"
      RESULTS_SUBDIR="$2"
      shift 2
      ;;
    --gate-file)
      [[ $# -ge 2 ]] || fail "--gate-file requires a value"
      GATE_FILE_INPUT="$2"
      shift 2
      ;;
    --pull)
      [[ $# -ge 2 ]] || fail "--pull requires a value"
      PULL_POLICY="$2"
      shift 2
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      fail "Unknown argument: $1"
      ;;
  esac
done

case "$PHASE" in
  all|build|test) ;;
  *) fail "--phase must be one of: all, build, test" ;;
esac

case "$PULL_POLICY" in
  if-missing|always|never) ;;
  *) fail "--pull must be one of: if-missing, always, never" ;;
esac

[[ "$RESULTS_SUBDIR" != /* ]] || fail "--results-subdir must be relative to the repo"

command -v docker >/dev/null 2>&1 || fail "docker command not found on PATH"

REPO_DIR="$(realpath "$REPO_DIR")"
[[ -d "$REPO_DIR" ]] || fail "Repo directory not found: $REPO_DIR"
[[ -f "$REPO_DIR/$UPROJECT_PATH" ]] || fail "Uproject not found: $REPO_DIR/$UPROJECT_PATH"

if [[ -n "$GATE_FILE_INPUT" ]]; then
  GATE_FILE="$(resolve_and_validate_gate_file "$GATE_FILE_INPUT")"
else
  GATE_FILE="$REPO_DIR/$RESULTS_SUBDIR/latest/gate.json"
fi

if [[ -n "$WORKSPACE_VOLUME" ]]; then
  validate_mount_target "$WORKSPACE_TARGET" "--workspace-target"
  WORKSPACE_MOUNT="type=volume,src=$WORKSPACE_VOLUME,dst=$WORKSPACE_TARGET"
  PROJECT_WORKDIR="$REPO_DIR"
else
  WORKSPACE_MOUNT="type=bind,src=$REPO_DIR,dst=/project"
  PROJECT_WORKDIR="/project"
fi

if [[ -n "$CCACHE_VOLUME" ]]; then
  CCACHE_MOUNT="type=volume,src=$CCACHE_VOLUME,dst=/home/ue4/.ccache"
else
  mkdir -p "$CCACHE_DIR"
  CCACHE_DIR="$(realpath "$CCACHE_DIR")"
  CCACHE_MOUNT="type=bind,src=$CCACHE_DIR,dst=/home/ue4/.ccache"
fi

if [[ -n "$UBT_VOLUME" ]]; then
  UBT_MOUNT="type=volume,src=$UBT_VOLUME,dst=/home/ue4/.config/Epic/UnrealBuildTool"
else
  mkdir -p "$UBT_DIR"
  UBT_DIR="$(realpath "$UBT_DIR")"
  UBT_MOUNT="type=bind,src=$UBT_DIR,dst=/home/ue4/.config/Epic/UnrealBuildTool"
fi

if [[ "$DRY_RUN" -ne 1 ]]; then
  GATE_DOCKER_STATUS="checking"
  if ! docker info >/dev/null 2>&1; then
    GATE_DOCKER_STATUS="fail"
    GATE_REASON="Cannot reach Docker daemon (check socket access from the worker)"
    fail "Cannot reach Docker daemon (check socket access from the worker)"
  fi
  GATE_DOCKER_STATUS="pass"

  if [[ "$PULL_POLICY" == "always" ]]; then
    docker pull "$IMAGE" >/dev/null
  elif [[ "$PULL_POLICY" == "if-missing" ]]; then
    if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
      docker pull "$IMAGE" >/dev/null
    fi
  fi
else
  GATE_DOCKER_STATUS="skipped"
fi

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
results_host_dir="$REPO_DIR/$RESULTS_SUBDIR/$timestamp"
build_log="$results_host_dir/build.log"
test_log="$results_host_dir/test.log"

build_container_name="mm-dood-tactics-build-${timestamp,,}"
test_container_name="mm-dood-tactics-test-${timestamp,,}"

UNREAL_BUILD="/home/ue4/UnrealEngine/Engine/Build/BatchFiles/Linux/Build.sh"
UNREAL_EDITOR="/home/ue4/UnrealEngine/Engine/Binaries/Linux/UnrealEditor-Cmd"

build_inner_cmd_args=(
  "$UNREAL_BUILD"
  "$TARGET"
  "$BUILD_PLATFORM"
  "$CONFIGURATION"
  "-project=${PROJECT_WORKDIR}/${UPROJECT_PATH}"
  "-NoHotReload"
)

build_inner_cmd="$(printf '%q ' "${build_inner_cmd_args[@]}")"
test_exec_cmd="Automation RunTests ${TEST_FILTER}; Quit"

build_cmd=(
  docker run --rm
  --name "$build_container_name"
  --platform "$PLATFORM"
  --workdir "$PROJECT_WORKDIR"
  -u root
  -e UE_CCACHE=1
  -e CCACHE_DIR=/home/ue4/.ccache
  --mount "$WORKSPACE_MOUNT"
  --mount "$CCACHE_MOUNT"
  --mount "$UBT_MOUNT"
  "$IMAGE"
  bash -lc "su -s /bin/bash -c '$build_inner_cmd' ue4"
)

test_cmd=(
  docker run --rm
  --name "$test_container_name"
  --platform "$PLATFORM"
  --workdir "$PROJECT_WORKDIR"
  --mount "$WORKSPACE_MOUNT"
  --mount "$UBT_MOUNT"
  "$IMAGE"
  "$UNREAL_EDITOR"
  "${PROJECT_WORKDIR}/$UPROJECT_PATH"
  -nullrhi
  -unattended
  -nop4
  -nosplash
  -NoSound
  -nohmd
  -nopause
  -log
  "-ExecCmds=$test_exec_cmd"
)

echo "Repo:              $REPO_DIR"
echo "Image:             $IMAGE"
echo "Platform:          $PLATFORM"
echo "Phase:             $PHASE"
echo "Target:            $TARGET $BUILD_PLATFORM $CONFIGURATION"
echo "Test filter:       $TEST_FILTER"
echo "Artifacts:         $results_host_dir"
echo "Workspace mount:   $WORKSPACE_MOUNT"
echo "ccache mount:      $CCACHE_MOUNT"
echo "UBT mount:         $UBT_MOUNT"
echo "Pull policy:       $PULL_POLICY"
echo "Gate file:         $GATE_FILE"

if [[ "$DRY_RUN" -eq 1 ]]; then
  GATE_STATUS="SKIPPED"
  GATE_REASON="Dry-run mode; build/test not executed"
  GATE_BUILD_STATUS="skipped"
  GATE_TEST_STATUS="skipped"
  echo
  if [[ "$PHASE" == "all" || "$PHASE" == "build" ]]; then
    echo "Build command:"
    print_cmd "${build_cmd[@]}"
  fi
  if [[ "$PHASE" == "all" || "$PHASE" == "test" ]]; then
    echo "Test command:"
    print_cmd "${test_cmd[@]}"
  fi
  exit 0
fi

mkdir -p "$results_host_dir"

if [[ "$PHASE" == "all" || "$PHASE" == "build" ]]; then
  if ! run_with_log "$build_log" "${build_cmd[@]}"; then
    GATE_BUILD_STATUS="fail"
    GATE_REASON="Build phase failed"
    echo "[FAIL] Build failed. Log: $build_log"
    exit 1
  fi
  GATE_BUILD_STATUS="pass"
  echo "[OK] Build completed. Log: $build_log"
else
  GATE_BUILD_STATUS="skipped"
fi

if [[ "$PHASE" == "all" || "$PHASE" == "test" ]]; then
  if ! run_with_log "$test_log" "${test_cmd[@]}"; then
    GATE_TEST_STATUS="fail"
    GATE_REASON="Test phase failed"
    echo "[FAIL] Test run failed. Log: $test_log"
    exit 1
  fi
  GATE_TEST_STATUS="pass"
  echo "[OK] Test run completed. Log: $test_log"
else
  GATE_TEST_STATUS="skipped"
fi

echo
echo "Workflow complete."
echo "Artifacts: $results_host_dir"
GATE_STATUS="PASS"
GATE_REASON="Requested phases completed successfully"
