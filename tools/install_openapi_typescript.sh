#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cli_path="$repo_root/node_modules/openapi-typescript/bin/cli.js"

if [[ -x "$cli_path" || -f "$cli_path" ]]; then
    exit 0
fi

version="$(
    cd "$repo_root"
    node -e "console.log(require('./package-lock.json').packages['node_modules/openapi-typescript'].version)"
)"

cd "$repo_root"
npm install \
    --ignore-scripts \
    --no-audit \
    --no-fund \
    --no-save \
    "openapi-typescript@$version"
