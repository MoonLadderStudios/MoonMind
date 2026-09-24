"""One small local operation record per deployment operation (REQ-04).

The record distinguishes desired state (what the operator requested) from
installed state (what the controller confirmed), records selected concrete
images once, keeps every attempt error so later noise cannot erase the first
one, and is written atomically so interruption leaves a recoverable record
instead of a truncated sole copy.

Automatic attempts are bounded by ``MAX_AUTO_ATTEMPTS``. Exhaustion is not a
permanent ban: an explicit ``begin_retry`` starts a fresh bounded attempt
group with prior diagnostics retained. Reporting or cleanup failures are
recorded alongside the result and can never erase a confirmed installation
or hide failed mandatory verification.
"""
from __future__ import annotations

import contextlib
import json
import os
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

MAX_AUTO_ATTEMPTS = 3
MAX_ATTEMPT_GROUPS = 5

OPEN_STATUSES = ("pending", "staged", "applying")
TERMINAL_STATUSES = ("succeeded", "partially_verified", "failed", "superseded")

# Operation ids originate from caller-supplied URL paths, so they are treated
# as untrusted input: only a bounded safe alphabet may reach the filesystem.
# Generated ids are UUID hex with dashes, which this pattern accepts.
_SAFE_OPERATION_ID_RE = None


def _safe_operation_id_pattern():
    global _SAFE_OPERATION_ID_RE
    if _SAFE_OPERATION_ID_RE is None:
        import re

        _SAFE_OPERATION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
    return _SAFE_OPERATION_ID_RE


def check_operation_id(operation_id: str) -> str:
    """Validate a caller-supplied operation id before any filesystem use."""
    if not isinstance(operation_id, str) or not _safe_operation_id_pattern().match(
        operation_id
    ):
        raise ValueError(f"Refusing unsafe operation id: {operation_id!r}")
    return operation_id


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=path.name + ".", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, sort_keys=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise


def error_summary(attempts: list) -> str:
    if not attempts:
        return ""
    first = attempts[0]
    last = attempts[-1]
    summary = f"attempt {first.get('attempt')}: {first.get('error')}"
    if len(attempts) > 1:
        summary += f" (latest attempt {last.get('attempt')}: {last.get('error')})"
    return summary


