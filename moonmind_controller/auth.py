"""Deployment-owned authentication for the standalone controller.

Stdlib-only. The controller exposes one small authenticated local endpoint.
Its bearer secret is deployment-owned: it is read from the deployment's own
environment/file and never looked up from an application service. No agent
receives the Docker socket or unrestricted controller access.
"""

from __future__ import annotations

import hmac
import os
from pathlib import Path

SECRET_ENV_VAR = "MOONMIND_CONTROLLER_SECRET"
SECRET_FILE_ENV_VAR = "MOONMIND_CONTROLLER_SECRET_FILE"
STATE_DIR_ENV_VAR = "MOONMIND_CONTROLLER_STATE_DIR"

# Deployment-owned default locations, derived exactly like the host
# installer (`tools/install-moonmind-controller.sh`): the installer creates
# the secret once at ``$STATE_DIR/.moonmind-controller-secret`` (default
# ``/var/lib/moonmind-controller``) and exports that path only inside its
# own short-lived process. Clients derive the same default so a normal
# subsequent invocation sees the installed controller as configured instead
# of silently staying on the legacy path.
DEFAULT_STATE_DIR = "/var/lib/moonmind-controller"
DEFAULT_SECRET_FILENAME = ".moonmind-controller-secret"


def verify_bearer(presented: str, expected: str) -> bool:
    """Constant-time bearer comparison; empty secrets never verify."""
    if not presented or not expected:
        return False
    return hmac.compare_digest(presented.encode(), expected.encode())


def secret_from_environ(environ: dict | None = None) -> str | None:
    """Load the deployment-owned secret; ``None`` when not configured."""
    env = environ if environ is not None else os.environ
    direct = str(env.get(SECRET_ENV_VAR) or "").strip()
    if direct:
        return direct
    file_var = str(env.get(SECRET_FILE_ENV_VAR) or "").strip()
    if file_var:
        try:
            value = Path(file_var).read_text().strip()
        except OSError:
            return None
        return value or None
    # Fall back to the installer's deployment-owned default path so later
    # callers in the same installation reuse the created secret without
    # re-exporting it.
    state_dir = str(env.get(STATE_DIR_ENV_VAR) or DEFAULT_STATE_DIR).strip()
    if not state_dir:
        return None
    try:
        value = Path(state_dir, DEFAULT_SECRET_FILENAME).read_text().strip()
    except OSError:
        return None
    return value or None


def authorization_header(secret: str) -> dict[str, str]:
    """Build the caller's Authorization header without logging the secret."""
    return {"Authorization": f"Bearer {secret}"}
