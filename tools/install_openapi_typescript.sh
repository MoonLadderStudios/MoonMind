#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cli_path="$repo_root/node_modules/openapi-typescript/bin/cli.js"

if [[ -x "$cli_path" || -f "$cli_path" ]]; then
    exit 0
fi

version="$(
    cd "$repo_root"
    node -e "const lock = require('./package-lock.json'); const ver = (lock.packages && lock.packages['node_modules/openapi-typescript'] || {}).version || (lock.dependencies && lock.dependencies['openapi-typescript'] || {}).version; if (!ver) { console.error('Error: openapi-typescript not found in package-lock.json'); process.exit(1); } console.log(ver);"
)"

cd "$repo_root"
npm install \
    --ignore-scripts \
    --no-audit \
    --no-fund \
    --no-save \
    "openapi-typescript@$version"
