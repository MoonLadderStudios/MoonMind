"""Policy-gated deployment operation service."""

from __future__ import annotations

import asyncio
import http.client
import json
import os
import re
import socket
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from moonmind.utils.logging import redact_sensitive_text
from moonmind.workflows.skills.deployment_tools import (
    DEPLOYMENT_UPDATE_TOOL_NAME,
    DEPLOYMENT_UPDATE_TOOL_VERSION,
)

CurrentImageEvidence = Literal[
    "desired_state", "environment", "policy", "unavailable"
]


_IMAGE_REFERENCE_PATTERN = re.compile(
    r"^(?:[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}|sha256:[A-Fa-f0-9]{64})$"
)

# Standalone controller (MoonLadderStudios/MoonMind#4500, #4502): the
# Operations API submits and observes the same controller operation as the
# host entrypoint. The controller owns the update's lifetime; this API never
# supervises it, and a timeout or lost reply is never permission to launch a
# second updater. The API reaches the controller through its read-only
# deployment-state mount (Unix socket, deployment-owned secret, and durable
# operation records), so no network path into the controller's separate
# Compose project and no browser-visible credential exist.
CONTROLLER_DEFAULT_STATE_DIR = "/workspace/deployment_state/controller"
CONTROLLER_SOCKET_NAME = "controller.sock"
CONTROLLER_REQUEST_TIMEOUT_SECONDS = 15
CONTROLLER_RECENT_RECORDS = 10
CONTROLLER_LOG_LINES = 20
CONTROLLER_LOG_LINE_CHARS = 1000
CONTROLLER_RESTORE_HINT = (
    "Restore it on the host with `python3 deploy/controller/bootstrap.py "
    "restore` (or run ./tools/update-moonmind.sh); the controller keeps "
    "ownership of its recorded operations."
)
_OPERATION_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")

ControllerInstallation = Literal["absent", "never_started", "installed"]


def controller_state_dir() -> Path:
    """Return the controller state directory as mounted into the API."""
    explicit = str(os.environ.get("MOONMIND_CONTROLLER_STATE_DIR") or "").strip()
    return Path(explicit or CONTROLLER_DEFAULT_STATE_DIR)


def _controller_secret_path(state_dir: Path) -> Path:
    explicit = str(os.environ.get("MOONMIND_CONTROLLER_SECRET_FILE") or "").strip()
    return Path(explicit) if explicit else state_dir / "secrets" / "controller-bearer"


