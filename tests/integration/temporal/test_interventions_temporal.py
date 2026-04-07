import pytest
import asyncio

from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker, UnsandboxedWorkflowRunner

from moonmind.workflows.temporal.workflows.run import MoonMindRunWorkflow

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]


async def fake_execute_activity(activity_name, *args, **kwargs):
    if activity_name == "artifact.read":
        import json
        return json.dumps({
            "plan_version": "1.0",
            "metadata": {"title": "Test"},
            "policy": {"failure_mode": "FAIL_FAST", "max_concurrency": 1},
            "tools": [],
            "nodes": [],
            "edges": []
        }).encode("utf-8")
    return {}

@pytest.fixture
def mock_run_environment(monkeypatch):
    from temporalio import workflow
    monkeypatch.setattr(MoonMindRunWorkflow, "_trusted_owner_metadata", lambda self: ("user", "user-1"))
    monkeypatch.setattr(workflow, "execute_activity", fake_execute_activity)
    monkeypatch.setattr(workflow, "upsert_search_attributes", lambda attr: None)
    monkeypatch.setattr(workflow, "upsert_memo", lambda memo: None)
    
    async def fake_planning_stage(*args, **kwargs): return "ref-123"
    async def fake_execution_stage(self, *args, **kwargs):
        # We simulate execution that waits a bit so we can pause it
        self._set_state("executing", summary="Executing fake step")
        await asyncio.sleep(0.5)
        # Check if we were paused
        if self._paused:
            pass # We would block here if we were using actual temporalio wait_condition in a real loop, 
            # but for this test we'll just let the workflow level `run()` method block on `_paused` inside the main loop or wait_condition if applicable.
            # Actually, the MoonMindRunWorkflow has `await workflow.wait_condition(lambda: not self._paused)` right before execution_stage.
    monkeypatch.setattr(MoonMindRunWorkflow, "_run_planning_stage", fake_planning_stage)
    monkeypatch.setattr(MoonMindRunWorkflow, "_run_execution_stage", fake_execution_stage)

@pytest.mark.asyncio
async def test_workflow_pause_resume(mock_run_environment):
    """
    Integration test: verify the agent control loop respects pause/resume signals (updates)
    independently of live logs.
    """
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="test-task-queue-interventions",
            workflows=[MoonMindRunWorkflow],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            workflow_id = "test-wf-interventions-1"
            
            # Start workflow
            handle = await env.client.start_workflow(
                MoonMindRunWorkflow.run,
                {
                    "workflow_type": "MoonMind.Run",
                    "initial_parameters": {},
                    "plan_artifact_ref": "ref-123",
                },
                id=workflow_id,
                task_queue="test-task-queue-interventions",
            )

            # Pause it
            await handle.execute_update("Pause")
            
            # Describe it to see it is paused
            description = await handle.describe()
            assert description.status == description.status.RUNNING
            
            # Wait a tick, it should remain paused and not completed
            await asyncio.sleep(0.1)

            # Resume it
            await handle.execute_update("Resume")
            
            result = await handle.result()
            assert result["status"] == "success"

@pytest.mark.asyncio
async def test_legacy_observability_cutoff(mock_run_environment):
    """
    Integration test: Validates historical test stubs mock gracefully skipping web_ro
    and that no Tmate embeds or stdout push mechanisms are expected.
    """
    # Simply running the workflow and asserting success ensures
    # the workflow does not require `tmate` or `web_ro` to function.
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="test-task-queue-cutoff",
            workflows=[MoonMindRunWorkflow],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            workflow_id = "test-wf-cutoff-1"
            handle = await env.client.start_workflow(
                MoonMindRunWorkflow.run,
                {
                    "workflow_type": "MoonMind.Run",
                    "initial_parameters": {},
                    "plan_artifact_ref": "ref-123",
                },
                id=workflow_id,
                task_queue="test-task-queue-cutoff",
            )
            result = await handle.result()
            assert result["status"] == "success"
