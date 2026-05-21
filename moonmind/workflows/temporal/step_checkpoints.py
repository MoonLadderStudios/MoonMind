"""Pure helpers for Step Execution checkpoint identity and validation."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from moonmind.schemas.temporal_models import (
    STEP_EXECUTION_CHECKPOINT_CONTENT_TYPE,
    StepExecutionCheckpointBoundary,
    StepExecutionCheckpointModel,
    StepExecutionIdentityModel,
    StepCheckpointValidationRequestModel,
    StepCheckpointValidationResultModel,
    WorkspaceCheckpointKind,
    WorkspacePolicy,
)

_POLICY_CHECKPOINT_KINDS: dict[WorkspacePolicy, tuple[WorkspaceCheckpointKind, ...]] = {
    "restore_pre_execution": (
        "git_commit",
        "worktree_archive",
        "ephemeral_workspace_ref",
    ),
    "continue_from_previous_execution": (
        "ephemeral_workspace_ref",
        "git_patch",
        "worktree_archive",
        "git_commit",
        "external_state_ref",
    ),
    "apply_previous_execution_diff_to_clean_baseline": ("git_patch",),
    "start_from_last_passed_commit": ("git_commit",),
    "fresh_branch_from_source": (),
}


def build_step_checkpoint_id(
    identity: StepExecutionIdentityModel,
    boundary: StepExecutionCheckpointBoundary,
) -> str:
    """Build the deterministic identifier for one Step Execution checkpoint."""

    return (
        f"{identity.workflow_id}:{identity.run_id}:"
        f"{identity.logical_step_id}:execution:{identity.execution_ordinal}:"
        f"checkpoint:{boundary}"
    )


def build_step_checkpoint_idempotency_key(
    identity: StepExecutionIdentityModel,
    boundary: StepExecutionCheckpointBoundary,
    operation: str = "write",
) -> str:
    """Build a deterministic idempotency key for checkpoint activity writes."""

    operation_id = str(operation or "").strip()
    if not operation_id:
        raise ValueError("operation must be a non-empty string")
    return f"{build_step_checkpoint_id(identity, boundary)}:{operation_id}"


def build_step_checkpoint_payload(
    *,
    identity: StepExecutionIdentityModel,
    boundary: StepExecutionCheckpointBoundary,
    task_input_snapshot_ref: str,
    workspace: Mapping[str, Any],
    created_at: datetime,
    plan_ref: str | None = None,
    plan_digest: str | None = None,
    prepared_input_refs: list[str] | tuple[str, ...] = (),
    step_outputs: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a JSON-serializable checkpoint artifact payload."""

    checkpoint = StepExecutionCheckpointModel(
        schemaVersion="v1",
        contentType=STEP_EXECUTION_CHECKPOINT_CONTENT_TYPE,
        checkpointId=build_step_checkpoint_id(identity, boundary),
        checkpointKind="step_boundary",
        boundary=boundary,
        source=identity,
        taskInputSnapshotRef=task_input_snapshot_ref,
        planRef=plan_ref,
        planDigest=plan_digest,
        preparedInputRefs=list(prepared_input_refs),
        workspace=dict(workspace),
        stepOutputs=dict(step_outputs or {}),
        createdAt=created_at,
    )
    return checkpoint.model_dump(by_alias=True, mode="json")


def checkpoint_kinds_for_workspace_policy(
    policy: WorkspacePolicy,
) -> tuple[WorkspaceCheckpointKind, ...]:
    """Return accepted checkpoint kinds for a workspace policy."""

    return _POLICY_CHECKPOINT_KINDS[policy]


