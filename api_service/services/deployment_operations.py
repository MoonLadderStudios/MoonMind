"""Policy-gated deployment operation service."""

from __future__ import annotations

import asyncio
import http.client
import json
import os
import re
import socket
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol
from urllib.parse import urlencode, urlsplit
from uuid import UUID, uuid4

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

# Standalone controller adoption (MoonLadderStudios/MoonMind#4500, #4502): the
# UI/API path submits and observes the same controller operation as the host
# entrypoint. The API never supervises the update's lifetime. The legacy
# Temporal workflow is used only while no controller is installed for this
# deployment (no deployment-owned secret), until the shared cutover retires it.
CONTROLLER_DEFAULT_PORT = 8472
CONTROLLER_SUBMIT_TIMEOUT_SECONDS = 30
CONTROLLER_STATUS_TIMEOUT_SECONDS = 10
CONTROLLER_LIST_LIMIT = 10
_CONTROLLER_STATE_DIR = Path("deploy") / "state" / "controller"
CONTROLLER_SOCKET_NAME = "controller.sock"
_OPERATION_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_CONTROLLER_OPEN_STATUSES = ("pending", "staged", "applying")
_CONTROLLER_ACTION_STATUSES = {
    "pending": "QUEUED",
    "staged": "RUNNING",
    "applying": "RUNNING",
    "succeeded": "SUCCEEDED",
    "partially_verified": "PARTIALLY_VERIFIED",
    "failed": "FAILED",
    "superseded": "SUPERSEDED",
}
_HOST_COMMAND_HINT = (
    "Run ./tools/update-moonmind.sh on the deployment host, which starts "
    "or repairs the controller."
)

ControllerAvailability = Literal["not_installed", "available", "unavailable"]


class _ControllerUnreachable(RuntimeError):
    """The controller never received the request (nothing is listening)."""


class _ControllerNoResponse(RuntimeError):
    """The request may have reached the controller; its outcome is unknown."""


def _controller_state_dir() -> Path:
    """Locate the host bootstrap's controller state as this process sees it.

    The API container mounts the checkout's ``deploy/state`` where the
    desired-state sidecar lives (``/workspace/deployment_state``), so the
    controller state is its ``controller`` directory; a host process falls
    back to the checkout-relative ``deploy/state/controller``.
    """
    sidecar = str(
        os.environ.get("MOONMIND_DEPLOYMENT_DESIRED_STATE_JSON_FILE") or ""
    ).strip()
    if sidecar:
        mounted = Path(sidecar).expanduser().parent / "controller"
        if mounted.is_dir():
            return mounted
    return _CONTROLLER_STATE_DIR


def _installed_controller_port(state_dir: Path) -> int:
    """Read the port the host bootstrap recorded for this deployment."""
    try:
        identity = json.loads(
            (state_dir / "controller-identity.json").read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return CONTROLLER_DEFAULT_PORT
    port = identity.get("port") if isinstance(identity, dict) else None
    if isinstance(port, int) and not isinstance(port, bool) and 0 < port < 65536:
        return port
    return CONTROLLER_DEFAULT_PORT


def controller_base_url() -> str:
    """Return the standalone controller endpoint.

    An explicit ``MOONMIND_CONTROLLER_URL`` wins. Otherwise the controller's
    socket in its own state directory (reachable from the API container
    through the mounted deployment state), else its host-loopback port.
    """
    configured = os.environ.get("MOONMIND_CONTROLLER_URL")
    if configured:
        return configured.rstrip("/")
    state_dir = _controller_state_dir()
    socket_path = state_dir / CONTROLLER_SOCKET_NAME
    if socket_path.is_socket():
        return f"unix://{socket_path.resolve()}"
    return f"http://127.0.0.1:{_installed_controller_port(state_dir)}"


def controller_secret() -> str | None:
    """Return the deployment-owned controller bearer secret, if installed.

    A credential that exists but this process cannot read means the
    controller is installed, so it is a distinct refusal rather than
    permission to fall back to the legacy workflow.
    """
    explicit = os.environ.get("MOONMIND_CONTROLLER_SECRET")
    if explicit and explicit.strip():
        return explicit.strip()
    candidates = [
        os.environ.get("MOONMIND_CONTROLLER_SECRET_FILE") or "",
        str(_controller_state_dir() / "secrets" / "controller-bearer"),
    ]
    for candidate in candidates:
        if not candidate:
            continue
        try:
            value = Path(candidate).read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            continue
        except OSError:
            raise DeploymentOperationError(
                "deployment_controller_access_denied",
                "The deployment controller is installed, but this API process "
                f"cannot read its deployment-owned credential ({Path(candidate).name}). "
                + _HOST_COMMAND_HINT,
            ) from None
        if value:
            return value
    return None


class _UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, socket_path: str, *, timeout: float) -> None:
        super().__init__("localhost", timeout=timeout)
        self._socket_path = socket_path

    def connect(self) -> None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        try:
            sock.connect(self._socket_path)
        except BaseException:
            sock.close()
            raise
        self.sock = sock


