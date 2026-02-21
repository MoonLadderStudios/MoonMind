#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE_ROOT_DEFAULT="$(cd "${SCRIPT_DIR}/.." && pwd)"

log() {
  printf '[codex-worker] %s\n' "$*"
}

lower() {
  printf '%s' "$1" | tr '[:upper:]' '[:lower:]'
}

CODEX_VOLUME_BASE="${CODEX_VOLUME_PATH:-/home/app/.codex}"
export CODEX_HOME="${CODEX_HOME:-$CODEX_VOLUME_BASE}"
export CODEX_CONFIG_HOME="${CODEX_CONFIG_HOME:-$CODEX_HOME}"
export CODEX_CONFIG_PATH="${CODEX_CONFIG_PATH:-$CODEX_HOME/config.toml}"
CLAUDE_VOLUME_BASE="${CLAUDE_VOLUME_PATH:-/home/app/.claude}"
export CLAUDE_HOME="${CLAUDE_HOME:-$CLAUDE_VOLUME_BASE}"
# Resolve relative skill roots against the mounted repo workspace for codex jobs.
export SPEC_WORKFLOW_REPO_ROOT="${SPEC_WORKFLOW_REPO_ROOT:-${MOONMIND_WORKER_SPEC_WORKFLOW_REPO_ROOT:-$WORKSPACE_ROOT_DEFAULT}}"
export SPEC_SKILLS_LOCAL_MIRROR_ROOT="${SPEC_SKILLS_LOCAL_MIRROR_ROOT:-${MOONMIND_WORKER_SPEC_SKILLS_LOCAL_MIRROR_ROOT:-$SPEC_WORKFLOW_REPO_ROOT/.agents/skills/local}}"
export SPEC_SKILLS_LEGACY_MIRROR_ROOT="${SPEC_SKILLS_LEGACY_MIRROR_ROOT:-${MOONMIND_WORKER_SPEC_SKILLS_LEGACY_MIRROR_ROOT:-$SPEC_WORKFLOW_REPO_ROOT/.agents/skills}}"

TOKEN_PATH_DEFAULT="${CODEX_HOME}/moonmind_worker_token"
TOKEN_PATH="${MOONMIND_WORKER_TOKEN_FILE:-$TOKEN_PATH_DEFAULT}"
TOKEN_POLICY_PATH_DEFAULT="${TOKEN_PATH}.policy"
TOKEN_POLICY_PATH="${MOONMIND_WORKER_TOKEN_POLICY_FILE:-$TOKEN_POLICY_PATH_DEFAULT}"
BOOTSTRAP="$(lower "${MOONMIND_WORKER_BOOTSTRAP_TOKEN:-true}")"
ENFORCE_TOKEN_POLICY="$(lower "${MOONMIND_WORKER_ENFORCE_TOKEN_POLICY:-false}")"
ALLOWED_TYPES_RAW="${MOONMIND_WORKER_ALLOWED_TYPES:-task,codex_exec,codex_skill}"
CAPABILITIES_RAW="${MOONMIND_WORKER_CAPABILITIES:-codex,git,gh,docker,proposals_write}"
normalize_csv() {
  printf '%s' "$1" | tr -d '[:space:]'
}
desired_token_policy="allowed_types=$(normalize_csv "$ALLOWED_TYPES_RAW");capabilities=$(normalize_csv "$CAPABILITIES_RAW")"

persist_token_policy_marker() {
  local policy_dir
  policy_dir="$(dirname "$TOKEN_POLICY_PATH")"
  if ! mkdir -p "$policy_dir"; then
    log "Failed to create worker token policy directory $policy_dir"
    return 1
  fi
  if ! printf '%s\n' "$desired_token_policy" >"$TOKEN_POLICY_PATH"; then
    log "Failed to persist worker token policy marker at $TOKEN_POLICY_PATH"
    return 1
  fi
  if ! chmod 600 "$TOKEN_POLICY_PATH"; then
    log "Failed to set permissions on worker token policy marker at $TOKEN_POLICY_PATH"
    return 1
  fi
  return 0
}

if [[ -z "${MOONMIND_WORKER_TOKEN:-}" && -f "$TOKEN_PATH" ]]; then
  should_load_cached_token=true
  refresh_policy_marker=false
  if [[ "$BOOTSTRAP" == "true" ]]; then
    if [[ ! -f "$TOKEN_POLICY_PATH" ]]; then
      if [[ "$ENFORCE_TOKEN_POLICY" == "true" ]]; then
        should_load_cached_token=false
        log "Cached worker token policy marker missing at $TOKEN_POLICY_PATH; rotating token because MOONMIND_WORKER_ENFORCE_TOKEN_POLICY=true."
      else
        refresh_policy_marker=true
        log "Cached worker token policy marker missing at $TOKEN_POLICY_PATH; loading cached token for backward compatibility."
      fi
    else
      cached_token_policy="$(tr -d '\r\n' <"$TOKEN_POLICY_PATH")"
      if [[ "$cached_token_policy" != "$desired_token_policy" ]]; then
        if [[ "$ENFORCE_TOKEN_POLICY" == "true" ]]; then
          should_load_cached_token=false
          log "Cached worker token policy does not match configured allowed types/capabilities; rotating token because MOONMIND_WORKER_ENFORCE_TOKEN_POLICY=true."
        else
          refresh_policy_marker=true
          log "Cached worker token policy does not match configured allowed types/capabilities; loading cached token to avoid startup disruption."
        fi
      fi
    fi
  fi

  if [[ "$should_load_cached_token" == "true" ]]; then
    token_file_value="$(tr -d '\r\n' <"$TOKEN_PATH")"
    if [[ -n "$token_file_value" ]]; then
      export MOONMIND_WORKER_TOKEN="$token_file_value"
      log "Loaded worker token from $TOKEN_PATH"
      if [[ "$refresh_policy_marker" == "true" ]]; then
        if persist_token_policy_marker; then
          log "Refreshed worker token policy marker at $TOKEN_POLICY_PATH"
        fi
      fi
    fi
  fi
