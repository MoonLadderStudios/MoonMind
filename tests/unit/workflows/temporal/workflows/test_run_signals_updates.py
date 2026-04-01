import asyncio
from unittest.mock import AsyncMock

import pytest

from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker, UnsandboxedWorkflowRunner

from temporalio import workflow
from moonmind.workflows.temporal.workflows.run import MoonMindRunWorkflow


async def fake_execute_activity(activity_name, *args, **kwargs):
    if activity_name == "artifact.read":
        import json

        return json.dumps(
            {
                "plan_version": "1.0",
                "metadata": {
                    "title": "Test",
                    "created_at": "2024-01-01T00:00:00Z",
                    "registry_snapshot": {
                        "digest": "reg:sha256:123",
                        "artifact_ref": "art:sha256:456",
                    },
                },
                "policy": {"failure_mode": "FAIL_FAST", "max_concurrency": 1},
                "tools": [
                    {
                        "name": "dummy_tool",
                        "version": "1.0.0",
                        "spec_ref": "art:sha256:789",
                        "inputs": {"schema": {"type": "object", "properties": {}}},
                        "outputs": {"schema": {"type": "object", "properties": {}}},
                        "executor": {"name": "dummy"},
                    }
                ],
                "nodes": [
                    {
                        "id": "node-1",
                        "type": "generic",
                        "title": "dummy",
                        "tool": {"name": "dummy_tool", "version": "1.0.0"},
                        "input": {},
                        "dependencies": [],
                    }
                ],
                "edges": [],
            }
        ).encode("utf-8")
    elif activity_name == "plan.summarize":
        return {"summary": "Done"}
    elif activity_name == "artifact.write_stream":
        return {"artifact_id": "art-123"}
    elif activity_name == "artifact.create":
        return {"artifact_id": "art-123"}, {"url": "test"}
    return {}


@pytest.fixture
def mock_run_environment(monkeypatch):
    monkeypatch.setattr(
        MoonMindRunWorkflow, "_trusted_owner_metadata", lambda self: ("user", "user-1")
    )
    monkeypatch.setattr(workflow, "execute_activity", fake_execute_activity)

    # Mock upsert_search_attributes since test env rejects unknown ones
    monkeypatch.setattr(workflow, "upsert_search_attributes", lambda attr: None)
    monkeypatch.setattr(workflow, "upsert_memo", lambda memo: None)

    # Mock complex stages to avoid payload validation errors during signal testing
    async def fake_planning_stage(*args, **kwargs):
        return "ref-123"

    async def fake_execution_stage(*args, **kwargs):
        pass

    monkeypatch.setattr(MoonMindRunWorkflow, "_run_planning_stage", fake_planning_stage)
    monkeypatch.setattr(
        MoonMindRunWorkflow, "_run_execution_stage", fake_execution_stage
    )


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
            )
            await handle.execute_update("Pause")

            # Workflow should be paused and waiting.
            await asyncio.sleep(0.1)
            status = await handle.query("get_status")
            assert status.get("paused") is True

            await handle.execute_update("Resume")
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
            )
            await handle.execute_update("Pause")

            await handle.execute_update(
                "update_parameters",
                {"new_parameters": {"param1": "new_value", "param2": "value2"}},
            )

            await handle.execute_update("Resume")
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
            )
            await handle.execute_update("Pause")

            await handle.execute_update("Cancel")
            result = await handle.result()
            assert result["status"] == "canceled"


@pytest.mark.asyncio
async def test_resume_forwards_operator_message_to_active_jules_child(monkeypatch):
    workflow_instance = MoonMindRunWorkflow()
    workflow_instance._active_agent_child_workflow_id = "wf:child"
    workflow_instance._active_agent_id = "jules"
    workflow_instance._awaiting_external = True

    mock_handle = type("MockHandle", (), {"signal": AsyncMock()})()
    monkeypatch.setattr(
        workflow,
        "get_external_workflow_handle",
        lambda workflow_id: mock_handle,
    )
    monkeypatch.setattr(workflow_instance, "_update_search_attributes", lambda: None)
    monkeypatch.setattr(workflow_instance, "_update_memo", lambda: None)

    await workflow_instance.resume(
        {"message": "Please rename it to Provider Profiles."}
    )

    mock_handle.signal.assert_awaited_once_with(
        "operator_message",
        {"message": "Please rename it to Provider Profiles."},
    )
    assert workflow_instance._resume_requested is True


