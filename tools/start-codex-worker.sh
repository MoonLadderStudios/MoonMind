#!/usr/bin/env bash
set -euo pipefail

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

TOKEN_PATH_DEFAULT="${CODEX_HOME}/moonmind_worker_token"
TOKEN_PATH="${MOONMIND_WORKER_TOKEN_FILE:-$TOKEN_PATH_DEFAULT}"
BOOTSTRAP="$(lower "${MOONMIND_WORKER_BOOTSTRAP_TOKEN:-true}")"

if [[ -z "${MOONMIND_WORKER_TOKEN:-}" && -f "$TOKEN_PATH" ]]; then
  token_file_value="$(tr -d '\r\n' <"$TOKEN_PATH")"
  if [[ -n "$token_file_value" ]]; then
    export MOONMIND_WORKER_TOKEN="$token_file_value"
    log "Loaded worker token from $TOKEN_PATH"
  fi
fi

if [[ -z "${MOONMIND_WORKER_TOKEN:-}" && "$BOOTSTRAP" == "true" ]]; then
  log "No MOONMIND_WORKER_TOKEN provided; requesting a worker token from MoonMind API."
  mkdir -p "$(dirname "$TOKEN_PATH")"
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
        "codex_exec,codex_skill",
    ).split(",")
    if item.strip()
]
capabilities = [
    item.strip()
    for item in os.environ.get("MOONMIND_WORKER_CAPABILITIES", "").split(",")
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
        detail = exc.read().decode("utf-8", errors="replace")
        if exc.code in (401, 403):
            print(
                "MoonMind API rejected worker token bootstrap ("
                f"HTTP {exc.code}). Set MOONMIND_WORKER_TOKEN manually or "
                "provide MOONMIND_API_TOKEN for authenticated bootstrap.",
                file=sys.stderr,
            )
            if detail:
                print(detail[:400], file=sys.stderr)
            sys.exit(1)
        if attempt == 60:
            print(
                "Failed to bootstrap worker token after 60 attempts "
                f"(HTTP {exc.code}). Last response: {detail[:400]}",
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
  chmod 600 "$TOKEN_PATH" || true
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
