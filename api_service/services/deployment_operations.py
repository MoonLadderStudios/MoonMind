"""Policy-gated deployment operation service."""

from __future__ import annotations

import asyncio
import json
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol
from uuid import UUID, uuid4

from moonmind.workflows.skills.deployment_controller import (
    ControllerEndpoint,
    ControllerTransportError,
    DeploymentOperationError,
    controller_action_status,
    controller_supports_journal_transition,
    list_controller_operations,
    require_worker_controller_bootstrap,
    resolve_controller_endpoint,
    retry_controller_operation,
    submit_controller_update,
    unavailable_controller_error,
)
from moonmind.workflows.skills.deployment_tools import (
    DEPLOYMENT_UPDATE_TOOL_NAME,
    DEPLOYMENT_UPDATE_TOOL_VERSION,
)

CurrentImageEvidence = Literal[
    "controller", "desired_state", "environment", "policy", "unavailable"
]


_IMAGE_REFERENCE_PATTERN = re.compile(
    r"^(?:[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}|sha256:[A-Fa-f0-9]{64})$"
)

# Settings Operations adopts the standalone deployment controller
# (MoonLadderStudios/MoonMind#4502). Once a deployment has installed it, the
# controller is the only update owner (see ``deployment_controller``). Until a
# deployment installs a working controller, the transitional legacy workflow
# (``_queue_legacy_workflow``) still serves updates, which is the same rule
# the host entrypoint applies. Remove it once the default install provides
# the controller.
_MAX_CONTROLLER_TEXT_CHARS = 2000
_MAX_CONTROLLER_ATTEMPTS_SHOWN = 10


def _bounded_text(value: object) -> str | None:
    text = str(value or "").strip()
    return text[:_MAX_CONTROLLER_TEXT_CHARS] if text else None


def recent_action_from_controller(operation: dict[str, Any]) -> "DeploymentRecentAction":
    """Project one controller record onto the Operations action shape."""
    operation_id = str(operation.get("operationId") or "")
    status_text = str(operation.get("status") or "")
    status = controller_action_status(status_text)
    desired = operation.get("desired") if isinstance(operation.get("desired"), dict) else {}
    installed = operation.get("installed") if isinstance(operation.get("installed"), dict) else {}
    attempts = tuple(
        DeploymentAttempt(
            attempt=int(item.get("attempt") or 0),
            error=_bounded_text(item.get("error")) or "",
            at=_bounded_text(item.get("at")),
        )
        for item in list(operation.get("attempts") or [])[-_MAX_CONTROLLER_ATTEMPTS_SHOWN:]
        if isinstance(item, dict)
    )
    verification = tuple(
        DeploymentVerificationCheck(
            name=_bounded_text(item.get("name")) or "check",
            status=_bounded_text(item.get("status")) or "unknown",
            detail=_bounded_text(item.get("detail")),
        )
        for item in list(operation.get("verification") or [])
        if isinstance(item, dict)
    )
    terminal = status in ("SUCCEEDED", "PARTIALLY_VERIFIED", "FAILED", "SUPERSEDED")
    return DeploymentRecentAction(
        id=f"ctl-{operation_id}",
        kind="update",
        status=status,
        requested_image=_bounded_text(desired.get("image")),
        installed_image=_bounded_text(installed.get("image")),
        reason=_bounded_text(desired.get("reason")),
        started_at=_bounded_text(operation.get("createdAt")),
        completed_at=(
            _bounded_text(installed.get("confirmedAt") or operation.get("updatedAt"))
            if terminal
            else None
        ),
        run_detail_url=None,
        run_id=None,
        owner="controller",
        operation_id=operation_id or None,
        error_summary=_bounded_text(
            operation.get("errorSummary") or operation.get("supersededReason")
        ),
        attempts=attempts,
        attempt_group=int(operation.get("attemptGroup") or 1),
        verification=verification,
        retry_allowed=status == "FAILED",
    )


