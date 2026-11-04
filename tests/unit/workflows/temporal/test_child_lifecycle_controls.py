"""Parent lifecycle updates cross the actual SDK child-control boundary."""

import asyncio
from datetime import timedelta
from uuid import uuid4

import pytest
from temporalio import workflow
from temporalio.client import WorkflowExecutionStatus, WorkflowUpdateFailedError
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, UnsandboxedWorkflowRunner, Worker

from moonmind.workflows.temporal.workflows.agent_run import MoonMindAgentRun
from moonmind.workflows.temporal.workflows.run import MoonMindUserWorkflow
from tests.unit.workflows.temporal.workflows.test_run_signals_updates import (
    mock_run_environment as mock_run_environment,
)


@workflow.defn(name="LifecycleBoundaryAgentRun")
class _LifecycleBoundaryAgentRun(MoonMindAgentRun):
    """Keep the production controls; replace only the agent's compute body."""

    @workflow.run
    async def run(self) -> None:
        await workflow.wait_condition(lambda: False)

    @workflow.query
    def paused_for_test(self) -> bool:
        return self._paused


async def _wait_for_child(client, child_id):
    child = client.get_workflow_handle(child_id)
    for _ in range(100):
        try:
            await child.query("paused_for_test", rpc_timeout=timedelta(seconds=2))
            return child
        except Exception:
            await asyncio.sleep(0.02)
    pytest.fail("The controlled AgentRun did not start")


@pytest.mark.asyncio
@pytest.mark.usefixtures("mock_run_environment")
@pytest.mark.parametrize("resume", [False, True], ids=["cancel-paused", "resume"])
async def test_parent_pause_reaches_real_agent_controls_and_replays(
    monkeypatch, resume
):
    queue, parent_id = f"lifecycle-{uuid4()}", f"parent-{uuid4()}"
    child_id = parent_id + ":agent:step"

    async def execute_child(self, *args, **kwargs):
        self._active_agent_child_workflow_id = child_id
        try:
            await workflow.execute_child_workflow(
                _LifecycleBoundaryAgentRun.run, id=child_id, task_queue=queue
            )
        finally:
            self._active_agent_child_workflow_id = None

    monkeypatch.setattr(MoonMindUserWorkflow, "_run_execution_stage", execute_child)
    async with await WorkflowEnvironment.start_time_skipping() as env:
        with env.auto_time_skipping_disabled():
            async with Worker(
                env.client,
                task_queue=queue,
                workflows=[MoonMindUserWorkflow, _LifecycleBoundaryAgentRun],
                workflow_runner=UnsandboxedWorkflowRunner(),
            ):
                parent = await env.client.start_workflow(
                    MoonMindUserWorkflow.run,
                    {
                        "workflow_type": "MoonMind.UserWorkflow",
                        "initial_parameters": {},
                        "plan_artifact_ref": "ref-123",
                    },
                    id=parent_id,
                    task_queue=queue,
                )
                child = await _wait_for_child(env.client, child_id)
                try:
                    await parent.execute_update(
                        "Pause", rpc_timeout=timedelta(seconds=4)
                    )
                    for _ in range(100):
                        if await child.query("paused_for_test"):
                            break
                        await asyncio.sleep(0.02)
                    assert await child.query("paused_for_test") is True
                    state = await parent.query("control_state")
                    assert state["paused"] is True
                    assert (
                        state["safePoint"] is False
                    ), "An active child is not a drained safe point"
                    if resume:
                        await parent.execute_update(
                            "Resume", rpc_timeout=timedelta(seconds=4)
                        )
                        for _ in range(100):
                            if not await child.query("paused_for_test"):
                                break
                            await asyncio.sleep(0.02)
                        assert await child.query("paused_for_test") is False
                        assert (await parent.query("control_state"))["resumed"] is True
                    history = await parent.fetch_history()
                    await Replayer(
                        workflows=[MoonMindUserWorkflow, _LifecycleBoundaryAgentRun],
                        workflow_runner=UnsandboxedWorkflowRunner(),
                    ).replay_workflow(history)
                    await parent.cancel()
                    for _ in range(100):
                        if (
                            await child.describe()
                        ).status is WorkflowExecutionStatus.CANCELED:
                            break
                        await asyncio.sleep(0.02)
                    assert (
                        await child.describe()
                    ).status is WorkflowExecutionStatus.CANCELED
                    assert (
                        len(
                            [
                                event
                                for event in (await parent.fetch_history()).events
                                if event.HasField(
                                    "child_workflow_execution_started_event_attributes"
                                )
                            ]
                        )
                        == 1
                    ), "Cancellation starts no successor"
                finally:
                    for handle in (parent, child):
                        if (
                            await handle.describe()
                        ).status is WorkflowExecutionStatus.RUNNING:
                            await handle.terminate(
                                reason="Hermetic control test complete"
                            )


@pytest.mark.asyncio
@pytest.mark.usefixtures("mock_run_environment")
async def test_undeliverable_pause_fails_the_update_without_poisoning_workflow(
    monkeypatch,
):
    queue, parent_id = f"lifecycle-{uuid4()}", f"parent-{uuid4()}"

    async def missing_child(self, *args, **kwargs):
        self._active_agent_child_workflow_id = parent_id + ":missing-child"
        await workflow.wait_condition(lambda: self._cancel_requested)

    monkeypatch.setattr(MoonMindUserWorkflow, "_run_execution_stage", missing_child)
    async with await WorkflowEnvironment.start_time_skipping() as env:
        with env.auto_time_skipping_disabled():
            async with Worker(
                env.client,
                task_queue=queue,
                workflows=[MoonMindUserWorkflow],
                workflow_runner=UnsandboxedWorkflowRunner(),
            ):
                parent = await env.client.start_workflow(
                    MoonMindUserWorkflow.run,
                    {
                        "workflow_type": "MoonMind.UserWorkflow",
                        "initial_parameters": {},
                        "plan_artifact_ref": "ref-123",
                    },
                    id=parent_id,
                    task_queue=queue,
                )
                try:
                    for _ in range(100):
                        if (await parent.query("get_status"))["state"] == "executing":
                            break
                        await asyncio.sleep(0.02)
                    with pytest.raises(WorkflowUpdateFailedError) as error:
                        await parent.execute_update(
                            "Pause", rpc_timeout=timedelta(seconds=4)
                        )
                    assert isinstance(error.value.cause, ApplicationError)
                    assert error.value.cause.type == "WorkflowControlDeliveryFailed"
                    assert "Failed to forward Pause" in str(error.value.cause)
                    assert (await parent.query("control_state"))["paused"] is False
                    await parent.execute_update(
                        "Cancel", rpc_timeout=timedelta(seconds=4)
                    )
                    assert (await parent.result())["status"] == "canceled"
                    await Replayer(
                        workflows=[MoonMindUserWorkflow],
                        workflow_runner=UnsandboxedWorkflowRunner(),
                    ).replay_workflow(await parent.fetch_history())
                finally:
                    if (
                        await parent.describe()
                    ).status is WorkflowExecutionStatus.RUNNING:
                        await parent.terminate(reason="Hermetic control test complete")