class OperationStore:
    """Crash-safe JSON store for controller operation records."""

    def __init__(self, state_dir: str | Path) -> None:
        self.state_dir = Path(state_dir)
        self.operations_dir = self.state_dir / "operations"

    def _path(self, operation_id: str) -> Path:
        return self.operations_dir / f"{check_operation_id(operation_id)}.json"

    def _write(self, operation: dict) -> dict:
        operation["updatedAt"] = _utc_now()
        _atomic_write_json(self._path(operation["operationId"]), operation)
        return operation

    def begin(
        self,
        *,
        stack: str,
        desired_image: str,
        source_revision: str,
        reason: str = "",
        target: Mapping[str, Any] | None = None,
    ) -> dict:
        """Start an operation, reattaching to an open one for the same target.

        A lost acknowledgment must not fork a duplicate writer: when an
        unfinished operation already targets the same concrete image, the
        caller reattaches to it instead of launching a competing apply.
        When no unfinished operation targets the image but a completed
        (succeeded or partially verified) one does, the caller reattaches
        to that recorded success so a retried submission observes the
        terminal result instead of repeating the Compose mutation.
        """
        for operation in self.list_open(stack=stack):
            if operation.get("desired", {}).get("image") == desired_image:
                return operation
        completed = self.find_completed(stack=stack, desired_image=desired_image)
        if completed is not None:
            return completed
        operation = {
            "operationId": str(uuid.uuid4()),
            "stack": stack,
            "status": "pending",
            "desired": {
                "image": desired_image,
                "sourceRevision": source_revision,
                "reason": reason,
            },
            "target": dict(target or {}),
            "installed": None,
            "attemptGroup": 1,
            "attempts": [],
            "autoAttemptsExhausted": False,
            "verification": [],
            "reportingFailures": [],
            "previousRelease": None,
            "createdAt": _utc_now(),
            "updatedAt": _utc_now(),
        }
        return self._write(operation)

    def load(self, operation_id: str) -> dict:
        path = self._path(operation_id)
        if not path.exists():
            raise KeyError(f"Unknown operation: {operation_id}")
        return json.loads(path.read_text(encoding="utf-8"))

    def list_open(self, *, stack: str | None = None) -> list:
        operations = []
        if not self.operations_dir.is_dir():
            return operations
        for path in sorted(self.operations_dir.glob("*.json")):
            try:
                operation = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if stack is not None and operation.get("stack") != stack:
                continue
            if operation.get("status") in OPEN_STATUSES:
                operations.append(operation)
        return operations

    def list_terminal(self, *, stack: str | None = None) -> list:
        """Return terminal (non-open) operation records, newest last."""
        operations = []
        if not self.operations_dir.is_dir():
            return operations
        for path in sorted(self.operations_dir.glob("*.json")):
            try:
                operation = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if stack is not None and operation.get("stack") != stack:
                continue
            if operation.get("status") in TERMINAL_STATUSES:
                operations.append(operation)
        operations.sort(
            key=lambda op: (
                ((op.get("installed") or {}).get("confirmedAt") or ""),
                op.get("updatedAt") or "",
                op.get("createdAt") or "",
            )
        )
        return operations

    def find_completed(
        self, *, stack: str, desired_image: str
    ) -> dict | None:
        """Return the newest completed record for the same target, if any.

        Only successful terminal states reattach: a retried submission for
        an already-installed image observes the recorded success instead
        of repeating the mutation.
        """
        matches = [
            operation
            for operation in self.list_terminal(stack=stack)
            if operation.get("desired", {}).get("image") == desired_image
            and operation.get("status") in ("succeeded", "partially_verified")
        ]
        if not matches:
            return None
        return matches[-1]

    def supersede(self, operation_id: str, *, reason: str = "") -> dict:
        """Close an open operation as superseded without applying it.

        Stale open targets (an older pending operation for a stack that has
        since moved on) must never be replayed by restart recovery. The
        record is retained with its diagnostics; only its status changes,
        so ``list_open`` no longer returns it.
        """
        operation = self.load(operation_id)
        if operation.get("status") in TERMINAL_STATUSES:
            return operation
        if operation.get("installed") is not None:
            return operation
        operation["status"] = "superseded"
        operation["supersededReason"] = reason
        operation["supersededAt"] = _utc_now()
        return self._write(operation)

    def mark_stage(self, operation_id: str, *, stage: str) -> dict:
        operation = self.load(operation_id)
        if operation.get("installed") is not None:
            return operation
        operation["status"] = stage
        return self._write(operation)

    def record_attempt_error(self, operation_id: str, *, error: str) -> dict:
        operation = self.load(operation_id)
        attempts = list(operation.get("attempts") or [])
        group_base = (operation.get("attemptGroup", 1) - 1) * MAX_AUTO_ATTEMPTS
        attempt = group_base + len(
            [a for a in attempts if a.get("attemptGroup", 1) == operation.get("attemptGroup", 1)]
        ) + 1
        attempts.append(
            {
                "attempt": attempt,
                "attemptGroup": operation.get("attemptGroup", 1),
                "error": error,
                "at": _utc_now(),
            }
        )
        operation["attempts"] = attempts
        operation["errorSummary"] = error_summary(attempts)
        group_attempts = len(
            [a for a in attempts if a.get("attemptGroup", 1) == operation.get("attemptGroup", 1)]
        )
        if group_attempts >= MAX_AUTO_ATTEMPTS:
            operation["autoAttemptsExhausted"] = True
            if operation.get("status") not in ("succeeded",):
                operation["status"] = "failed"
        elif operation.get("status") == "failed" and not operation.get("autoAttemptsExhausted"):
            operation["status"] = "pending"
        return self._write(operation)

    def begin_retry(self, operation_id: str) -> dict:
        """Start a fresh bounded attempt group; prior diagnostics are kept."""
        operation = self.load(operation_id)
        if operation.get("attemptGroup", 1) >= MAX_ATTEMPT_GROUPS:
            raise RuntimeError(
                "Retry budget exhausted for this operation; start a new "
                "authorized operation instead of editing state files."
            )
        operation["attemptGroup"] = operation.get("attemptGroup", 1) + 1
        operation["autoAttemptsExhausted"] = False
        operation["status"] = "pending"
        return self._write(operation)

    def confirm_installed(self, operation_id: str, *, image: str) -> dict:
        """Record a confirmed installation. Append-only: nothing clears it."""
        operation = self.load(operation_id)
        operation["installed"] = {"image": image, "confirmedAt": _utc_now()}
        operation["status"] = "succeeded"
        return self._write(operation)

    def record_verification(
        self, operation_id: str, *, name: str, status: str, detail: str = ""
    ) -> dict:
        operation = self.load(operation_id)
        checks = list(operation.get("verification") or [])
        checks.append({"name": name, "status": status, "detail": detail, "at": _utc_now()})
        operation["verification"] = checks
        if status in ("failed", "unavailable") and operation.get("status") == "succeeded":
            operation["status"] = "partially_verified"
        return self._write(operation)

    def note_reporting_failure(self, operation_id: str, *, error: str) -> dict:
        """A reporting/cleanup failure must not erase confirmed installation."""
        operation = self.load(operation_id)
        failures = list(operation.get("reportingFailures") or [])
        failures.append(error)
        operation["reportingFailures"] = failures
        return self._write(operation)

    def retain_previous_release(self, operation_id: str, *, image: str, compatible: bool) -> dict:
        """Retain a supported previous release for restoration, gated on
        actual schema/history compatibility, never automatic DB downgrade."""
        operation = self.load(operation_id)
        operation["previousRelease"] = {"image": image, "compatible": compatible}
        return self._write(operation)


__all__ = [
    "MAX_ATTEMPT_GROUPS",
    "MAX_AUTO_ATTEMPTS",
    "OPEN_STATUSES",
    "TERMINAL_STATUSES",
    "OperationStore",
    "error_summary",
]
