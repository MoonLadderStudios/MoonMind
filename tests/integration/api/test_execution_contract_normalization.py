"""MM-638: Hermetic integration_ci tests for workflow contract normalization.

These tests call build_canonical_workflow_view directly (no external credentials or
compose networking required) and verify the executions API normalization path
for canonical workflow submissions.

Acceptance scenarios covered:
  SC-001  Well-formed recover_from_failed_step payload accepted; fields preserved
  SC-002  recover_from_failed_step without resume block → WorkflowContractError
  SC-003  resume block with wrong recovery.kind → WorkflowContractError
  SC-006  workflow.git.targetBranch stripped as active authored branch input
  MM-945  git capability derived only from repository-backed execution context
"""
from __future__ import annotations

import pytest

from moonmind.workflows.executions.execution_contract import (
    WorkflowContractError,
    build_canonical_workflow_view,
)
from tests.helpers.step_type_payloads import (
    preset_step,
    skill_step,
    tool_step,
)

pytestmark = [pytest.mark.integration, pytest.mark.integration_ci]

_VALID_RESUME_BLOCK = {
    "kind": "recover_from_failed_step",
    "sourceWorkflowId": "mm:abc123",
    "sourceRunId": "run-1",
    "failedStepId": "step-3",
    "recoveryCheckpointRef": "art_ckpt_abc",
    "taskInputSnapshotRef": "art_snap_abc",
}

_BASE_PAYLOAD = {
    "repository": "test/repo",
    "workflow": {"instructions": "Do work"},
}


def _workflow_payload(workflow_overrides: dict) -> dict:
    return {
        **_BASE_PAYLOAD,
        "workflow": {**_BASE_PAYLOAD["workflow"], **workflow_overrides},
    }


def _workflow_payload_without_repository(workflow_overrides: dict) -> dict:
    payload = _workflow_payload(workflow_overrides)
    payload.pop("repository", None)
    return payload


def _workflow_step_payload(*steps: dict) -> dict:
    return _workflow_payload(
        {
            "instructions": "Validate explicit Step Type payloads for MM-569.",
            "steps": list(steps),
        }
    )


# T017 — SC-001
def test_sc001_well_formed_recover_from_failed_step_accepted() -> None:
    """MM-638 SC-001: Complete recover_from_failed_step payload is accepted;
    all recovery and resume fields are preserved in the normalized output."""
    result = build_canonical_workflow_view(
        job_type="task",
        payload=_workflow_payload(
            {
                "recovery": {
                    "kind": "recover_from_failed_step",
                    "sourceWorkflowId": "mm:abc123",
                    "sourceRunId": "run-1",
                },
                "resume": _VALID_RESUME_BLOCK,
            }
        ),
    )

    workflow = result["workflow"]
    assert workflow["recovery"]["kind"] == "recover_from_failed_step"
    assert workflow["recovery"]["sourceWorkflowId"] == "mm:abc123"
    assert workflow["recovery"]["sourceRunId"] == "run-1"
    assert workflow["resume"]["failedStepId"] == "step-3"
    assert workflow["resume"]["recoveryCheckpointRef"] == "art_ckpt_abc"
    assert workflow["resume"]["taskInputSnapshotRef"] == "art_snap_abc"


# T018 — SC-002
def test_sc002_recover_from_failed_step_without_recovery_block_raises() -> None:
    """MM-638 SC-002: recover_from_failed_step without a resume block raises
    WorkflowContractError with an operator-readable message identifying the missing field."""
    with pytest.raises(WorkflowContractError, match="task.resume is required"):
        build_canonical_workflow_view(
            job_type="task",
            payload=_workflow_payload(
                {
                    "recovery": {
                        "kind": "recover_from_failed_step",
                        "sourceWorkflowId": "mm:abc123",
                        "sourceRunId": "run-1",
                    },
                }
            ),
        )


