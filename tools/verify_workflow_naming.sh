#!/usr/bin/env bash
set -euo pipefail

MODE="all"
EXCEPTIONS_FILE=""

usage() {
  cat <<'USAGE'
Usage: ./tools/verify_workflow_naming.sh [--mode docs-spec|runtime|all] [--exceptions-file <path>]

Scans repository surfaces for legacy workflow naming tokens and fails when
unapproved matches are found.

Options:
  --mode             Surface to scan (default: all)
  --exceptions-file  Newline-delimited regex patterns. Matching rg output lines
                     are treated as approved exceptions.
  -h, --help         Show this message.
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --mode)
      MODE="${2:-}"
      shift 2
      ;;
    --exceptions-file)
      EXCEPTIONS_FILE="${2:-}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

case "$MODE" in
  docs-spec|runtime|all) ;;
  *)
    echo "Invalid --mode '$MODE'. Expected: docs-spec, runtime, or all." >&2
    exit 2
    ;;
esac

if [[ -n "$EXCEPTIONS_FILE" && ! -f "$EXCEPTIONS_FILE" ]]; then
  echo "Exceptions file not found: $EXCEPTIONS_FILE" >&2
  exit 2
fi

if ! command -v rg >/dev/null 2>&1; then
  echo "required command not found: rg" >&2
  exit 2
fi

PATTERN='WORKFLOW_|WORKFLOW_|/api/workflows|/api/workflows|Workflow|workflow|workflows|spec[-_]automation|moonmind\.workflow|var/artifacts/workflows'
DOCS_SPEC_GLOBS=(--glob '*.md' --glob '*.yaml' --glob '*.yml' --glob '!specs/task/**')
RUNTIME_GLOBS=(--glob '*.py' --glob '*.sh' --glob '*.md' --glob '*.yaml' --glob '*.yml')

run_rg_or_fail() {
  local mode="$1"
  local matches=""
  local rg_status=0
  if ! matches="$(rg -n "${@:2}" 2>&1)"; then
    rg_status=$?
    if [[ "$rg_status" -eq 1 ]]; then
      matches=""
    elif [[ "$rg_status" -ge 2 ]]; then
      echo "[$mode] ERROR: ripgrep failed during command execution." >&2
      printf '%s\n' "$matches" >&2
      return 2
    fi
  fi
  printf '%s' "$matches"
}

scan_mode() {
  local mode="$1"
  shift
  local -a paths=("$@")
  local -a globs
  local raw
  local filtered

  if [[ "$mode" == "docs-spec" ]]; then
    globs=("${DOCS_SPEC_GLOBS[@]}")
  else
    globs=("${RUNTIME_GLOBS[@]}")
  fi

  raw="$(run_rg_or_fail "$mode" "$PATTERN" "${paths[@]}" "${globs[@]}")"
  if [[ "$?" -ne 0 ]]; then
    return 2
  fi

  if [[ -n "$EXCEPTIONS_FILE" ]]; then
    filtered="$raw"
    local line_number=0
    while IFS= read -r exception || [[ -n "$exception" ]]; do
      ((line_number += 1))
      [[ -z "$exception" || "$exception" =~ ^[[:space:]]*# ]] && continue
      if ! filtered="$(printf '%s\n' "$filtered" | rg -v -e "$exception" 2>&1)"; then
        local filter_status=$?
        if [[ "$filter_status" -eq 1 ]]; then
          filtered=""
        else
          echo "[$mode] ERROR: invalid exception regex at $EXCEPTIONS_FILE:$line_number: $exception" >&2
          printf '%s\n' "$filtered" >&2
          return 2
        fi
      fi
    done < "$EXCEPTIONS_FILE"
  else
    filtered="$raw"
  fi

  if [[ -n "${filtered//[[:space:]]/}" ]]; then
    echo "[$mode] FAIL: Found unapproved legacy naming matches:"
    printf '%s\n' "$filtered"
    return 1
  fi

  echo "[$mode] PASS: No unapproved legacy naming matches found."
  return 0
}

status=0

if [[ "$MODE" == "docs-spec" || "$MODE" == "all" ]]; then
  if ! scan_mode "docs-spec" docs specs; then
    status=1
  fi
fi

if [[ "$MODE" == "runtime" || "$MODE" == "all" ]]; then
  if ! scan_mode "runtime" api_service moonmind services tests celery_worker; then
    status=1
  fi
fi

exit "$status"
