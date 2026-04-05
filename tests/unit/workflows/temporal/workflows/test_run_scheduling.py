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

    async def fake_planning_stage(*args, **kwargs):
        call_order.append(("planning", "started"))
        return "ref-123"

    async def fake_execution_stage(*args, **kwargs):
        call_order.append(("execution", "started"))

    async def fake_reconcile(self, dependency_ids):
        for workflow_id in dependency_ids:
            call_order.append(("dependency", workflow_id))
            self._record_dependency_outcome(
                prerequisite_workflow_id=workflow_id,
                terminal_state="completed",
                close_status="completed",
                resolved_at="2026-04-05T00:00:00Z",
                failure_category=None,
                message=None,
            )

    monkeypatch.setattr(
        workflow,
        "patched",
        lambda patch_id: patch_id == DEPENDENCY_GATE_PATCH,
    )
    monkeypatch.setattr(
        MoonMindRunWorkflow, "_reconcile_dependencies", fake_reconcile
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
    """Verify that Pause/Resume work while the workflow is waiting on dependencies.

    The fake reconcile leaves dependencies unresolved on the first call so the
    workflow enters the dependency-wait loop.  On the second call (triggered
    after Resume by the reconciliation timeout) the dependencies are resolved.
    We use env.sleep() to advance virtual time past DEPENDENCY_RECONCILE_INTERVAL
    and drive the workflow forward — no cross-event-loop asyncio.Event is used.
    """
    reconcile_call_count = 0

    async def fake_planning_stage(*args, **kwargs):
        return "ref-123"

    async def fake_reconcile(self, dependency_ids):
        nonlocal reconcile_call_count
        reconcile_call_count += 1
        if reconcile_call_count >= 2:
            for workflow_id in dependency_ids:
                self._record_dependency_outcome(
                    prerequisite_workflow_id=workflow_id,
                    terminal_state="completed",
                    close_status="completed",
                    resolved_at="2026-04-05T00:00:00Z",
                    failure_category=None,
                    message=None,
                )

    monkeypatch.setattr(
        workflow,
        "patched",
        lambda patch_id: patch_id == DEPENDENCY_GATE_PATCH,
    )
    monkeypatch.setattr(
        MoonMindRunWorkflow, "_reconcile_dependencies", fake_reconcile
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

            # Wait until the workflow enters the dependency-wait state.
            for _ in range(50):
                status = await handle.query("get_status")
                if status.get("state") == "waiting_on_dependencies":
                    break
                await asyncio.sleep(0.01)

            status = await handle.query("get_status")
            assert status.get("state") == "waiting_on_dependencies"

            # Pause while still waiting on dependencies.
            await handle.execute_update("Pause")

            status = await handle.query("get_status")
            assert status.get("paused") is True

            # Resume — the workflow will exit the wait loop on the next
            # reconciliation timeout when fake_reconcile resolves the deps.
            await handle.execute_update("Resume")

            # Advance virtual time past the reconciliation interval so the
            # wait_condition timeout fires and fake_reconcile is called again.
            await env.sleep(35)

            result = await handle.result()

    assert result["status"] == "success"
    assert reconcile_call_count >= 2


@pytest.mark.asyncio
async def test_run_workflow_dependency_cancel_interrupts_wait(
    mock_run_environment,
    monkeypatch,
):
    """Verify that Cancel works while the workflow is waiting on dependencies.

    fake_reconcile leaves dependencies unresolved so the workflow enters the
    wait loop.  A short env.sleep() advances virtual time just enough for the
    workflow to reach wait_condition, then we cancel it.
    """

    async def fake_reconcile(self, dependency_ids):
        # Leave dependencies unresolved — the workflow enters the wait loop.
        pass

    monkeypatch.setattr(
        workflow,
        "patched",
        lambda patch_id: patch_id == DEPENDENCY_GATE_PATCH,
    )
    monkeypatch.setattr(
        MoonMindRunWorkflow, "_reconcile_dependencies", fake_reconcile
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

            # Advance virtual time just enough for the workflow to reach the
            # wait_condition inside _wait_for_dependencies.
            await env.sleep(1)

            # Cancel should interrupt the dependency wait immediately.
            await handle.execute_update("Cancel")
            result = await handle.result()

    assert result["status"] == "canceled"


@pytest.mark.asyncio
async def test_run_workflow_dependency_gate_unpatched_skips_wait(
    mock_run_environment,
    monkeypatch,
):
    """When the dependency gate patch is not applied, the workflow should
    complete successfully even with declared dependencies — proving the
    dependency-wait step was skipped entirely."""

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

    # Workflow completed without waiting on dependencies, proving the
    # dependency gate was correctly skipped when the patch was absent.
    assert result["status"] == "success"


@pytest.mark.asyncio
async def test_run_workflow_handles_failed_dependency_with_degraded_outcome(
    mock_run_environment,
    monkeypatch,
):
    call_order: list[tuple[str, str | tuple[str, ...]]] = []

    async def fake_planning_stage(*args, **kwargs):
        call_order.append(("planning", "started"))
        return "ref-should-not-be-used"

    async def fake_execution_stage(*args, **kwargs):
        call_order.append(("execution", "started"))

    async def fake_reconcile(self, dependency_ids):
        workflow_id = dependency_ids[0]
        call_order.append(("dependency", workflow_id))
        self._record_dependency_outcome(
            prerequisite_workflow_id=workflow_id,
            terminal_state="failed",
            close_status="failed",
            resolved_at="2026-04-05T00:00:00Z",
            failure_category="dependency_failed",
            message="dependency failed",
        )

    monkeypatch.setattr(
        workflow,
        "patched",
        lambda patch_id: patch_id == DEPENDENCY_GATE_PATCH,
    )
    monkeypatch.setattr(
        MoonMindRunWorkflow, "_reconcile_dependencies", fake_reconcile
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
