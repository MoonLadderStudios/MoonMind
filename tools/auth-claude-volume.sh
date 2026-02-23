#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

usage() {
  cat <<'USAGE'
auth-claude-volume.sh - Claude volume auth helper (interactive + non-interactive modes).

Usage:
  ./tools/auth-claude-volume.sh                     # interactive OAuth login (default)
  ./tools/auth-claude-volume.sh --token VALUE       # write OAuth access token directly
  ./tools/auth-claude-volume.sh --token-value VALUE  # write OAuth access token directly
  ./tools/auth-claude-volume.sh --token-file PATH     # write token from file
  ./tools/auth-claude-volume.sh --token-stdin         # write token from STDIN
  ./tools/auth-claude-volume.sh --help

Aliases:
- Interactive flow:  ./tools/auth-claude-volume-oauth.sh
- Token flow:        ./tools/auth-claude-volume-token.sh
USAGE
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

if [[ $# -eq 0 ]]; then
  exec "${SCRIPT_DIR}/auth-claude-volume-oauth.sh"
fi

case "${1:-}" in
  --token|--token-value|--token-file|--token-stdin)
    exec "${SCRIPT_DIR}/auth-claude-volume-token.sh" "$@"
    ;;
  *)
    echo "Unknown option: ${1:-}" >&2
    usage
    exit 1
    ;;
esac
