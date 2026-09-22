"""Crash-safe local operation record: desired vs installed state.

One small record per operation plus ``installed.json`` (last supported
installed configuration) and ``prepared.json`` (staged target). All writes
are atomic (tmp + fsync + rename) so interruption leaves recoverable intent,
never a truncated sole copy. Stdlib only.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from .redact import bound_tail, redact_text

CONTRACT = "moonmind.deployment-controller.v1"

# Bounded automatic attempts per operation. An explicit operator Retry starts
# a fresh bounded attempt; exhaustion never bans retrying the same target.
MAX_AUTO_ATTEMPTS = 3

TERMINAL = frozenset({"SUCCEEDED", "FAILED", "PARTIALLY_VERIFIED"})


def atomic_write(path: Path, payload: bytes) -> None:
    """Write ``payload`` atomically: tmp file in the same dir + fsync + rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def write_json(path: Path, record: Mapping[str, Any]) -> None:
    atomic_write(path, (json.dumps(record, sort_keys=True, indent=2) + "\n").encode())


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


@dataclass
class OperationRecord:
    operation_id: str
    target_image: str
    resolved_images: dict[str, str] = field(default_factory=dict)
    status: str = "PENDING"
    attempts: list[dict[str, Any]] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract": CONTRACT,
            "operationId": self.operation_id,
            "targetImage": self.target_image,
            "resolvedImages": dict(self.resolved_images),
            "status": self.status,
            "attempts": [dict(a) for a in self.attempts],
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "OperationRecord":
        return cls(
            operation_id=str(data.get("operationId") or ""),
            target_image=str(data.get("targetImage") or ""),
            resolved_images=dict(data.get("resolvedImages") or {}),
            status=str(data.get("status") or "PENDING"),
            attempts=[dict(a) for a in (data.get("attempts") or [])],
            created_at=float(data.get("createdAt") or 0),
            updated_at=float(data.get("updatedAt") or 0),
        )


class OperationStore:
    """Durable store rooted at ``state_dir`` with one live operation file."""

    def __init__(self, state_dir: str | Path) -> None:
        self.state_dir = Path(state_dir).expanduser()
        self.state_dir.mkdir(parents=True, exist_ok=True)

    @property
    def operation_path(self) -> Path:
        return self.state_dir / "operation.json"

    @property
    def installed_path(self) -> Path:
        return self.state_dir / "installed.json"

    @property
    def prepared_path(self) -> Path:
        return self.state_dir / "prepared.json"

    @property
    def log_path(self) -> Path:
        return self.state_dir / "operation.log"

    def load_operation(self) -> OperationRecord | None:
        data = read_json(self.operation_path)
        if not isinstance(data, dict) or not data.get("operationId"):
            return None
        try:
            return OperationRecord.from_dict(data)
        except (TypeError, ValueError):
            return None

    def save_operation(self, record: OperationRecord) -> None:
        record.updated_at = time.time()
        write_json(self.operation_path, record.to_dict())

    def append_log(self, text: str) -> None:
        """Append redacted log text; readable without any application service."""
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.log_path, "a", encoding="utf-8") as handle:
            handle.write(redact_text(text))
            if not text.endswith("\n"):
                handle.write("\n")

    def record_attempt(
        self,
        record: OperationRecord,
        *,
        phase: str,
        ok: bool,
        exit_code: int | None = None,
        log_tail: str = "",
    ) -> None:
        """Record one attempt outcome. Prior diagnostics are retained, never
        overwritten: reporting/cleanup failures cannot erase confirmed
        installation or hide failed mandatory verification."""
        record.attempts.append(
            {
                "phase": phase,
                "ok": ok,
                "exitCode": exit_code,
                "logTail": redact_text(bound_tail(log_tail)),
                "at": time.time(),
            }
        )
        self.save_operation(record)

    # -- desired vs installed -------------------------------------------

    def mark_prepared(self, *, target_image: str, resolved_images: Mapping[str, str]) -> None:
        """Persist the staged (desired) target once images are staged."""
        write_json(
            self.prepared_path,
            {
                "contract": CONTRACT,
                "targetImage": target_image,
                "resolvedImages": dict(resolved_images),
                "preparedAt": time.time(),
            },
        )

    def read_prepared(self) -> dict[str, Any] | None:
        data = read_json(self.prepared_path)
        return data if isinstance(data, dict) else None

    def mark_installed(
        self,
        *,
        target_image: str,
        resolved_images: Mapping[str, str],
        operation_id: str,
    ) -> None:
        """Persist the last supported installed configuration. Only called
        after apply + mandatory verification; secondary reporting failures
        must never erase this confirmation."""
        write_json(
            self.installed_path,
            {
                "contract": CONTRACT,
                "targetImage": target_image,
                "resolvedImages": dict(resolved_images),
                "operationId": operation_id,
                "installedAt": time.time(),
            },
        )

    def read_installed(self) -> dict[str, Any] | None:
        data = read_json(self.installed_path)
        return data if isinstance(data, dict) else None

    # -- restart convergence --------------------------------------------

    def converge_on_restart(self, *, observed_running: Mapping[str, str]) -> str:
        """Decide unfinished work after a restart without repeating a
        completed apply.

        Returns one of ``no-operation`` (nothing durable to do), ``resume``
        (prepared target exists but installed does not match: converge toward
        the same prepared target), or ``settled`` (installed already matches
        the prepared target or the operation is terminal: do not re-apply).
        """
        record = self.load_operation()
        if record is None:
            return "no-operation"
        if record.status in TERMINAL:
            return "settled"
        prepared = self.read_prepared()
        installed = self.read_installed()
        if prepared is None:
            return "resume"
        if (
            isinstance(installed, dict)
            and installed.get("targetImage") == prepared.get("targetImage")
            and installed.get("resolvedImages") == prepared.get("resolvedImages")
        ):
            record.status = "SUCCEEDED"
            self.save_operation(record)
            return "settled"
        _ = observed_running  # Docker inspection happens in controller, not here.
        return "resume"

    # -- retry ------------------------------------------------------------

    def explicit_retry(self, *, target_image: str | None = None) -> OperationRecord:
        """Start a fresh bounded attempt for an explicit operator Retry.

        Prior diagnostics are retained on the same record; no manual counter
        edits and no permanently spent submission lock.
        """
        record = self.load_operation()
        if record is None:
            record = OperationRecord(
                operation_id=f"op-{uuid.uuid4().hex[:12]}",
                target_image=(target_image or "").strip(),
            )
        else:
            if target_image:
                record.target_image = target_image
            record.status = "PENDING"
        automatic = [a for a in record.attempts if not a.get("explicitRetry")]
        if len(automatic) >= MAX_AUTO_ATTEMPTS and not target_image:
            # Fresh explicit retry: keep history, reset the automatic budget
            # marker so the new attempt is genuinely bounded, not spent.
            record.attempts.append({"phase": "retry", "explicitRetry": True, "at": time.time()})
        self.save_operation(record)
        return record
