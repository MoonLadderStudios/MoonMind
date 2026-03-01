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

PATTERN='SPEC_WORKFLOW_|SPEC_AUTOMATION_|/api/spec-automation|/api/workflows/speckit|SpecWorkflow|spec_workflow|spec_workflows|spec-automation|spec_automation|moonmind\.spec_workflow|var/artifacts/spec_workflows'
DOCS_SPEC_GLOBS=(--glob '*.md' --glob '*.yaml' --glob '*.yml' --glob '!specs/task/**')
RUNTIME_GLOBS=(--glob '*.py' --glob '*.sh' --glob '*.md' --glob '*.yaml' --glob '*.yml')

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

  raw="$(rg -n "$PATTERN" "${paths[@]}" "${globs[@]}" || true)"

  if [[ -n "$EXCEPTIONS_FILE" ]]; then
    filtered="$raw"
    while IFS= read -r exception || [[ -n "$exception" ]]; do
      [[ -z "$exception" || "$exception" =~ ^[[:space:]]*# ]] && continue
      filtered="$(printf '%s\n' "$filtered" | rg -v "$exception" || true)"
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
  if ! scan_mode "runtime" api_service moonmind services tests; then
    status=1
  fi
fi

exit "$status"
