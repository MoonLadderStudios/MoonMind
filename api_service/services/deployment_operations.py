"""Policy-gated deployment operation service."""

from __future__ import annotations

import asyncio
import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol
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

# Standalone controller adoption (MoonLadderStudios/MoonMind#4502): the
# Settings Operations API submits and observes the same ``deploy/controller``
# operation as the host entrypoint. Once a controller secret is configured,
# the controller is the only mutation owner: an unreachable controller or a
# lost acknowledgment is reported as such and never forks the legacy
# Temporal updater. The legacy workflow remains only while no controller is
# installed (no deployment-owned secret, or a bootstrap that never started a
# controller), per the #4500 bootstrap cutover.
CONTROLLER_DEFAULT_URL = "http://127.0.0.1:8472"
CONTROLLER_DEFAULT_HOST = "127.0.0.1"
_CONTROLLER_SECRET_RELATIVE_PATH = Path("secrets") / "controller-bearer"
_CONTROLLER_IDENTITY_FILE = "controller-identity.json"
CONTROLLER_SUBMIT_TIMEOUT_SECONDS: float = 30
CONTROLLER_STATUS_TIMEOUT_SECONDS: float = 10

_CONTROLLER_OPERATION_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")

# Controller record status -> recent-action status. Unknown statuses stay
# UNKNOWN instead of being reported as queued or failed.
_CONTROLLER_ACTION_STATUSES = {
    "pending": "QUEUED",
    "staged": "RUNNING",
    "applying": "RUNNING",
    "succeeded": "SUCCEEDED",
    "partially_verified": "PARTIALLY_VERIFIED",
    "failed": "FAILED",
    "superseded": "SUPERSEDED",
}
_CONTROLLER_TERMINAL_STATUSES = frozenset(
    {"succeeded", "partially_verified", "failed", "superseded"}
)

# Controller HTTP answers -> distinct, credential-free API outcomes.
_CONTROLLER_HTTP_ERRORS = {
    400: ("deployment_controller_rejected", "The controller rejected the request"),
    401: (
        "deployment_controller_access_denied",
        "The controller refused the deployment-owned credential",
    ),
    403: (
        "deployment_controller_access_denied",
        "The controller refused the deployment-owned credential",
    ),
    404: (
        "deployment_controller_not_found",
        "The controller has no such operation or route",
    ),
    409: (
        "deployment_controller_conflict",
        "The controller refused the request in the operation's current state",
    ),
}

CONTROLLER_ACTION_ID_PREFIX = "ctl_"
CONTROLLER_OPERATIONS_ROUTE = "/api/v1/operations/deployment/controller-operations"


def _installed_controller_state() -> Path | None:
    """The state directory a host bootstrap installed a controller into.

    Inside the stack, Compose points ``MOONMIND_CONTROLLER_STATE_DIR`` at the
    controller's directory within the mounted ``./deploy/state``; a
    checkout-local process finds bootstrap's default ``deploy/state/controller``.
    """
    for candidate in _controller_state_candidates():
        try:
            if (candidate / _CONTROLLER_SECRET_RELATIVE_PATH).is_file():
                return candidate
        except OSError:
            continue
    return None


def _controller_selected_explicitly() -> bool:
    return any(
        (os.environ.get(name) or "").strip()
        for name in (
            "MOONMIND_CONTROLLER_URL",
            "MOONMIND_CONTROLLER_SECRET",
            "MOONMIND_CONTROLLER_SECRET_FILE",
        )
    )


