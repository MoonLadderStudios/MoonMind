"""Retained native pool histories keep their commands; new handoffs replay."""

import pytest
from temporalio import workflow
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, UnsandboxedWorkflowRunner, Worker

from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from moonmind.workflows.temporal.workflows.agent_run import (
    AGENT_RUN_SELECTED_POOL_SHARED_PLAN_PATCH_ID,
    MoonMindAgentRun,
)
from tests.unit.workflows.temporal.workflows.test_agent_run_omnigent_capacity_admission import (
    PLAN_REF,
    _plan_binding,
)


class _BeforePoolHandoff(MoonMindAgentRun):
    @staticmethod
    def _workflow_patch_enabled(marker):
        if marker == AGENT_RUN_SELECTED_POOL_SHARED_PLAN_PATCH_ID:
            return False
        return MoonMindAgentRun._workflow_patch_enabled(marker)


@workflow.defn
class _PreparationTransport:
    def __init__(self):
        self.finished = False

    @workflow.signal
    async def prepare_shared_execution_plan(self, payload):
        request = dict(payload["request"])
        request.update(
            agentKind="external",
            agentId="omnigent",
            omnigentExecutionPlan=_plan_binding(PLAN_REF),
        )
        request["stepExecution"]["omnigentExecutionPlan"] = _plan_binding(PLAN_REF)
        await workflow.get_external_workflow_handle(
            payload["owner_id"], run_id=payload["runId"]
        ).signal(
            "pool_plan_prepared",
            {
                "disposition": "shared",
                "request": request,
                "requestId": request["idempotencyKey"],
                "fencingGeneration": payload["fencing_generation"],
            },
        )

    @workflow.signal
    def finish(self):
        self.finished = True

    @workflow.run
    async def run(self):
        await workflow.wait_condition(lambda: self.finished)


class _PoolFixture:
    implementation = MoonMindAgentRun

    def __init__(self):
        self.agent = self.implementation()

    @workflow.signal
    def pool_plan_prepared(self, payload):
        self.agent.pool_plan_prepared(payload)

    async def execute(self):
        request = AgentExecutionRequest(
            agentKind="managed",
            agentId="codex_cli",
            executionProfileRef="selected-account",
            profileSelector={"tagsAll": ["pool"]},
            correlationId="parent",
            idempotencyKey="request-1",
            instructionRef="Preserve the accepted task.",
            stepExecution={
                "workflowId": "parent",
                "runId": "parent-run",
                "logicalStepId": "work",
                "executionOrdinal": 1,
                "stepExecutionId": "parent:parent-run:work:execution:1",
                "runtimeContextPolicy": "fresh_agent_run",
            },
        )
        self.agent._assigned_profile_id = request.execution_profile_ref
        self.agent._assigned_slot_fencing_generation = 7
        if self.agent._uses_selected_pool_shared_admission(request, "codex_cli"):
            request = await self.agent._prepare_selected_pool_shared_request(
                request=request,
                runtime_id="codex_cli",
                original_profile_ref=None,
                manager_handle=workflow.get_external_workflow_handle(
                    "pool-preparation-transport"
                ),
            )
        await workflow.sleep(1)
        return request.agent_id


@workflow.defn(name="SelectedPoolPlanReplayFixture")
class _LegacyFixture(_PoolFixture):
    implementation = _BeforePoolHandoff

    @workflow.run
    async def run(self):
        return await self.execute()


@workflow.defn(name="SelectedPoolPlanReplayFixture")
class _CurrentFixture(_PoolFixture):
    @workflow.run
    async def run(self):
        return await self.execute()


@pytest.mark.asyncio
async def test_retained_native_and_shared_pool_handoffs_replay():
    histories = []
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="pool-preparation",
            workflows=[_PreparationTransport],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            manager = await env.client.start_workflow(
                _PreparationTransport.run,
                id="pool-preparation-transport",
                task_queue="pool-preparation",
            )
            for label, fixture, expected in (
                ("retained", _LegacyFixture, "codex_cli"),
                ("current", _CurrentFixture, "omnigent"),
            ):
                queue = f"pool-replay-{label}"
                async with Worker(
                    env.client,
                    task_queue=queue,
                    workflows=[fixture],
                    workflow_runner=UnsandboxedWorkflowRunner(),
                ):
                    handle = await env.client.start_workflow(
                        fixture.run, id=queue, task_queue=queue
                    )
                    # Keep the idle transport alive between fixture runs; global
                    # time skipping could otherwise advance to its execution timeout.
                    with env.auto_time_skipping_disabled():
                        result = await handle.result()
                    assert result == expected
                    histories.append(await handle.fetch_history())
            await manager.signal("finish")
            await manager.result()
    replayer = Replayer(
        workflows=[_CurrentFixture], workflow_runner=UnsandboxedWorkflowRunner()
    )
    for history in histories:
        await replayer.replay_workflow(history)
