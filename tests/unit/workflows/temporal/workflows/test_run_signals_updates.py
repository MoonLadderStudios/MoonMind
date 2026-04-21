import asyncio
from unittest.mock import AsyncMock
from datetime import datetime, timedelta, timezone

import pytest

from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker, UnsandboxedWorkflowRunner

from temporalio import workflow
from moonmind.workflows.temporal.workflows.run import (
    DEPENDENCY_RECONCILE_INTERVAL,
    STATE_AWAITING_SLOT,
    STATE_WAITING_ON_DEPENDENCIES,
    MoonMindRunWorkflow,
)


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

    async def fake_execution_stage(self, *args, **kwargs):
        try:
            await workflow.wait_condition(
                lambda: self._paused or self._cancel_requested,
                timeout=timedelta(seconds=1),
            )
        except asyncio.TimeoutError:
            return
        while self._paused and not self._cancel_requested:
            await workflow.wait_condition(
                lambda: not self._paused or self._cancel_requested
            )

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


@pytest.mark.asyncio
async def test_wait_for_dependencies_records_dependency_metadata(monkeypatch):
    workflow_instance = MoonMindRunWorkflow()
    workflow_instance._owner_id = "owner-1"
    workflow_instance._owner_type = "user"
    memo_updates: list[dict[str, object]] = []

    async def fake_wait_condition(predicate, timeout=None):
        while not predicate():
            await asyncio.sleep(0)

    async def fake_reconcile(dependency_ids):
        for workflow_id in dependency_ids:
            workflow_instance._record_dependency_outcome(
                prerequisite_workflow_id=workflow_id,
                terminal_state="completed",
                close_status="completed",
                resolved_at="2026-04-05T00:00:00Z",
                failure_category=None,
                message=None,
            )

    monkeypatch.setattr(workflow_instance, "_reconcile_dependencies", fake_reconcile)
    monkeypatch.setattr(workflow, "wait_condition", fake_wait_condition)
    monkeypatch.setattr(workflow, "upsert_search_attributes", lambda attr: None)
    monkeypatch.setattr(workflow, "upsert_memo", lambda memo: memo_updates.append(memo))
    monkeypatch.setattr(workflow, "now", lambda: datetime.now(timezone.utc))
    workflow_info = type(
        "WorkflowInfo",
        (),
        {"namespace": "default", "workflow_id": "wf-1", "run_id": "run-1", "search_attributes": {}},
    )
    monkeypatch.setattr(workflow, "info", lambda: workflow_info())
    monkeypatch.setattr(
        workflow,
        "logger",
        type("Logger", (), {"warning": lambda *a, **k: None, "info": lambda *a, **k: None})(),
    )

    await workflow_instance._wait_for_dependencies(["dep-1", "dep-2"])

    assert workflow_instance._state == STATE_WAITING_ON_DEPENDENCIES
    assert workflow_instance._waiting_reason is None
    assert any(
        memo.get("waiting_reason") == "dependency_wait" for memo in memo_updates
    )
    assert any(
        (memo.get("dependencies") or {}).get("declaredIds") == ["dep-1", "dep-2"]
        for memo in memo_updates
    )


def test_child_state_changed_sets_provider_profile_waiting_reason(monkeypatch):
    workflow_instance = MoonMindRunWorkflow()
    monkeypatch.setattr(workflow_instance, "_update_search_attributes", lambda: None)
    monkeypatch.setattr(workflow_instance, "_update_memo", lambda: None)

    workflow_instance.child_state_changed(
        "awaiting_slot",
        "Managed provider capacity exhausted.",
    )

    assert workflow_instance._state == STATE_AWAITING_SLOT
    assert workflow_instance._summary == "Managed provider capacity exhausted."
    assert workflow_instance._waiting_reason == "provider_profile_slot"
    assert workflow_instance._attention_required is False

    workflow_instance._attention_required = True
    workflow_instance.child_state_changed("launching", "Slot acquired.")

    assert workflow_instance._waiting_reason is None
    assert workflow_instance._attention_required is False

    workflow_instance._attention_required = True
    workflow_instance.child_state_changed("running", "Agent started.")

    assert workflow_instance._waiting_reason is None
    assert workflow_instance._attention_required is False


@pytest.mark.asyncio
async def test_wait_for_dependencies_raises_dependency_specific_failure(monkeypatch):
    workflow_instance = MoonMindRunWorkflow()
    workflow_instance._owner_id = "owner-1"
    workflow_instance._owner_type = "user"

    async def fake_wait_condition(predicate, timeout=None):
        while not predicate():
            await asyncio.sleep(0)

    async def fake_reconcile(dependency_ids):
        workflow_id = dependency_ids[0]
        workflow_instance._record_dependency_outcome(
            prerequisite_workflow_id=workflow_id,
            terminal_state="failed",
            close_status="failed",
            resolved_at="2026-04-05T00:00:00Z",
            failure_category="dependency_failed",
            message="prerequisite failed",
        )

    monkeypatch.setattr(workflow_instance, "_reconcile_dependencies", fake_reconcile)
    monkeypatch.setattr(workflow, "wait_condition", fake_wait_condition)
    monkeypatch.setattr(workflow, "upsert_search_attributes", lambda attr: None)
    monkeypatch.setattr(workflow, "upsert_memo", lambda memo: None)
    monkeypatch.setattr(workflow, "now", lambda: datetime.now(timezone.utc))
    workflow_info = type(
        "WorkflowInfo",
        (),
        {"namespace": "default", "workflow_id": "wf-1", "run_id": "run-1", "search_attributes": {}},
    )
    monkeypatch.setattr(workflow, "info", lambda: workflow_info())
    monkeypatch.setattr(
        workflow,
        "logger",
        type("Logger", (), {"warning": lambda *a, **k: None, "info": lambda *a, **k: None})(),
    )

    with pytest.raises(ValueError, match="prerequisite failed"):
        await workflow_instance._wait_for_dependencies(["dep-1"])


