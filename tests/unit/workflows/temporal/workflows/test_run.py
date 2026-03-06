import asyncio
import unittest
from datetime import timedelta
from typing import Any, Dict

from temporalio import activity
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker, UnsandboxedWorkflowRunner

from moonmind.workflows.temporal.workflows.run import MoonMindRunWorkflow

@activity.defn(name="plan.generate")
async def mock_plan_generate(args: Dict[str, Any]) -> Dict[str, Any]:
    return {"plan_ref": "artifact://plan/123"}

@activity.defn(name="sandbox.command")
async def mock_sandbox_command(args: Dict[str, Any]) -> Dict[str, Any]:
    return {"exit_code": 0, "stdout": "executing", "stderr": ""}

@activity.defn(name="integration.start")
async def mock_integration_start(args: Dict[str, Any]) -> Dict[str, Any]:
    return {"correlation_id": "corr-123"}

class TestMoonMindRunWorkflow(unittest.IsolatedAsyncioTestCase):
    async def test_moonmind_run_workflow(self) -> None:
        async with await WorkflowEnvironment.start_time_skipping() as env:
            # We use UnsandboxedWorkflowRunner here for isolated tests to avoid dependency sandboxing issues
            async with Worker(
                env.client,
                task_queue="test-task-queue",
                workflows=[MoonMindRunWorkflow],
                workflow_runner=UnsandboxedWorkflowRunner(),
            ):
                # Using main task queue worker but for activities we start individual workers on requested queues
                pass

            # Create additional workers for the different task queues our workflow calls
            async with Worker(
                env.client,
                task_queue="mm-llm",
                activities=[mock_plan_generate],
            ), Worker(
                env.client,
                task_queue="mm-sandbox",
                activities=[mock_sandbox_command],
            ), Worker(
                env.client,
                task_queue="mm-integrations",
                activities=[mock_integration_start],
            ), Worker(
                env.client,
                task_queue="test-task-queue",
                workflows=[MoonMindRunWorkflow],
                workflow_runner=UnsandboxedWorkflowRunner(),
            ):
                request = {
                    "workflowType": "MoonMind.Run",
                    "title": "Test Run",
                    "initialParameters": {
                        "repo": "moonladder/moonmind",
                        "integration": "github"
                    }
                }

                # Start workflow
                handle = await env.client.start_workflow(
                    MoonMindRunWorkflow.run,
                    request,
                    id="test-workflow-id",
                    task_queue="test-task-queue",
                )

                # We need to resume it because integration forces wait
                await handle.signal(MoonMindRunWorkflow.resume)

                result = await handle.result()
                self.assertEqual(result["status"], "success")

    async def test_moonmind_run_workflow_validation_error(self) -> None:
        async with await WorkflowEnvironment.start_time_skipping() as env:
            async with Worker(
                env.client,
                task_queue="test-task-queue",
                workflows=[MoonMindRunWorkflow],
                workflow_runner=UnsandboxedWorkflowRunner(),
            ):
                request = {
                    "title": "Test Run",
                    # Missing workflowType
                }

                with self.assertRaises(Exception):
                    await env.client.execute_workflow(
                        MoonMindRunWorkflow.run,
                        request,
                        id="test-workflow-id-error",
                        task_queue="test-task-queue",
                    )
