#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
auth-claude-volume-token.sh - Write Claude OAuth access token into the claude worker volume.

Usage:
  ./tools/auth-claude-volume-token.sh --token <value>
  ./tools/auth-claude-volume-token.sh --token-value <value>
  ./tools/auth-claude-volume-token.sh --token-file <path>
  ./tools/auth-claude-volume-token.sh --token-stdin
  ./tools/auth-claude-volume-token.sh --help

Options:
  --token <value>         OAuth access token to persist into .credentials.json.
  --token-value <value>   OAuth access token to persist into .credentials.json.
  --token-file <path>     Read token from a local file and use it as the access token.
  --token-stdin           Read token from STDIN. Useful for non-interactive pipelines.
  --help                  Show this help.
  --dark                  Also force settings.json theme to dark mode.
USAGE
}

NETWORK_NAME="${MOONMIND_DOCKER_NETWORK:-local-network}"
CLAUDE_HOME="${CLAUDE_HOME:-${CLAUDE_VOLUME_PATH:-/home/app/.claude}}"
CLAUDE_CONFIG_DIR="${CLAUDE_CONFIG_DIR:-$CLAUDE_HOME}"
FORCE_DARK_THEME="${FORCE_DARK_THEME:-1}"

if ! command -v docker >/dev/null 2>&1; then
  echo "Error: docker CLI is not available." >&2
  exit 127
fi

if ! docker network inspect "$NETWORK_NAME" >/dev/null 2>&1; then
  docker network create "$NETWORK_NAME" >/dev/null
fi

COMPOSE_NETWORK_ARGS=()
if docker compose run --help 2>/dev/null | grep -Eq '(^|[[:space:]])--network([[:space:]]|=|$)'; then
  COMPOSE_NETWORK_ARGS+=(--network "$NETWORK_NAME")
fi

ACCESS_TOKEN=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --token|--token-value)
      if [[ $# -lt 2 || "${2:0:1}" == "-" ]]; then
        echo "Error: --token-value (or --token) requires a value." >&2
        usage
        exit 1
      fi
      ACCESS_TOKEN="$2"
      shift 2
      ;;
    --token-file)
      if [[ $# -lt 2 || "${2:0:1}" == "-" ]]; then
        echo "Error: --token-file requires a value." >&2
        usage
        exit 1
      fi
      if [[ ! -f "$2" ]]; then
        echo "Error: token file not found: $2" >&2
        exit 1
      fi
      ACCESS_TOKEN="$(cat "$2" | tr -d '\r\n')"
      shift 2
      ;;
    --token-stdin)
      if [ -t 0 ]; then
        echo "Error: --token-stdin requires token content on STDIN." >&2
        exit 1
      fi
      ACCESS_TOKEN="$(cat)"
      ACCESS_TOKEN="${ACCESS_TOKEN//$'\r'/}"
      ACCESS_TOKEN="${ACCESS_TOKEN%$'\n'}"
      shift
      ;;
    --dark)
      FORCE_DARK_THEME="1"
      shift
      ;;
    --help|-h)
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

if [[ -z "$ACCESS_TOKEN" ]]; then
  if [[ -n "${CLAUDE_ACCESS_TOKEN:-}" ]]; then
    ACCESS_TOKEN="$CLAUDE_ACCESS_TOKEN"
  else
    echo "Error: no token provided." >&2
    echo "Provide one of --token-value, --token-file, --token-stdin, or CLAUDE_ACCESS_TOKEN." >&2
    usage
    exit 1
  fi
fi

if [[ -z "${ACCESS_TOKEN//[[:space:]]/}" ]]; then
  echo "Error: token is empty after trimming whitespace." >&2
  exit 1
fi

TOKEN_FILE="$(mktemp)"
printf '%s\n' "$ACCESS_TOKEN" > "$TOKEN_FILE"
trap 'rm -f "$TOKEN_FILE"' EXIT

docker compose run --rm \
  --user app \
  -e CLAUDE_HOME="$CLAUDE_HOME" \
  -e CLAUDE_CONFIG_DIR="$CLAUDE_CONFIG_DIR" \
  -e FORCE_DARK_THEME="$FORCE_DARK_THEME" \
  -v "$TOKEN_FILE:/tmp/claude-access-token.txt:ro" \
  "${COMPOSE_NETWORK_ARGS[@]}" claude-worker \
  bash -lc '
set -e
TOKEN="$(cat /tmp/claude-access-token.txt | tr -d "\r\n")"
export TOKEN
node - <<'"'"'EOF'"'"'
const fs = require("fs");
const path = require("path");

const claudeHome = process.env.CLAUDE_HOME || "/home/app/.claude";
const tokenPath = path.join(claudeHome, ".credentials.json");
const settingsPath = path.join(claudeHome, "settings.json");
const token = process.env.TOKEN || "";

if (!token) {
  console.error("No token value provided for /tmp/claude-access-token.txt");
  process.exit(1);
}

try {
  fs.mkdirSync(claudeHome, { recursive: true });

  let creds = {};
  if (fs.existsSync(tokenPath)) {
    try {
      const raw = fs.readFileSync(tokenPath, "utf8");
      creds = raw ? JSON.parse(raw) : {};
      if (typeof creds !== "object" || creds === null || Array.isArray(creds)) {
        creds = {};
      }
    } catch (_) {
      creds = {};
    }
  }

  const existing = creds.claudeAiOauth || {};
  existing.accessToken = token;
  creds.claudeAiOauth = existing;
  fs.writeFileSync(tokenPath, JSON.stringify(creds, null, 2));
  try {
    fs.chmodSync(tokenPath, 0o600);
  } catch (_) {}

  if (process.env.FORCE_DARK_THEME === "1") {
    const defaults = {
      theme: "dark",
      hasCompletedOnboarding: true,
      hasCompletedClaudeInChromeOnboarding: true,
    };
    let settings = {};
    if (fs.existsSync(settingsPath)) {
      try {
        const raw = fs.readFileSync(settingsPath, "utf8");
        settings = raw ? JSON.parse(raw) : {};
        if (typeof settings !== "object" || settings === null || Array.isArray(settings)) {
          settings = {};
        }
      } catch (_) {
        settings = {};
      }
    }
    for (const [key, value] of Object.entries(defaults)) {
      if (settings[key] === undefined) settings[key] = value;
    }
    fs.writeFileSync(settingsPath, JSON.stringify(settings, null, 2));
    try {
      fs.chmodSync(settingsPath, 0o600);
    } catch (_) {}
  }
} catch (error) {
  console.error(error);
  process.exit(1);
}
EOF
echo "Verifying Claude auth status."
if ! claude auth status --json; then
  echo "Claude auth status reported not logged in after writing credentials." >&2
  echo "Run: ./tools/auth-claude-volume-oauth.sh to complete interactive OAuth." >&2
  exit 1
fi
'
