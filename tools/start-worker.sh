#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE_ROOT_DEFAULT="$(cd "${SCRIPT_DIR}/.." && pwd)"

log() {
  printf '[worker] %s\n' "$*"
}

lower() {
  printf '%s' "$1" | tr '[:upper:]' '[:lower:]'
}

CODEX_VOLUME_BASE="${CODEX_VOLUME_PATH:-/home/app/.codex}"
export CODEX_HOME="${CODEX_HOME:-$CODEX_VOLUME_BASE}"
export CODEX_CONFIG_HOME="${CODEX_CONFIG_HOME:-$CODEX_HOME}"
export CODEX_CONFIG_PATH="${CODEX_CONFIG_PATH:-$CODEX_HOME/config.toml}"
# Resolve relative skill roots against the mounted repo workspace for codex jobs.
export WORKFLOW_REPO_ROOT="${WORKFLOW_REPO_ROOT:-${SPEC_WORKFLOW_REPO_ROOT:-${MOONMIND_WORKER_WORKFLOW_REPO_ROOT:-${MOONMIND_WORKER_SPEC_WORKFLOW_REPO_ROOT:-$WORKSPACE_ROOT_DEFAULT}}}}"
export SPEC_WORKFLOW_REPO_ROOT="${SPEC_WORKFLOW_REPO_ROOT:-$WORKFLOW_REPO_ROOT}"
export WORKFLOW_SKILLS_LOCAL_MIRROR_ROOT="${WORKFLOW_SKILLS_LOCAL_MIRROR_ROOT:-${SPEC_SKILLS_LOCAL_MIRROR_ROOT:-${MOONMIND_WORKER_WORKFLOW_SKILLS_LOCAL_MIRROR_ROOT:-${MOONMIND_WORKER_SPEC_SKILLS_LOCAL_MIRROR_ROOT:-$WORKFLOW_REPO_ROOT/.agents/skills/local}}}}"
export SPEC_SKILLS_LOCAL_MIRROR_ROOT="${SPEC_SKILLS_LOCAL_MIRROR_ROOT:-$WORKFLOW_SKILLS_LOCAL_MIRROR_ROOT}"
export WORKFLOW_SKILLS_LEGACY_MIRROR_ROOT="${WORKFLOW_SKILLS_LEGACY_MIRROR_ROOT:-${SPEC_SKILLS_LEGACY_MIRROR_ROOT:-${MOONMIND_WORKER_WORKFLOW_SKILLS_LEGACY_MIRROR_ROOT:-${MOONMIND_WORKER_SPEC_SKILLS_LEGACY_MIRROR_ROOT:-$WORKFLOW_REPO_ROOT/.agents/skills}}}}"
export SPEC_SKILLS_LEGACY_MIRROR_ROOT="${SPEC_SKILLS_LEGACY_MIRROR_ROOT:-$WORKFLOW_SKILLS_LEGACY_MIRROR_ROOT}"
WORKDIR_PATH="${MOONMIND_WORKDIR:-/work/agent_jobs}"