def _controller_connection(
    timeout: float,
) -> tuple[http.client.HTTPConnection, str]:
    base_url = controller_base_url()
    if base_url.startswith("unix://"):
        return _UnixHTTPConnection(base_url[len("unix://") :], timeout=timeout), ""
    endpoint = urlsplit(base_url)
    connection_class = (
        http.client.HTTPSConnection
        if endpoint.scheme == "https"
        else http.client.HTTPConnection
    )
    connection = connection_class(
        endpoint.hostname or "127.0.0.1", endpoint.port, timeout=timeout
    )
    return connection, endpoint.path.rstrip("/")


def _controller_request(
    *,
    method: str,
    path: str,
    secret: str,
    payload: dict[str, Any] | None = None,
    timeout: float,
) -> tuple[int, dict[str, Any]]:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    try:
        connection, prefix = _controller_connection(timeout)
    except ValueError as exc:
        raise _ControllerUnreachable(str(exc)) from None
    try:
        try:
            connection.connect()
        except OSError as exc:
            # Nothing was sent, so nothing can have been accepted.
            raise _ControllerUnreachable(type(exc).__name__) from None
        try:
            connection.request(
                method,
                f"{prefix}{path}",
                body=body,
                headers={
                    "Authorization": f"Bearer {secret}",
                    "Content-Type": "application/json",
                },
            )
            response = connection.getresponse()
            raw = response.read().decode("utf-8", errors="replace") or "{}"
        except (OSError, http.client.HTTPException) as exc:
            # Timeouts, resets, and truncated replies: the controller may have
            # accepted the request, so the caller must reconcile, not repeat.
            raise _ControllerNoResponse(type(exc).__name__) from None
    finally:
        connection.close()
    try:
        parsed = json.loads(raw)
    except ValueError:
        if 200 <= response.status < 300:
            raise _ControllerNoResponse("unreadable acknowledgment") from None
        parsed = {}
    return response.status, parsed if isinstance(parsed, dict) else {}


def _controller_error(
    status: int, parsed: dict[str, Any], *, not_found_code: str
) -> "DeploymentOperationError":
    """Translate a controller refusal into a distinct, credential-free result."""
    detail = str(parsed.get("error") or "").strip()
    if status == 400:
        return DeploymentOperationError(
            "deployment_controller_rejected",
            f"The deployment controller rejected the request: {detail or 'invalid request'}.",
        )
    if status == 401:
        return DeploymentOperationError(
            "deployment_controller_access_denied",
            "The deployment controller refused this deployment's credentials. "
            + _HOST_COMMAND_HINT,
        )
    if status == 404:
        return DeploymentOperationError(
            not_found_code,
            "The deployment controller has no such operation."
            if not_found_code == "deployment_controller_operation_not_found"
            else "The deployment controller does not support this request.",
        )
    if status == 409:
        return DeploymentOperationError(
            "deployment_controller_conflict",
            f"The deployment controller refused the request: {detail or 'conflict'}. "
            "A different target is new intent; submit it after the current "
            "operation finishes.",
        )
    if status == 500:
        return DeploymentOperationError(
            "deployment_controller_failed",
            "The deployment controller recorded a failed operation; its "
            "original error and logs are shown with the operation.",
        )
    return DeploymentOperationError(
        "deployment_controller_unexpected",
        f"The deployment controller answered HTTP {status}.",
    )


def _unavailable_error() -> "DeploymentOperationError":
    return DeploymentOperationError(
        "deployment_controller_unavailable",
        "The deployment controller is installed but not reachable, so the "
        "dashboard cannot submit or observe updates. " + _HOST_COMMAND_HINT,
    )


def _controller_now() -> str:
    # Same resolution as the controller record so reconciliation can compare.
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def list_controller_operations(
    stack: str, *, secret: str, limit: int = CONTROLLER_LIST_LIMIT
) -> list[dict[str, Any]]:
    """Return the controller's recent operations for a stack, newest first."""
    status, parsed = _controller_request(
        method="GET",
        path=f"/v1/operations?{urlencode({'stack': stack, 'limit': limit})}",
        secret=secret,
        timeout=CONTROLLER_STATUS_TIMEOUT_SECONDS,
    )
    if status != 200:
        raise _controller_error(
            status, parsed, not_found_code="deployment_controller_unexpected"
        )
    operations = parsed.get("operations")
    return [op for op in operations or [] if isinstance(op, dict)]