@pytest.mark.asyncio
async def test_send_message_forwards_operator_message_without_resuming(monkeypatch):
    workflow_instance = MoonMindRunWorkflow()
    workflow_instance._active_agent_child_workflow_id = "wf:child"
    workflow_instance._active_agent_id = "jules"
    workflow_instance._awaiting_external = True

    mock_handle = type("MockHandle", (), {"signal": AsyncMock()})()
    monkeypatch.setattr(
        workflow,
        "get_external_workflow_handle",
        lambda workflow_id: mock_handle,
    )
    monkeypatch.setattr(workflow_instance, "_update_search_attributes", lambda: None)
    monkeypatch.setattr(workflow_instance, "_update_memo", lambda: None)

    await workflow_instance.send_message({"message": "Please use Provider Profiles."})

    mock_handle.signal.assert_awaited_once_with(
        "operator_message",
        {"message": "Please use Provider Profiles."},
    )
    assert workflow_instance._resume_requested is False


@pytest.mark.asyncio
async def test_run_workflow_send_message_update_uses_temporal_boundary(
    mock_run_environment, monkeypatch
):
    forwarded_payloads = []

    async def fake_forward(self, payload):
        forwarded_payloads.append(payload)
        return True

    monkeypatch.setattr(
        MoonMindRunWorkflow,
        "_forward_operator_message_to_active_child",
        fake_forward,
    )

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="test-task-queue-send-message",
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
                id="test-wf-send-message",
                task_queue="test-task-queue-send-message",
            )
            await handle.execute_update("Pause")
            await handle.execute_update(
                "SendMessage", {"message": "Please use Provider Profiles."}
            )

            await asyncio.sleep(0.1)
            status = await handle.query("get_status")
            assert status.get("paused") is True
            assert forwarded_payloads == [{"message": "Please use Provider Profiles."}]

            await handle.execute_update("Cancel")
            result = await handle.result()
            assert result["status"] == "canceled"


@pytest.mark.asyncio
async def test_run_workflow_send_message_update_rejects_non_canonical_payload(
    mock_run_environment,
):
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="test-task-queue-send-message-invalid",
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
                id="test-wf-send-message-invalid",
                task_queue="test-task-queue-send-message-invalid",
            )
            await handle.execute_update("Pause")

            with pytest.raises(Exception, match="Workflow update failed"):
                await handle.execute_update(
                    "SendMessage",
                    {"clarificationResponse": "Please use Provider Profiles."},
                )

            status = await handle.query("get_status")
            assert status.get("paused") is True

            await handle.execute_update("Cancel")
            result = await handle.result()
            assert result["status"] == "canceled"


@pytest.mark.asyncio
async def test_run_workflow_send_message_update_rejects_blank_message(
    mock_run_environment,
):
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="test-task-queue-send-message-blank",
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
                id="test-wf-send-message-blank",
                task_queue="test-task-queue-send-message-blank",
            )
            await handle.execute_update("Pause")

            with pytest.raises(Exception, match="Workflow update failed"):
                await handle.execute_update("SendMessage", {"message": "   "})

            status = await handle.query("get_status")
            assert status.get("paused") is True

            await handle.execute_update("Cancel")
            result = await handle.result()
            assert result["status"] == "canceled"


def test_update_inputs_extracts_clarification_message_from_parameters_patch():
    workflow_instance = MoonMindRunWorkflow()

    message = workflow_instance._extract_clarification_message(
        {
            "parametersPatch": {
                "message": "Use the Workers page copy for now.",
            }
        }
    )

    assert message == "Use the Workers page copy for now."
