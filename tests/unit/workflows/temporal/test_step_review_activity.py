"""Tests for the step.review Temporal activity."""

from __future__ import annotations

import pytest

from moonmind.workflows.temporal.activities.step_review import (
    step_review_activity,
)

@pytest.mark.asyncio
async def test_step_review_activity_does_not_infer_review_from_completed_execution():
    """MoonLadderStudios/MoonMind#3927: execution success is not review evidence."""
    result = await step_review_activity(
        {
            "node_id": "n1",
            "step_index": 1,
            "total_steps": 3,
            "review_attempt": 1,
            "tool_name": "repo.run_tests",
            "tool_type": "skill",
            "inputs": {"repo_ref": "git:org/repo#branch"},
            "execution_result": {"status": "COMPLETED", "outputs": {}},
            "workflow_context": {"plan_title": "Fix tests"},
        }
    )
    assert result["verdict"] == "NO_DETERMINATION"
    assert result["confidence"] == 0.0
    assert result["recommendedNextAction"] == "needs_human"
    assert result["recoverableInCurrentRuntime"] is False
    assert result["issues"][0]["code"] == "reviewer_unavailable"
    assert "no reviewer implementation is configured" in result["feedback"]
    assert "validatedRefs" not in result

@pytest.mark.asyncio
async def test_step_review_activity_with_minimal_payload():
    """Activity handles sparse payloads gracefully."""
    result = await step_review_activity(
        {
            "node_id": "n1",
            "step_index": 1,
            "total_steps": 1,
            "review_attempt": 1,
            "tool_name": "test",
        }
    )
    assert result["verdict"] == "NO_DETERMINATION"

@pytest.mark.asyncio
async def test_step_review_activity_with_previous_feedback():
    """Activity accepts previous_feedback without error."""
    result = await step_review_activity(
        {
            "node_id": "n1",
            "step_index": 2,
            "total_steps": 5,
            "review_attempt": 2,
            "tool_name": "repo.apply_patch",
            "tool_type": "skill",
            "inputs": {},
            "execution_result": {},
            "workflow_context": {},
            "previous_feedback": "Missing import in utils.py",
        }
    )
    assert result["verdict"] == "NO_DETERMINATION"
