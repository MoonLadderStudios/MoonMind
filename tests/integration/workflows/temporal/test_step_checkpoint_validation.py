from __future__ import annotations

from datetime import UTC, datetime

from moonmind.schemas.temporal_models import (
    StepCheckpointValidationRequestModel,
    StepExecutionCheckpointModel,
    StepExecutionIdentityModel,
)
from moonmind.workflows.temporal.step_checkpoints import (
    build_step_checkpoint_payload,
    validate_step_checkpoint,
)


def _identity() -> StepExecutionIdentityModel:
    return StepExecutionIdentityModel(
        workflowId="workflow-1",
        runId="run-1",
        logicalStepId="implement-story",
        attempt=2,
    )


def _valid_checkpoint() -> StepExecutionCheckpointModel:
    return StepExecutionCheckpointModel.model_validate(
        build_step_checkpoint_payload(
            identity=_identity(),
            boundary="before_attempt",
            task_input_snapshot_ref="artifact-input",
            plan_ref="artifact-plan",
            plan_digest="sha256:plan",
            workspace={
                "kind": "git_patch",
                "baseCommit": "abc123",
                "patchRef": "artifact-patch",
                "manifestRef": "artifact-manifest",
            },
            step_outputs={"summaryRef": "artifact-summary"},
            created_at=datetime(2026, 5, 17, 12, 0, tzinfo=UTC),
        )
    )


def test_checkpoint_validation_boundary_allows_compact_valid_evidence() -> None:
    result = validate_step_checkpoint(
        StepCheckpointValidationRequestModel(
            checkpoint=_valid_checkpoint(),
            expectedSource=_identity(),
            expectedTaskInputSnapshotRef="artifact-input",
            expectedPlanRef="artifact-plan",
            expectedPlanDigest="sha256:plan",
            workspacePolicy="apply_previous_execution_diff_to_clean_baseline",
            requiredArtifactRefs=[
                "artifact-input",
                "artifact-plan",
                "artifact-patch",
                "artifact-manifest",
            ],
            checkpointRef="artifact-checkpoint",
        )
    )

    serialized = result.model_dump(by_alias=True)
    assert serialized == {
        "valid": True,
        "failureCode": None,
        "message": "checkpoint validation passed",
        "checkpointId": (
            "workflow-1:run-1:implement-story:execution:2:checkpoint:before_attempt"
        ),
        "checkpointRef": "artifact-checkpoint",
    }


def test_checkpoint_validation_boundary_blocks_runtime_launch_on_mismatch() -> None:
    plan_mismatch = validate_step_checkpoint(
        StepCheckpointValidationRequestModel(
            checkpoint=_valid_checkpoint(),
            expectedSource=_identity(),
            expectedTaskInputSnapshotRef="artifact-input",
            expectedPlanDigest="sha256:other",
            workspacePolicy="apply_previous_execution_diff_to_clean_baseline",
        )
    )
    assert plan_mismatch.valid is False
    assert plan_mismatch.failure_code == "plan_mismatch"

    policy_mismatch = validate_step_checkpoint(
        StepCheckpointValidationRequestModel(
            checkpoint=_valid_checkpoint(),
            expectedSource=_identity(),
            expectedTaskInputSnapshotRef="artifact-input",
            expectedPlanDigest="sha256:plan",
            workspacePolicy="start_from_last_passed_commit",
        )
    )
    assert policy_mismatch.valid is False
    assert policy_mismatch.failure_code == "policy_incompatible"
