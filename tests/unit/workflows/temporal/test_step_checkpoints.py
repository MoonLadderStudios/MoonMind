from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from moonmind.schemas.temporal_models import (
    STEP_EXECUTION_CHECKPOINT_CONTENT_TYPE,
    StepCheckpointValidationRequestModel,
    StepExecutionCheckpointModel,
    StepExecutionIdentityModel,
    WorkspaceCheckpointEvidenceModel,
)
from moonmind.workflows.temporal.step_checkpoints import (
    build_step_checkpoint_id,
    build_step_checkpoint_idempotency_key,
    build_step_checkpoint_payload,
    checkpoint_kinds_for_workspace_policy,
    validate_step_checkpoint,
)


def _identity() -> StepExecutionIdentityModel:
    return StepExecutionIdentityModel.model_validate(
        {
            "workflowId": "workflow-1",
            "runId": "run-1",
            "logicalStepId": "implement-story",
            "executionOrdinal": 2,
        }
    )


def _workspace_patch() -> dict[str, object]:
    return {
        "kind": "git_patch",
        "baseCommit": "abc123",
        "patchRef": "artifact-patch",
        "manifestRef": "artifact-manifest",
        "includesUntracked": True,
    }


def test_checkpoint_payload_uses_deterministic_boundary_identity() -> None:
    identity = _identity()
    created_at = datetime(2026, 5, 17, 12, 0, tzinfo=UTC)

    payload = build_step_checkpoint_payload(
        identity=identity,
        boundary="before_execution",
        task_input_snapshot_ref="artifact-input",
        plan_ref="artifact-plan",
        plan_digest="sha256:plan",
        workspace=_workspace_patch(),
        step_outputs={"summaryRef": "artifact-summary"},
        created_at=created_at,
    )

    expected_id = (
        "workflow-1:run-1:implement-story:execution:2:checkpoint:before_execution"
    )
    assert payload["contentType"] == STEP_EXECUTION_CHECKPOINT_CONTENT_TYPE
    assert payload["checkpointId"] == expected_id
    assert build_step_checkpoint_id(identity, "before_execution") == expected_id
    assert build_step_checkpoint_idempotency_key(identity, "before_execution") == (
        f"{expected_id}:write"
    )

    repeated = build_step_checkpoint_payload(
        identity=identity,
        boundary="before_execution",
        task_input_snapshot_ref="artifact-input",
        plan_ref="artifact-plan",
        plan_digest="sha256:plan",
        workspace=_workspace_patch(),
        step_outputs={"summaryRef": "artifact-summary"},
        created_at=created_at,
    )
    assert repeated["checkpointId"] == payload["checkpointId"]


def test_checkpoint_model_rejects_inline_large_or_raw_content() -> None:
    identity = _identity()
    payload = {
        **build_step_checkpoint_payload(
            identity=identity,
            boundary="after_execution",
            task_input_snapshot_ref="artifact-input",
            plan_ref="artifact-plan",
            workspace=_workspace_patch(),
            created_at=datetime(2026, 5, 17, 12, 0, tzinfo=UTC),
        ),
        "stepOutputs": {"logText": "x" * 2000},
    }
    with pytest.raises(ValidationError, match="compact refs"):
        StepExecutionCheckpointModel.model_validate(payload)


def test_workspace_checkpoint_model_rejects_unexpected_fields() -> None:
    workspace = {
        **_workspace_patch(),
        "inlineCheckpointPayload": {"raw": "x" * 20},
    }

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        WorkspaceCheckpointEvidenceModel.model_validate(workspace)


@pytest.mark.parametrize(
    ("workspace", "message"),
    [
        ({"kind": "git_commit"}, "git_commit checkpoint requires"),
        ({"kind": "git_patch", "patchRef": "artifact-patch"}, "git_patch checkpoint requires"),
        ({"kind": "worktree_archive", "archiveRef": "artifact-archive"}, "worktree_archive checkpoint requires"),
        ({"kind": "ephemeral_workspace_ref"}, "ephemeral_workspace_ref checkpoint requires"),
        ({"kind": "external_state_ref"}, "external_state_ref checkpoint requires"),
    ],
)
def test_workspace_checkpoint_kinds_require_kind_specific_evidence(
    workspace: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValidationError, match=message):
        WorkspaceCheckpointEvidenceModel.model_validate(workspace)