def _reconcile_submission(
    *, stack: str, desired_image: str, secret: str, since: str
) -> dict[str, Any] | None:
    """Find the operation an unacknowledged submission created or joined."""
    try:
        operations = list_controller_operations(stack, secret=secret)
    except (_ControllerUnreachable, _ControllerNoResponse, DeploymentOperationError):
        return None
    for operation in operations:
        if (operation.get("desired") or {}).get("image") != desired_image:
            continue
        if operation.get("status") in _CONTROLLER_OPEN_STATUSES or str(
            operation.get("updatedAt") or ""
        ) >= since:
            return operation
    return None


def submit_controller_update(
    *,
    stack: str,
    desired_image: str,
    source_revision: str = "",
    reason: str = "",
) -> dict[str, Any] | None:
    """Submit the update to the standalone controller (same as host path).

    Returns ``None`` only when no controller is installed for this
    deployment. Otherwise returns the controller operation or raises a
    distinct :class:`DeploymentOperationError`. A lost acknowledgment is
    reconciled through the controller's own record: it never forks the
    legacy updater or reports a still-running operation as failed.
    """
    secret = controller_secret()
    if not secret:
        return None
    since = _controller_now()
    try:
        status, parsed = _controller_request(
            method="POST",
            path="/v1/operations",
            secret=secret,
            payload={
                "stack": stack,
                "desiredImage": desired_image,
                "sourceRevision": source_revision,
                "reason": reason,
            },
            timeout=CONTROLLER_SUBMIT_TIMEOUT_SECONDS,
        )
    except _ControllerUnreachable:
        raise _unavailable_error() from None
    except _ControllerNoResponse:
        recovered = _reconcile_submission(
            stack=stack, desired_image=desired_image, secret=secret, since=since
        )
        if recovered is not None:
            return recovered
        raise DeploymentOperationError(
            "deployment_controller_outcome_uncertain",
            "The deployment controller did not acknowledge the request and its "
            "operation could not be observed yet. It may still be running; "
            "reload Operations to observe it. Resubmitting the same target "
            "reattaches instead of starting a second update.",
        ) from None
    if status == 202:
        return parsed
    raise _controller_error(
        status, parsed, not_found_code="deployment_controller_unexpected"
    )


def _require_controller_secret() -> str:
    secret = controller_secret()
    if not secret:
        raise DeploymentOperationError(
            "deployment_controller_not_installed",
            "No deployment controller is installed for this deployment. "
            + _HOST_COMMAND_HINT,
        )
    return secret


def _checked_operation_id(operation_id: str) -> str:
    if not _OPERATION_ID_PATTERN.fullmatch(str(operation_id or "")):
        raise DeploymentOperationError(
            "deployment_controller_operation_not_found",
            "The deployment controller has no such operation.",
        )
    return operation_id


