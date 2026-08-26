#!/bin/sh
set -eu

state_root=${OMNIGENT_STATE_PATH:-/home/app/.omnigent}
opencode_auth=${OPENCODE_AUTH_PATH:-/home/app/.local/share/opencode/auth.json}
expected_generation=${OPENCODE_CREDENTIAL_GENERATION:-}

[ "$(id -u):$(id -g)" = "1000:1000" ] || exit 70
[ "$HOME" = "/home/app" ] || exit 71

# Verify opencode binary is present (command -v opencode)
command -v opencode >/dev/null 2>&1 || exit 72

# Verify version is within pinned supported range (>=1.17.7,<1.19.0)
opencode_version="$(opencode --version 2>&1 | head -n1 | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -n1)" || exit 73
[ -n "$opencode_version" ] || exit 74
# Simple semver compare via python or shell; use python if available
if command -v python3 >/dev/null 2>&1; then
  python3 -c "from packaging.version import Version; import sys; iv=Version('$opencode_version'); sys.exit(0 if Version('1.17.7') <= iv < Version('1.19.0') else 1)" || exit 75
else
  # Fallback: require 1.18.x or 1.17.7+
  case "$opencode_version" in
    1.18.*|1.17.7|1.17.8|1.17.9) ;;
    *) exit 75 ;;
  esac
fi

# Verify credential file exists at expected location without printing contents
[ -f "$opencode_auth" ] || exit 76
# Parent dir must be 0700, file 0600, owned by 1000:1000
[ "$(stat -c %a "$opencode_auth" 2>/dev/null || stat -f %A "$opencode_auth" 2>/dev/null || echo 600)" = "600" ] || exit 77
parent_dir="$(dirname "$opencode_auth")"
[ "$(stat -c %a "$parent_dir" 2>/dev/null || stat -f %A "$parent_dir" 2>/dev/null || echo 700)" = "700" ] || exit 78
[ "$(stat -c %u:%g "$opencode_auth" 2>/dev/null || echo "1000:1000")" = "1000:1000" ] || exit 79

# Verify expected generation matches materialized credential (stored in state_root)
[ -n "$expected_generation" ] || exit 80
[ -f "$state_root/credential-generation" ] || exit 81
[ "$(cat "$state_root/credential-generation")" = "$expected_generation" ] || exit 82

# Ensure conflicting ambient credentials are not present
for key in OPENCODE_AUTH_CONTENT OPENCODE_CONFIG OPENCODE_CONFIG_CONTENT OPENAI_API_KEY ANTHROPIC_API_KEY; do
  eval "present=\${$key+x}"
  [ -z "$present" ] || exit 83
done

# Verify opencode provider key is present in auth.json (without leaking)
if command -v python3 >/dev/null 2>&1; then
  python3 -c "import json,sys; d=json.load(open('$opencode_auth')); p=d.get('opencode-go'); sys.exit(0 if isinstance(p,dict) and p.get('type') == 'api' and isinstance(p.get('key'),str) and p.get('key') else 1)" || exit 84
else
  grep -q "opencode-go" "$opencode_auth" || exit 84
fi

# Verify host advertises opencode-native (via local check; full attestation is via API)
# This is a local sanity; the control plane's exact-host attestation does the authoritative check.
command -v omnigent >/dev/null 2>&1 || exit 85
