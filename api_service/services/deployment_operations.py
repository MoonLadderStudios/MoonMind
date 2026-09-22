"""Policy-gated deployment operation service.

Submission goes directly to the shared deployment-controller operation
(MoonMind#4502), never through ``MoonMind.UserWorkflow``. The controller owns
the durable operation identity; duplicate submission and lost acknowledgment
reattach to the same operation, and an explicit retry starts a fresh bounded
attempt. Historical workflow-backed actions remain readable as history through
the legacy readers in the router, not as an executable fallback.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol
from uuid import UUID

from api_service.services.deployment_controller import (
    ControllerOperation,
    DeploymentControllerClient,
)

CurrentImageEvidence = Literal[
    "desired_state", "environment", "policy", "unavailable"
]


_IMAGE_REFERENCE_PATTERN = re.compile(
    r"^(?:[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}|sha256:[A-Fa-f0-9]{64})$"
)


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
    # Controller-operation identity (MoonMind#4502). ``operation_id`` is the
    # durable controller operation; ``original_error`` preserves the first
    # failure across bounded retries; ``log_excerpt`` carries redacted,
    # bounded controller logs for the existing Operations UI.
    operation_id: str | None = None
    original_error: str | None = None
    verification_pending: bool = False
    log_excerpt: str | None = None


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


class DeploymentControllerSubmitter(Protocol):
    """Narrow controller-submission surface used by the Operations service."""

    def submit(
        self,
        request: dict[str, Any],
        *,
        operator: str | None,
        credential: str | None = ...,
    ) -> ControllerOperation:
        raise NotImplementedError

    def retry(
        self,
        operation_id: str,
        *,
        operator: str | None,
        credential: str | None = ...,
    ) -> ControllerOperation:
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
        return self._recent_actions.get(policy.stack, ())

    def submit_update(
        self,
        *,
        controller: DeploymentControllerSubmitter | DeploymentControllerClient,
        policy: DeploymentStackPolicy,
        submission: DeploymentUpdateSubmission,
        operator_credential: str | None = None,
    ) -> dict[str, str]:
        """Submit directly to the shared controller operation.

        No ``MoonMind.UserWorkflow`` or application artifact is created. The
        returned identifiers are the controller's durable operation identity:
        ``workflowId`` carries that identity for clients migrating off the
        retired workflow route and must not be treated as a Temporal workflow.
        """

        operation = controller.submit(
            {
                "stack": policy.stack,
                "repository": submission.repository,
                "reference": submission.reference,
                "mode": submission.mode,
                "operation_kind": submission.operation_kind,
                "reason": submission.reason or "",
            },
            operator=(
                str(submission.requested_by_user_id)
                if submission.requested_by_user_id is not None
                else None
            ),
            credential=operator_credential,
        )
        return {
            "deploymentUpdateRunId": operation.operation_id,
            "taskId": operation.operation_id,
            "workflowId": operation.operation_id,
            "operationId": operation.operation_id,
            "status": operation.status,
        }

    def retry_update(
        self,
        *,
        controller: DeploymentControllerSubmitter | DeploymentControllerClient,
        operation_id: str,
        operator: UUID | str | None,
        operator_credential: str | None = None,
    ) -> dict[str, str]:
        """Request the controller's fresh bounded attempt for a terminal update.

        The first failure is preserved on both the original and the retried
        operation; a still-running operation reattaches instead of duplicating
        mutation.
        """

        operation = controller.retry(
            operation_id,
            operator=str(operator) if operator is not None else None,
            credential=operator_credential,
        )
        return {
            "deploymentUpdateRunId": operation.operation_id,
            "taskId": operation.operation_id,
            "workflowId": operation.operation_id,
            "operationId": operation.operation_id,
            "status": operation.status,
        }

    def controller_recent_actions(
        self,
        stack: str,
        *,
        controller: DeploymentControllerClient,
    ) -> tuple[DeploymentRecentAction, ...]:
        """Read controller operations for a stack, newest first."""

        policy = self.get_policy(stack)
        return tuple(
            recent_action_from_controller_operation(operation, policy=policy)
            for operation in controller.list_stack_operations(policy.stack)
        )


TERMINAL_STATES = frozenset({"SUCCEEDED", "FAILED", "PARTIALLY_VERIFIED"})


def recent_action_from_controller_operation(
    operation: ControllerOperation,
    *,
    policy: DeploymentStackPolicy,
) -> DeploymentRecentAction:
    """Project a controller operation onto the Operations read surface.

    The controller's durable operation ID, selected target, status, original
    failure, pending verification, and redacted logs are shown directly.
    Installed versus requested state comes from the desired-state evidence
    readers; accepted versus completed is the operation status. No workflow ID
    or artifact reference is manufactured for a local operation.
    """

    status = (operation.status or "QUEUED").upper()
    if operation.operation_kind == "rollback":
        kind = "rollback"
    elif status in {"FAILED", "TERMINATED", "CANCELED", "CANCELLED", "FAILURE"}:
        kind = "failure"
    else:
        kind = "update"
    completed_at = operation.updated_at if status in TERMINAL_STATES else None
    logs = (operation.logs_text or "").strip()
    return DeploymentRecentAction(
        id=operation.operation_id,
        kind=kind,
        status=status,
        requested_image=operation.requested_image,
        resolved_digest=operation.resolved_digest,
        operator=operation.operator,
        reason=operation.reason,
        started_at=operation.created_at,
        completed_at=completed_at,
        run_detail_url=None,
        logs_artifact_url=None,
        raw_command_log_url=None,
        raw_command_log_permitted=False,
        run_id=None,
        before_summary=None,
        after_summary=operation.error,
        before_build_id=None,
        after_build_id=None,
        rollback_eligibility=RollbackEligibilityDecision(
            eligible=False,
            target_image=None,
            source_action_id=operation.operation_id,
            reason="Controller operations carry no workflow before-state.",
            evidence_ref=None,
        ),
        operation_id=operation.operation_id,
        original_error=operation.first_error or operation.error,
        verification_pending=operation.verification_pending,
        log_excerpt=logs[-1000:] if logs else None,
    )


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
