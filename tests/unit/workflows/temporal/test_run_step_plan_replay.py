"""Retained root-plan requests replay while new steps use admitted step plans."""

import pytest
from temporalio import workflow
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, UnsandboxedWorkflowRunner, Worker

from moonmind.workflows.temporal.workflows.run import (
    RUN_OMNIGENT_STEP_EXECUTION_PLANS_PATCH,
    MoonMindRunWorkflow,
)
from tests.unit.workflows.temporal.workflows.test_run_omnigent_plan_authority import _binding


class _BeforeStepPlans(MoonMindRunWorkflow):
    @staticmethod
    def _workflow_patch_enabled(patch_id):
        if patch_id == RUN_OMNIGENT_STEP_EXECUTION_PLANS_PATCH:
            return False
        return MoonMindRunWorkflow._workflow_patch_enabled(patch_id)


async def _request(implementation, *, forged=False):
    runtime = {"mode": "omnigent", "model": "step-model", "effort": "high"}
    if forged:
        runtime["omnigentExecutionPlan"] = _binding("3")
    request = implementation._build_agent_execution_request(
        node_inputs={"runtime": runtime}, node_id="verify", tool_name="auto",
        workflow_parameters={
            "omnigentExecutionPlan": _binding("1"),
            "workflow": {"runtime": {"model": "root-model", "effort": "medium"}},
            "omnigentStepExecutionPlans": {
                "verify": {
                    "omnigentExecutionPlan": _binding("2"),
                    "resolvedSkillsetRef": "art_step_skills",
                    "model": "step-model", "effort": "high",
                },
            },
        },
    )
    await workflow.sleep(1)
    return request.omnigent_execution_plan.plan_ref


@workflow.defn(name="LogicalStepPlanReplayFixture")
class _LegacyFixture:
    @workflow.run
    async def run(self):
        return await _request(_BeforeStepPlans())


@workflow.defn(name="LogicalStepPlanReplayFixture")
class _CurrentFixture:
    @workflow.run
    async def run(self):
        return await _request(MoonMindRunWorkflow())


@pytest.mark.asyncio
async def test_retained_root_plan_and_new_step_plan_histories_replay():
    histories = []
    async with await WorkflowEnvironment.start_time_skipping() as env:
        for label, fixture, expected in (
            ("retained", _LegacyFixture, _binding("1")["planRef"]),
            ("current", _CurrentFixture, _binding("2")["planRef"]),
        ):
            queue = f"test-logical-step-plan-{label}"
            async with Worker(env.client, task_queue=queue, workflows=[fixture], workflow_runner=UnsandboxedWorkflowRunner()):
                handle = await env.client.start_workflow(fixture.run, id=queue, task_queue=queue)
                assert await handle.result() == expected
                histories.append(await handle.fetch_history())
    replayer = Replayer(workflows=[_CurrentFixture], workflow_runner=UnsandboxedWorkflowRunner())
    for history in histories:
        await replayer.replay_workflow(history)


@pytest.mark.asyncio
async def test_authored_plan_cannot_replace_the_admitted_step_plan(monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(workflow, "info", lambda: SimpleNamespace(namespace="default", workflow_id="mm:step-plans", run_id="run-1", parent=None))
    monkeypatch.setattr(workflow, "patched", lambda _patch: True)
    with pytest.raises(ValueError, match="omnigentExecutionPlan.*admitted"):
        await _request(MoonMindRunWorkflow(), forged=True)