# T019 — SC-003
def test_sc003_recovery_block_with_wrong_recovery_kind_raises() -> None:
    """MM-638 SC-003: resume block paired with recovery.kind != recover_from_failed_step
    raises WorkflowContractError preventing ambiguous recovery inference."""
    with pytest.raises(WorkflowContractError, match="recover_from_failed_step"):
        build_canonical_workflow_view(
            job_type="task",
            payload=_workflow_payload(
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
def test_sc006_target_branch_rejected_as_active_authored_input() -> None:
    """MM-668: workflow.git.targetBranch is legacy metadata, not active authored input."""
    result = build_canonical_workflow_view(
        job_type="task",
        payload=_workflow_payload({"git": {"targetBranch": "feature/legacy-branch"}}),
    )
    assert result["workflow"]["git"]["branch"] is None
    assert "targetBranch" not in result["workflow"]["git"]


def test_mm641_task_git_branch_remains_the_active_authored_branch() -> None:
    result = build_canonical_workflow_view(
        job_type="task",
        payload=_workflow_payload(
            {
                "git": {"branch": "feature/mm-641-create-page"},
                "publish": {"mode": "branch"},
            }
        ),
    )

    assert result["workflow"]["git"]["branch"] == "feature/mm-641-create-page"
    assert result["workflow"]["publish"]["mode"] == "branch"
    assert "targetBranch" not in result["workflow"]["git"]


def test_mm945_document_only_workflow_without_repository_does_not_derive_git() -> None:
    result = build_canonical_workflow_view(
        job_type="task",
        payload=_workflow_payload_without_repository(
            {
                "publish": {"mode": "none"},
                "reportOutput": {"enabled": True},
            }
        ),
    )

    assert result["repository"] is None
    assert "git" not in result["requiredCapabilities"]
    assert result["requiredCapabilities"] == ["codex"]


def test_mm945_jira_only_step_without_repository_does_not_derive_git() -> None:
    result = build_canonical_workflow_view(
        job_type="task",
        payload=_workflow_payload_without_repository(
            {
                "publish": {"mode": "none"},
                "steps": [
                    {
                        "type": "tool",
                        "instructions": "Fetch Jira issue context.",
                        "tool": {
                            "type": "tool",
                            "id": "jira.get_issue",
                            "inputs": {"issueKey": "MM-945"},
                            "requiredCapabilities": ["jira"],
                        },
                    }
                ],
            }
        ),
    )

    assert result["repository"] is None
    assert result["requiredCapabilities"] == ["codex", "jira"]


def test_mm945_pr_publish_without_repository_derives_gh_not_git() -> None:
    result = build_canonical_workflow_view(
        job_type="task",
        payload=_workflow_payload_without_repository({"publish": {"mode": "pr"}}),
    )

    assert result["repository"] is None
    assert "git" not in result["requiredCapabilities"]
    assert result["requiredCapabilities"] == ["codex", "gh"]


def test_mm945_explicit_git_capability_remains_authoritative_without_repository() -> None:
    result = build_canonical_workflow_view(
        job_type="task",
        payload={
            **_workflow_payload_without_repository({"publish": {"mode": "none"}}),
            "requiredCapabilities": ["git"],
        },
    )

    assert result["repository"] is None
    assert result["requiredCapabilities"] == ["git", "codex"]


def test_mm945_malformed_workflow_node_raises_contract_error_not_attribute_error() -> None:
    with pytest.raises(WorkflowContractError, match="task.instructions is required"):
        build_canonical_workflow_view(
            job_type="task",
            payload={
                "workflow": "malformed",
                "requiredCapabilities": ["jira"],
            },
        )


def test_mm569_unresolved_preset_submission_rejected_with_field_path() -> None:
    with pytest.raises(WorkflowContractError) as excinfo:
        build_canonical_workflow_view(
            job_type="task", payload=_workflow_step_payload(preset_step())
        )

    assert "workflow.steps[].type" in str(excinfo.value)
    assert "tool, skill" in str(excinfo.value)


def test_mm569_flat_executable_steps_preserve_preset_provenance_without_lookup() -> None:
    tool = tool_step()
    tool["source"] = {
        "kind": "preset-derived",
        "presetSlug": "mm569-parent",
        "presetDigest": "digest-mm569-parent",
        "includePath": ["mm569-parent"],
    }
    skill = skill_step()
    skill["source"] = {
        "kind": "preset-derived",
        "presetSlug": "mm569-parent",
        "presetDigest": "digest-mm569-parent",
        "includePath": ["mm569-parent"],
    }

    result = build_canonical_workflow_view(
        job_type="task", payload=_workflow_step_payload(tool, skill)
    )

    steps = result["workflow"]["steps"]
    assert [step["type"] for step in steps] == ["tool", "skill"]
    assert steps[0]["source"]["presetSlug"] == "mm569-parent"
    assert steps[1]["source"]["presetSlug"] == "mm569-parent"
    assert all(step.get("type") != "preset" for step in steps)


def test_mm786_flat_steps_preserve_per_step_runtime_selection() -> None:
    result = build_canonical_workflow_view(
        job_type="task",
        payload=_workflow_payload(
            {
                "runtime": {
                    "mode": "codex_cli",
                    "model": "gpt-5.4",
                    "effort": "medium",
                },
                "steps": [
                    {
                        "id": "low-cost-step",
                        "instructions": "Run this step with a lower-cost runtime.",
                        "runtime": {
                            "mode": "claude_code",
                            "model": "claude-sonnet-4-5",
                            "effort": "low",
                        },
                    }
                ],
            }
        ),
    )

    runtime = result["workflow"]["steps"][0]["runtime"]
    assert {key: runtime[key] for key in ("mode", "model", "effort")} == {
        "mode": "claude_code",
        "model": "claude-sonnet-4-5",
        "effort": "low",
    }
