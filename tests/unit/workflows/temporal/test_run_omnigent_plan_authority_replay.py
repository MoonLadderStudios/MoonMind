"""Retained Omnigent plan-input requests replay across the authority cutoff."""

import pytest
from temporalio import workflow
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, UnsandboxedWorkflowRunner, Worker

from moonmind.workflows.temporal.workflows.run import (
    RUN_OMNIGENT_EXECUTION_PLAN_BINDING_AUTHORITY_PATCH,
    MoonMindRunWorkflow,
)
from tests.unit.workflows.temporal.workflows.test_run_omnigent_plan_authority import (
    _binding,
)


class _PrePatchPlanWorkflow(MoonMindRunWorkflow):
    @staticmethod
    def _workflow_patch_enabled(patch_id: str) -> bool:
        if patch_id == RUN_OMNIGENT_EXECUTION_PLAN_BINDING_AUTHORITY_PATCH:
            return False
        return MoonMindRunWorkflow._workflow_patch_enabled(patch_id)


async def _request_result(run_workflow, admitted: bool) -> str:
    try:
        request = run_workflow._build_agent_execution_request(
            node_inputs={
                "runtime": {"mode": "omnigent", "omnigentExecutionPlan": _binding("2")}
            },
            node_id="step-1",
            tool_name="auto",
            workflow_parameters=(
                {"omnigentExecutionPlan": _binding("1")} if admitted else {}
            ),
        )
    except ValueError as exc:
        assert "omnigentExecutionPlan" in str(exc) and "admitted" in str(exc)
        return "rejected"
    # This command distinguishes the accepted historical path from rejection.
    await workflow.sleep(1)
    return request.omnigent_execution_plan.plan_ref


@workflow.defn(name="OmnigentPlanAuthorityReplayFixture")
class _LegacyPlanAuthorityFixture:
    @workflow.run
    async def run(self, admitted: bool) -> str:
        return await _request_result(_PrePatchPlanWorkflow(), admitted)


@workflow.defn(name="OmnigentPlanAuthorityReplayFixture")
class _CurrentPlanAuthorityFixture:
    @workflow.run
    async def run(self, admitted: bool) -> str:
        return await _request_result(MoonMindRunWorkflow(), admitted)


@pytest.mark.asyncio
@pytest.mark.parametrize("admitted", [True, False])
async def test_pre_and_post_patch_plan_binding_histories_replay(admitted):
    histories = []
    async with await WorkflowEnvironment.start_time_skipping() as env:
        for kind, implementation, expected in (
            ("legacy", _LegacyPlanAuthorityFixture, _binding("2")["planRef"]),
            ("current", _CurrentPlanAuthorityFixture, "rejected"),
        ):
            queue = f"test-omnigent-plan-authority-{kind}-{admitted}"
            async with Worker(
                env.client,
                task_queue=queue,
                workflows=[implementation],
                workflow_runner=UnsandboxedWorkflowRunner(),
            ):
                handle = await env.client.start_workflow(
                    implementation.run,
                    admitted,
                    id=queue,
                    task_queue=queue,
                )
                assert await handle.result() == expected
                histories.append(await handle.fetch_history())
    replayer = Replayer(
        workflows=[_CurrentPlanAuthorityFixture],
        workflow_runner=UnsandboxedWorkflowRunner(),
    )
    for history in histories:
        await replayer.replay_workflow(history)
