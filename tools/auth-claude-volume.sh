#!/usr/bin/env bash
set -euo pipefail

NETWORK_NAME="${MOONMIND_DOCKER_NETWORK:-local-network}"
NO_DEPS="${MOONMIND_CLAUDE_AUTH_NO_DEPS:-1}"
if [[ -n "${CLAUDE_AUTH_ALLOW_INTERACTIVE:-}" ]]; then
  ALLOW_INTERACTIVE="$CLAUDE_AUTH_ALLOW_INTERACTIVE"
elif [[ -t 0 && -t 1 ]]; then
  ALLOW_INTERACTIVE=1
else
  ALLOW_INTERACTIVE=0
fi
CLAUDE_THEME="${CLAUDE_THEME:-dark}"

if ! command -v docker >/dev/null 2>&1; then
  echo "Error: docker CLI is not available." >&2
  exit 127
fi

if ! docker network inspect "$NETWORK_NAME" >/dev/null 2>&1; then
  docker network create "$NETWORK_NAME" >/dev/null
fi

RUN_OPTS=(run --rm --user root)
if [[ "$NO_DEPS" == "1" ]]; then
  RUN_OPTS+=(--no-deps)
fi

if [[ "$ALLOW_INTERACTIVE" == "1" ]]; then
  RUN_OPTS+=(--interactive --tty)
fi

RUN_OPTS+=(
  -e "CLAUDE_AUTH_ALLOW_INTERACTIVE=$ALLOW_INTERACTIVE"
)

if [[ -n "$CLAUDE_THEME" ]]; then
  RUN_OPTS+=(-e "CLAUDE_THEME=$CLAUDE_THEME")
fi

docker compose "${RUN_OPTS[@]}" codex-worker \
  bash -lc '
set -euo pipefail

claude_home="/home/app/.claude"
settings_file="$claude_home/settings.json"

status_quiet() {
  claude auth status >/dev/null 2>&1 || claude login status >/dev/null 2>&1
}

status_verbose() {
  claude auth status || claude login status
}

initialize_settings() {
  mkdir -p "$claude_home" "$claude_home/debug"

  if command -v python3 >/dev/null 2>&1; then
    python3 - "$settings_file" "$CLAUDE_THEME" <<'PY'
import json
import os
import sys

path, theme = sys.argv[1], sys.argv[2]
data = {}
if os.path.exists(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            loaded = json.load(f)
            if isinstance(loaded, dict):
                data = loaded
    except Exception:
        data = {}

data.update(
    {
        "theme": theme,
        "hasCompletedOnboarding": True,
        "hasCompletedClaudeInChromeOnboarding": True,
        "hasCompletedProjectOnboarding": True,
        "hasCompletedAuthFlow": True,
        "hasCompletedResults": True,
    }
)

with open(path + ".tmp", "w", encoding="utf-8") as f:
    json.dump(data, f, indent=2, sort_keys=True)
    f.write("\n")
os.replace(path + ".tmp", path)
PY
  else
    cat > "$settings_file" <<EOF
{
  "theme": "${CLAUDE_THEME}",
  "hasCompletedOnboarding": true,
  "hasCompletedClaudeInChromeOnboarding": true,
  "hasCompletedProjectOnboarding": true,
  "hasCompletedAuthFlow": true,
  "hasCompletedResults": true
}
EOF
  fi

  chown -R app:app "$claude_home"
}

if status_quiet; then
  status_verbose
  exit 0
fi

initialize_settings

if [[ "${CLAUDE_AUTH_ALLOW_INTERACTIVE:-0}" == "1" ]]; then
  login_args=()
  if [[ -n "${CLAUDE_AUTH_EMAIL:-}" ]]; then
    login_args+=(--email "$CLAUDE_AUTH_EMAIL")
  fi
  if [[ "${CLAUDE_AUTH_SSO:-0}" == "1" ]]; then
    login_args+=(--sso)
  fi
  claude auth login "${login_args[@]}"
else
  echo "Claude auth is not interactive in this run. Set CLAUDE_AUTH_ALLOW_INTERACTIVE=1 to open OAuth login."
  exit 1
fi

status_verbose
'
