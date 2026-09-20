#!/bin/sh
# Operator-side static-host image prelaunch gate (MoonLadderStudios/MoonMind#3936).
#
# Qualified static launches must pass the Compose prelaunch admission boundary
# before `docker compose --profile <profile> up`: this script judges the
# selected service's rendered effective environment and resolves the effective
# launch image through the same trusted boundary the managed launch path uses
# (`admit_static_host_compose_launch` / `admit_static_host_effective_launch`),
# failing closed on anything but a digest-pinned image ref. The bootstrap
# persistence gate (`require_static_host_image_authority`) runs inside the API
# container after Compose has already started creating services, so it alone
# cannot prevent the mutable fallback image from being pulled and launched.
#
# Usage:
#   services/omnigent/scripts/check-static-host-image.sh --profile omnigent-host-codex
#   services/omnigent/scripts/check-static-host-image.sh --profile omnigent-host-claude
#   COMPOSE_PROFILES=omnigent-host-claude services/omnigent/scripts/check-static-host-image.sh
set -eu

profile=""
while [ $# -gt 0 ]; do
  case "$1" in
    --profile) profile=${2:-}; shift 2 ;;
    --profile=*) profile=${1#--profile=}; shift ;;
    -h|--help)
      echo "usage: $(basename "$0") --profile <omnigent-host-codex|omnigent-host-claude>" >&2
      exit 0
      ;;
    *) echo "unknown argument: $1" >&2; exit 64 ;;
  esac
done

if [ -z "$profile" ]; then
  # Fall back to COMPOSE_PROFILES when no explicit profile is given.
  raw_profiles=${COMPOSE_PROFILES:-}
  case ",$(printf '%s' "$raw_profiles" | tr ' ' ',')," in
    *,omnigent-host-codex,*) profile="omnigent-host-codex" ;;
    *,omnigent-host-claude,*) profile="omnigent-host-claude" ;;
    *) echo "static host profile is required (--profile omnigent-host-codex|omnigent-host-claude)" >&2; exit 64 ;;
  esac
fi

case "$profile" in
  omnigent-host-codex|omnigent-host-claude) ;;
  *) echo "unsupported static host profile: $profile" >&2; exit 64 ;;
esac

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
repo_root=$(CDPATH= cd -- "$script_dir/../../.." && pwd)

MOONMIND_DEPLOYMENT_LOCAL_PROJECT_DIR=${MOONMIND_DEPLOYMENT_LOCAL_PROJECT_DIR:-$repo_root} \
MOONMIND_DEPLOYMENT_COMPOSE_FILE=${MOONMIND_DEPLOYMENT_COMPOSE_FILE:-$repo_root/docker-compose.yaml} \
  python3 - "$profile" << 'PYEOF'
import os
import sys

sys.path.insert(0, os.environ.get("MOONMIND_DEPLOYMENT_LOCAL_PROJECT_DIR", "."))
sys.path.insert(0, os.getcwd())

from moonmind.omnigent.harness_platform import static_hosts

profile = sys.argv[1]
service = profile

# Resolve the Compose file the same way the runtime does.
local_root = os.environ.get("MOONMIND_DEPLOYMENT_LOCAL_PROJECT_DIR", ".")
compose_file = os.environ.get("MOONMIND_DEPLOYMENT_COMPOSE_FILE", "docker-compose.yaml")
try:
    import yaml

    with open(compose_file, encoding="utf-8") as fh:
        loaded = yaml.safe_load(fh)
    service_def = (loaded or {}).get("services", {}).get(service, {})
    environment = service_def.get("environment", {})
    if isinstance(environment, dict):
        raw = {str(k): str(v) for k, v in environment.items()}
    else:
        raw = {}
        for item in environment or []:
            text = str(item)
            if "=" in text:
                key, value = text.split("=", 1)
                raw[key] = value
except Exception as exc:  # noqa: BLE001 - prelaunch gate fails closed
    print(f"static host prelaunch admission unavailable: {exc}", file=sys.stderr)
    sys.exit(69)

operator_env = dict(os.environ)
rendered = static_hosts.render_static_service_env(raw, operator_env)
pack = "codex-native-pack@1" if service == "omnigent-host-codex" else "claude-native-pack@1"
materializer = "codex-oauth-home@1" if service == "omnigent-host-codex" else "claude-oauth-home@1"
admission = static_hosts.admit_static_host_compose_launch(
    service=service,
    pack_ref=pack,
    materializer_ref=materializer,
    operator_env=operator_env,
    raw_service_env=raw,
)
print(f"static host prelaunch admission passed: {service} image={admission['image_ref']}")
PYEOF
