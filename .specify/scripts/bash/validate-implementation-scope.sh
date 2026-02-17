#!/usr/bin/env bash

set -euo pipefail

usage() {
  cat <<USAGE
Usage: $0 --check <tasks|diff> --mode <runtime|docs> [--base-ref <ref>]

Validates minimum implementation scope for Spec Kit workflows.
USAGE
}

CHECK=""
MODE=""
BASE_REF="origin/main"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --check)
      CHECK="${2:-}"
      shift 2
      ;;
    --mode)
      MODE="${2:-}"
      shift 2
      ;;
    --base-ref)
      BASE_REF="${2:-}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage
      exit 2
      ;;
  esac
done

if [[ -z "$CHECK" || -z "$MODE" ]]; then
  usage
  exit 2
fi

if [[ "$CHECK" != "tasks" && "$CHECK" != "diff" ]]; then
  echo "--check must be one of: tasks, diff" >&2
  exit 2
fi

if [[ "$MODE" != "runtime" && "$MODE" != "docs" ]]; then
  echo "--mode must be one of: runtime, docs" >&2
  exit 2
fi

if [[ "$MODE" == "docs" ]]; then
  echo "Scope validation passed for docs mode (${CHECK} check skipped)."
  exit 0
fi

repo_root="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
cd "$repo_root"

feature_branch="$(git rev-parse --abbrev-ref HEAD)"
feature_dir="specs/${feature_branch}"
tasks_file="${feature_dir}/tasks.md"

matches_runtime_path() {
  local path="$1"
  [[ "$path" =~ ^(api_service/|moonmind/|celery_worker/|services/|docker-compose\.yaml$|docker-compose\.test\.yaml$) ]]
}

matches_validation_path() {
  local path="$1"
  [[ "$path" =~ ^tests/ ]]
}

unique_nonempty_lines() {
  awk 'NF {print $0}' | sort -u
}

validate_tasks_scope() {
  if [[ ! -f "$tasks_file" ]]; then
    echo "Scope validation failed: missing tasks file at ${tasks_file}" >&2
    return 1
  fi

  local runtime_count validation_count
  runtime_count="$((
    $(grep -E '^- \[[ Xx]\] T[0-9]+' "$tasks_file" \
      | grep -E '(api_service/|moonmind/|celery_worker/|services/|docker-compose\.yaml|docker-compose\.test\.yaml)' \
      | grep -Ev '(tests/|specs/|docs/)' \
      | wc -l)
  ))"

  validation_count="$((
    $(grep -E '^- \[[ Xx]\] T[0-9]+' "$tasks_file" \
      | grep -E '(tests/|\./tools/test_unit\.sh|validate-implementation-scope\.sh)' \
      | wc -l)
  ))"

  if (( runtime_count < 1 )); then
    echo "Scope validation failed: tasks.md must include at least one production runtime file task." >&2
    return 1
  fi

  if (( validation_count < 1 )); then
    echo "Scope validation failed: tasks.md must include at least one validation task." >&2
    return 1
  fi

  echo "Scope validation passed: tasks check (runtime tasks=${runtime_count}, validation tasks=${validation_count})."
}

validate_diff_scope() {
  local merge_base
  merge_base="$(git merge-base "$BASE_REF" HEAD 2>/dev/null || true)"
  if [[ -z "$merge_base" ]]; then
    echo "Scope validation failed: unable to resolve base ref '${BASE_REF}'." >&2
    return 1
  fi

  mapfile -t committed < <(git diff --name-only "${merge_base}"..HEAD)
  mapfile -t staged < <(git diff --name-only --cached)
  mapfile -t unstaged < <(git diff --name-only)

  local all_files
  all_files="$(printf '%s\n' "${committed[@]:-}" "${staged[@]:-}" "${unstaged[@]:-}" | unique_nonempty_lines)"

  if [[ -z "$all_files" ]]; then
    echo "Scope validation failed: no changes detected against ${BASE_REF}." >&2
    return 1
  fi

  local runtime_count=0
  local validation_count=0
  while IFS= read -r path; do
    [[ -z "$path" ]] && continue
    if matches_runtime_path "$path"; then
      ((runtime_count += 1))
    fi
    if matches_validation_path "$path"; then
      ((validation_count += 1))
    fi
  done <<< "$all_files"

  if (( runtime_count < 1 )); then
    echo "Scope validation failed: diff must include production runtime file changes." >&2
    return 1
  fi

  if (( validation_count < 1 )); then
    echo "Scope validation failed: diff must include test file changes under tests/." >&2
    return 1
  fi

  echo "Scope validation passed: diff check (runtime files=${runtime_count}, test files=${validation_count})."
}

if [[ "$CHECK" == "tasks" ]]; then
  validate_tasks_scope
else
  validate_diff_scope
fi
