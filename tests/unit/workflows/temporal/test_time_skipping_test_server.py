"""The suite's time-skipping test server supports the Temporal features tests use."""

import asyncio
import uuid

import pytest
from temporalio import workflow
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

_TASK_QUEUE = "time-skipping-update-probe"


@workflow.defn
class _UpdateProbe:
    def __init__(self) -> None:
        self._finished = False

    @workflow.run
    async def run(self) -> str:
        await workflow.wait_condition(lambda: self._finished)
        return "finished"

    @workflow.update
    def finish(self) -> str:
        self._finished = True
        return "updated"


@pytest.mark.asyncio
async def test_time_skipping_server_completes_workflow_updates():
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=_TASK_QUEUE,
            workflows=[_UpdateProbe],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            handle = await env.client.start_workflow(
                _UpdateProbe.run,
                id=f"update-probe-{uuid.uuid4()}",
                task_queue=_TASK_QUEUE,
            )
            # A server that drops updates leaves this waiting until the test
            # times out; fail promptly with the update named as the cause.
            update = await asyncio.wait_for(
                handle.execute_update(_UpdateProbe.finish), timeout=30
            )

            assert update == "updated"
            assert await handle.result() == "finished"
