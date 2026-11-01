"""Controller-issued context survives Temporal delivery and old histories."""

from datetime import timedelta

import pytest
from temporalio import activity, workflow
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, UnsandboxedWorkflowRunner, Worker

with workflow.unsafe.imports_passed_through():
    from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
    from moonmind.workflows.temporal.workflows.run import MoonMindUserWorkflow


def _request(logical_step, policy):
    info = workflow.info()
    return AgentExecutionRequest.model_validate(
        {
            "agentKind": "external",
            "agentId": "omnigent",
            "correlationId": info.workflow_id,
            "idempotencyKey": logical_step,
            "stepExecution": {
                "workflowId": info.workflow_id,
                "runId": info.run_id,
                "logicalStepId": logical_step,
                "executionOrdinal": 1,
                "stepExecutionId": f"{info.workflow_id}:{info.run_id}:{logical_step}:execution:1",
                "runtimeContextPolicy": policy,
            },
        }
    )


@activity.defn(name="context.fixture_result")
async def _result(request: dict) -> dict:
    step = request["stepExecution"]
    return {
        "metadata": {
            "runtimeSessionContext": {
                "workflowId": step["workflowId"],
                "runId": step["runId"],
                "stepExecutionId": step["stepExecutionId"],
                "epoch": step["stepExecutionId"],
                "runtimeBindingRef": "omnigent-runtime-binding:sha256:" + "1" * 64,
            }
        }
    }


@activity.defn(name="context.fixture_observe")
async def _observe(request: dict) -> bool:
    step = request["stepExecution"]
    reference = step.get("runtimeSessionContext")
    if reference:
        assert reference["workflowId"] == step["workflowId"]
        assert ":first:execution:1" in reference["stepExecutionId"]
    return reference is not None


@workflow.defn(name="SharedContextReplayFixture")
class _LegacyContextFixture:
    @workflow.run
    async def run(self) -> bool:
        await workflow.execute_activity(
            "context.fixture_result",
            _request("first", "fresh_agent_run").model_dump(
                by_alias=True, mode="json", exclude_none=True
            ),
            start_to_close_timeout=timedelta(seconds=5),
        )
        return await workflow.execute_activity(
            "context.fixture_observe",
            _request("second", "reuse_session_same_epoch").model_dump(
                by_alias=True, mode="json", exclude_none=True
            ),
            start_to_close_timeout=timedelta(seconds=5),
        )


@workflow.defn(name="SharedContextReplayFixture")
class _SharedContextFixture:
    @workflow.run
    async def run(self) -> bool:
        owner = MoonMindUserWorkflow()
        first = _request("first", "fresh_agent_run")
        result = await workflow.execute_activity(
            "context.fixture_result",
            first.model_dump(by_alias=True, mode="json", exclude_none=True),
            start_to_close_timeout=timedelta(seconds=5),
        )
        owner._record_shared_runtime_context(first, result)
        second = owner._bind_shared_runtime_context(
            _request("second", "reuse_session_same_epoch")
        )
        return await workflow.execute_activity(
            "context.fixture_observe",
            second.model_dump(by_alias=True, mode="json", exclude_none=True),
            start_to_close_timeout=timedelta(seconds=5),
        )


@pytest.mark.asyncio
async def test_pre_and_post_context_histories_replay_on_the_current_controller():
    async with await WorkflowEnvironment.start_time_skipping() as environment:
        for index, implementation in enumerate(
            (_LegacyContextFixture, _SharedContextFixture)
        ):
            queue = f"shared-context-replay-{index}"
            async with Worker(
                environment.client,
                task_queue=queue,
                workflows=[implementation],
                activities=[_result, _observe],
                workflow_runner=UnsandboxedWorkflowRunner(),
            ):
                handle = await environment.client.start_workflow(
                    "SharedContextReplayFixture",
                    id=f"shared-context-{index}",
                    task_queue=queue,
                    execution_timeout=timedelta(seconds=20),
                )
                assert await handle.result() is bool(index)
                history = await handle.fetch_history()
            await Replayer(
                workflows=[_SharedContextFixture],
                workflow_runner=UnsandboxedWorkflowRunner(),
            ).replay_workflow(history)
