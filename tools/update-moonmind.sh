#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$BASH" "$SCRIPT_DIR/../.agents/skills/update-moonmind/scripts/run-update-moonmind.sh" "$@"
