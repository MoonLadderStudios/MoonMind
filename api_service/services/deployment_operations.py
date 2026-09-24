"""Policy-gated deployment operation service."""

from __future__ import annotations

import asyncio
import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
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

# Standalone controller cutover (issue #4500): the UI/API path submits and
# observes the same controller operation as the host entrypoint instead of
# depending on the API, Temporal, and the legacy deployment worker. The
# legacy Temporal workflow remains the fallback only while no controller
# writer owns the operation.
CONTROLLER_DEFAULT_URL = "http://127.0.0.1:8472"
CONTROLLER_SUBMIT_TIMEOUT_SECONDS = 30
CONTROLLER_STATUS_TIMEOUT_SECONDS = 10

_CONTROLLER_OPEN_STATUSES = ("pending", "staged", "applying")


def controller_base_url(explicit: str | None = None) -> str:
    """Return the standalone controller endpoint (loopback only by default)."""
    return (
        explicit
        or os.environ.get("MOONMIND_CONTROLLER_URL")
        or CONTROLLER_DEFAULT_URL
    ).rstrip("/")


def controller_secret() -> str | None:
    """Return the deployment-owned controller bearer secret, if configured."""
    explicit = os.environ.get("MOONMIND_CONTROLLER_SECRET")
    if explicit and explicit.strip():
        return explicit.strip()
    candidates = [
        os.environ.get("MOONMIND_CONTROLLER_SECRET_FILE") or "",
        "deploy/state/controller/secrets/controller-bearer",
    ]
    for candidate in candidates:
        if not candidate:
            continue
        try:
            value = Path(candidate).read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if value:
            return value
    return None


def _controller_request(
    *,
    method: str,
    url: str,
    secret: str,
    payload: dict[str, Any] | None = None,
    timeout: int,
) -> tuple[int, dict[str, Any]]:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        url,
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
            return response.status, json.loads(raw)
    except urllib.error.HTTPError as exc:
        try:
            detail = exc.read().decode("utf-8", errors="replace") or "{}"
            parsed = json.loads(detail)
        except (OSError, ValueError):
            parsed = {"error": "controller refused the request"}
        return exc.code, parsed if isinstance(parsed, dict) else {"error": str(parsed)}


def submit_controller_update(
    *,
    stack: str,
    desired_image: str,
    source_revision: str = "",
    reason: str = "",
    base_url: str | None = None,
    secret: str | None = None,
) -> dict[str, Any] | None:
    """Submit the update to the standalone controller (same as host path).

    Returns the controller operation on HTTP 202, ``None`` when no
    controller writer is reachable (unconfigured secret, refused
    connection, timeout, or unknown route: the legacy path stays safe),
    and raises :class:`DeploymentOperationError` when the controller
    answered with an ownership decision (refusal, conflict, or internal
    error): the legacy updater must never fork a competing writer then.
    """
    resolved_secret = secret if secret is not None else controller_secret()
    if not resolved_secret:
        return None
    url = f"{controller_base_url(base_url)}/v1/operations"
    try:
        status, parsed = _controller_request(
            method="POST",
            url=url,
            secret=resolved_secret,
            payload={
                "stack": stack,
                "desiredImage": desired_image,
                "sourceRevision": source_revision,
                "reason": reason,
            },
            timeout=CONTROLLER_SUBMIT_TIMEOUT_SECONDS,
        )
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        raise _ControllerUnavailable(
            f"standalone controller is unreachable ({exc})"
        ) from exc
    if status == 202:
        return parsed
    if status in (400, 404, 409, 500):
        raise DeploymentOperationError(
            "deployment_controller_owned",
            f"Standalone controller answered HTTP {status}: "
            f"{parsed.get('error', 'unknown controller decision')}; refusing "
            "to fork the legacy updater while it may own the stack.",
        )
    raise DeploymentOperationError(
        "deployment_controller_unexpected",
        f"Standalone controller answered HTTP {status}; refusing to fork "
        "the legacy updater on an uncertain outcome.",
    )


class _ControllerUnavailable(RuntimeError):
    """No controller writer owns the operation; legacy fallback stays safe."""


