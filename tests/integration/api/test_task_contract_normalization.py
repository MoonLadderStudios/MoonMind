"""MM-638: Hermetic integration_ci tests for task contract normalization.

These tests call build_canonical_task_view directly (no external credentials or
compose networking required) and verify the executions API normalization path
for canonical task-typed submissions.

Acceptance scenarios covered:
  SC-001  Well-formed resume_from_failed_step payload accepted; fields preserved
  SC-002  resume_from_failed_step without resume block → TaskContractError
  SC-003  resume block with wrong recovery.kind → TaskContractError
  SC-006  task.git.targetBranch normalized to branch in canonical output
"""
from __future__ import annotations

import pytest

from moonmind.workflows.tasks.task_contract import (
    TaskContractError,
    build_canonical_task_view,
)

pytestmark = [pytest.mark.integration, pytest.mark.integration_ci]

_VALID_RESUME_BLOCK = {
    "kind": "resume_from_failed_step",
    "sourceWorkflowId": "mm:abc123",
    "sourceRunId": "run-1",
    "failedStepId": "step-3",
    "resumeCheckpointRef": "art_ckpt_abc",
    "taskInputSnapshotRef": "art_snap_abc",
}

_BASE_PAYLOAD = {
    "repository": "test/repo",
    "task": {"instructions": "Do work"},
}


def _task_payload(task_overrides: dict) -> dict:
    return {**_BASE_PAYLOAD, "task": {**_BASE_PAYLOAD["task"], **task_overrides}}


# T017 — SC-001
def test_sc001_well_formed_resume_from_failed_step_accepted() -> None:
    """MM-638 SC-001: Complete resume_from_failed_step payload is accepted;
    all recovery and resume fields are preserved in the normalized output."""
    result = build_canonical_task_view(
        job_type="task",
        payload=_task_payload(
            {
                "recovery": {
                    "kind": "resume_from_failed_step",
                    "sourceWorkflowId": "mm:abc123",
                    "sourceRunId": "run-1",
                },
                "resume": _VALID_RESUME_BLOCK,
            }
        ),
    )

    task = result["task"]
    assert task["recovery"]["kind"] == "resume_from_failed_step"
    assert task["recovery"]["sourceWorkflowId"] == "mm:abc123"
    assert task["recovery"]["sourceRunId"] == "run-1"
    assert task["resume"]["failedStepId"] == "step-3"
    assert task["resume"]["resumeCheckpointRef"] == "art_ckpt_abc"
    assert task["resume"]["taskInputSnapshotRef"] == "art_snap_abc"


# T018 — SC-002
def test_sc002_resume_from_failed_step_without_resume_block_raises() -> None:
    """MM-638 SC-002: resume_from_failed_step without a resume block raises
    TaskContractError with an operator-readable message identifying the missing field."""
    with pytest.raises(TaskContractError, match="task.resume is required"):
        build_canonical_task_view(
            job_type="task",
            payload=_task_payload(
                {
                    "recovery": {
                        "kind": "resume_from_failed_step",
                        "sourceWorkflowId": "mm:abc123",
                        "sourceRunId": "run-1",
                    },
                }
            ),
        )


# T019 — SC-003
def test_sc003_resume_block_with_wrong_recovery_kind_raises() -> None:
    """MM-638 SC-003: resume block paired with recovery.kind != resume_from_failed_step
    raises TaskContractError preventing ambiguous recovery inference."""
    with pytest.raises(TaskContractError, match="resume_from_failed_step"):
        build_canonical_task_view(
            job_type="task",
            payload=_task_payload(
                {
                    "recovery": {
                        "kind": "exact_full_rerun",
                        "sourceWorkflowId": "mm:abc123",
                        "sourceRunId": "run-1",
                    },
                    "resume": _VALID_RESUME_BLOCK,
                }
            ),
        )


# T020 — SC-006
def test_sc006_target_branch_normalized_to_branch_in_canonical_output() -> None:
    """MM-638 SC-006: task.git.targetBranch in a canonical payload is normalized so
    the canonical output carries task.git.branch and does not carry targetBranch."""
    result = build_canonical_task_view(
        job_type="task",
        payload=_task_payload({"git": {"targetBranch": "feature/legacy-branch"}}),
    )

    git = result["task"]["git"]
    assert git.get("branch") == "feature/legacy-branch"
    assert git.get("targetBranch") is None
