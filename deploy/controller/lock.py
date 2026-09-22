"""Kernel-owned per-stack lock (issue #4500, REQ-07).

Exactly one controller mutates a stack at a time. Ownership is the open file
description holding an ``flock(LOCK_EX | LOCK_NB)`` on an installation-local
lock file. Ownership is never transferred because a PID or timestamp looks
old in another namespace, and release never deletes a live lock inode:
stale unlocks keep the file so a competing writer cannot slip in between an
unlink and a re-create.
"""
from __future__ import annotations

import contextlib
import fcntl
import os
import re
from pathlib import Path

_STACK_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")


class LockBusyError(RuntimeError):
    """Another controller currently owns the stack lock."""


class StackLock:
    """An installation-local, kernel-owned exclusive lock for one stack."""

    def __init__(self, state_dir: str | Path, stack: str) -> None:
        if not _STACK_NAME_RE.match(stack or ""):
            raise ValueError(f"Refusing unsafe stack name for lock path: {stack!r}")
        self.state_dir = Path(state_dir)
        self.stack = stack
        self.path = self.state_dir / "locks" / f"{stack}.lock"
        self._handle = None

    def owner_snapshot(self) -> dict:
        """Best-effort owner description for contention diagnostics."""
        try:
            stat = self.path.stat()
            return {"lockFile": str(self.path), "inode": stat.st_ino}
        except OSError:
            return {"lockFile": str(self.path), "inode": None}

    @contextlib.contextmanager
    def acquire(self):
        """Hold the exclusive lock; raise :class:`LockBusyError` if held."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(self.path, "a+b")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            raise LockBusyError(
                f"Stack {self.stack!r} is owned by another controller "
                f"({self.owner_snapshot()}); refusing a competing writer."
            ) from None
        self._handle = handle
        try:
            yield self
        finally:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            finally:
                handle.close()
                self._handle = None
            # Never unlink: the inode stays so a live owner is never deleted
            # from under itself by a racing releaser.

    def probe(self) -> bool:
        """Return True when the lock is currently held by anyone."""
        if not self.path.exists():
            return False
        handle = open(self.path, "a+b")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return True
        with contextlib.suppress(OSError):
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()
        return False


def ensure_no_competing_writer(state_dir: str | Path, stack: str) -> None:
    """Fail fast when another controller owns the stack."""
    candidate = StackLock(state_dir, stack)
    if candidate.probe():
        raise LockBusyError(
            f"Stack {stack!r} is owned by another controller "
            f"({candidate.owner_snapshot()}); reconcile or stop the existing "
            "writer before launching a competing one."
        )


__all__ = ["LockBusyError", "StackLock", "ensure_no_competing_writer"]