@pytest.mark.asyncio
async def test_wait_for_dependencies_can_be_bypassed_by_operator_signal(monkeypatch):
    workflow_instance = MoonMindRunWorkflow()
    workflow_instance._owner_id = "owner-1"
    workflow_instance._owner_type = "user"
    memo_updates: list[dict[str, object]] = []

    async def fake_reconcile(dependency_ids):
        return None

    async def fake_wait_condition(predicate, timeout=None):
        workflow_instance._bypass_dependencies(
            {"payload": {"reason": "No longer needed."}}
        )
        assert predicate()

    monkeypatch.setattr(workflow_instance, "_reconcile_dependencies", fake_reconcile)
    monkeypatch.setattr(workflow, "wait_condition", fake_wait_condition)
    monkeypatch.setattr(workflow, "upsert_search_attributes", lambda attr: None)
    monkeypatch.setattr(workflow, "upsert_memo", lambda memo: memo_updates.append(memo))
    monkeypatch.setattr(workflow, "now", lambda: datetime.now(timezone.utc))
    workflow_info = type(
        "WorkflowInfo",
        (),
        {"namespace": "default", "workflow_id": "wf-1", "run_id": "run-1", "search_attributes": {}},
    )
    monkeypatch.setattr(workflow, "info", lambda: workflow_info())
    monkeypatch.setattr(
        workflow,
        "logger",
        type("Logger", (), {"warning": lambda *a, **k: None, "info": lambda *a, **k: None})(),
    )

    await workflow_instance._wait_for_dependencies(["dep-1", "dep-2"])

    assert workflow_instance._dependency_resolution == "bypassed"
    assert workflow_instance._unresolved_dependency_ids == set()
    assert workflow_instance._failed_dependency_id is None
    assert workflow_instance._dependency_outcomes() == [
        {
            "workflowId": "dep-1",
            "terminalState": "bypassed",
            "closeStatus": None,
            "resolvedAt": workflow_instance._dependency_outcomes_by_id["dep-1"]["resolvedAt"],
            "failureCategory": None,
            "message": "No longer needed.",
        },
        {
            "workflowId": "dep-2",
            "terminalState": "bypassed",
            "closeStatus": None,
            "resolvedAt": workflow_instance._dependency_outcomes_by_id["dep-2"]["resolvedAt"],
            "failureCategory": None,
            "message": "No longer needed.",
        },
    ]
    assert all(
        workflow_instance._dependency_outcomes_by_id[dependency_id]["resolvedAt"].endswith("Z")
        for dependency_id in ("dep-1", "dep-2")
    )
    assert any(
        (memo.get("dependencies") or {}).get("resolution") == "bypassed"
        for memo in memo_updates
    )


@pytest.mark.asyncio
async def test_wait_for_dependencies_reconciles_again_after_timeout(monkeypatch):
    workflow_instance = MoonMindRunWorkflow()
    workflow_instance._owner_id = "owner-1"
    workflow_instance._owner_type = "user"
    reconcile_calls: list[list[str]] = []
    wait_timeouts: list[object] = []

    async def fake_reconcile(dependency_ids):
        reconcile_calls.append(list(dependency_ids))
        if len(reconcile_calls) == 2:
            workflow_instance._record_dependency_outcome(
                prerequisite_workflow_id=dependency_ids[0],
                terminal_state="completed",
                close_status="completed",
                resolved_at="2026-04-05T00:00:00Z",
                failure_category=None,
                message=None,
            )

    async def fake_wait_condition(predicate, timeout=None):
        wait_timeouts.append(timeout)
        if len(wait_timeouts) == 1:
            raise asyncio.TimeoutError()
        while not predicate():
            await asyncio.sleep(0)

    monkeypatch.setattr(workflow_instance, "_reconcile_dependencies", fake_reconcile)
    monkeypatch.setattr(workflow, "wait_condition", fake_wait_condition)
    monkeypatch.setattr(workflow, "upsert_search_attributes", lambda attr: None)
    monkeypatch.setattr(workflow, "upsert_memo", lambda memo: None)
    monkeypatch.setattr(workflow, "now", lambda: datetime.now(timezone.utc))
    workflow_info = type(
        "WorkflowInfo",
        (),
        {"namespace": "default", "workflow_id": "wf-1", "run_id": "run-1", "search_attributes": {}},
    )
    monkeypatch.setattr(workflow, "info", lambda: workflow_info())
    monkeypatch.setattr(
        workflow,
        "logger",
        type("Logger", (), {"warning": lambda *a, **k: None, "info": lambda *a, **k: None})(),
    )

    await workflow_instance._wait_for_dependencies(["dep-1"])

    assert reconcile_calls == [["dep-1"], ["dep-1"]]
    assert wait_timeouts == [DEPENDENCY_RECONCILE_INTERVAL]
