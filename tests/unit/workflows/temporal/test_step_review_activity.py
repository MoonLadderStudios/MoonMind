"""Tests for the step.review Temporal activity."""

from __future__ import annotations

import pytest

from moonmind.workflows.temporal.activities.step_review import (
    step_review_activity,
)


@pytest.mark.asyncio
async def test_step_review_activity_returns_pass():
    """The placeholder implementation always returns PASS."""
    result = await step_review_activity(
        {
            "node_id": "n1",
            "step_index": 1,
            "total_steps": 3,
            "review_attempt": 1,
            "tool_name": "repo.run_tests",
            "tool_version": "1.0",
            "tool_type": "skill",
            "inputs": {"repo_ref": "git:org/repo#branch"},
            "execution_result": {"status": "COMPLETED", "outputs": {}},
            "workflow_context": {"plan_title": "Fix tests"},
        }
    )
    assert result["verdict"] == "PASS"
    assert result["confidence"] == 1.0


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
            "tool_version": "1.0",
        }
    )
    assert result["verdict"] == "PASS"


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
            "tool_version": "2.0",
            "tool_type": "skill",
            "inputs": {},
            "execution_result": {},
            "workflow_context": {},
            "previous_feedback": "Missing import in utils.py",
        }
    )
    assert result["verdict"] == "PASS"
