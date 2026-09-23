"""Kernel-owned per-stack lock for the standalone controller.

Stdlib-only. Reuses the ``moonmind.deployment-kernel-lock.v1`` contract from
the application-owned updater so cutover is safe: the controller takes
ownership only after the old writer has positively stopped or reconciled
(its lock file released). A legacy lock file blocks cutover explicitly
instead of being deleted, and a live lock inode is never removed.
Ownership is installation-local: the lock directory lives in the
installation's own durable state, so two independent installations on one
daemon hold independent locks.
"""

from __future__ import annotations

import contextlib
import json
from pathlib import Path

CONTRACT = "moonmind.deployment-kernel-lock.v1"


def lock_path_for(lock_dir: str | Path, stack: str) -> Path:
    """Resolve the installation-local lock file for a stack."""
    normalized = str(stack).strip()
    if not normalized or normalized in {".", ".."} or "/" in normalized:
        raise ValueError("Deployment stack name is not a safe path component")
    return Path(lock_dir) / f"{normalized}.lock"


@contextlib.contextmanager
def hold(lock_dir: str | Path, *, stack: str):
    """Hold the kernel lock for ``stack``; refuse legacy-owner cutover."""
    import fcntl

    path = lock_path_for(lock_dir, stack)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            previous = json.loads(path.read_text())
        except (OSError, ValueError):
            previous = None
        if not isinstance(previous, dict) or previous.get("contract") != CONTRACT:
            raise RuntimeError(
                "A legacy deployment lock remains; its original controller "
                "must release ownership before cutover."
            )
    handle = path.open("a+")
    try:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError(
                "Another controller holds the deployment lock for this stack."
            ) from exc
        handle.seek(0)
        handle.truncate()
        handle.write(json.dumps({"contract": CONTRACT, "stack": stack}))
        handle.flush()
        yield path
    finally:
        handle.close()
