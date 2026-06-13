from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from moonmind.schemas.temporal_models import (
    STEP_EXECUTION_CHECKPOINT_CONTENT_TYPE,
    StepCheckpointCreateInput,
    StepCheckpointCreateResult,
    StepCheckpointValidateInput,
    StepCheckpointValidateResult,
    StepExecutionIdentityModel,
    WorkspaceCheckpointCaptureInput,
    WorkspaceCheckpointCaptureResult,
    WorkspacePolicyApplyInput,
    WorkspacePolicyApplyResult,
)


def _identity() -> StepExecutionIdentityModel:
    return StepExecutionIdentityModel(
        workflowId="workflow-1",
        runId="run-1",
        logicalStepId="checkpoint-story",
        executionOrdinal=1,
    )


def _workspace_patch() -> dict[str, object]:
    return {
        "kind": "git_patch",
        "baseCommit": "abc123",
        "patchRef": "artifact-patch",
        "manifestRef": "artifact-manifest",
    }


def _assert_secret_absent(payload: object) -> None:
    rendered = str(payload)
    assert "GHCR_PULL_USER" not in rendered
    assert "GHCR_PULL_TOKEN" not in rendered
    assert "secret-token" not in rendered


def test_checkpoint_activity_contracts_are_compact_and_ref_only() -> None:
    capture = WorkspaceCheckpointCaptureInput(
        identity=_identity(),
        boundary="after_execution",
        kind="git_patch",
        workspacePath="/workspace/repo",
        artifactNamespace="checkpoint",
        idempotencyKey="idem-capture",
        baseCommit="abc123",
        pullAuthContextRef="artifact-pull-auth-context",
        providerLeaseContextRef="artifact-provider-lease-context",
    )
    assert capture.kind == "git_patch"

    result = WorkspaceCheckpointCaptureResult(
        status="captured",
        workspace=_workspace_patch(),
        summary="patch captured",
        pullAuth={"mode": "authenticated", "diagnosticRefs": ["artifact-ghcr"]},
        providerLeaseRefs=["artifact-provider-lease"],
    )
    dumped = result.model_dump(by_alias=True, mode="json")
    assert dumped["pullAuth"]["mode"] == "authenticated"
    _assert_secret_absent(dumped)

    with pytest.raises(ValidationError, match="compact refs|Extra inputs"):
        WorkspaceCheckpointCaptureResult(
            status="captured",
            workspace={**_workspace_patch(), "diff": "inline patch"},
            pullAuth={"mode": "anonymous"},
        )


def test_create_and_policy_contracts_reject_inline_evidence_and_secrets() -> None:
    create_input = StepCheckpointCreateInput(
        identity=_identity(),
        boundary="after_execution",
        taskInputSnapshotRef="artifact-input",
        workspace=_workspace_patch(),
        createdAt=datetime(2026, 6, 13, 12, 0, tzinfo=UTC),
        planDigest="sha256:plan",
        diagnosticRefs=["artifact-diagnostic"],
        idempotencyKey="idem-create",
    )
    assert create_input.workspace.kind == "git_patch"

    create_result = StepCheckpointCreateResult(
        checkpointRef="artifact-checkpoint",
        checkpointId="workflow-1:run-1:checkpoint-story:execution:1:checkpoint:after_execution",
        contentType=STEP_EXECUTION_CHECKPOINT_CONTENT_TYPE,
        workspaceKind="git_patch",
        summary="checkpoint written",
        diagnosticRefs=["artifact-diagnostic"],
        idempotencyKey="idem-create",
    )
    assert create_result.content_type == STEP_EXECUTION_CHECKPOINT_CONTENT_TYPE

    with pytest.raises(ValidationError, match="compact refs"):
        StepCheckpointCreateInput(
            identity=_identity(),
            boundary="after_execution",
            taskInputSnapshotRef="artifact-input",
            workspace=_workspace_patch(),
            createdAt=datetime(2026, 6, 13, 12, 0, tzinfo=UTC),
            planDigest="sha256:plan",
            stepOutputs={"stdout": "raw output"},
            idempotencyKey="idem-create",
        )

    policy = WorkspacePolicyApplyInput(
        identity=_identity(),
        workspacePolicy="apply_previous_execution_diff_to_clean_baseline",
        checkpointRef="artifact-checkpoint",
        checkpoint={"workspace": _workspace_patch()},
        targetWorkspaceRef="workspace-ref",
        expectedPlanDigest="sha256:plan",
        providerLeaseContextRef="artifact-provider-context",
        idempotencyKey="idem-policy",
    )
    assert policy.provider_lease_context_ref == "artifact-provider-context"

    with pytest.raises(ValidationError, match="raw credentials"):
        WorkspacePolicyApplyResult(
            status="prepared",
            workspaceRef="workspace-ref",
            providerLeaseRefs=["secret-token-value"],
            summary="prepared",
            failureCode=None,
        )


def test_validate_result_failure_invariants_cover_required_failure_codes() -> None:
    validate_input = StepCheckpointValidateInput(
        checkpoint={"checkpointId": "checkpoint-1"},
        expectedSource=_identity(),
        expectedTaskInputSnapshotRef="artifact-input",
        unsupportedArtifactRefs=["artifact-unsupported"],
        unsafeArtifactRefs=["artifact-unsafe"],
        workspaceIncompatibleRefs=["artifact-workspace"],
        checkpointRef="artifact-checkpoint",
    )
    assert validate_input.unsupported_artifact_refs == ["artifact-unsupported"]

    invalid = StepCheckpointValidateResult(
        valid=False,
        failureCode="unsupported_checkpoint_kind",
        message="unsupported",
        checkpointId="checkpoint-1",
        checkpointRef="artifact-checkpoint",
        diagnosticRefs=["artifact-diagnostic"],
    )
    assert invalid.failure_code == "unsupported_checkpoint_kind"

    with pytest.raises(ValidationError, match="invalid checkpoint result requires"):
        StepCheckpointValidateResult(
            valid=False,
            message="missing code",
            checkpointId="checkpoint-1",
        )

    with pytest.raises(ValidationError, match="valid checkpoint result cannot"):
        StepCheckpointValidateResult(
            valid=True,
            failureCode="unsafe_checkpoint",
            message="bad",
            checkpointId="checkpoint-1",
        )