def get_controller_operation(operation_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """Read one controller operation and its redacted logs."""
    secret = _require_controller_secret()
    checked = _checked_operation_id(operation_id)
    results = []
    for path in (f"/v1/operations/{checked}", f"/v1/operations/{checked}/logs"):
        try:
            status, parsed = _controller_request(
                method="GET",
                path=path,
                secret=secret,
                timeout=CONTROLLER_STATUS_TIMEOUT_SECONDS,
            )
        except (_ControllerUnreachable, _ControllerNoResponse):
            raise _unavailable_error() from None
        if status != 200:
            raise _controller_error(
                status,
                parsed,
                not_found_code="deployment_controller_operation_not_found",
            )
        results.append(parsed)
    return results[0], results[1]


def retry_controller_operation(operation_id: str) -> dict[str, Any]:
    """Request the controller's fresh bounded attempt for one operation."""
    secret = _require_controller_secret()
    checked = _checked_operation_id(operation_id)
    since = _controller_now()
    try:
        status, parsed = _controller_request(
            method="POST",
            path=f"/v1/operations/{checked}/retry",
            secret=secret,
            timeout=CONTROLLER_SUBMIT_TIMEOUT_SECONDS,
        )
    except _ControllerUnreachable:
        raise _unavailable_error() from None
    except _ControllerNoResponse:
        try:
            operation, _logs = get_controller_operation(checked)
        except DeploymentOperationError:
            operation = {}
        if str(operation.get("updatedAt") or "") >= since:
            return operation
        raise DeploymentOperationError(
            "deployment_controller_outcome_uncertain",
            "The deployment controller did not acknowledge the retry; reload "
            "Operations to observe the operation before retrying again.",
        ) from None
    if status == 202:
        return parsed
    raise _controller_error(
        status, parsed, not_found_code="deployment_controller_operation_not_found"
    )


def controller_action_status(controller_status: str) -> str:
    """Map a controller operation status onto a recent-action status."""
    return _CONTROLLER_ACTION_STATUSES.get(controller_status, "UNKNOWN")


def controller_action(operation: dict[str, Any]) -> "DeploymentRecentAction":
    """Project one durable controller operation onto a recent action."""
    operation_id = str(operation.get("operationId") or "")
    desired = operation.get("desired") or {}
    installed = operation.get("installed") or {}
    controller_status = str(operation.get("status") or "")
    status = controller_action_status(controller_status)
    attempts = [a for a in operation.get("attempts") or [] if isinstance(a, dict)]
    error_summary = str(operation.get("errorSummary") or "").strip() or None
    if controller_status == "superseded":
        error_summary = str(operation.get("supersededReason") or "").strip() or None
    completed_at = str(installed.get("confirmedAt") or "").strip() or None
    if completed_at is None and controller_status not in _CONTROLLER_OPEN_STATUSES:
        completed_at = str(operation.get("updatedAt") or "").strip() or None
    return DeploymentRecentAction(
        id=f"ctl-{operation_id}",
        kind="update",
        status=status,
        requested_image=str(desired.get("image") or "") or None,
        reason=str(desired.get("reason") or "").strip() or None,
        started_at=str(operation.get("createdAt") or "") or None,
        completed_at=completed_at,
        operation_id=operation_id or None,
        owner="controller",
        installed_image=str(installed.get("image") or "") or None,
        original_error=(str(attempts[0].get("error") or "") or None) if attempts else None,
        error_summary=error_summary,
        verification=tuple(
            DeploymentVerificationCheck(
                name=str(check.get("name") or ""),
                status=str(check.get("status") or ""),
                detail=str(check.get("detail") or "") or None,
            )
            for check in operation.get("verification") or []
            if isinstance(check, dict)
        ),
        retryable=status == "FAILED",
    )


def controller_update_result(operation: dict[str, Any]) -> dict[str, Any]:
    """Build the submission response for a controller-owned operation."""
    action = controller_action(operation)
    return {
        "deploymentUpdateRunId": action.id,
        "operationId": action.operation_id,
        "owner": "controller",
        "status": action.status,
        "desiredImage": action.requested_image,
        "installedImage": action.installed_image,
        "taskId": None,
        "workflowId": None,
    }


class DeploymentOperationError(ValueError):
    """Raised when a deployment operation request violates policy."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


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
class DeploymentVerificationCheck:
    name: str
    status: str
    detail: str | None = None


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
    # Controller-owned operations carry their durable identity and outcome;
    # historical workflow-backed rows keep ``owner="workflow"``.
    operation_id: str | None = None
    owner: Literal["controller", "workflow"] = "workflow"
    installed_image: str | None = None
    original_error: str | None = None
    error_summary: str | None = None
    verification: tuple[DeploymentVerificationCheck, ...] = ()
    retryable: bool = False


@dataclass(frozen=True)
class DeploymentStackObservation:
    controller_availability: ControllerAvailability
    recent_actions: tuple[DeploymentRecentAction, ...]


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

    def observe_stack(self, stack: str) -> DeploymentStackObservation:
        """Return controller-observed and stored actions for one stack.

        The controller's durable record is the index of controller-owned
        updates, so a fresh API process (after replacement or a browser
        reload) reconnects to the same operations.
        """
        policy = self.get_policy(stack)
        stored = self._recent_actions.get(policy.stack, ())
        try:
            secret = controller_secret()
        except DeploymentOperationError:
            return DeploymentStackObservation("unavailable", stored)
        if not secret:
            return DeploymentStackObservation("not_installed", stored)
        try:
            operations = list_controller_operations(policy.stack, secret=secret)
        except (_ControllerUnreachable, _ControllerNoResponse, DeploymentOperationError):
            return DeploymentStackObservation("unavailable", stored)
        return DeploymentStackObservation(
            "available",
            (*(controller_action(op) for op in operations), *stored),
        )

    async def queue_update(
        self,
        *,
        execution_service: DeploymentExecutionCreator,
        policy: DeploymentStackPolicy,
        submission: DeploymentUpdateSubmission,
    ) -> dict[str, Any]:
        desired_image = f"{submission.repository}:{submission.reference}"
        controller_operation = await asyncio.to_thread(
            submit_controller_update,
            stack=policy.stack,
            desired_image=desired_image,
            source_revision="",
            reason=submission.reason or "",
        )
        if controller_operation is not None:
            # The UI/API path submits the same controller operation as the
            # host entrypoint: no Temporal workflow is created, so no second
            # updater can own the stack.
            return controller_update_result(controller_operation)
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
            "operationId": None,
            "owner": "workflow",
            "status": "QUEUED",
            "desiredImage": desired_image,
            "installedImage": None,
            "taskId": workflow_id,
            "workflowId": workflow_id,
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