def controller_installation(state_dir: Path | None = None) -> ControllerInstallation:
    """Classify this deployment's controller from its deployment-owned state.

    ``absent``: no controller secret (never installed). ``never_started``:
    bootstrap wrote a secret but no verified image could start a controller
    and it owns no operation record. ``installed``: the controller owns
    updates for this deployment. Mirrors the host entrypoint's cutover rule.
    """
    root = state_dir or controller_state_dir()
    if not _controller_secret_path(root).exists():
        return "absent"
    operations = root / "operations"
    try:
        if operations.is_dir() and any(operations.glob("*.json")):
            return "installed"
    except OSError:
        return "installed"
    try:
        image = json.loads((root / "controller-image.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        image = None
    if isinstance(image, dict) and image.get("verified"):
        return "installed"
    return "never_started"


class ControllerUnreachable(RuntimeError):
    """The controller endpoint could not be reached (nothing was sent)."""


class ControllerUncertain(RuntimeError):
    """The request was sent but no reply arrived; it may have been accepted."""


class _ControllerConnection(http.client.HTTPConnection):
    """HTTP over the controller's state-dir Unix socket (or explicit TCP URL)."""

    def __init__(self, *, socket_path: str | None, host: str, port: int | None, timeout: float) -> None:
        super().__init__(host, port, timeout=timeout)
        self._socket_path = socket_path

    def connect(self) -> None:
        if self._socket_path is None:
            super().connect()
            return
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        try:
            sock.connect(self._socket_path)
        except BaseException:
            sock.close()
            raise
        self.sock = sock


def _controller_connection(state_dir: Path) -> _ControllerConnection:
    explicit = str(os.environ.get("MOONMIND_CONTROLLER_URL") or "").strip()
    if explicit:
        parsed = urlsplit(explicit)
        return _ControllerConnection(
            socket_path=None,
            host=parsed.hostname or "127.0.0.1",
            port=parsed.port,
            timeout=CONTROLLER_REQUEST_TIMEOUT_SECONDS,
        )
    return _ControllerConnection(
        socket_path=str(state_dir / CONTROLLER_SOCKET_NAME),
        host="localhost",
        port=None,
        timeout=CONTROLLER_REQUEST_TIMEOUT_SECONDS,
    )


def _read_controller_secret(state_dir: Path) -> str:
    path = _controller_secret_path(state_dir)
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise ControllerUnreachable(
            "the deployment-owned controller secret is not readable by the API"
        ) from exc
    if not value:
        raise ControllerUnreachable("the deployment-owned controller secret is empty")
    return value


def controller_request(
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    *,
    state_dir: Path | None = None,
) -> tuple[int, dict[str, Any]]:
    """Send one authenticated request to the standalone controller.

    Raises :class:`ControllerUnreachable` when nothing reached the
    controller and :class:`ControllerUncertain` when the request was sent
    but no reply arrived (the controller may have accepted it).
    """
    root = state_dir or controller_state_dir()
    secret = _read_controller_secret(root)
    connection = _controller_connection(root)
    try:
        try:
            connection.connect()
        except OSError as exc:
            raise ControllerUnreachable(
                f"the controller endpoint is not answering ({type(exc).__name__})"
            ) from exc
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        try:
            connection.request(
                method,
                path,
                body=body,
                headers={
                    "Authorization": f"Bearer {secret}",
                    "Content-Type": "application/json",
                },
            )
            response = connection.getresponse()
            raw = response.read().decode("utf-8", errors="replace") or "{}"
        except (OSError, http.client.HTTPException) as exc:
            raise ControllerUncertain(
                f"the controller did not answer ({type(exc).__name__})"
            ) from exc
    finally:
        connection.close()
    try:
        parsed = json.loads(raw)
    except ValueError:
        parsed = {}
    return response.status, parsed if isinstance(parsed, dict) else {}


def _controller_decision_error(status: int, parsed: dict[str, Any]) -> DeploymentOperationError:
    """Map a controller answer onto a truthful, credential-free API error."""
    detail = redact_sensitive_text(str(parsed.get("error") or ""))[:500]
    if status == 401:
        return DeploymentOperationError(
            "deployment_controller_unauthorized",
            "The standalone controller rejected the API's deployment-owned "
            "credential; reinstall or restore the controller so both read the "
            "same secret.",
            http_status=502,
        )
    if status == 409:
        return DeploymentOperationError(
            "deployment_controller_busy",
            f"The standalone controller owns an unfinished operation for this "
            f"stack ({detail or 'conflict'}); observe or retry it instead of "
            "starting a competing update.",
            http_status=409,
            operation_id=str(parsed.get("operationId") or "") or None,
        )
    if status == 404:
        return DeploymentOperationError(
            "deployment_controller_operation_not_found",
            "The standalone controller has no such operation.",
            http_status=404,
        )
    if status == 400:
        return DeploymentOperationError(
            "deployment_controller_refused",
            f"The standalone controller refused the request: {detail or 'invalid request'}",
            http_status=422,
        )
    return DeploymentOperationError(
        "deployment_controller_failed",
        f"The standalone controller answered HTTP {status}; the operation "
        "state is recorded by the controller.",
        http_status=502,
    )


def _controller_unavailable_error(exc: Exception) -> DeploymentOperationError:
    return DeploymentOperationError(
        "deployment_controller_unavailable",
        f"The standalone deployment controller is unavailable ({exc}). "
        + CONTROLLER_RESTORE_HINT,
        http_status=503,
    )


def controller_action_status(controller_status: str) -> str:
    """Map a controller operation status onto a recent-action status."""
    return {
        "pending": "QUEUED",
        "staged": "RUNNING",
        "applying": "RUNNING",
        "succeeded": "SUCCEEDED",
        "partially_verified": "PARTIALLY_VERIFIED",
        "failed": "FAILED",
        "superseded": "SUPERSEDED",
    }.get(controller_status, "UNKNOWN")


def controller_log_lines(operation: dict[str, Any]) -> tuple[str, ...]:
    """Bounded, redacted diagnostic lines from one controller record."""
    lines: list[str] = []
    for attempt in operation.get("attempts") or ():
        if isinstance(attempt, dict):
            lines.append(f"attempt {attempt.get('attempt')}: {attempt.get('error')}")
    for check in operation.get("verification") or ():
        if isinstance(check, dict):
            lines.append(
                f"check {check.get('name')}: {check.get('status')} - {check.get('detail')}"
            )
    for failure in operation.get("reportingFailures") or ():
        lines.append(f"reporting: {failure}")
    if operation.get("supersededReason"):
        lines.append(f"superseded: {operation.get('supersededReason')}")
    return tuple(
        redact_sensitive_text(str(line))[:CONTROLLER_LOG_LINE_CHARS]
        for line in lines[-CONTROLLER_LOG_LINES:]
    )


def _controller_record_paths(state_dir: Path) -> list[Path]:
    operations_dir = state_dir / "operations"
    try:
        paths = [path for path in operations_dir.glob("*.json") if path.is_file()]
        return sorted(paths, key=lambda path: path.stat().st_mtime_ns)
    except OSError:
        return []


def read_controller_record(
    operation_id: str, *, state_dir: Path | None = None
) -> dict[str, Any] | None:
    """Read one durable controller record directly (controller may be down)."""
    if not _OPERATION_ID_PATTERN.fullmatch(operation_id or ""):
        return None
    filename = f"{operation_id}.json"
    for path in _controller_record_paths(state_dir or controller_state_dir()):
        if path.name != filename:
            continue
        try:
            parsed = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def controller_action(operation: dict[str, Any]) -> DeploymentRecentAction:
    """Project one controller record as an Operations recent action."""
    operation_id = str(operation.get("operationId") or "")
    desired = operation.get("desired") or {}
    installed = operation.get("installed") or {}
    status = controller_action_status(str(operation.get("status") or ""))
    return DeploymentRecentAction(
        id=f"ctl-{operation_id}",
        kind="update",
        status=status,
        requested_image=str(desired.get("image") or "") or None,
        resolved_digest=str(installed.get("image") or "") or None,
        reason=str(desired.get("reason") or "") or None,
        started_at=str(operation.get("createdAt") or "") or None,
        completed_at=str(installed.get("confirmedAt") or "")
        or (str(operation.get("updatedAt") or "") or None if status == "FAILED" else None),
        # No workflow or artifact exists for a controller operation.
        run_detail_url=None,
        run_id=None,
        operation_id=operation_id or None,
        error_summary=redact_sensitive_text(str(operation.get("errorSummary") or ""))
        or None,
        log_lines=controller_log_lines(operation),
        retry_permitted=status == "FAILED",
    )


def controller_recent_actions(
    stack: str, *, limit: int = CONTROLLER_RECENT_RECORDS
) -> tuple[DeploymentRecentAction, ...]:
    """Recent controller operations for the stack, newest first.

    The controller's crash-safe record (mounted read-only) is the durable
    index, so progress survives API replacement and browser reload with
    ordinary bounded reads, even while the controller itself restarts.
    """
    records = []
    for path in reversed(_controller_record_paths(controller_state_dir())):
        try:
            parsed = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(parsed, dict) and parsed.get("stack") == stack:
            records.append(parsed)
        if len(records) >= limit:
            break
    return tuple(controller_action(record) for record in records)


def observe_controller_operation(operation_id: str) -> tuple[dict[str, Any], str]:
    """Observe one operation through the controller, else its durable record."""
    if not _OPERATION_ID_PATTERN.fullmatch(operation_id or ""):
        raise DeploymentOperationError(
            "deployment_controller_operation_not_found",
            "The standalone controller has no such operation.",
            http_status=404,
        )
    try:
        status, parsed = controller_request("GET", f"/v1/operations/{operation_id}")
    except (ControllerUnreachable, ControllerUncertain):
        status, parsed = 0, {}
    if status == 200:
        return parsed, "controller"
    record = read_controller_record(operation_id)
    if record is not None:
        return record, "record"
    if status:
        raise _controller_decision_error(status, parsed)
    raise DeploymentOperationError(
        "deployment_controller_operation_not_found",
        "The standalone controller has no such operation.",
        http_status=404,
    )


def retry_controller_operation(operation_id: str) -> dict[str, Any]:
    """Request the controller's fresh bounded attempt for one operation."""
    if not _OPERATION_ID_PATTERN.fullmatch(operation_id or ""):
        raise DeploymentOperationError(
            "deployment_controller_operation_not_found",
            "The standalone controller has no such operation.",
            http_status=404,
        )
    try:
        status, parsed = controller_request(
            "POST", f"/v1/operations/{operation_id}/retry", {}
        )
    except ControllerUnreachable as exc:
        raise _controller_unavailable_error(exc) from exc
    except ControllerUncertain as exc:
        raise DeploymentOperationError(
            "deployment_controller_uncertain",
            "The standalone controller did not acknowledge the retry in time; "
            "it may have started. Refresh to observe the operation.",
            http_status=504,
        ) from exc
    if status != 202:
        raise _controller_decision_error(status, parsed)
    return parsed


class DeploymentOperationError(ValueError):
    """Raised when a deployment operation request violates policy or fails."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        http_status: int = 422,
        operation_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status
        self.operation_id = operation_id


@dataclass(frozen=True)
class DeploymentStackPolicy:
    stack: str
    project_name: str
    repository: str
    allowed_references: tuple[str, ...]
    recent_tags: tuple[str, ...]
    allow_mutable_tags: bool
    allow_custom_digest: bool
    allowed_modes: tuple[str, ...]
    configured_reference: str

    @property
    def configured_image(self) -> str:
        return f"{self.repository}:{self.configured_reference}"


@dataclass(frozen=True)
class DeploymentUpdateSubmission:
    stack: str
    repository: str
    reference: str
    mode: str
    remove_orphans: bool
    wait: bool
    run_smoke_check: bool
    pause_work: bool
    prune_old_images: bool
    reason: str | None
    requested_by_user_id: UUID | str | None
    operation_kind: str = "update"
    rollback_source_action_id: str | None = None
    confirmation: str | None = None
    before_build_id: str | None = None


@dataclass(frozen=True)
class RollbackImageTarget:
    repository: str
    reference: str


@dataclass(frozen=True)
class RollbackEligibilityDecision:
    eligible: bool
    target_image: RollbackImageTarget | None = None
    source_action_id: str | None = None
    reason: str | None = None
    evidence_ref: str | None = None


@dataclass(frozen=True)
class DeploymentRecentAction:
    id: str
    kind: str
    status: str
    requested_image: str | None = None
    resolved_digest: str | None = None
    operator: str | None = None
    reason: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    run_detail_url: str | None = None
    logs_artifact_url: str | None = None
    raw_command_log_url: str | None = None
    raw_command_log_permitted: bool = False
    run_id: str | None = None
    before_summary: str | None = None
    after_summary: str | None = None
    before_build_id: str | None = None
    after_build_id: str | None = None
    rollback_eligibility: RollbackEligibilityDecision | None = None
    # Controller-backed actions carry the controller's durable operation.
    operation_id: str | None = None
    error_summary: str | None = None
    log_lines: tuple[str, ...] = ()
    retry_permitted: bool = False


@dataclass(frozen=True)
class DeploymentCurrentImage:
    """The current MoonMind image as known from desired-state evidence."""

    requested_image: str | None
    deployed_image: str | None
    repository: str | None
    reference: str | None
    resolved_digest: str | None
    source_run_id: str | None
    updated_at: str | None
    evidence: CurrentImageEvidence


class DeploymentExecutionCreator(Protocol):
    async def create_execution(
        self,
        *,
        workflow_type: str,
        owner_id: UUID | str | None,
        owner_type: str | None = None,
        title: str | None,
        input_artifact_ref: str | None,
        plan_artifact_ref: str | None,
        manifest_artifact_ref: str | None,
        failure_policy: str | None,
        initial_parameters: dict[str, Any] | None,
        idempotency_key: str | None,
        repository: str | None = None,
        integration: str | None = None,
        summary: str | None = None,
    ) -> Any:
        raise NotImplementedError


DEFAULT_DEPLOYMENT_POLICIES: dict[str, DeploymentStackPolicy] = {
    "moonmind": DeploymentStackPolicy(
        stack="moonmind",
        project_name="moonmind",
        repository="ghcr.io/moonladderstudios/moonmind",
        allowed_references=("stable", "latest"),
        recent_tags=("20260425.1234",),
        allow_mutable_tags=True,
        allow_custom_digest=True,
        allowed_modes=("changed_services", "force_recreate"),
        configured_reference="stable",
    )
}


class DeploymentOperationsService:
    """Validate deployment policy and build typed operation responses."""

    def __init__(
        self,
        policies: dict[str, DeploymentStackPolicy] | None = None,
        recent_actions: dict[str, tuple[DeploymentRecentAction, ...]] | None = None,
    ) -> None:
        self._policies = policies or DEFAULT_DEPLOYMENT_POLICIES
        self._recent_actions = recent_actions or {}

    def get_policy(self, stack: str) -> DeploymentStackPolicy:
        normalized = str(stack or "").strip()
        policy = self._policies.get(normalized)
        if policy is None:
            raise DeploymentOperationError(
                "deployment_stack_not_allowed",
                "Deployment stack is not allowlisted.",
            )
        return policy

    def validate_update_request(
        self,
        *,
        stack: str,
        repository: str,
        reference: str,
        mode: str,
        reason: str | None,
        operation_kind: str = "update",
        confirmation: str | None = None,
        rollback_source_action_id: str | None = None,
    ) -> DeploymentStackPolicy:
        policy = self.get_policy(stack)
        if repository != policy.repository:
            raise DeploymentOperationError(
                "deployment_repository_not_allowed",
                "Image repository is not allowlisted for this stack.",
            )
        if not _IMAGE_REFERENCE_PATTERN.fullmatch(str(reference or "").strip()):
            raise DeploymentOperationError(
                "deployment_image_reference_invalid",
                "Image reference is invalid.",
            )
        if mode not in policy.allowed_modes:
            raise DeploymentOperationError(
                "deployment_mode_not_allowed",
                "Deployment update mode is not permitted by policy.",
            )
        normalized_operation = str(operation_kind or "update").strip()
        if normalized_operation not in {"update", "rollback"}:
            raise DeploymentOperationError(
                "deployment_operation_kind_invalid",
                "Deployment operation kind is invalid.",
            )
        if normalized_operation == "rollback":
            if not str(confirmation or "").strip():
                raise DeploymentOperationError(
                    "deployment_confirmation_required",
                    "Rollback confirmation is required.",
                )
            if not str(rollback_source_action_id or "").strip():
                raise DeploymentOperationError(
                    "deployment_rollback_source_required",
                    "Rollback source action is required.",
                )
        return policy

    def recent_actions(self, stack: str) -> tuple[DeploymentRecentAction, ...]:
        """Controller operations (newest first), then injected history."""
        policy = self.get_policy(stack)
        return (
            *controller_recent_actions(policy.stack),
            *self._recent_actions.get(policy.stack, ()),
        )

    def _submit_to_controller(
        self,
        *,
        policy: DeploymentStackPolicy,
        submission: DeploymentUpdateSubmission,
        installation: ControllerInstallation,
    ) -> dict[str, Any] | None:
        """Submit to the installed controller; ``None`` only before cutover.

        An installed controller keeps ownership: unreachable is a distinct
        503 and an unanswered request is a distinct 504, never a fallback.
        Only a bootstrap that never started a controller (no verified image,
        no recorded operation) keeps the transitional workflow path.
        """
        desired_image = f"{submission.repository}:{submission.reference}"
        try:
            status, parsed = controller_request(
                "POST",
                "/v1/operations",
                {
                    "stack": policy.stack,
                    "desiredImage": desired_image,
                    "sourceRevision": "",
                    "reason": submission.reason or "",
                },
            )
        except ControllerUnreachable as exc:
            if installation == "never_started":
                return None
            raise _controller_unavailable_error(exc) from exc
        except ControllerUncertain as exc:
            raise DeploymentOperationError(
                "deployment_controller_uncertain",
                "The standalone controller did not acknowledge the update in "
                "time; it may have accepted it. Refresh to observe the "
                "operation; resubmitting the same target reattaches to it.",
                http_status=504,
            ) from exc
        if status != 202 or not parsed.get("operationId"):
            raise _controller_decision_error(status, parsed)
        return parsed

    async def queue_update(
        self,
        *,
        execution_service: DeploymentExecutionCreator,
        policy: DeploymentStackPolicy,
        submission: DeploymentUpdateSubmission,
    ) -> dict[str, Any]:
        installation = controller_installation()
        if installation != "absent":
            operation = await asyncio.to_thread(
                self._submit_to_controller,
                policy=policy,
                submission=submission,
                installation=installation,
            )
            if operation is not None:
                operation_id = str(operation["operationId"])
                return {
                    "deploymentUpdateRunId": f"ctl-{operation_id}",
                    "owner": "controller",
                    "operationId": operation_id,
                    "desiredImage": str(
                        (operation.get("desired") or {}).get("image") or ""
                    )
                    or None,
                    "status": controller_action_status(str(operation.get("status") or "")),
                    "taskId": None,
                    "workflowId": None,
                }
        # Transitional (MoonLadderStudios/MoonMind#4502): until this
        # deployment installs a controller, the workflow-owned updater stays
        # the one owner, exactly as for the host entrypoint. Removed once
        # bootstrap can install a published controller image by default.
        initial_parameters = self._build_initial_parameters(
            policy=policy,
            submission=submission,
        )
        execution = await execution_service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=submission.requested_by_user_id,
            owner_type="user",
            title=f"Update deployment stack {policy.stack}",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref=None,
            failure_policy="fail_fast",
            initial_parameters=initial_parameters,
            idempotency_key=self._idempotency_key(
                policy=policy,
                submission=submission,
            ),
            repository=None,
            integration=DEPLOYMENT_UPDATE_TOOL_NAME,
            summary=(
                f"Policy-gated deployment update for {policy.stack} to "
                f"{submission.repository}:{submission.reference}."
            ),
        )
        workflow_id = str(getattr(execution, "workflow_id", "") or "").strip()
        run_id = str(getattr(execution, "run_id", "") or "").strip()
        if not workflow_id or not run_id:
            raise DeploymentOperationError(
                "deployment_update_queue_failed",
                "Deployment update workflow was not created.",
            )
        deployment_update_run_id = f"depupd_{run_id.replace('-', '')}"
        return {
            "deploymentUpdateRunId": deployment_update_run_id,
            "owner": "legacy_workflow",
            "operationId": None,
            "desiredImage": f"{submission.repository}:{submission.reference}",
            "taskId": workflow_id,
            "workflowId": workflow_id,
            "status": "QUEUED",
        }

    def _build_initial_parameters(
        self,
        *,
        policy: DeploymentStackPolicy,
        submission: DeploymentUpdateSubmission,
    ) -> dict[str, Any]:
        plan_inputs = {
            "stack": policy.stack,
            "image": {
                "repository": submission.repository,
                "reference": submission.reference,
            },
            "mode": submission.mode,
            "removeOrphans": submission.remove_orphans,
            "wait": submission.wait,
            "runSmokeCheck": submission.run_smoke_check,
            "pauseWork": submission.pause_work,
            "pruneOldImages": submission.prune_old_images,
            "operationKind": submission.operation_kind,
        }
        if submission.reason and submission.reason.strip():
            plan_inputs["reason"] = submission.reason.strip()
        if submission.rollback_source_action_id:
            plan_inputs["rollbackSourceActionId"] = submission.rollback_source_action_id
        if submission.confirmation:
            plan_inputs["confirmation"] = submission.confirmation
        deployment_step = {
            "id": "update-moonmind-deployment",
            "type": "tool",
            "title": "Update MoonMind deployment",
            "instructions": (
                "Run the policy-gated deployment update operation for "
                f"stack '{policy.stack}' using the typed "
                f"{DEPLOYMENT_UPDATE_TOOL_NAME} tool contract."
            ),
            "tool": {
                "type": "skill",
                "name": DEPLOYMENT_UPDATE_TOOL_NAME,
                "id": DEPLOYMENT_UPDATE_TOOL_NAME,
                "version": DEPLOYMENT_UPDATE_TOOL_VERSION,
                "inputs": plan_inputs,
            },
        }
        return {
            "task": {
                "instructions": (
                    "Run the policy-gated deployment update operation for "
                    f"stack '{policy.stack}' using the typed "
                    f"{DEPLOYMENT_UPDATE_TOOL_NAME} tool contract."
                ),
                "operation": {
                    "type": "deployment.update",
                    "source": "api.v1.operations.deployment.update",
                    "jiraIssue": "MM-523",
                    "kind": submission.operation_kind,
                    "rollbackSourceActionId": submission.rollback_source_action_id,
                    "beforeBuildId": submission.before_build_id,
                },
                "steps": [deployment_step],
                # Keep the legacy projection shape until deployment action
                # readers are fully migrated to task.steps.
                "plan": [
                    {
                        "id": deployment_step["id"],
                        "title": deployment_step["title"],
                        "tool": {
                            "type": "skill",
                            "name": DEPLOYMENT_UPDATE_TOOL_NAME,
                            "version": DEPLOYMENT_UPDATE_TOOL_VERSION,
                        },
                        "inputs": plan_inputs,
                    }
                ],
            }
        }

    def _idempotency_key(
        self,
        *,
        policy: DeploymentStackPolicy,
        submission: DeploymentUpdateSubmission,
    ) -> str:
        normalized_reason = str(submission.reason or "").strip()
        explicit_action_key = normalized_reason
        if submission.operation_kind == "rollback" or _is_mutable_reference(
            policy=policy, reference=submission.reference
        ):
            explicit_action_key = uuid4().hex
        return "|".join(
            [
                "deployment-update",
                policy.stack,
                submission.repository,
                submission.reference,
                submission.mode,
                explicit_action_key,
            ]
        )[:128]


def _is_mutable_reference(*, policy: DeploymentStackPolicy, reference: str) -> bool:
    normalized = str(reference or "").strip()
    if not normalized or normalized.startswith("sha256:"):
        return False
    return policy.allow_mutable_tags and normalized in policy.allowed_references


def mutable_references(policy: DeploymentStackPolicy) -> tuple[str, ...]:
    """Return the policy references that resolve mutably over time."""

    if not policy.allow_mutable_tags:
        return ()
    return tuple(
        reference
        for reference in policy.allowed_references
        if _is_mutable_reference(policy=policy, reference=reference)
    )


def _split_image_reference(
    image: str,
) -> tuple[str | None, str | None, str | None]:
    """Split a full image string into (repository, reference, digest)."""

    text = str(image or "").strip()
    if not text:
        return None, None, None
    if "@" in text:
        repository, _, digest = text.partition("@")
        digest = digest.strip() or None
        return repository.strip() or None, None, digest
    repository, separator, tag = text.rpartition(":")
    if separator and "/" not in tag:
        return repository.strip() or None, tag.strip() or None, None
    return text or None, None, None


def _current_image_from_record(
    record: dict[str, Any],
    *,
    policy: DeploymentStackPolicy,
    evidence: CurrentImageEvidence,
) -> DeploymentCurrentImage | None:
    repository = str(record.get("imageRepository") or "").strip() or None
    reference = str(record.get("requestedReference") or "").strip() or None
    digest = str(record.get("resolvedDigest") or "").strip() or None
    if not repository and not reference:
        return None
    repository = repository or policy.repository
    requested_image: str | None = None
    if repository and reference:
        separator = "@" if reference.startswith("sha256:") else ":"
        requested_image = f"{repository}{separator}{reference}"
    deployed_image = f"{repository}@{digest}" if digest else requested_image
    return DeploymentCurrentImage(
        requested_image=requested_image,
        deployed_image=deployed_image,
        repository=repository,
        reference=reference,
        resolved_digest=digest,
        source_run_id=str(record.get("sourceRunId") or "").strip() or None,
        updated_at=str(record.get("createdAt") or "").strip() or None,
        evidence=evidence,
    )


def resolve_current_deployment_image(
    policy: DeploymentStackPolicy,
    *,
    environ: dict[str, str] | None = None,
) -> DeploymentCurrentImage:
    """Resolve the current MoonMind image from desired-state evidence.

    Resolution order, most authoritative first:
    1. Desired-state JSON sidecar written by the update executor.
    2. ``MOONMIND_IMAGE`` / ``MOONMIND_IMAGE_REQUESTED`` environment values.
    3. The policy configured image, reported only as ``policy`` evidence.
    """

    env = environ if environ is not None else dict(os.environ)

    sidecar_path = str(
        env.get("MOONMIND_DEPLOYMENT_DESIRED_STATE_JSON_FILE") or ""
    ).strip()
    if sidecar_path:
        try:
            raw = Path(sidecar_path).expanduser().read_text(encoding="utf-8")
            record = json.loads(raw)
        except (OSError, ValueError, RuntimeError):
            record = None
        if isinstance(record, dict):
            stack = str(record.get("stack") or "").strip()
            if not stack or stack == policy.stack:
                resolved = _current_image_from_record(
                    record, policy=policy, evidence="desired_state"
                )
                if resolved is not None:
                    return resolved

    deployed = str(env.get("MOONMIND_IMAGE") or "").strip()
    requested = str(env.get("MOONMIND_IMAGE_REQUESTED") or "").strip()
    run_id = str(env.get("MOONMIND_DEPLOYMENT_RUN_ID") or "").strip() or None
    if deployed or requested:
        repository, reference, digest = _split_image_reference(
            requested or deployed
        )
        _, _, deployed_digest = _split_image_reference(deployed)
        return DeploymentCurrentImage(
            requested_image=requested or deployed or None,
            deployed_image=deployed or requested or None,
            repository=repository or policy.repository,
            reference=reference,
            resolved_digest=digest or deployed_digest,
            source_run_id=run_id,
            updated_at=None,
            evidence="environment",
        )

    return DeploymentCurrentImage(
        requested_image=policy.configured_image,
        deployed_image=None,
        repository=policy.repository,
        reference=policy.configured_reference,
        resolved_digest=None,
        source_run_id=None,
        updated_at=None,
        evidence="policy",
    )
