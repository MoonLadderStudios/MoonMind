#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PATTERNS=(
  'claude oauth'
  'auth-claude-volume'
  'claude_auth_volume'
  'moonmind_claude_auth_status_command'
  'CLAUDE_HOME'
  'CLAUDE_VOLUME_'
)
SEARCH_PATHS=(
  "$ROOT_DIR/README.md"
  "$ROOT_DIR/docs"
  "$ROOT_DIR/specs"
  "$ROOT_DIR/tools"
  "$ROOT_DIR/.env-template"
  "$ROOT_DIR/docker-compose.yaml"
  "$ROOT_DIR/docker-compose.yml"
  "$ROOT_DIR/docker-compose.override.yaml"
  "$ROOT_DIR/docker-compose.override.yml"
  "$ROOT_DIR/docker-compose.test.yaml"
  "$ROOT_DIR/docker-compose.test.yml"
)
failed=0

for pattern in "${PATTERNS[@]}"; do
  matches="$(grep -RIn -i \
    --include='*.md' --include='*.sh' --include='*.yml' --include='*.yaml' --include='*.env*' \
    --exclude-dir='.git' --exclude-dir='.venv' --exclude-dir='node_modules' \
    --exclude="${BASH_SOURCE[0]##*/}" "$pattern" "${SEARCH_PATHS[@]}" 2>/dev/null || true)"

  if [[ -n "${matches}" ]]; then
    failed=1
    echo "Pattern ${pattern} found:"
    echo "${matches}"
    echo
  fi
done

if [[ "${failed}" -eq 1 ]]; then
  echo "No-Claude OAuth guard failed: clear these references before continuing."
  exit 1
fi

echo "No Claude OAuth or Claude auth-volume references found in checked docs/specs/scripts."
