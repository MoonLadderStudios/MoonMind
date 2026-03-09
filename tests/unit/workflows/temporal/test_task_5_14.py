import uuid

import pytest
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from moonmind.workflows.temporal.activities.task_5_14 import task_5_14_activity
from moonmind.workflows.temporal.workflows.task_5_14_workflow import Task514Workflow


@pytest.mark.asyncio
async def test_task_5_14_workflow():
    """Test validation for Task514Workflow and Task514Activity."""
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="task-5-14-queue",
            workflows=[Task514Workflow],
            activities=[task_5_14_activity],
        ):
            result = await env.client.execute_workflow(
                Task514Workflow.run,
                "test input",
                id=f"test-5-14-{uuid.uuid4()}",
                task_queue="task-5-14-queue",
            )
            assert result == "Processed 5.14: test input"
