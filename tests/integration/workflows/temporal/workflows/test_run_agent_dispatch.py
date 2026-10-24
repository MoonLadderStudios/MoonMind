"""Registered UserWorkflow dispatch, with provider effects isolated at its child."""

import pytest
from temporalio import client

from tests.integration.workflows.temporal.workflows.test_run import (
    _plan,
    _running_user_workflow,
)

pytestmark = [pytest.mark.integration, pytest.mark.integration_ci]


@pytest.mark.asyncio
async def test_agent_runtime_node_dispatches_child_and_skips_legacy_registry() -> None:
    async with _running_user_workflow() as (handle, calls):
        assert (await handle.result())["status"] == "success"
        assert len(calls["children"]) == 1
        assert calls["children"][0].agent_id == "claude_code"
        assert calls["children"][0].resolved_skillset_ref == "artifact://resolved/test"
        assert len(calls["skills"]) == 1
        assert [r["artifact_ref"] for r in calls["reads"]] == ["artifact://plan/test"]


@pytest.mark.asyncio
async def test_unknown_tool_node_cannot_dispatch() -> None:
    tool_type = "unknown_type"
    async with _running_user_workflow(plan=_plan(tool_type)) as (handle, calls):
        with pytest.raises(client.WorkflowFailureError) as error:
            await handle.result()
        assert (
            "node.tool.type must be 'skill' or 'agent_runtime', got 'unknown_type'"
            in error.value.cause.message
        )
        assert calls["children"] == []
        assert [r["artifact_ref"] for r in calls["reads"]] == ["artifact://plan/test"]


@pytest.mark.asyncio
async def test_independent_runs_resolve_skill_inputs_and_never_read_legacy_registry() -> (
    None
):
    # Independent starts each resolve inputs; this is not a step retry.
    for workflow_id in ["test-first-run", "test-second-run"]:
        async with _running_user_workflow(workflow_id=workflow_id) as (handle, calls):
            assert (await handle.result())["status"] == "success"
            assert len(calls["children"]) == len(calls["skills"]) == 1
            assert (
                calls["children"][0].resolved_skillset_ref == "artifact://resolved/test"
            )
            assert [r["artifact_ref"] for r in calls["reads"]] == [
                "artifact://plan/test"
            ]