def test_workspace_policy_matrix_and_validation_failure_codes() -> None:
    assert checkpoint_kinds_for_workspace_policy("apply_previous_execution_diff_to_clean_baseline") == (
        "git_patch",
    )
    assert "worktree_archive" in checkpoint_kinds_for_workspace_policy(
        "restore_pre_execution"
    )
    assert checkpoint_kinds_for_workspace_policy("fresh_branch_from_source") == ()

    checkpoint = StepExecutionCheckpointModel.model_validate(
        build_step_checkpoint_payload(
            identity=_identity(),
            boundary="before_execution",
            task_input_snapshot_ref="artifact-input",
            plan_digest="sha256:plan",
            workspace={
                "kind": "git_commit",
                "headCommit": "def456",
            },
            created_at=datetime(2026, 5, 17, 12, 0, tzinfo=UTC),
        )
    )
    result = validate_step_checkpoint(
        StepCheckpointValidationRequestModel(
            checkpoint=checkpoint,
            expectedSource=_identity(),
            expectedTaskInputSnapshotRef="artifact-input",
            expectedPlanDigest="sha256:plan",
            workspacePolicy="apply_previous_execution_diff_to_clean_baseline",
            requiredArtifactRefs=["artifact-input"],
        )
    )

    assert result.valid is False
    assert result.failure_code == "policy_incompatible"
    assert "apply_previous_execution_diff_to_clean_baseline" in result.message

    fresh_branch_mismatch = validate_step_checkpoint(
        StepCheckpointValidationRequestModel(
            checkpoint=checkpoint,
            expectedSource=_identity(),
            expectedTaskInputSnapshotRef="artifact-input",
            expectedPlanDigest="sha256:plan",
            workspacePolicy="fresh_branch_from_source",
        )
    )

    assert fresh_branch_mismatch.valid is False
    assert fresh_branch_mismatch.failure_code == "policy_incompatible"


def test_validation_reports_source_plan_and_artifact_failures() -> None:
    checkpoint = StepExecutionCheckpointModel.model_validate(
        build_step_checkpoint_payload(
            identity=_identity(),
            boundary="before_recovery_restoration",
            task_input_snapshot_ref="artifact-input",
            plan_digest="sha256:plan",
            workspace=_workspace_patch(),
            created_at=datetime(2026, 5, 17, 12, 0, tzinfo=UTC),
        )
    )

    source_mismatch = validate_step_checkpoint(
        StepCheckpointValidationRequestModel(
            checkpoint=checkpoint,
            expectedSource=_identity().model_copy(update={"run_id": "run-2"}),
            expectedTaskInputSnapshotRef="artifact-input",
            expectedPlanDigest="sha256:plan",
        )
    )
    assert source_mismatch.failure_code == "source_mismatch"

    plan_mismatch = validate_step_checkpoint(
        StepCheckpointValidationRequestModel(
            checkpoint=checkpoint,
            expectedSource=_identity(),
            expectedTaskInputSnapshotRef="artifact-input",
            expectedPlanDigest="sha256:other",
        )
    )
    assert plan_mismatch.failure_code == "plan_mismatch"

    missing_artifact = validate_step_checkpoint(
        StepCheckpointValidationRequestModel(
            checkpoint=checkpoint,
            expectedSource=_identity(),
            expectedTaskInputSnapshotRef="artifact-input",
            expectedPlanDigest="sha256:plan",
            requiredArtifactRefs=["artifact-missing"],
        )
    )
    assert missing_artifact.failure_code == "artifact_missing"

    unauthorized_artifact = validate_step_checkpoint(
        StepCheckpointValidationRequestModel(
            checkpoint=checkpoint,
            expectedSource=_identity(),
            expectedTaskInputSnapshotRef="artifact-input",
            expectedPlanDigest="sha256:plan",
            unauthorizedArtifactRefs=["artifact-patch"],
        )
    )
    assert unauthorized_artifact.failure_code == "artifact_unauthorized"

    corrupted_artifact = validate_step_checkpoint(
        StepCheckpointValidationRequestModel(
            checkpoint=checkpoint,
            expectedSource=_identity(),
            expectedTaskInputSnapshotRef="artifact-input",
            expectedPlanDigest="sha256:plan",
            corruptedArtifactRefs=["artifact-manifest"],
        )
    )
    assert corrupted_artifact.failure_code == "artifact_corrupted"

    workspace_mismatch = validate_step_checkpoint(
        StepCheckpointValidationRequestModel(
            checkpoint=checkpoint,
            expectedSource=_identity(),
            expectedTaskInputSnapshotRef="artifact-input",
            expectedPlanDigest="sha256:plan",
            expectedWorkspace={"baseCommit": "different"},
        )
    )
    assert workspace_mismatch.failure_code == "workspace_mismatch"


def test_validation_collects_artifact_refs_from_space_separated_keys() -> None:
    checkpoint = StepExecutionCheckpointModel.model_validate(
        build_step_checkpoint_payload(
            identity=_identity(),
            boundary="before_recovery_restoration",
            task_input_snapshot_ref="artifact-input",
            plan_digest="sha256:plan",
            workspace=_workspace_patch(),
            step_outputs={"patch Ref": "artifact-space-ref"},
            created_at=datetime(2026, 5, 17, 12, 0, tzinfo=UTC),
        )
    )

    result = validate_step_checkpoint(
        StepCheckpointValidationRequestModel(
            checkpoint=checkpoint,
            expectedSource=_identity(),
            expectedTaskInputSnapshotRef="artifact-input",
            expectedPlanDigest="sha256:plan",
            requiredArtifactRefs=["artifact-space-ref"],
        )
    )

    assert result.valid is True