fi

if [[ -z "${MOONMIND_WORKER_TOKEN:-}" && "$BOOTSTRAP" == "true" ]]; then
  log "No MOONMIND_WORKER_TOKEN provided; requesting a worker token from MoonMind API."
  mkdir -p "$(dirname "$TOKEN_PATH")" "$(dirname "$TOKEN_POLICY_PATH")"
  worker_token="$(
    python - <<'PY'
import json
import os
import sys
import time
import urllib.error
import urllib.request

moonmind_url = os.environ.get("MOONMIND_URL", "http://api:5000").rstrip("/")
worker_id = os.environ.get("MOONMIND_WORKER_ID", "codex-worker-1").strip()
description = os.environ.get(
    "MOONMIND_WORKER_TOKEN_DESCRIPTION",
    "Docker Compose codex worker bootstrap token",
).strip()
allowed_types = [
    item.strip()
    for item in os.environ.get(
        "MOONMIND_WORKER_ALLOWED_TYPES",
        "task,codex_exec,codex_skill",
    ).split(",")
    if item.strip()
]
capabilities = [
    item.strip()
    for item in os.environ.get(
        "MOONMIND_WORKER_CAPABILITIES", "codex,git,gh,docker,proposals_write"
    ).split(",")
    if item.strip()
]

payload = {
    "workerId": worker_id,
    "description": description,
}
if allowed_types:
    payload["allowedJobTypes"] = allowed_types
if capabilities:
    payload["capabilities"] = capabilities

headers = {
    "Accept": "application/json",
    "Content-Type": "application/json",
}
api_token = os.environ.get("MOONMIND_API_TOKEN", "").strip()
if api_token:
    headers["Authorization"] = f"Bearer {api_token}"

url = f"{moonmind_url}/api/queue/workers/tokens"
body_bytes = json.dumps(payload).encode("utf-8")

for attempt in range(1, 61):
    try:
        request = urllib.request.Request(
            url=url,
            data=body_bytes,
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            content = response.read().decode("utf-8")
        parsed = json.loads(content)
        token = str(parsed.get("token", "")).strip()
        if not token:
            raise RuntimeError("API response did not include token")
        print(token)
        sys.exit(0)
    except urllib.error.HTTPError as exc:
        _ = exc.read()
        if exc.code in (401, 403):
            print(
                "MoonMind API rejected worker token bootstrap ("
                f"HTTP {exc.code}). Set MOONMIND_WORKER_TOKEN manually or "
                "provide MOONMIND_API_TOKEN for authenticated bootstrap.",
                file=sys.stderr,
            )
            sys.exit(1)
        if attempt == 60:
            print(
                "Failed to bootstrap worker token after 60 attempts "
                f"(HTTP {exc.code}).",
                file=sys.stderr,
            )
            sys.exit(1)
    except Exception as exc:
        if attempt == 60:
            print(
                "Failed to bootstrap worker token after 60 attempts: "
                f"{exc}",
                file=sys.stderr,
            )
            sys.exit(1)
    time.sleep(1)
PY
  )"
  export MOONMIND_WORKER_TOKEN="$worker_token"
  printf '%s\n' "$worker_token" >"$TOKEN_PATH"
  chmod 600 "$TOKEN_PATH"
  if persist_token_policy_marker; then
    :
  fi
  log "Persisted worker token to $TOKEN_PATH"
fi

if [[ -z "${MOONMIND_WORKER_TOKEN:-}" ]]; then
  log "MOONMIND_WORKER_TOKEN is empty and bootstrap is disabled."
  exit 1
fi

retry_enabled="$(lower "${MOONMIND_WORKER_RETRY_ENABLED:-true}")"
retry_seconds="${MOONMIND_WORKER_RETRY_SECONDS:-20}"

while true; do
  set +e
  moonmind-codex-worker "$@"
  exit_code=$?
  set -e
  if [[ "$exit_code" -eq 0 ]]; then
    exit 0
  fi
  if [[ "$retry_enabled" != "true" ]]; then
    log "moonmind-codex-worker exited with code $exit_code and retries are disabled."
    exit "$exit_code"
  fi
  log "moonmind-codex-worker exited with code $exit_code; retrying in ${retry_seconds}s."
  sleep "$retry_seconds"
done
