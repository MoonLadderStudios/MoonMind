#!/usr/bin/env bash
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"
if [[ ! -x "node_modules/.bin/tailwindcss" ]]; then
  echo "tailwindcss CLI not found under node_modules/.bin. Run 'npm install' before executing this script." >&2
  exit 1
fi
npx tailwindcss \
  -i api_service/static/task_dashboard/dashboard.tailwind.css \
  -o api_service/static/task_dashboard/dashboard.css \
  --minify