def _installed_controller_port(state_dir: Path) -> int | None:
    try:
        identity = json.loads(
            (state_dir / _CONTROLLER_IDENTITY_FILE).read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return None
    port = identity.get("port") if isinstance(identity, dict) else None
    if isinstance(port, int) and not isinstance(port, bool) and 0 < port < 65536:
        return port
    return None


def controller_base_url(explicit: str | None = None) -> str:
    """Return the standalone controller endpoint.

    An explicit URL wins. Otherwise the port comes from the installed
    controller's recorded identity and the host from
    ``MOONMIND_CONTROLLER_HOST`` (the stack network alias inside Compose,
    host loopback elsewhere).
    """
    selected = explicit or os.environ.get("MOONMIND_CONTROLLER_URL")
    if selected:
        return selected.rstrip("/")
    state_dir = _installed_controller_state()
    port = _installed_controller_port(state_dir) if state_dir else None
    if port is None:
        return CONTROLLER_DEFAULT_URL
    host = (os.environ.get("MOONMIND_CONTROLLER_HOST") or "").strip()
    return f"http://{host or CONTROLLER_DEFAULT_HOST}:{port}"


def controller_secret() -> str | None:
    """Return the deployment-owned controller bearer secret, if configured."""
    explicit = os.environ.get("MOONMIND_CONTROLLER_SECRET")
    if explicit and explicit.strip():
        return explicit.strip()
    secret_file = os.environ.get("MOONMIND_CONTROLLER_SECRET_FILE")
    if secret_file:
        candidate = Path(secret_file)
    else:
        state_dir = _installed_controller_state()
        if state_dir is None:
            return None
        candidate = state_dir / _CONTROLLER_SECRET_RELATIVE_PATH
    try:
        value = candidate.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return value or None


def _bootstrap_never_started_controller() -> bool:
    """Whether a discovered controller has provably never owned any work.

    Mirrors the host entrypoint's transitional rule: bootstrap may leave a
    secret before its controller can start (for example an unpublished
    image). An unreachable controller that recorded no operation owns no
    stack, so the application-owned updater remains the only writer. An
    explicitly selected controller, or one with recorded work, keeps its
    authority.
    """
    if _controller_selected_explicitly():
        return False
    state_dir = _installed_controller_state()
    if state_dir is None:
        return False
    operations_dir = state_dir / "operations"
    try:
        return not (operations_dir.is_dir() and any(operations_dir.iterdir()))
    except OSError:
        return False


def check_controller_operation_id(operation_id: str) -> str:
    """Validate an operation id before it reaches a controller URL."""
    normalized = str(operation_id or "").strip()
    if not _CONTROLLER_OPERATION_ID_PATTERN.fullmatch(normalized):
        raise DeploymentOperationError(
            "deployment_operation_id_invalid",
            "Deployment operation id is invalid.",
        )
    return normalized


def _controller_request(
    *,
    method: str,
    path: str,
    secret: str,
    payload: dict[str, Any] | None = None,
    timeout: float,
) -> dict[str, Any]:
    """Call the controller and translate its answer into typed outcomes.

    A refused or unresolvable connection means the request never reached a
    writer (``deployment_controller_unavailable``). A timeout or dropped
    connection after sending is a lost acknowledgment: the controller may
    own the operation, so the caller must observe or resubmit the same
    target (which reattaches) rather than start another updater.
    """
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        f"{controller_base_url()}{path}",
        data=body,
        method=method,
        headers={
            "Authorization": f"Bearer {secret}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8") or "{}"
    except urllib.error.HTTPError as exc:
        try:
            parsed = json.loads(exc.read().decode("utf-8", errors="replace") or "{}")
        except (OSError, ValueError):
            parsed = {}
        answered = str(parsed.get("error") or "") if isinstance(parsed, dict) else ""
        code, message = _CONTROLLER_HTTP_ERRORS.get(
            exc.code,
            (
                "deployment_controller_failed",
                "The controller could not complete the request",
            ),
        )
        if exc.code == 400 and answered:
            message = f"{message}: {answered}"
        raise DeploymentOperationError(
            code, f"{message} (HTTP {exc.code})."
        ) from None
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, TimeoutError):
            raise _controller_ack_lost() from None
        raise DeploymentOperationError(
            "deployment_controller_unavailable",
            "The standalone deployment controller is unreachable; use the "
            "host update command or restore the controller with "
            "`python3 deploy/controller/bootstrap.py restore`.",
        ) from None
    except (TimeoutError, OSError):
        raise _controller_ack_lost() from None
    try:
        parsed = json.loads(raw)
    except ValueError:
        raise _controller_ack_lost() from None
    if not isinstance(parsed, dict):
        raise _controller_ack_lost()
    return parsed


def _controller_ack_lost() -> DeploymentOperationError:
    return DeploymentOperationError(
        "deployment_controller_ack_lost",
        "The controller did not acknowledge the request in time; it may "
        "still own the operation. Recent actions show its progress, and "
        "resubmitting the same target reattaches instead of starting "
        "another update.",
    )