def validate_step_checkpoint(
    request: StepCheckpointValidationRequestModel,
) -> StepCheckpointValidationResultModel:
    """Validate checkpoint evidence before launching execution or recovery work."""

    checkpoint = request.checkpoint
    source = checkpoint.source
    expected = request.expected_source
    if source.workflow_id != expected.workflow_id or source.run_id != expected.run_id:
        return _invalid(request, "source_mismatch", "checkpoint source workflow/run mismatch")
    if source.logical_step_id != expected.logical_step_id:
        return _invalid(request, "step_mismatch", "checkpoint logical step mismatch")
    if source.execution_ordinal != expected.execution_ordinal:
        return _invalid(
            request,
            "attempt_mismatch",
            "checkpoint execution ordinal mismatch",
        )
    if checkpoint.task_input_snapshot_ref != request.expected_task_input_snapshot_ref:
        return _invalid(
            request,
            "task_input_mismatch",
            "checkpoint task input snapshot mismatch",
        )
    if request.expected_plan_ref is not None and (
        checkpoint.plan_ref != request.expected_plan_ref
    ):
        return _invalid(request, "plan_mismatch", "checkpoint plan ref mismatch")
    if request.expected_plan_digest is not None and (
        checkpoint.plan_digest != request.expected_plan_digest
    ):
        return _invalid(request, "plan_mismatch", "checkpoint plan digest mismatch")
    if request.workspace_policy is not None:
        accepted_kinds = checkpoint_kinds_for_workspace_policy(request.workspace_policy)
        if checkpoint.workspace.kind not in accepted_kinds:
            return _invalid(
                request,
                "policy_incompatible",
                (
                    f"checkpoint kind {checkpoint.workspace.kind} does not satisfy "
                    f"workspace policy {request.workspace_policy}"
                ),
            )
    workspace_payload = checkpoint.workspace.model_dump(by_alias=True, mode="json")
    for key, expected_value in request.expected_workspace.items():
        if workspace_payload.get(key) != expected_value:
            return _invalid(
                request,
                "workspace_mismatch",
                f"checkpoint workspace {key} mismatch",
            )
    available_artifact_refs = _checkpoint_artifact_refs(checkpoint, request)
    for artifact_ref in request.unauthorized_artifact_refs:
        if artifact_ref in available_artifact_refs:
            return _invalid(
                request,
                "artifact_unauthorized",
                f"checkpoint artifact ref {artifact_ref} is unauthorized",
            )
    for artifact_ref in request.corrupted_artifact_refs:
        if artifact_ref in available_artifact_refs:
            return _invalid(
                request,
                "artifact_corrupted",
                f"checkpoint artifact ref {artifact_ref} is corrupted",
            )
    for artifact_ref in request.required_artifact_refs:
        if artifact_ref not in available_artifact_refs:
            return _invalid(
                request,
                "artifact_missing",
                f"checkpoint missing required artifact ref {artifact_ref}",
            )
    return StepCheckpointValidationResultModel(
        valid=True,
        failureCode=None,
        message="checkpoint validation passed",
        checkpointId=checkpoint.checkpoint_id,
        checkpointRef=request.checkpoint_ref,
    )


def _invalid(
    request: StepCheckpointValidationRequestModel,
    failure_code: str,
    message: str,
) -> StepCheckpointValidationResultModel:
    return StepCheckpointValidationResultModel(
        valid=False,
        failureCode=failure_code,
        message=message,
        checkpointId=request.checkpoint.checkpoint_id,
        checkpointRef=request.checkpoint_ref,
    )


def _checkpoint_artifact_refs(
    checkpoint: StepExecutionCheckpointModel,
    request: StepCheckpointValidationRequestModel,
) -> set[str]:
    refs: set[str] = {
        checkpoint.task_input_snapshot_ref,
        *checkpoint.prepared_input_refs,
    }
    if checkpoint.plan_ref:
        refs.add(checkpoint.plan_ref)
    if request.checkpoint_ref:
        refs.add(request.checkpoint_ref)
    _collect_ref_values(checkpoint.workspace.model_dump(by_alias=True, mode="json"), refs)
    _collect_ref_values(checkpoint.step_outputs, refs)
    return refs


def _collect_ref_values(value: Any, refs: set[str]) -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            normalized = (
                str(key).replace("_", "").replace("-", "").replace(" ", "").lower()
            )
            if normalized.endswith("ref") and isinstance(nested, str) and nested.strip():
                refs.add(nested.strip())
            elif normalized.endswith("refs") and isinstance(nested, list):
                for item in nested:
                    if isinstance(item, str) and item.strip():
                        refs.add(item.strip())
            _collect_ref_values(nested, refs)
    elif isinstance(value, list):
        for item in value:
            _collect_ref_values(item, refs)
