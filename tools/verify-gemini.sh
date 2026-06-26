#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
verify-gemini.sh - Verify Gemini CLI availability inside a container.

Options:
  --prompt "text"     Override the prompt used for the live request.
  --model NAME        Gemini model to target (default: gemini-1.5-flash if supported by the CLI).
  --skip-prompt       Skip the live prompt call (still checks binary + version).
  -h, --help          Show this help message.

Environment variables:
  GOOGLE_API_KEY or GEMINI_API_KEY   Required for the live prompt check unless --skip-prompt is provided.
  GEMINI_MODEL                       Optional model override; same as --model.
  VERIFY_GEMINI_SKIP_PROMPT          If set to a truthy value, skips the live prompt check.
  MOONMIND_GEMINI_CLI_AUTH_MODE      Auth mode for prompt check: api_key (default) or oauth.
USAGE
}

PROMPT="Gemini CLI connectivity check. Respond with a brief acknowledgment."
MODEL="${GEMINI_MODEL:-gemini-1.5-flash}"
SKIP_PROMPT=false
AUTH_MODE="${MOONMIND_GEMINI_CLI_AUTH_MODE:-api_key}"

if [[ -n "${VERIFY_GEMINI_SKIP_PROMPT:-}" ]]; then
  SKIP_PROMPT=true
fi

AUTH_MODE="$(printf '%s' "$AUTH_MODE" | tr '[:upper:]' '[:lower:]')"
if [[ "$AUTH_MODE" != "api_key" && "$AUTH_MODE" != "oauth" ]]; then
  echo "Unknown MOONMIND_GEMINI_CLI_AUTH_MODE='$AUTH_MODE'; defaulting to api_key." >&2
  AUTH_MODE="api_key"
fi

while [[ $# -gt 0 ]]; do
  case "$1" in
    --prompt)
      if [[ $# -lt 2 || "${2:0:1}" == "-" ]]; then
        echo "Error: --prompt requires a value that is not another option." >&2
        usage
        exit 1
      fi
      PROMPT="$2"
      shift 2
      ;;
    --model)
      if [[ $# -lt 2 || "${2:0:1}" == "-" ]]; then
        echo "Error: --model requires a value that is not another option." >&2
        usage
        exit 1
      fi
      MODEL="$2"
      shift 2
      ;;
    --skip-prompt)
      SKIP_PROMPT=true
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage
      exit 1
      ;;
  esac
done

if ! command -v gemini >/dev/null 2>&1; then
  echo "gemini binary not found in PATH" >&2
  exit 1
fi

echo "Gemini CLI path: $(command -v gemini)"
echo "Checking gemini --version..."
VERSION_OUTPUT="$(gemini --version 2>&1)"
echo "$VERSION_OUTPUT"
if [[ "$VERSION_OUTPUT" == *"gemini-cli stub"* ]]; then
  echo "Gemini CLI stub detected; real CLI is not installed." >&2
  exit 1
fi

declare -a MODEL_FLAG=()
if [[ -n "${MODEL:-}" ]]; then
  MODEL_FLAG=(--model "$MODEL")
fi

if [[ "$SKIP_PROMPT" == true ]]; then
  echo "Skipping live prompt check (requested)."
  exit 0
fi

if [[ "$AUTH_MODE" == "oauth" ]]; then
  echo "Running live prompt in OAuth mode (API keys are ignored for this check)..."
  unset GOOGLE_API_KEY GEMINI_API_KEY || true
  gemini "${MODEL_FLAG[@]}" "$PROMPT"
  exit 0
fi

if [[ -z "${GOOGLE_API_KEY:-${GEMINI_API_KEY:-}}" ]]; then
  echo "GOOGLE_API_KEY or GEMINI_API_KEY must be set for api_key mode. Use --skip-prompt or set MOONMIND_GEMINI_CLI_AUTH_MODE=oauth." >&2
  exit 1
fi

echo "Running live prompt against model '${MODEL}'..."
gemini "${MODEL_FLAG[@]}" "$PROMPT"
