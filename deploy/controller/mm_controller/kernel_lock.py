"""Kernel-owned per-stack file lock with legacy-owner protection.

Carried forward from ``deployment_execution.FileDeploymentUpdateLockManager``
without importing MoonMind code. The lock file's inode is the owner: process
exit releases the kernel lease, and PIDs/timestamps are never ownership
evidence across containers. Controller/job ownership is installation-local:
each installation uses its own ``lock_dir``, so two independent installations
on one daemon never share a lock.

Cutover rule: when the lock file holds a legacy (non-contract) payload, the
old writer must be positively stopped or reconciled first; the controller
refuses to delete a live lock inode or auto-restart obsolete controllers.
Stdlib only.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

LOCK_CONTRACT = "moonmind.deployment-kernel-lock.v1"

_STACK_COMPONENT = re.compile(r"^[A-Za-z0-9._-]+$")

DEFAULT_WAIT_SECONDS = 900.0
_POLL_SECONDS = 0.5


class LockUnavailable(RuntimeError):
    def __init__(self, stack: str, *, retryable: bool = True) -> None:
        super().__init__(f"Deployment update for stack {stack!r} is already running.")
        self.stack = stack
        self.retryable = retryable


class LegacyOwnerActive(RuntimeError):
    """A legacy deployment lock remains; its original controller must release
    ownership before cutover. Historical logs are kept; obsolete controllers
    are never restarted automatically and a live lock inode is never deleted."""

    def __init__(self, stack: str) -> None:
        super().__init__(
            f"A legacy deployment lock for stack {stack!r} remains; "
            "its original controller must release ownership before cutover."
        )
        self.stack = stack


@dataclass(frozen=True, slots=True)
class KernelLockManager:
    """Installation-local kernel lock manager rooted at ``lock_dir``."""

    lock_dir: str

    def _lock_path(self, stack: str) -> Path:
        if not _STACK_COMPONENT.match(stack or ""):
            raise ValueError(f"invalid stack name {stack!r}")
        return Path(self.lock_dir).expanduser() / f"{stack}.lock"

    def acquire(self, stack: str, *, wait_seconds: float = 0.0) -> "KernelLockLease":
        """Acquire the kernel lock, waiting up to ``wait_seconds``."""
        lock_path = self._lock_path(stack)
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + max(0.0, wait_seconds)
        while True:
            handle = lock_path.open("a+")
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                handle.close()
                if time.monotonic() >= deadline:
                    raise LockUnavailable(stack)
                time.sleep(min(_POLL_SECONDS, max(0.0, deadline - time.monotonic()) or _POLL_SECONDS))
                continue
            break
        handle.seek(0)
        previous = handle.read()
        if previous:
            try:
                compatible = json.loads(previous).get("contract") == LOCK_CONTRACT
            except (ValueError, AttributeError):
                compatible = False
            if not compatible:
                handle.close()
                raise LegacyOwnerActive(stack)
        handle.seek(0)
        handle.truncate()
        handle.write(json.dumps({"contract": LOCK_CONTRACT, "stack": stack}))
        handle.flush()
        os.fsync(handle.fileno())
        return KernelLockLease(handle=handle, path=str(lock_path))


@dataclass(slots=True)
class KernelLockLease:
    handle: Any
    path: str

    def release(self) -> None:
        try:
            self.handle.close()
        except OSError:
            pass

    def __enter__(self) -> "KernelLockLease":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.release()
