"""Checkpoint-branch retrieval launch coverage for MoonMind#3514."""

from __future__ import annotations

import pytest
from unittest.mock import patch

from moonmind.workflows.temporal.workflows.run import (
    MoonMindRunWorkflow,
    RUN_CHECKPOINT_BRANCH_RETRIEVAL_PATCH,
    RUN_CHECKPOINT_BRANCH_TURN_CONTEXT_PATCH,
)


def _parent_policy() -> dict:
    return {
        "enabled": True,
        "required": False,
        "collections": ["repo", "docs"],
        "filters": {"repository": "MoonMind"},
        "topK": 8,
        "maxContextTokens": 6000,
        "fallbackAllowed": False,
        "staleOverlayAllowed": False,
        "overlayPolicy": "include",
    }


def test_checkpoint_branch_retrieval_override_narrows_launch_policy() -> None:
    result = MoonMindRunWorkflow._narrow_checkpoint_branch_follow_up_retrieval(
        _parent_policy(),
        {
            "enabled": True,
            "required": True,
            "collections": ["docs"],
            "filters": {"repository": "MoonMind", "branch": "feature"},
            "topK": 3,
            "maxContextTokens": 2000,
            "overlayPolicy": "skip",
        },
    )

    assert result["enabled"] is True
    assert result["required"] is True
    assert result["collections"] == ["docs"]
    assert result["filters"] == {
        "repository": "MoonMind",
        "branch": "feature",
    }
    assert result["topK"] == 3
    assert result["maxContextTokens"] == 2000
    assert result["overlayPolicy"] == "skip"


@pytest.mark.parametrize(
    "override, message",
    [
        ({"enabled": True}, "parent-denied"),
        (
            {"enabled": True, "collections": ["private"]},
            "collections exceed",
        ),
        ({"enabled": True, "topK": 9}, "topK exceeds"),
        (
            {"enabled": True, "fallbackAllowed": True},
            "fallbackAllowed exceeds",
        ),
        (
            {"enabled": True, "filters": {"branch": "feature"}},
            "filters relax",
        ),
    ],
)
def test_checkpoint_branch_retrieval_override_rejects_broadening(
    override: dict,
    message: str,
) -> None:
    parent = {} if "parent-denied" in message else _parent_policy()
    with pytest.raises(ValueError, match=message):
        MoonMindRunWorkflow._narrow_checkpoint_branch_follow_up_retrieval(
            parent, override
        )


def test_checkpoint_branch_retrieval_override_can_disable_parent() -> None:
    assert MoonMindRunWorkflow._narrow_checkpoint_branch_follow_up_retrieval(
        _parent_policy(), {"enabled": False}
    ) == {"enabled": False}


def test_real_checkpoint_branch_request_carries_narrowed_retrieval_with_patch() -> None:
    """Cover the production request constructor and its replay patch boundary."""

    class MockInfo:
        namespace = "default"
        workflow_id = "workflow-1"
        run_id = "run-1"

    branch_turn = {
        "branchId": "branch-1",
        "branchTurnId": "turn-1",
        "sourceWorkflowId": "source-workflow",
        "sourceRunId": "source-run",
        "sourceLogicalStepId": "source-step",
        "sourceCheckpointRef": "artifact://checkpoint/source",
        "instructionArtifactRef": "artifact://instructions/turn-1",
        "followUpRetrieval": {
            "enabled": True,
            "collections": ["docs"],
            "topK": 3,
        },
    }
    node_inputs = {
        "followUpRetrieval": _parent_policy(),
        "runtime": {
            "mode": "codex_cli",
            "metadata": {"moonmind": {"checkpointBranchTurn": branch_turn}},
        },
    }
    with patch(
        "moonmind.workflows.temporal.workflows.run.workflow.info",
        return_value=MockInfo(),
    ), patch(
        "moonmind.workflows.temporal.workflows.run.workflow.patched",
        side_effect=lambda patch_id: patch_id
        in {RUN_CHECKPOINT_BRANCH_TURN_CONTEXT_PATCH, RUN_CHECKPOINT_BRANCH_RETRIEVAL_PATCH},
    ):
        request = MoonMindRunWorkflow()._build_agent_execution_request(
            node_inputs=node_inputs,
            node_id="branch-step",
            tool_name="codex_cli",
        )
    assert request.parameters["followUpRetrieval"]["collections"] == ["docs"]
    assert request.parameters["followUpRetrieval"]["topK"] == 3

    with patch(
        "moonmind.workflows.temporal.workflows.run.workflow.info",
        return_value=MockInfo(),
    ), patch(
        "moonmind.workflows.temporal.workflows.run.workflow.patched",
        side_effect=lambda patch_id: patch_id == RUN_CHECKPOINT_BRANCH_TURN_CONTEXT_PATCH,
    ):
        replay_request = MoonMindRunWorkflow()._build_agent_execution_request(
            node_inputs=node_inputs,
            node_id="branch-step",
            tool_name="codex_cli",
        )
    assert replay_request.parameters["followUpRetrieval"] == _parent_policy()
