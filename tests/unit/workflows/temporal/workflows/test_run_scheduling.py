from datetime import datetime, timedelta, timezone
import asyncio

import pytest

from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker, UnsandboxedWorkflowRunner

from temporalio import workflow
from moonmind.workflows.temporal.workflows.run import (
    DEPENDENCY_GATE_PATCH,
    MoonMindRunWorkflow,
)

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
            # Wait a tiny bit of virtual time so the workflow can enter the wait_condition
            await env.sleep(1)
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


@pytest.mark.asyncio
async def test_run_workflow_waits_on_dependencies_before_planning(
    mock_run_environment,
    monkeypatch,
):
    call_order: list[tuple[str, str | tuple[str, ...]]] = []

    class _ImmediateHandle:
        def __init__(self, workflow_id: str) -> None:
            self._workflow_id = workflow_id

        async def result(self) -> None:
            call_order.append(("dependency", self._workflow_id))

    async def fake_planning_stage(*args, **kwargs):
        call_order.append(("planning", "started"))
        return "ref-123"

    async def fake_execution_stage(*args, **kwargs):
        call_order.append(("execution", "started"))

    monkeypatch.setattr(
        workflow,
        "get_external_workflow_handle",
        lambda workflow_id: _ImmediateHandle(workflow_id),
    )
    monkeypatch.setattr(
        workflow,
        "patched",
        lambda patch_id: patch_id == DEPENDENCY_GATE_PATCH,
    )
    monkeypatch.setattr(MoonMindRunWorkflow, "_run_planning_stage", fake_planning_stage)
    monkeypatch.setattr(MoonMindRunWorkflow, "_run_execution_stage", fake_execution_stage)

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="test-task-queue-dep-order",
            workflows=[MoonMindRunWorkflow],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            handle = await env.client.start_workflow(
                MoonMindRunWorkflow.run,
                {
                    "workflow_type": "MoonMind.Run",
                    "initial_parameters": {"task": {"dependsOn": ["dep-1", "dep-2"]}},
                    "plan_artifact_ref": "ref-123",
                },
                id="test-wf-dep-order",
                task_queue="test-task-queue-dep-order",
            )

            result = await handle.result()

    assert result["status"] == "success"
    dependency_indexes = [
        idx for idx, call in enumerate(call_order) if call[0] == "dependency"
    ]
    planning_index = next(
        idx for idx, call in enumerate(call_order) if call[0] == "planning"
    )
    assert dependency_indexes
    assert max(dependency_indexes) < planning_index


@pytest.mark.asyncio
async def test_run_workflow_dependency_pause_gate_blocks_planning_until_resume(
    mock_run_environment,
    monkeypatch,
):
    dependency_released = asyncio.Event()
    planning_started = asyncio.Event()

    class _BlockingHandle:
        async def result(self) -> None:
            await dependency_released.wait()

    async def fake_planning_stage(*args, **kwargs):
        planning_started.set()
        return "ref-123"

    monkeypatch.setattr(
        workflow,
        "get_external_workflow_handle",
        lambda workflow_id: _BlockingHandle(),
    )
    monkeypatch.setattr(
        workflow,
        "patched",
        lambda patch_id: patch_id == DEPENDENCY_GATE_PATCH,
    )
    monkeypatch.setattr(MoonMindRunWorkflow, "_run_planning_stage", fake_planning_stage)

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="test-task-queue-dep-pause",
            workflows=[MoonMindRunWorkflow],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            handle = await env.client.start_workflow(
                MoonMindRunWorkflow.run,
                {
                    "workflow_type": "MoonMind.Run",
                    "initial_parameters": {"task": {"dependsOn": ["dep-1"]}},
                    "plan_artifact_ref": "ref-123",
                },
                id="test-wf-dep-pause",
                task_queue="test-task-queue-dep-pause",
            )

            for _ in range(50):
                status = await handle.query("get_status")
                if status.get("state") == "waiting_on_dependencies":
                    break
                await asyncio.sleep(0.01)

            await handle.execute_update("Pause")
            dependency_released.set()

            for _ in range(50):
                status = await handle.query("get_status")
                if status.get("paused") is True:
                    break
                await asyncio.sleep(0.01)

            status = await handle.query("get_status")
            assert status.get("paused") is True
            assert planning_started.is_set() is False

            await handle.execute_update("Resume")
            result = await handle.result()

    assert result["status"] == "success"
    assert planning_started.is_set() is True


