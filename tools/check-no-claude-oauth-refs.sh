#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PATTERNS=(
  'claude oauth'
  'Claude OAuth'
  'auth-claude-volume'
  'claude_auth_volume'
  'MOONMIND_CLAUDE_AUTH_STATUS_COMMAND'
  'CLAUDE_HOME'
  'CLAUDE_VOLUME_'
)
SEARCH_PATHS=(
  "$ROOT_DIR/README.md"
  "$ROOT_DIR/docs"
  "$ROOT_DIR/specs"
  "$ROOT_DIR/tools"
)
failed=0

for pattern in "${PATTERNS[@]}"; do
  matches="$(grep -RIn --include='*.md' --include='*.sh' \
    --exclude-dir='.git' --exclude-dir='.venv' --exclude-dir='node_modules' \
    --exclude="check-no-claude-oauth-refs.sh" "$pattern" "${SEARCH_PATHS[@]}" 2>/dev/null || true)"

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
