import pytest
from datetime import datetime, timedelta, timezone

from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker, UnsandboxedWorkflowRunner

from moonmind.workflows.temporal.workflows.run import MoonMindRunWorkflow
from moonmind.workflows.temporal.client import TemporalClientAdapter

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
    async def fake_execution_stage(*args, **kwargs): pass
    monkeypatch.setattr(MoonMindRunWorkflow, "_run_planning_stage", fake_planning_stage)
    monkeypatch.setattr(MoonMindRunWorkflow, "_run_execution_stage", fake_execution_stage)

@pytest.mark.asyncio
async def test_create_deferred_reschedule_verify(mock_run_environment):
    """
    Integration test: create deferred → reschedule → verify new execution time
    """
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="test-task-queue-resched-int",
            workflows=[MoonMindRunWorkflow],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            # Create deferred execution 2 hours in the future
            future_time = (datetime.now(timezone.utc) + timedelta(hours=2))
            
            # Use TemporalClientAdapter to simulate real client interaction
            adapter = TemporalClientAdapter(client=env.client)
            
            workflow_id = "test-wf-resched-int-1"
            
            # Start via adapter
            await env.client.start_workflow(
                MoonMindRunWorkflow.run,
                {
                    "workflow_type": "MoonMind.Run",
                    "initial_parameters": {},
                    "plan_artifact_ref": "ref-123",
                    "scheduled_for": future_time.isoformat()
                },
                id=workflow_id,
                task_queue="test-task-queue-resched-int",
            )
            
            # Reschedule via adapter to a time 30 mins in the past to trigger immediately
            past_time = datetime.now(timezone.utc) - timedelta(minutes=30)
            await adapter.send_reschedule_signal(workflow_id, past_time)
            
            # Verify the workflow finishes successfully (due to immediate execution)
            handle = env.client.get_workflow_handle(workflow_id)
            result = await handle.result()
            
            assert result["status"] == "success"

            # Verify that rescheduling to the past caused near-immediate execution
            description = await handle.describe()
            execution_duration = description.close_time - description.start_time
            assert execution_duration < timedelta(hours=1)