@pytest.mark.asyncio
async def test_run_workflow_dependency_cancel_interrupts_wait(
    mock_run_environment,
    monkeypatch,
):
    dependency_started = asyncio.Event()
    keep_waiting = asyncio.Event()

    class _BlockingHandle:
        async def result(self) -> None:
            dependency_started.set()
            await keep_waiting.wait()

    monkeypatch.setattr(
        workflow,
        "get_external_workflow_handle",
        lambda workflow_id: _BlockingHandle(),
    )
    monkeypatch.setattr(
        workflow,
        "patched",
        lambda patch_id: patch_id == DEPENDENCY_GATE_PATCH,
    )

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="test-task-queue-dep-cancel",
            workflows=[MoonMindRunWorkflow],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            handle = await env.client.start_workflow(
                MoonMindRunWorkflow.run,
                {
                    "workflow_type": "MoonMind.Run",
                    "initial_parameters": {"task": {"dependsOn": ["dep-1"]}},
                    "plan_artifact_ref": "ref-123",
                },
                id="test-wf-dep-cancel",
                task_queue="test-task-queue-dep-cancel",
            )

            await asyncio.wait_for(dependency_started.wait(), timeout=5)
            await handle.execute_update("Cancel")
            result = await handle.result()

    assert result["status"] == "canceled"


@pytest.mark.asyncio
async def test_run_workflow_dependency_gate_unpatched_skips_wait(
    mock_run_environment,
    monkeypatch,
):
    handle_calls: list[str] = []

    monkeypatch.setattr(
        workflow,
        "get_external_workflow_handle",
        lambda workflow_id: handle_calls.append(workflow_id),
    )
    monkeypatch.setattr(workflow, "patched", lambda _patch_id: False)

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="test-task-queue-dep-unpatched",
            workflows=[MoonMindRunWorkflow],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            handle = await env.client.start_workflow(
                MoonMindRunWorkflow.run,
                {
                    "workflow_type": "MoonMind.Run",
                    "initial_parameters": {"task": {"dependsOn": ["dep-1"]}},
                    "plan_artifact_ref": "ref-123",
                },
                id="test-wf-dep-unpatched",
                task_queue="test-task-queue-dep-unpatched",
            )

            result = await handle.result()

    assert result["status"] == "success"
    assert handle_calls == []


@pytest.mark.asyncio
async def test_run_workflow_handles_failed_dependency_with_degraded_outcome(
    mock_run_environment,
    monkeypatch,
):
    call_order: list[tuple[str, str | tuple[str, ...]]] = []

    class _FailingHandle:
        def __init__(self, workflow_id: str) -> None:
            self._workflow_id = workflow_id

        async def result(self) -> None:
            call_order.append(("dependency", self._workflow_id))
            raise RuntimeError("dependency failed")

    async def fake_planning_stage(*args, **kwargs):
        call_order.append(("planning", "started"))
        return "ref-should-not-be-used"

    async def fake_execution_stage(*args, **kwargs):
        call_order.append(("execution", "started"))

    monkeypatch.setattr(
        workflow,
        "get_external_workflow_handle",
        lambda workflow_id: _FailingHandle(workflow_id),
    )
    monkeypatch.setattr(
        workflow,
        "patched",
        lambda patch_id: patch_id == DEPENDENCY_GATE_PATCH,
    )
    monkeypatch.setattr(MoonMindRunWorkflow, "_run_planning_stage", fake_planning_stage)
    monkeypatch.setattr(MoonMindRunWorkflow, "_run_execution_stage", fake_execution_stage)

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="test-task-queue-dep-failure",
            workflows=[MoonMindRunWorkflow],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            handle = await env.client.start_workflow(
                MoonMindRunWorkflow.run,
                {
                    "workflow_type": "MoonMind.Run",
                    "initial_parameters": {"task": {"dependsOn": ["dep-1"]}},
                    "plan_artifact_ref": "ref-123",
                },
                id="test-wf-dep-failure",
                task_queue="test-task-queue-dep-failure",
            )

            from temporalio.client import WorkflowFailureError
            try:
                await handle.result()
                assert False, "Workflow should have failed"
            except WorkflowFailureError as exc:
                assert "dependency failed" in str(exc.cause)

    dependency_calls = [c for c in call_order if c[0] == "dependency"]
    planning_calls = [c for c in call_order if c[0] == "planning"]
    assert dependency_calls
    assert not planning_calls