ensure_writable_workdir() {
  local workdir="$1"
  if [[ "$workdir" != /* ]]; then
    log "MOONMIND_WORKDIR must be an absolute path. Received: $workdir"
    exit 1
  fi
  if ! mkdir -p "$workdir"; then
    log "Cannot create MOONMIND_WORKDIR at $workdir. Check volume mount and permissions."
    exit 1
  fi

  local probe_path="${workdir}/.moonmind-write-check.$$"
  if ! : >"$probe_path" 2>/dev/null; then
    local owner="unknown"
    local perms="unknown"
    owner="$(stat -c '%U:%G (%u:%g)' "$workdir" 2>/dev/null || printf 'unknown')"
    perms="$(stat -c '%a' "$workdir" 2>/dev/null || printf 'unknown')"
    log "MOONMIND_WORKDIR is not writable: $workdir (owner=$owner perms=$perms uid=$(id -u) gid=$(id -g))."
    log "Fix workspace volume ownership and restart the worker."
    exit 1
  fi
  rm -f "$probe_path"
}

ensure_writable_workdir "$WORKDIR_PATH"

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
is_truthy() {
  case "$(lower "$1")" in
    1|true|yes|on)
      return 0
      ;;
  esac
  return 1
}
extract_policy_value() {
  local policy="$1"
  local key="$2"
  printf '%s' "$policy" | tr ';' '\n' | awk -F= -v key="$key" '$1 == key {print $2; exit}'
}
build_worker_runtime_capabilities_payload() {
  python - <<'PY'
import json
import os


def _parse_csv(value: str) -> list[str]:
    raw = str(value or "").strip()
    if not raw:
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]


def _first_non_empty(*keys: str) -> list[str]:
    for key in keys:
        parsed = _parse_csv(os.environ.get(key, ""))
        if parsed:
            return parsed
    return []


runtime_capabilities: dict[str, dict[str, list[str]]] = {}
for runtime, model_keys, effort_keys in (
    ("codex", ("MOONMIND_CODEX_MODELS", "MOONMIND_CODEX_MODEL"), ("MOONMIND_CODEX_EFFORTS", "MOONMIND_CODEX_EFFORT", "CODEX_MODEL_REASONING_EFFORT")),
    ("gemini", ("MOONMIND_GEMINI_MODELS", "MOONMIND_GEMINI_MODEL"), ("MOONMIND_GEMINI_EFFORTS", "MOONMIND_GEMINI_EFFORT")),
    ("claude", ("MOONMIND_CLAUDE_MODELS", "MOONMIND_CLAUDE_MODEL"), ("MOONMIND_CLAUDE_EFFORTS", "MOONMIND_CLAUDE_EFFORT")),
):
    models = _first_non_empty(*model_keys)
    efforts = _first_non_empty(*effort_keys)
    if models or efforts:
        entry: dict[str, list[str]] = {}
        if models:
            entry["models"] = models
        if efforts:
            entry["efforts"] = efforts
        runtime_capabilities[runtime] = entry

print(json.dumps({"runtimeCapabilities": runtime_capabilities}))
PY
}
sync_worker_runtime_capabilities() {
  local payload="$1"
  if [[ -z "$payload" || "$payload" == '{"runtimeCapabilities": {}}' ]]; then
    log "No runtime capability payload to sync."
    return 0
  fi
  if [[ -z "${MOONMIND_WORKER_TOKEN:-}" ]]; then
    log "Cannot sync runtime capabilities without MOONMIND_WORKER_TOKEN."
    return 1
  fi
  MOONMIND_WORKER_RUNTIME_CAPABILITIES="$payload" \
  python - <<'PY'
import json
import os
import sys
import time
import urllib.error
import urllib.request

moonmind_url = os.environ.get("MOONMIND_URL", "http://api:5000").rstrip("/")
payload_raw = os.environ.get("MOONMIND_WORKER_RUNTIME_CAPABILITIES", "{}").strip()
worker_token = os.environ.get("MOONMIND_WORKER_TOKEN", "").strip()
if not payload_raw:
    sys.exit(0)

try:
    payload = json.loads(payload_raw)
except (TypeError, ValueError):
    print("Invalid worker runtime capabilities payload.", file=sys.stderr)
    sys.exit(1)

if not payload.get("runtimeCapabilities"):
    sys.exit(0)

url = f"{moonmind_url}/api/queue/workers/tokens/capabilities"
body = json.dumps(payload).encode("utf-8")
headers = {
    "Accept": "application/json",
    "Content-Type": "application/json",
    "X-MoonMind-Worker-Token": worker_token,
}

for attempt in range(1, 61):
    try:
        request = urllib.request.Request(url=url, data=body, headers=headers, method="PUT")
        with urllib.request.urlopen(request, timeout=10) as response:
            _ = response.read()
        print("ok")
        sys.exit(0)
    except urllib.error.HTTPError as exc:
        _ = exc.read()
        if exc.code in (401, 403, 404):
            print(
                "MoonMind API rejected worker runtime capability sync ("
                f"HTTP {exc.code}). Check MOONMIND_URL or worker token.",
                file=sys.stderr,
            )
            sys.exit(1)
        if attempt == 60:
            print(
                "Failed to sync runtime capabilities after 60 attempts "
                f"(HTTP {exc.code}).",
                file=sys.stderr,
            )
            sys.exit(1)
    except Exception as exc:
        if attempt == 60:
            print(
                "Failed to sync runtime capabilities after 60 attempts: "
                f"{exc}",
                file=sys.stderr,
            )
            sys.exit(1)
    time.sleep(1)
PY
}

PROPOSALS_ENABLED_RAW="${MOONMIND_ENABLE_TASK_PROPOSALS:-${ENABLE_TASK_PROPOSALS:-true}}"
ROTATE_TOKEN_FOR_PROPOSALS_RAW="${MOONMIND_WORKER_ROTATE_TOKEN_FOR_PROPOSALS:-false}"
if is_truthy "$PROPOSALS_ENABLED_RAW"; then
  PROPOSALS_ENABLED="true"
else
  PROPOSALS_ENABLED="false"
fi
if is_truthy "$ROTATE_TOKEN_FOR_PROPOSALS_RAW"; then
  ROTATE_TOKEN_FOR_PROPOSALS="true"
else
  ROTATE_TOKEN_FOR_PROPOSALS="false"
fi
CAPABILITIES_CSV="$(normalize_csv "$CAPABILITIES_RAW")"
if [[ "$PROPOSALS_ENABLED" == "true" ]] && [[ ",$CAPABILITIES_CSV," != *",proposals_write,"* ]]; then
  log "MOONMIND_ENABLE_TASK_PROPOSALS is enabled; adding proposals_write to MOONMIND_WORKER_CAPABILITIES for proposal support."
  CAPABILITIES_RAW="${CAPABILITIES_RAW:+$CAPABILITIES_RAW,}proposals_write"
  CAPABILITIES_CSV="$(normalize_csv "$CAPABILITIES_RAW")"
fi
export MOONMIND_WORKER_CAPABILITIES="$CAPABILITIES_CSV"
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
  if [[ "$BOOTSTRAP" == "true" ]]; then
    if [[ ! -f "$TOKEN_POLICY_PATH" ]]; then
      if [[ "$ENFORCE_TOKEN_POLICY" == "true" ]]; then
        should_load_cached_token=false
        log "Cached worker token policy marker missing at $TOKEN_POLICY_PATH; rotating token because MOONMIND_WORKER_ENFORCE_TOKEN_POLICY=true."
      else
        log "Cached worker token policy marker missing at $TOKEN_POLICY_PATH; loading cached token for backward compatibility and leaving marker unchanged."
      fi
    else
      cached_token_policy="$(tr -d '\r\n' <"$TOKEN_POLICY_PATH")"
      cached_token_capabilities="$(normalize_csv "$(extract_policy_value "$cached_token_policy" capabilities)")"
      if [[ "$PROPOSALS_ENABLED" == "true" && ",$cached_token_capabilities," != *",proposals_write,"* ]]; then
        if [[ "$ENFORCE_TOKEN_POLICY" == "true" || -n "${MOONMIND_API_TOKEN:-}" || "$ROTATE_TOKEN_FOR_PROPOSALS" == "true" ]]; then
          should_load_cached_token=false
          log "Cached worker token policy is missing proposals_write while proposals are enabled; rotating token to refresh capabilities."
        else
          log "WARNING: Cached worker token policy is missing proposals_write while proposals are enabled, but policy enforcement/bootstrap auth is unavailable; loading cached token and proposal submission may fail until token refresh."
        fi
      elif [[ "$cached_token_policy" != "$desired_token_policy" ]]; then
        if [[ "$ENFORCE_TOKEN_POLICY" == "true" ]]; then
          should_load_cached_token=false
          log "Cached worker token policy does not match configured allowed types/capabilities; rotating token because MOONMIND_WORKER_ENFORCE_TOKEN_POLICY=true."
        else
          log "Cached worker token policy does not match configured allowed types/capabilities; loading cached token to avoid startup disruption and leaving marker unchanged."
        fi
      fi
    fi
  fi

  if [[ "$should_load_cached_token" == "true" ]]; then
    token_file_value="$(tr -d '\r\n' <"$TOKEN_PATH")"
    if [[ -n "$token_file_value" ]]; then
      export MOONMIND_WORKER_TOKEN="$token_file_value"
      log "Loaded worker token from $TOKEN_PATH"
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
runtime_capabilities_payload="$(build_worker_runtime_capabilities_payload)"
if ! sync_worker_runtime_capabilities "$runtime_capabilities_payload"; then
  log "Worker runtime capability sync failed."
  exit 1
fi

retry_enabled="$(lower "${MOONMIND_WORKER_RETRY_ENABLED:-true}")"
retry_seconds="${MOONMIND_WORKER_RETRY_SECONDS:-20}"
worker_command="${MOONMIND_WORKER_COMMAND:-moonmind-codex-worker}"

while true; do
  set +e
  "$worker_command" "$@"
  exit_code=$?
  set -e
  if [[ "$exit_code" -eq 0 ]]; then
    exit 0
  fi
  if [[ "$retry_enabled" != "true" ]]; then
    log "$worker_command exited with code $exit_code and retries are disabled."
    exit "$exit_code"
  fi
  log "$worker_command exited with code $exit_code; retrying in ${retry_seconds}s."
  sleep "$retry_seconds"
done
