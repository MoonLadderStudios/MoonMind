import pytest
from datetime import datetime, timedelta, timezone

from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker, UnsandboxedWorkflowRunner

from temporalio import workflow
from moonmind.workflows.temporal.workflows.run import MoonMindRunWorkflow

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
    monkeypatch.setattr(MoonMindRunWorkflow, "_trusted_owner_metadata", lambda self: ("user", "user-1"))
    monkeypatch.setattr(workflow, "execute_activity", fake_execute_activity)
    monkeypatch.setattr(workflow, "upsert_search_attributes", lambda attr: None)
    monkeypatch.setattr(workflow, "upsert_memo", lambda memo: None)
    
    async def fake_planning_stage(*args, **kwargs): return "ref-123"
    async def fake_execution_stage(*args, **kwargs): pass
    monkeypatch.setattr(MoonMindRunWorkflow, "_run_planning_stage", fake_planning_stage)
    monkeypatch.setattr(MoonMindRunWorkflow, "_run_execution_stage", fake_execution_stage)

@pytest.mark.asyncio
async def test_run_workflow_scheduled(mock_run_environment):
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="test-task-queue-sched",
            workflows=[MoonMindRunWorkflow],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            future_time = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
            handle = await env.client.start_workflow(
                MoonMindRunWorkflow.run,
                {
                    "workflow_type": "MoonMind.Run",
                    "initial_parameters": {},
                    "plan_artifact_ref": "ref-123",
                    "scheduled_for": future_time
                },
                id="test-wf-sched",
                task_queue="test-task-queue-sched",
            )
            
            # Since we are in time-skipping mode, the wait will finish.
            result = await handle.result()
            assert result["status"] == "success"

@pytest.mark.asyncio
async def test_run_workflow_rescheduled(mock_run_environment):
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="test-task-queue-sched",
            workflows=[MoonMindRunWorkflow],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            future_time = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
            handle = await env.client.start_workflow(
                MoonMindRunWorkflow.run,
                {
                    "workflow_type": "MoonMind.Run",
                    "initial_parameters": {},
                    "plan_artifact_ref": "ref-123",
                    "scheduled_for": future_time
                },
                id="test-wf-resched",
                task_queue="test-task-queue-sched",
            )
            
            # Reschedule to a closer time to trigger a wake-up
            new_time = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()
            await handle.signal("reschedule", new_time)
            
            result = await handle.result()
            assert result["status"] == "success"

@pytest.mark.asyncio
async def test_run_workflow_rescheduled_past(mock_run_environment):
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="test-task-queue-sched",
            workflows=[MoonMindRunWorkflow],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            future_time = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
            handle = await env.client.start_workflow(
                MoonMindRunWorkflow.run,
                {
                    "workflow_type": "MoonMind.Run",
                    "initial_parameters": {},
                    "plan_artifact_ref": "ref-123",
                    "scheduled_for": future_time
                },
                id="test-wf-resched-past",
                task_queue="test-task-queue-sched",
            )
            
            # Reschedule to the past to trigger immediate execution
            past_time = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
            await handle.signal("reschedule", past_time)
            
            result = await handle.result()
            assert result["status"] == "success"

            # Verify that rescheduling to the past caused near-immediate execution
            description = await handle.describe()
            execution_duration = description.close_time - description.start_time
            assert execution_duration < timedelta(minutes=1)

@pytest.mark.asyncio
async def test_run_workflow_scheduled_cancel(mock_run_environment):
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="test-task-queue-sched",
            workflows=[MoonMindRunWorkflow],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            future_time = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
            handle = await env.client.start_workflow(
                MoonMindRunWorkflow.run,
                {
                    "workflow_type": "MoonMind.Run",
                    "initial_parameters": {},
                    "plan_artifact_ref": "ref-123",
                    "scheduled_for": future_time
                },
                id="test-wf-sched-cancel",
                task_queue="test-task-queue-sched",
            )
            
            await handle.execute_update("Cancel")
            
            result = await handle.result()
            assert result["status"] == "canceled"

@pytest.mark.asyncio
async def test_run_workflow_invalid_scheduled_for(mock_run_environment):
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="test-task-queue-sched",
            workflows=[MoonMindRunWorkflow],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            with pytest.raises(Exception) as excinfo:
                handle = await env.client.start_workflow(
                    MoonMindRunWorkflow.run,
                    {
                        "workflow_type": "MoonMind.Run",
                        "initial_parameters": {},
                        "plan_artifact_ref": "ref-123",
                        "scheduled_for": "not-a-valid-date"
                    },
                    id="test-wf-invalid-sched",
                    task_queue="test-task-queue-sched",
                )
                await handle.result()
            
            assert "Invalid scheduled_for format" in str(excinfo.value.cause)