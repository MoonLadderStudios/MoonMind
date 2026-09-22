#!/usr/bin/env bash
# Host-owned install/update/restore for the standalone deployment controller
# (MoonLadderStudios/MoonMind#4500).
#
# The host CLI owns the controller lifecycle: it installs/starts the
# controller, and it updates or restores the controller itself. The
# controller never replaces itself. Controller update is serialized against
# active deployment mutation via the installation-local lock directory.
set -euo pipefail

CONTROLLER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../deploy/moonmind-controller" && pwd)"
STATE_DIR="${MOONMIND_CONTROLLER_STATE_DIR:-/var/lib/moonmind-controller}"
LOCK_DIR="${MOONMIND_CONTROLLER_LOCK_DIR:-${STATE_DIR}/locks}"
SECRET_FILE="${MOONMIND_CONTROLLER_SECRET_FILE:-${STATE_DIR}/.moonmind-controller-secret}"
PROJECT_NAME="${MOONMIND_CONTROLLER_PROJECT:-moonmind-controller}"
# Read-only target checkout bind-mounted into the controller container so
# every `docker compose` invocation is scoped to the MoonMind stack.
# Defaults to the repo this installer runs from.
REPO_ROOT="$(cd "${CONTROLLER_DIR}/../.." && pwd)"
export MOONMIND_TARGET_REPO="${MOONMIND_TARGET_REPO:-${REPO_ROOT}}"

usage() {
  cat <<'EOF'
Usage: install-moonmind-controller.sh [install|update|restore|status]

  install   Create the deployment-owned secret (once) and start the controller.
  update    Rebuild and recreate the controller (host-owned; never self-applied).
  restore   Recreate the controller container from its durable state volume.
  status    Show controller container and endpoint health (default with no args).

The controller project is separate from the MoonMind application stack, so
this bootstrap works while MoonMind itself is unhealthy.
EOF
}

ensure_secret() {
  if [[ -f "${SECRET_FILE}" ]]; then
    return 0
  fi
  mkdir -p "$(dirname "${SECRET_FILE}")"
  python3 -c 'import secrets; print(secrets.token_hex(32))' > "${SECRET_FILE}"
  chmod 600 "${SECRET_FILE}"
  echo "Created deployment-owned controller secret at ${SECRET_FILE}"
}

assert_no_active_mutation() {
  # Serialized against active deployment mutation: refuse to replace the
  # controller while an operation record shows unfinished work, unless the
  # operator passes --force for an explicit restore.
  if [[ "${1:-}" == "--force" ]]; then
    return 0
  fi
  # The durable record lives inside the controller's named volume, not at
  # this host path: query the controller's actual durable state instead of
  # a host-side file that can never observe it.
  local token response
  token="$(cat "${SECRET_FILE}" 2>/dev/null || true)"
  if ! response="$(curl -sf --max-time 10 http://127.0.0.1:8099/operation -H "Authorization: Bearer ${token}" 2>/dev/null)"; then
    echo "Warning: controller endpoint unreachable; no active mutation observable." >&2
    return 0
  fi
  if python3 -c '
import json, sys
record = json.loads(sys.argv[1])
if record.get("status") == "installed" and record.get("installedImage") == record.get("desiredImage"):
    sys.exit(0)
sys.exit(1)
' "${response}"; then
    return 0
  fi
  echo "Refusing: a deployment operation is unfinished; finish or explicitly" >&2
  echo "retry it first, or re-run with --force for an explicit restore." >&2
  exit 1
}

compose() {
  docker compose --project-name "${PROJECT_NAME}" -f "${CONTROLLER_DIR}/compose.yaml" "$@"
}

cmd_install() {
  ensure_secret
  export MOONMIND_CONTROLLER_SECRET_FILE="${SECRET_FILE}"
  compose up -d --wait
  echo "Controller installed and running (project ${PROJECT_NAME})."
}

cmd_update() {
  assert_no_active_mutation "${1:-}"
  export MOONMIND_CONTROLLER_SECRET_FILE="${SECRET_FILE}"
  compose build controller
  compose up -d --wait
  echo "Controller updated by the host (never by itself)."
}

cmd_restore() {
  assert_no_active_mutation "${1:-}"
  export MOONMIND_CONTROLLER_SECRET_FILE="${SECRET_FILE}"
  compose up -d --wait
  echo "Controller restored from its durable state volume."
}

cmd_status() {
  compose ps
  # A failed health request is unhealthy automation input: report it but
  # keep the failing exit status instead of masking it with `|| echo`.
  if curl -sf --max-time 10 http://127.0.0.1:8099/healthz -H "Authorization: Bearer $(cat "${SECRET_FILE}")"; then
    echo " (endpoint healthy)"
  else
    echo " (endpoint unreachable or unauthorized)"
    return 1
  fi
}

# A bare invocation selects the safe read-only action instead of failing
# after printing usage.
command="${1:-status}"
case "${command}" in
  install) cmd_install ;;
  update) cmd_update "${2:-}" ;;
  restore) cmd_restore "${2:-}" ;;
  status) cmd_status ;;
  *) usage; exit 1 ;;
esac
