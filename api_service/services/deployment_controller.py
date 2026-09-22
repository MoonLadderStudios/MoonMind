"""Shared deployment-controller operation identity (MoonMind#4502).

Settings Operations, the host update command, and the typed update Skill/tool
all submit or observe the same controller operation. An update is not a
``MoonMind.UserWorkflow``: the controller owns the durable operation identity,
selected target, status, and redacted logs in local deployment state, and
neither the browser, the workflow engine, nor the application artifact service
supervises its lifetime.

Duplicate submission and lost acknowledgment (browser refresh, API restart,
client timeout, disconnection) reattach to the same operation instead of
launching a second updater. An explicit retry starts a fresh bounded attempt
while preserving the first failure. A changed target is explicit new intent
and creates a new operation.

Only the standard library is used so the portable host entrypoint can share
this file format and identity scheme without importing application code.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

try:
    import fcntl  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - non-POSIX hosts
    fcntl = None  # type: ignore[assignment]

OPERATIONS_SUBDIR = "update-operations"

TERMINAL_STATUSES = frozenset({"SUCCEEDED", "FAILED", "PARTIALLY_VERIFIED"})

_MAX_LOG_CHARS = 4000
_MAX_OPERATIONS_LISTED = 20

_URL_USERINFO_RE = re.compile(r"(://)[^/\s:]+:[^/\s@]+@")
_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)(password|passwd|token|secret|authorization|cookie)(\s*[:=]\s*)(\S+)"
)


class DeploymentControllerError(ValueError):
    """A truthful, distinct controller-operation failure.

    Codes: ``controller_unavailable``, ``controller_access_denied``,
    ``controller_operation_not_found``, ``controller_retry_exhausted``,
    ``controller_history_upload_failed``. The message never carries secret
    material.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class ControllerOperation:
    """One durable deployment-controller operation."""

    operation_id: str
    stack: str
    repository: str
    reference: str
    mode: str
    operation_kind: str = "update"
    status: str = "QUEUED"
    requested_image: str | None = None
    resolved_digest: str | None = None
    reason: str | None = None
    operator: str | None = None
    retry_of: str | None = None
    attempt: int = 1
    first_error: str | None = None
    error: str | None = None
    verification_pending: bool = False
    logs_text: str = ""
    created_at: str | None = None
    updated_at: str | None = None
    history_import: dict | None = None


