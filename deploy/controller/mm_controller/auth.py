"""Deployment-owned shared-secret authentication for the local endpoint.

One small authenticated local endpoint; the secret lives in a
deployment-owned file (default ``$CONTROLLER_STATE_DIR/controller.secret``),
read directly from disk. There is deliberately no application-service lookup:
the controller must stay usable while the application stack is down.
Agents never receive the secret or the Docker socket. Stdlib only.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from pathlib import Path

SECRET_ENV_VAR = "MOONMIND_CONTROLLER_SECRET"
SECRET_FILE_NAME = "controller.secret"


def secret_path(state_dir: str | Path) -> Path:
    return Path(state_dir).expanduser() / SECRET_FILE_NAME


def load_or_create_secret(state_dir: str | Path) -> str:
    """Load the deployment-owned secret, creating it once with mode 0600."""
    override = os.environ.get(SECRET_ENV_VAR, "").strip()
    if override:
        return override
    path = secret_path(state_dir)
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    secret = secrets.token_hex(32)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(secret + "\n")
    except BaseException:
        try:
            path.unlink()
        except OSError:
            pass
        raise
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return secret


def sign(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def verify(secret: str, body: bytes, signature: str) -> bool:
    expected = sign(secret, body)
    return hmac.compare_digest(expected, (signature or "").strip())
