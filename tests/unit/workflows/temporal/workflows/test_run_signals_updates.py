import asyncio
from datetime import timedelta
from typing import Any
import pytest

from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker, UnsandboxedWorkflowRunner
from temporalio.client import WorkflowHandle

from moonmind.workflows.temporal.workflows.run import MoonMindRunWorkflow

async def fake_execute_activity(activity_name, *args, **kwargs):
    if activity_name == "artifact.read":
        import json
        return json.dumps({
            "plan_version": "1.0",
            "metadata": {
                "title": "Test",
                "created_at": "2024-01-01T00:00:00Z",
                "registry_snapshot": {
                    "digest": "reg:sha256:123",
                    "artifact_ref": "art:sha256:456"
                }
            },
            "policy": {"failure_mode": "FAIL_FAST", "max_concurrency": 1},
            "tools": [
                {
                    "name": "dummy_tool",
                    "version": "1.0.0",
                    "spec_ref": "art:sha256:789",
                    "inputs": {
                        "schema": {"type": "object", "properties": {}}
                    },
                    "outputs": {
                        "schema": {"type": "object", "properties": {}}
                    },
                    "executor": {"name": "dummy"}
                }
            ],
            "nodes": [
                {
                    "id": "node-1",
                    "type": "generic",
                    "title": "dummy",
                    "tool": {"name": "dummy_tool", "version": "1.0.0"},
                    "input": {},
                    "dependencies": []
                }
            ],
            "edges": []
        }).encode("utf-8")
    elif activity_name == "plan.summarize":
        return {"summary": "Done"}
    elif activity_name == "artifact.write_stream":
        return {"artifact_id": "art-123"}
    elif activity_name == "artifact.create":
        return {"artifact_id": "art-123"}, {"url": "test"}
    return {}

@pytest.fixture
def mock_run_environment(monkeypatch):
    monkeypatch.setattr(MoonMindRunWorkflow, "_trusted_owner_metadata", lambda self: ("user", "user-1"))
    import moonmind.workflows.temporal.workflows.run as run_module
    monkeypatch.setattr(run_module.workflow, "execute_activity", fake_execute_activity)
    
    # Mock upsert_search_attributes since test env rejects unknown ones
    monkeypatch.setattr(run_module.workflow, "upsert_search_attributes", lambda attr: None)
    monkeypatch.setattr(run_module.workflow, "upsert_memo", lambda memo: None)
    
    # Mock complex stages to avoid payload validation errors during signal testing
    async def fake_planning_stage(*args, **kwargs): return "ref-123"
    async def fake_execution_stage(*args, **kwargs): pass
    monkeypatch.setattr(MoonMindRunWorkflow, "_run_planning_stage", fake_planning_stage)
    monkeypatch.setattr(MoonMindRunWorkflow, "_run_execution_stage", fake_execution_stage)
    
@pytest.mark.asyncio
async def test_run_workflow_pause_resume(mock_run_environment):
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="test-task-queue-signals",
            workflows=[MoonMindRunWorkflow],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            handle = await env.client.start_workflow(
                MoonMindRunWorkflow.run,
                {
                    "workflow_type": "MoonMind.Run",
                    "initial_parameters": {},
                    "plan_artifact_ref": "ref-123",
                },
                id="test-wf-pause",
                task_queue="test-task-queue-signals",
                start_signal="pause",
            )
            
            # Workflow should be paused and waiting.
            await asyncio.sleep(0.1)
            status = await handle.query("get_status")
            assert status.get("paused") is True
            
            await handle.signal("resume")
            result = await handle.result()
            assert result["status"] == "success"

@pytest.mark.asyncio
async def test_run_workflow_update_parameters(mock_run_environment):
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="test-task-queue-signals",
            workflows=[MoonMindRunWorkflow],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            handle = await env.client.start_workflow(
                MoonMindRunWorkflow.run,
                {
                    "workflow_type": "MoonMind.Run",
                    "initial_parameters": {},
                    "plan_artifact_ref": "ref-123",
                },
                id="test-wf-update",
                task_queue="test-task-queue-signals",
                start_signal="pause",
            )
            
            await handle.execute_update(
                "update_parameters",
                {"parameters": {"param1": "new_value", "param2": "value2"}}
            )
            
            await handle.signal("resume")
            result = await handle.result()
            assert result["status"] == "success"

@pytest.mark.asyncio
async def test_run_workflow_cancel_signal(mock_run_environment):
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="test-task-queue-signals",
            workflows=[MoonMindRunWorkflow],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            handle = await env.client.start_workflow(
                MoonMindRunWorkflow.run,
                {
                    "workflow_type": "MoonMind.Run",
                    "initial_parameters": {},
                    "plan_artifact_ref": "ref-123",
                },
                id="test-wf-cancel",
                task_queue="test-task-queue-signals",
                start_signal="pause",
            )
            
            await handle.signal("cancel")
            await handle.signal("resume")
            result = await handle.result()
            assert result["status"] == "canceled"

