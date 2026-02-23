#!/usr/bin/env bash
set -euo pipefail

NETWORK_NAME="${MOONMIND_DOCKER_NETWORK:-local-network}"
CLAUDE_HOME="${CLAUDE_HOME:-${CLAUDE_VOLUME_PATH:-/home/app/.claude}}"
CLAUDE_CONFIG_DIR="${CLAUDE_CONFIG_DIR:-$CLAUDE_HOME}"

if ! command -v docker >/dev/null 2>&1; then
  echo "Error: docker CLI is not available." >&2
  exit 127
fi

if ! docker network inspect "$NETWORK_NAME" >/dev/null 2>&1; then
  docker network create "$NETWORK_NAME" >/dev/null
fi

if [ ! -t 0 ] || [ ! -t 1 ]; then
  echo "Error: OAuth login requires an interactive terminal."
  echo "Run this command from an interactive shell or use the token mode instead."
  echo "Example: ./tools/auth-claude-volume-token.sh --token <oauth-token>"
  exit 1
fi

docker compose run --rm -it \
  --user app \
  -e CLAUDE_AUTH_ALLOW_INTERACTIVE=1 \
  -e CLAUDE_HOME="$CLAUDE_HOME" \
  -e CLAUDE_CONFIG_DIR="$CLAUDE_CONFIG_DIR" \
  --network "$NETWORK_NAME" codex-worker \
  bash -lc '
set -e
node - <<'"'"'EOF'"'"'
const fs = require("fs");
const path = require("path");

const claudeHome = process.env.CLAUDE_HOME || "/home/app/.claude";
const settingsPath = path.join(claudeHome, "settings.json");
const defaults = {
  theme: "dark",
  hasCompletedOnboarding: true,
  hasCompletedClaudeInChromeOnboarding: true,
};

try {
  fs.mkdirSync(claudeHome, { recursive: true });
  let existing = {};
  if (fs.existsSync(settingsPath)) {
    try {
      const raw = fs.readFileSync(settingsPath, "utf8");
      existing = raw ? JSON.parse(raw) : {};
      if (typeof existing !== "object" || existing === null || Array.isArray(existing)) {
        existing = {};
      }
    } catch (_) {
      existing = {};
    }
  }

  for (const [k, v] of Object.entries(defaults)) {
    if (existing[k] === undefined) existing[k] = v;
  }

  fs.writeFileSync(settingsPath, JSON.stringify(existing, null, 2));
  try {
    fs.chmodSync(settingsPath, 0o600);
  } catch (_) {}
} catch (error) {
  console.error(error);
  process.exit(1);
}
EOF
echo "Launching Claude OAuth login inside codex-worker container."
echo "If prompted, keep auth in dark theme and complete the browser sign-in flow."
claude auth login
echo "Verifying Claude auth status."
claude auth status --json
'