def observe_controller_operation(
    *,
    operation_id: str,
    base_url: str | None = None,
    secret: str | None = None,
) -> dict[str, Any] | None:
    """Observe the controller's record for one operation (read-only)."""
    resolved_secret = secret if secret is not None else controller_secret()
    if not resolved_secret or not operation_id:
        return None
    url = f"{controller_base_url(base_url)}/v1/operations/{operation_id}"
    try:
        status, parsed = _controller_request(
            method="GET",
            url=url,
            secret=resolved_secret,
            timeout=CONTROLLER_STATUS_TIMEOUT_SECONDS,
        )
    except (urllib.error.URLError, OSError, TimeoutError, ValueError):
        return None
    if status != 200:
        return None
    return parsed


def controller_action_status(controller_status: str) -> str:
    """Map a controller operation status onto a recent-action status."""
    if controller_status in ("succeeded", "partially_verified"):
        return "SUCCEEDED" if controller_status == "succeeded" else "PARTIALLY_VERIFIED"
    if controller_status == "failed":
        return "FAILED"
    return "QUEUED"


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
    actions = []
    for parsed in records[-limit:]:
        operation_id = str(parsed.get("operationId") or "")
        desired = parsed.get("desired") or {}
        installed = parsed.get("installed") or {}
        actions.append(
            DeploymentRecentAction(
                id=f"ctl-{operation_id}",
                kind="update",
                status=controller_action_status(str(parsed.get("status") or "")),
                requested_image=str(desired.get("image") or "") or None,
                resolved_digest=str(installed.get("image") or "") or None,
                reason=str((desired.get("reason") or "") or "") or None,
                started_at=str(parsed.get("createdAt") or "") or None,
                completed_at=str(installed.get("confirmedAt") or "") or None,
                run_detail_url=None,
                run_id=f"ctl_{operation_id}",
            )
        )
    return tuple(reversed(actions))


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


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
        changed = False
        for action in stored:
            if not str(getattr(action, "run_id", "") or "").startswith("ctl_"):
                refreshed.append(action)
                continue
            operation_id = str(action.run_id or "")[len("ctl_") :]
            observed = observe_controller_operation(operation_id=operation_id)
            if observed is None:
                refreshed.append(action)
                continue
            updated_status = controller_action_status(str(observed.get("status") or ""))
            if updated_status == action.status:
                refreshed.append(action)
                continue
            changed = True
            refreshed.append(
                DeploymentRecentAction(
                    **{
                        **action.__dict__,
                        "status": updated_status,
                        "completed_at": (
                            action.completed_at
                            if updated_status == "QUEUED"
                            else _utc_now_iso()
                        ),
                    }
                )
            )
        if changed:
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
        submission: DeploymentUpdateSubmission,
        operation: dict[str, Any],
    ) -> DeploymentRecentAction:
        operation_id = str(operation.get("operationId") or "").strip()
        action = DeploymentRecentAction(
            id=f"ctl-{operation_id or uuid4().hex}",
            kind="update",
            status=controller_action_status(str(operation.get("status") or "")),
            requested_image=f"{submission.repository}:{submission.reference}",
            operator=(
                str(submission.requested_by_user_id)
                if submission.requested_by_user_id is not None
                else None
            ),
            reason=submission.reason,
            started_at=_utc_now_iso(),
            # No separate controller UI route exists; status is observed
            # from the controller record via recent_actions.
            run_detail_url=None,
            run_id=f"ctl_{operation_id}" if operation_id else None,
        )
        self._recent_actions[policy.stack] = (
            action,
            *self._recent_actions.get(policy.stack, ()),
        )[:50]
        return action

    async def queue_update(
        self,
        *,
        execution_service: DeploymentExecutionCreator,
        policy: DeploymentStackPolicy,
        submission: DeploymentUpdateSubmission,
    ) -> dict[str, str]:
        desired_image = f"{submission.repository}:{submission.reference}"
        try:
            controller_operation = await asyncio.to_thread(
                submit_controller_update,
                stack=policy.stack,
                desired_image=desired_image,
                source_revision="",
                reason=submission.reason or "",
            )
        except _ControllerUnavailable:
            controller_operation = None
        if controller_operation is not None:
            # The UI/API path submits the same controller operation as the
            # host entrypoint: no Temporal workflow is created, so no second
            # updater can own the stack. Status is observed from the
            # controller record (see recent_actions).
            action = self._record_controller_action(
                policy=policy,
                submission=submission,
                operation=controller_operation,
            )
            operation_id = str(controller_operation.get("operationId") or "")
            return {
                "deploymentUpdateRunId": action.id,
                "taskId": operation_id,
                "workflowId": operation_id,
                "status": "QUEUED",
            }
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