def redact_text(text: str) -> str:
    """Redact likely credential material while keeping diagnostics readable."""

    redacted = _URL_USERINFO_RE.sub(r"\1***@", text or "")
    return _SECRET_ASSIGNMENT_RE.sub(r"\1\2***", redacted)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _dedupe_key(
    *,
    stack: str,
    repository: str,
    reference: str,
    mode: str,
    operation_kind: str,
    reason: str,
) -> str:
    material = "|".join(
        [
            "deployment-operation",
            stack,
            repository,
            reference,
            mode,
            operation_kind or "update",
            reason,
        ]
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _default_state_dir() -> Path:
    configured = str(os.environ.get("MOONMIND_DEPLOYMENT_STATE_DIR") or "").strip()
    if configured:
        return Path(configured) / OPERATIONS_SUBDIR
    return Path("/workspace/deployment_state") / OPERATIONS_SUBDIR


def _atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=path.name + ".", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _operation_to_record(operation: ControllerOperation) -> dict:
    return {
        "operationId": operation.operation_id,
        "stack": operation.stack,
        "repository": operation.repository,
        "reference": operation.reference,
        "mode": operation.mode,
        "operationKind": operation.operation_kind,
        "status": operation.status,
        "requestedImage": operation.requested_image,
        "resolvedDigest": operation.resolved_digest,
        "reason": operation.reason,
        "operator": operation.operator,
        "retryOf": operation.retry_of,
        "attempt": operation.attempt,
        "firstError": operation.first_error,
        "error": operation.error,
        "verificationPending": operation.verification_pending,
        "logsText": operation.logs_text,
        "createdAt": operation.created_at,
        "updatedAt": operation.updated_at,
        "historyImport": operation.history_import,
    }


def _operation_from_record(record: dict) -> ControllerOperation:
    return ControllerOperation(
        operation_id=str(record.get("operationId") or ""),
        stack=str(record.get("stack") or ""),
        repository=str(record.get("repository") or ""),
        reference=str(record.get("reference") or ""),
        mode=str(record.get("mode") or ""),
        operation_kind=str(record.get("operationKind") or "update"),
        status=str(record.get("status") or "QUEUED"),
        requested_image=record.get("requestedImage"),
        resolved_digest=record.get("resolvedDigest"),
        reason=record.get("reason"),
        operator=record.get("operator"),
        retry_of=record.get("retryOf"),
        attempt=int(record.get("attempt") or 1),
        first_error=record.get("firstError"),
        error=record.get("error"),
        verification_pending=bool(record.get("verificationPending", False)),
        logs_text=str(record.get("logsText") or ""),
        created_at=record.get("createdAt"),
        updated_at=record.get("updatedAt"),
        history_import=record.get("historyImport"),
    )


@dataclass
class DeploymentControllerClient:
    """Narrow authenticated interface to the deployment controller's operations.

    ``controller_secret`` is deployment-owned and server-to-controller only:
    it is compared with a constant-time check and never appears in operation
    records, logs, or error messages. When no secret is configured (local
    single-operator default), submissions are admitted without a credential.
    """

    state_dir: Path | None = None
    controller_secret: str | None = None
    available: bool = True
    max_attempts: int = 3

    def _directory(self) -> Path:
        if self.state_dir is not None:
            return Path(self.state_dir) / OPERATIONS_SUBDIR if Path(
                self.state_dir
            ).name != OPERATIONS_SUBDIR else Path(self.state_dir)
        return _default_state_dir()

    def _check_available(self) -> None:
        if not self.available:
            raise DeploymentControllerError(
                "controller_unavailable",
                "The deployment controller is not reachable; the API "
                "dashboard cannot submit work right now. Use the host "
                "update command as the recovery path.",
            )

    def _check_credential(self, credential: str | None) -> None:
        if not self.controller_secret:
            return
        presented = str(credential or "")
        if not presented or not hmac.compare_digest(
            presented, self.controller_secret
        ):
            raise DeploymentControllerError(
                "controller_access_denied",
                "The deployment controller rejected the request credential.",
            )

    def _read_record(self, operation_id: str) -> dict | None:
        path = self._directory() / f"{operation_id}.json"
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def _find_live_by_dedupe(self, dedupe: str) -> dict | None:
        directory = self._directory()
        try:
            names = sorted(
                name
                for name in os.listdir(directory)
                if name.startswith("depupd_") and name.endswith(".json")
            )
        except OSError:
            return None
        for name in names:
            try:
                record = json.loads((directory / name).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(record, dict):
                continue
            if record.get("dedupeKey") != dedupe:
                continue
            if str(record.get("status") or "") not in TERMINAL_STATUSES:
                return record
        return None

    def submit(
        self,
        request: dict,
        *,
        operator: str | None,
        credential: str | None = None,
    ) -> ControllerOperation:
        """Submit an update or observe the live operation for identical intent.

        Identical intent (same stack, target, mode, kind, and reason) while a
        previous operation is still accepted/running reattaches to that
        operation: exactly one mutation owner exists per live intent.
        """

        self._check_available()
        self._check_credential(credential)
        stack = str(request.get("stack") or "").strip()
        repository = str(request.get("repository") or "").strip()
        reference = str(request.get("reference") or "").strip()
        mode = str(request.get("mode") or "").strip()
        operation_kind = str(request.get("operation_kind") or "update").strip()
        reason = str(request.get("reason") or "").strip()
        dedupe = _dedupe_key(
            stack=stack,
            repository=repository,
            reference=reference,
            mode=mode,
            operation_kind=operation_kind,
            reason=reason,
        )
        live = self._find_live_by_dedupe(dedupe)
        if live is not None:
            return _operation_from_record(live)
        now = _utc_now_iso()
        operation_id = f"depupd_{uuid4().hex[:24]}"
        requested_image = f"{repository}:{reference}" if repository else None
        record = _operation_to_record(
            ControllerOperation(
                operation_id=operation_id,
                stack=stack,
                repository=repository,
                reference=reference,
                mode=mode,
                operation_kind=operation_kind,
                status="QUEUED",
                requested_image=requested_image,
                reason=reason or None,
                operator=(str(operator).strip() or None) if operator else None,
                created_at=now,
                updated_at=now,
            )
        )
        record["dedupeKey"] = dedupe
        _atomic_write_json(self._directory() / f"{operation_id}.json", record)
        return _operation_from_record(record)

    def observe(self, operation_id: str) -> ControllerOperation:
        self._check_available()
        record = self._read_record(operation_id)
        if record is None:
            raise DeploymentControllerError(
                "controller_operation_not_found",
                f"Deployment operation {operation_id} is not known.",
            )
        return _operation_from_record(record)

    def record_result(
        self,
        operation_id: str,
        *,
        status: str,
        error: str | None = None,
        resolved_digest: str | None = None,
        verification_pending: bool = False,
    ) -> ControllerOperation:
        self._check_available()
        record = self._read_record(operation_id)
        if record is None:
            raise DeploymentControllerError(
                "controller_operation_not_found",
                f"Deployment operation {operation_id} is not known.",
            )
        redacted_error = redact_text(str(error or "").strip()) or None
        first_error = record.get("firstError") or redacted_error
        record.update(
            {
                "status": status,
                "error": redacted_error,
                "firstError": first_error,
                "verificationPending": bool(verification_pending),
                "updatedAt": _utc_now_iso(),
            }
        )
        if resolved_digest:
            record["resolvedDigest"] = str(resolved_digest).strip()
        _atomic_write_json(self._directory() / f"{operation_id}.json", record)
        return _operation_from_record(record)

    def retry(
        self,
        operation_id: str,
        *,
        operator: str | None,
        credential: str | None = None,
    ) -> ControllerOperation:
        """Request a fresh bounded attempt for a terminal operation.

        A still-running operation reattaches instead of duplicating mutation.
        The new attempt preserves the first failure and links ``retry_of``.
        """

        self._check_available()
        self._check_credential(credential)
        record = self._read_record(operation_id)
        if record is None:
            raise DeploymentControllerError(
                "controller_operation_not_found",
                f"Deployment operation {operation_id} is not known.",
            )
        if str(record.get("status") or "") not in TERMINAL_STATUSES:
            return _operation_from_record(record)
        attempt = int(record.get("attempt") or 1)
        if attempt >= max(1, self.max_attempts):
            raise DeploymentControllerError(
                "controller_retry_exhausted",
                f"Deployment operation {operation_id} exhausted its "
                f"bounded retry budget ({self.max_attempts} attempts); "
                "a changed target is explicit new intent.",
            )
        first_error = record.get("firstError") or record.get("error")
        now = _utc_now_iso()
        new_id = f"depupd_{uuid4().hex[:24]}"
        new_record = _operation_to_record(
            ControllerOperation(
                operation_id=new_id,
                stack=str(record.get("stack") or ""),
                repository=str(record.get("repository") or ""),
                reference=str(record.get("reference") or ""),
                mode=str(record.get("mode") or ""),
                operation_kind=str(record.get("operationKind") or "update"),
                status="QUEUED",
                requested_image=record.get("requestedImage"),
                reason=record.get("reason"),
                operator=(str(operator).strip() or None) if operator else None,
                retry_of=operation_id,
                attempt=attempt + 1,
                first_error=first_error,
                error=None,
                created_at=now,
                updated_at=now,
            )
        )
        # A retry is explicit fresh intent: it must not reattach to the
        # terminal record it supersedes.
        new_record["dedupeKey"] = f"{record.get('dedupeKey')}|retry:{new_id}"
        _atomic_write_json(self._directory() / f"{new_id}.json", new_record)
        return _operation_from_record(new_record)

    def append_log(self, operation_id: str, text: str) -> ControllerOperation:
        self._check_available()
        record = self._read_record(operation_id)
        if record is None:
            raise DeploymentControllerError(
                "controller_operation_not_found",
                f"Deployment operation {operation_id} is not known.",
            )
        combined = (str(record.get("logsText") or "") + "\n" + str(text or "")).strip()
        combined = redact_text(combined)
        if len(combined) > _MAX_LOG_CHARS:
            keep = _MAX_LOG_CHARS - len("\n...[elided]...\n")
            head = keep // 2
            combined = (
                combined[:head] + "\n...[elided]...\n" + combined[len(combined) - (keep - head):]
            )
        record["logsText"] = combined
        record["updatedAt"] = _utc_now_iso()
        _atomic_write_json(self._directory() / f"{operation_id}.json", record)
        return _operation_from_record(record)

    def record_history_import(
        self,
        operation_id: str,
        *,
        imported: bool,
        error: str | None = None,
    ) -> ControllerOperation:
        """Record an optional application-history import after recovery.

        The import never changes the confirmed deployment outcome: status,
        error, and first error are preserved exactly.
        """

        self._check_available()
        record = self._read_record(operation_id)
        if record is None:
            raise DeploymentControllerError(
                "controller_operation_not_found",
                f"Deployment operation {operation_id} is not known.",
            )
        record["historyImport"] = {
            "imported": bool(imported),
            "error": redact_text(str(error or "").strip()) or None,
            "recordedAt": _utc_now_iso(),
        }
        _atomic_write_json(self._directory() / f"{operation_id}.json", record)
        return _operation_from_record(record)

    def mutation_owner_count(self, operation_id: str) -> int:
        """Exactly one mutation owner exists per live operation identity."""

        record = self._read_record(operation_id)
        return 1 if record is not None else 0

    def list_stack_operations(
        self, stack: str, *, limit: int = _MAX_OPERATIONS_LISTED
    ) -> tuple[ControllerOperation, ...]:
        directory = self._directory()
        try:
            names = sorted(
                (
                    name
                    for name in os.listdir(directory)
                    if name.startswith("depupd_") and name.endswith(".json")
                ),
                reverse=True,
            )
        except OSError:
            return ()
        operations: list[ControllerOperation] = []
        for name in names[: max(1, limit)]:
            try:
                record = json.loads((directory / name).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(record, dict) or record.get("stack") != stack:
                continue
            operations.append(_operation_from_record(record))
        operations.sort(key=lambda op: op.created_at or "", reverse=True)
        return tuple(operations)