def submit_controller_update(
    *,
    stack: str,
    desired_image: str,
    source_revision: str = "",
    reason: str = "",
) -> dict[str, Any] | None:
    """Submit the update to the standalone controller (same as host path).

    Returns the controller operation, or ``None`` only when no controller
    is installed (no deployment-owned secret, or an unreachable bootstrap
    controller that never recorded work). Every other outcome is the
    controller's: failures raise :class:`DeploymentOperationError` with a
    distinct code and never permit a competing legacy writer.
    """
    secret = controller_secret()
    if not secret:
        return None
    try:
        return _controller_request(
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
    except DeploymentOperationError as exc:
        if (
            exc.code == "deployment_controller_unavailable"
            and _bootstrap_never_started_controller()
        ):
            return None
        raise


def _require_controller_secret() -> str:
    secret = controller_secret()
    if not secret:
        raise DeploymentOperationError(
            "deployment_controller_not_installed",
            "The standalone deployment controller is not installed.",
        )
    return secret


def retry_controller_operation(operation_id: str) -> dict[str, Any]:
    """Request the controller's fresh bounded attempt for one operation."""
    checked = check_controller_operation_id(operation_id)
    return _controller_request(
        method="POST",
        path=f"/v1/operations/{checked}/retry",
        secret=_require_controller_secret(),
        timeout=CONTROLLER_SUBMIT_TIMEOUT_SECONDS,
    )


def controller_operation_logs(operation_id: str) -> dict[str, Any]:
    """Return the controller's redacted attempt/verification log record."""
    checked = check_controller_operation_id(operation_id)
    return _controller_request(
        method="GET",
        path=f"/v1/operations/{checked}/logs",
        secret=_require_controller_secret(),
        timeout=CONTROLLER_STATUS_TIMEOUT_SECONDS,
    )


def observe_controller_operation(*, operation_id: str) -> dict[str, Any] | None:
    """Observe one controller record; ``None`` when it cannot be read.

    An unreadable observer is not evidence of failure: callers keep the
    last known state instead of marking the operation failed.
    """
    secret = controller_secret()
    if not secret or not operation_id:
        return None
    try:
        return _controller_request(
            method="GET",
            path=f"/v1/operations/{check_controller_operation_id(operation_id)}",
            secret=secret,
            timeout=CONTROLLER_STATUS_TIMEOUT_SECONDS,
        )
    except DeploymentOperationError:
        return None


def controller_action_status(controller_status: str) -> str:
    """Map a controller operation status onto a recent-action status."""
    return _CONTROLLER_ACTION_STATUSES.get(str(controller_status or ""), "UNKNOWN")


def controller_action(
    operation: dict[str, Any], *, operator: str | None = None
) -> DeploymentRecentAction:
    """Project one controller record onto the Operations recent-action shape.

    Requested and installed images, the original error, verification gaps,
    and reporting failures come from the controller's own record. No
    workflow detail or artifact reference is manufactured for it.
    """
    operation_id = str(operation.get("operationId") or "")
    controller_status = str(operation.get("status") or "")
    desired = operation.get("desired") if isinstance(operation.get("desired"), dict) else {}
    installed = (
        operation.get("installed") if isinstance(operation.get("installed"), dict) else {}
    )
    verification = tuple(
        DeploymentVerificationCheck(
            name=str(check.get("name") or ""),
            status=str(check.get("status") or ""),
            detail=str(check.get("detail") or "") or None,
        )
        for check in operation.get("verification") or ()
        if isinstance(check, dict)
    )
    completed_at = str(installed.get("confirmedAt") or "") or None
    if completed_at is None and controller_status in _CONTROLLER_TERMINAL_STATUSES:
        completed_at = str(operation.get("updatedAt") or "") or None
    return DeploymentRecentAction(
        id=f"ctl-{operation_id}",
        kind="update",
        status=controller_action_status(controller_status),
        requested_image=str(desired.get("image") or "") or None,
        operator=operator,
        reason=str(desired.get("reason") or "") or None,
        started_at=str(operation.get("createdAt") or "") or None,
        completed_at=completed_at,
        run_id=f"{CONTROLLER_ACTION_ID_PREFIX}{operation_id}",
        operation_id=operation_id or None,
        controller_status=controller_status or None,
        installed_image=str(installed.get("image") or "") or None,
        error_summary=str(operation.get("errorSummary") or "") or None,
        verification=verification,
        reporting_failures=tuple(
            str(item) for item in operation.get("reportingFailures") or ()
        ),
        retryable=controller_status == "failed",
        logs_url=(
            f"{CONTROLLER_OPERATIONS_ROUTE}/{operation_id}/logs"
            if operation_id
            else None
        ),
    )


def _controller_state_candidates() -> list[Path]:
    """Locate the standalone controller's durable record, if co-located."""
    candidates = []
    explicit = os.environ.get("MOONMIND_CONTROLLER_STATE_DIR")
    if explicit:
        candidates.append(Path(explicit))
    candidates.append(Path.cwd() / "deploy" / "state" / "controller")
    candidates.append(Path("/var/lib/moonmind-controller"))
    return candidates


def controller_recent_actions(
    stack: str, *, limit: int = 10
) -> tuple["DeploymentRecentAction", ...]:
    """Observe controller-backed updates from the durable operation record.

    The API service is constructed per request, so in-memory submissions do
    not survive across requests. The controller's own crash-safe record is
    the durable index: when co-located, recent open and terminal operations
    for the stack surface here as recent actions with live statuses.
    """
    operations_dir = None
    for candidate in _controller_state_candidates():
        if candidate.is_dir() and (candidate / "operations").is_dir():
            operations_dir = candidate / "operations"
            break
    if operations_dir is None:
        return ()
    records = []
    for path in sorted(operations_dir.glob("*.json"))[-200:]:
        try:
            parsed = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(parsed, dict) or parsed.get("stack") != stack:
            continue
        records.append(parsed)
    records.sort(key=lambda op: str(op.get("updatedAt") or ""))
    return tuple(reversed([controller_action(parsed) for parsed in records[-limit:]]))


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
    operation_id: str | None = None
    controller_status: str | None = None
    installed_image: str | None = None
    error_summary: str | None = None
    verification: tuple[DeploymentVerificationCheck, ...] = ()
    reporting_failures: tuple[str, ...] = ()
    retryable: bool = False
    logs_url: str | None = None


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
        policy = self.get_policy(stack)
        stored = self._recent_actions.get(policy.stack, ())
        refreshed: list[DeploymentRecentAction] = []
        for action in stored:
            observed = (
                observe_controller_operation(operation_id=action.operation_id)
                if action.operation_id
                else None
            )
            # An unreadable controller keeps the last known state; it is
            # never evidence that a still-running operation failed.
            refreshed.append(
                controller_action(observed, operator=action.operator)
                if observed is not None
                else action
            )
        self._recent_actions[policy.stack] = tuple(refreshed)
        # Durable controller-backed updates survive per-request service
        # instances through the controller's own record; merge them with
        # same-process submissions, newest first, without duplicates.
        durable = controller_recent_actions(policy.stack)
        seen = {str(action.run_id) for action in refreshed if action.run_id}
        merged = list(refreshed)
        for action in durable:
            if action.run_id in seen:
                continue
            seen.add(action.run_id)
            merged.append(action)
        return tuple(merged)

    def _record_controller_action(
        self,
        *,
        policy: DeploymentStackPolicy,
        operation: dict[str, Any],
        operator: str | None,
    ) -> dict[str, str | None]:
        action = controller_action(operation, operator=operator)
        self._recent_actions[policy.stack] = (
            action,
            *(
                existing
                for existing in self._recent_actions.get(policy.stack, ())
                if existing.run_id != action.run_id
            ),
        )[:50]
        return {
            "deploymentUpdateRunId": action.id,
            # A local controller operation has no workflow or task identity.
            "taskId": None,
            "workflowId": None,
            "operationId": action.operation_id,
            "status": action.status,
        }

    async def retry_controller_update(
        self, *, stack: str, operation_id: str, operator: str | None = None
    ) -> dict[str, str | None]:
        """Request the controller's fresh bounded attempt for an operation.

        The controller retains prior attempts and the original error; the
        retry is never a resubmission through another updater.
        """
        policy = self.get_policy(stack)
        operation = await asyncio.to_thread(retry_controller_operation, operation_id)
        return self._record_controller_action(
            policy=policy, operation=operation, operator=operator
        )

    async def queue_update(
        self,
        *,
        execution_service: DeploymentExecutionCreator,
        policy: DeploymentStackPolicy,
        submission: DeploymentUpdateSubmission,
    ) -> dict[str, str | None]:
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
            # updater can own the stack. Status is observed from the
            # controller record (see recent_actions).
            return self._record_controller_action(
                policy=policy,
                operation=controller_operation,
                operator=(
                    str(submission.requested_by_user_id)
                    if submission.requested_by_user_id is not None
                    else None
                ),
            )
        # Transitional: no standalone controller is installed yet, so the
        # application-owned deployment workflow remains the update owner.
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
            "taskId": workflow_id,
            "workflowId": workflow_id,
            "operationId": None,
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
