#!/bin/sh
# Optional prelaunch diagnostic for the static service's effective environment.
# Uses the same credential/pack validation as the managed launcher. Tags and
# latest are valid image choices; actual artifact and host/session identity are
# attested by the lifecycle owner before work is admitted.
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
import json
import os
import subprocess
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
    configured = subprocess.run(
        ["docker", "compose", "--project-directory", local_root, "-f", compose_file,
         "--profile", profile, "config", "--format", "json"],
        check=True, capture_output=True, text=True, timeout=30,
    )
    service_def = json.loads(configured.stdout)["services"][service]
    rendered = service_def.get("environment", {})
    image = service_def["image"]
except Exception as exc:  # noqa: BLE001 - prelaunch gate fails closed
    print(f"static host prelaunch admission unavailable: {exc}", file=sys.stderr)
    sys.exit(69)

admission = static_hosts.admit_static_host_effective_launch(
    service=service,
    pack_ref=str(rendered.get("MOONMIND_OMNIGENT_RUNTIME_PACK_REF") or ""),
    materializer_ref=str(rendered.get("MOONMIND_OMNIGENT_CREDENTIAL_MATERIALIZER_REF") or ""),
    operator_env={"OMNIGENT_SHARED_HOST_IMAGE_REF": image},
    rendered_env=rendered,
)
print(f"static host prelaunch admission passed: {service} image={admission['image_ref']}")
PYEOF