@dataclass(frozen=True)
class DeploymentControllerObservation:
    installed: bool
    reachable: bool
    message: str | None
    actions: tuple["DeploymentRecentAction", ...] = ()


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


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
class DeploymentAttempt:
    attempt: int
    error: str
    at: str | None = None


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
    # ``workflow`` rows are read-only history of the transitional updater;
    # ``controller`` rows mirror the controller's durable operation record.
    owner: Literal["controller", "workflow"] = "workflow"
    operation_id: str | None = None
    installed_image: str | None = None
    error_summary: str | None = None
    attempts: tuple[DeploymentAttempt, ...] = ()
    attempt_group: int | None = None
    verification: tuple[DeploymentVerificationCheck, ...] = ()
    retry_allowed: bool = False


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
        environ: dict[str, str] | None = None,
    ) -> None:
        self._policies = policies or DEFAULT_DEPLOYMENT_POLICIES
        self._recent_actions = recent_actions or {}
        self._environ = environ

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
        return tuple(self._recent_actions.get(policy.stack, ()))

    def controller_endpoint(self) -> ControllerEndpoint | None:
        return resolve_controller_endpoint(self._environ)

    def observe_controller(self, stack: str) -> DeploymentControllerObservation:
        """Read recent controller operations with one bounded request."""
        policy = self.get_policy(stack)
        endpoint = self.controller_endpoint()
        if endpoint is None:
            return DeploymentControllerObservation(
                installed=False,
                reachable=False,
                message=(
                    "The standalone deployment controller is not installed; "
                    "updates use the transitional workflow updater."
                ),
            )
        try:
            operations = list_controller_operations(endpoint, stack=policy.stack)
        except ControllerTransportError as exc:
            return DeploymentControllerObservation(
                installed=True,
                reachable=False,
                message=unavailable_controller_error(str(exc)).message,
            )
        except DeploymentOperationError as exc:
            return DeploymentControllerObservation(
                installed=True, reachable=False, message=exc.message
            )
        return DeploymentControllerObservation(
            installed=True,
            reachable=True,
            message=None,
            actions=tuple(recent_action_from_controller(op) for op in operations),
        )

    async def retry_operation(self, operation_id: str) -> dict[str, Any]:
        """Request the controller's explicit fresh bounded attempt."""
        endpoint = self.controller_endpoint()
        if endpoint is None:
            raise DeploymentOperationError(
                "deployment_controller_not_installed",
                "The standalone deployment controller is not installed.",
                status_code=409,
            )
        operation = await asyncio.to_thread(
            retry_controller_operation, endpoint, operation_id
        )
        return _controller_submission_response(operation)

    async def queue_update(
        self,
        *,
        execution_service: DeploymentExecutionCreator,
        policy: DeploymentStackPolicy,
        submission: DeploymentUpdateSubmission,
    ) -> dict[str, Any]:
        endpoint = self.controller_endpoint()
        if endpoint is None:
            return await self._queue_legacy_workflow(
                execution_service=execution_service,
                policy=policy,
                submission=submission,
            )
        operation_id = f"ui-{uuid4().hex}"
        if not await asyncio.to_thread(
            controller_supports_journal_transition,
            endpoint,
            operation_id=operation_id,
        ):
            await asyncio.to_thread(require_worker_controller_bootstrap, self._environ)
            # The API has read-only controller state. Its existing privileged
            # submitter performs host-owned bootstrap before submitting to the
            # controller; the API gains no new filesystem or Docker authority.
            return await self._queue_legacy_workflow(
                execution_service=execution_service,
                policy=policy,
                submission=submission,
            )
        separator = "@" if submission.reference.startswith("sha256:") else ":"
        operation = await asyncio.to_thread(
            submit_controller_update,
            endpoint,
            operation_id=operation_id,
            stack=policy.stack,
            desired_image=f"{submission.repository}{separator}{submission.reference}",
            reason=submission.reason or "",
        )
        return _controller_submission_response(operation)

    async def _queue_legacy_workflow(
        self,
        *,
        execution_service: DeploymentExecutionCreator,
        policy: DeploymentStackPolicy,
        submission: DeploymentUpdateSubmission,
    ) -> dict[str, Any]:
        """Transitional updater for deployments without an installed controller."""
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
            "owner": "workflow",
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


def _controller_submission_response(operation: dict[str, Any]) -> dict[str, Any]:
    operation_id = str(operation.get("operationId") or "")
    return {
        "deploymentUpdateRunId": f"ctl-{operation_id}",
        "taskId": None,
        "workflowId": None,
        "operationId": operation_id,
        "owner": "controller",
        "status": controller_action_status(str(operation.get("status") or "")),
    }


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


def current_image_from_controller(
    actions: tuple[DeploymentRecentAction, ...],
    policy: DeploymentStackPolicy,
) -> DeploymentCurrentImage | None:
    """Return the newest installation the controller confirmed, if any.

    Once a deployment installs the controller it is the only update owner,
    and it records ``installed`` only for a confirmed apply (append-only), so
    a newer failed or superseded request never replaces it. Ordered by
    confirmation time because an explicit Retry can confirm an older
    operation last.
    """

    confirmed = [
        action
        for action in actions
        if action.owner == "controller" and action.installed_image
    ]
    if not confirmed:
        return None
    newest = max(confirmed, key=lambda action: action.completed_at or "")
    installed = str(newest.installed_image)
    repository, reference, digest = _split_image_reference(installed)
    return DeploymentCurrentImage(
        requested_image=newest.requested_image or installed,
        deployed_image=installed,
        repository=repository or policy.repository,
        reference=reference,
        resolved_digest=digest,
        source_run_id=newest.operation_id,
        updated_at=newest.completed_at,
        evidence="controller",
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
