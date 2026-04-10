"""Integration tests for signal-driven dependency resolution.

These tests exercise the real DependencyResolved signal handler path,
covering signal-driven dependency scenarios that the existing
test_run_scheduling.py tests do not reach.
"""

import asyncio

import pytest

from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker, UnsandboxedWorkflowRunner
from temporalio.client import WorkflowFailureError
from temporalio.service import RPCError

from temporalio import workflow
from moonmind.workflows.temporal.workflows.run import (
    DEPENDENCY_GATE_PATCH,
    MoonMindRunWorkflow,
)

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

async def fake_execute_activity(activity_name, *args, **kwargs):
    """Stub out all activity execution for the workflow under test."""
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
    """Provide a time-skipping environment with workflow stubs."""
    monkeypatch.setattr(
        MoonMindRunWorkflow, "_trusted_owner_metadata",
        lambda self: ("user", "user-1"),
    )
    monkeypatch.setattr(workflow, "execute_activity", fake_execute_activity)
    monkeypatch.setattr(workflow, "upsert_search_attributes", lambda attr: None)
    monkeypatch.setattr(workflow, "upsert_memo", lambda memo: None)

    async def fake_planning_stage(*args, **kwargs):
        return "ref-123"

    async def fake_execution_stage(*args, **kwargs):
        pass

    monkeypatch.setattr(
        MoonMindRunWorkflow, "_run_planning_stage", fake_planning_stage
    )
    monkeypatch.setattr(
        MoonMindRunWorkflow, "_run_execution_stage", fake_execution_stage
    )


def _make_dependency_resolved_payload(
    prerequisite_workflow_id: str,
    terminal_state: str = "completed",
    close_status: str = "completed",
    resolved_at: str | None = None,
    failure_category: str | None = None,
    message: str | None = None,
) -> dict:
    """Build a canonical DependencyResolved signal payload."""
    return {
        "prerequisiteWorkflowId": prerequisite_workflow_id,
        "terminalState": terminal_state,
        "closeStatus": close_status,
        "resolvedAt": resolved_at or "2026-04-05T00:00:00Z",
        "failureCategory": failure_category,
        "message": message,
    }


async def _wait_for_state(handle, expected_state: str, max_retries: int = 50):
    """Poll a workflow's get_status query until it reaches *expected_state*."""
    for _ in range(max_retries):
        status = await handle.query("get_status")
        if status.get("state") == expected_state:
            return status
        await asyncio.sleep(0.01)
    status = await handle.query("get_status")
    raise AssertionError(
        f"Workflow did not reach {expected_state!r}; last state was "
        f"{status.get('state')!r}"
    )


def _start_dep_workflow(
    env,
    queue: str,
    wf_id: str,
    depends_on: list[str] | None = None,
):
    """Helper: start a MoonMind.Run workflow on the given environment."""
    initial_params: dict = {}
    if depends_on:
        initial_params["task"] = {"dependsOn": depends_on}
    return env.client.start_workflow(
        MoonMindRunWorkflow.run,
        {
            "workflow_type": "MoonMind.Run",
            "initial_parameters": initial_params,
            "plan_artifact_ref": "ref-123",
        },
        id=wf_id,
        task_queue=queue,
    )


# ---------------------------------------------------------------------------
# 1. Signal-driven unblocking after startup reconciliation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_workflow_unblocked_by_real_dependency_resolved_signal(
    mock_run_environment,
    monkeypatch,
):
    """A dependent workflow enters waiting_on_dependencies and is unblocked
    only when a real DependencyResolved signal arrives for its prerequisite."""

    reconcile_called = False

    async def fake_reconcile(self, dependency_ids):
        # First call: leave dependencies unresolved so the workflow enters
        # the wait loop.
        nonlocal reconcile_called
        reconcile_called = True

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
            task_queue="test-signal-unblock",
            workflows=[MoonMindRunWorkflow],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            handle = await _start_dep_workflow(
                env, "test-signal-unblock", "wf-signal-unblock",
                depends_on=["dep-prereq-1"],
            )

            # Wait until the workflow enters dependency-wait.
            await _wait_for_state(handle, "waiting_on_dependencies")

            # Advance virtual time a little but NOT enough for reconcile
            # timeout to fire — the workflow should still be waiting.
            await env.sleep(5)

            # Send the real DependencyResolved signal.
            await handle.signal(
                "DependencyResolved",
                _make_dependency_resolved_payload("dep-prereq-1"),
            )

            # The workflow should now unblock and complete.
            result = await handle.result()

    assert result["status"] == "success"
    assert reconcile_called, "Reconcile should have been called at startup"


# ---------------------------------------------------------------------------
# 2. Duplicate signal idempotency
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_workflow_duplicate_signal_is_idempotent(
    mock_run_environment,
    monkeypatch,
):
    """Sending the same DependencyResolved signal twice must not corrupt state
    or cause duplicate processing."""

    memo_updates: list[dict] = []

    async def fake_reconcile(self, dependency_ids):
        pass  # Leave unresolved

    monkeypatch.setattr(
        workflow, "patched",
        lambda patch_id: patch_id == DEPENDENCY_GATE_PATCH,
    )
    monkeypatch.setattr(workflow, "upsert_memo", lambda memo: memo_updates.append(memo))
    monkeypatch.setattr(
        MoonMindRunWorkflow, "_reconcile_dependencies", fake_reconcile
    )

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="test-signal-dup",
            workflows=[MoonMindRunWorkflow],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            handle = await _start_dep_workflow(
                env, "test-signal-dup", "wf-signal-dup",
                depends_on=["dep-dup-1"],
            )

            await _wait_for_state(handle, "waiting_on_dependencies")

            # Send the same signal three times.
            payload = _make_dependency_resolved_payload("dep-dup-1")
            successful_signals = 0
            completed_workflow_rejections = 0
            for _ in range(3):
                try:
                    await handle.signal("DependencyResolved", payload)
                    successful_signals += 1
                except RPCError as exc:
                    # The first signal can satisfy the dependency gate fast
                    # enough that later duplicates race with workflow
                    # completion in the test server.
                    if "Completed workflow" not in str(exc):
                        raise
                    completed_workflow_rejections += 1

            result = await handle.result()

    assert successful_signals >= 1
    assert successful_signals + completed_workflow_rejections == 3
    assert result["status"] == "success"
    final_dependency_memo = next(
        memo for memo in reversed(memo_updates) if "dependencies" in memo
    )
    assert final_dependency_memo["dependencies"]["resolution"] == "satisfied"
    assert final_dependency_memo["dependencies"]["failedDependencyId"] is None
    assert final_dependency_memo["dependencies"]["outcomes"] == [
        {
            "workflowId": "dep-dup-1",
            "terminalState": "completed",
            "closeStatus": "completed",
            "resolvedAt": "2026-04-05T00:00:00+00:00",
            "failureCategory": None,
            "message": None,
        }
    ]


# ---------------------------------------------------------------------------
# 3. Stale / unexpected signal for undeclared prerequisite
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_workflow_ignores_undeclared_dependency_signal(
    mock_run_environment,
    monkeypatch,
):
    """A DependencyResolved signal for a workflow ID that was NOT declared as
    a dependency must be silently ignored."""

    async def fake_reconcile(self, dependency_ids):
        pass  # Leave unresolved

    monkeypatch.setattr(
        workflow, "patched",
        lambda patch_id: patch_id == DEPENDENCY_GATE_PATCH,
    )
    monkeypatch.setattr(
        MoonMindRunWorkflow, "_reconcile_dependencies", fake_reconcile
    )

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="test-signal-stale",
            workflows=[MoonMindRunWorkflow],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            handle = await _start_dep_workflow(
                env, "test-signal-stale", "wf-signal-stale",
                depends_on=["dep-real-1"],
            )

            await _wait_for_state(handle, "waiting_on_dependencies")

            # Send signal for an UNDECLARED prerequisite — should be ignored.
            await handle.signal(
                "DependencyResolved",
                _make_dependency_resolved_payload("dep-undeclared-ghost"),
            )

            # Give it a moment — the workflow should still be waiting.
            await env.sleep(5)
            status = await handle.query("get_status")
            assert status["state"] == "waiting_on_dependencies", (
                "Workflow should still be waiting — the ghost signal was ignored."
            )

            # Now send the REAL signal.
            await handle.signal(
                "DependencyResolved",
                _make_dependency_resolved_payload("dep-real-1"),
            )

            result = await handle.result()

    assert result["status"] == "success"


# ---------------------------------------------------------------------------
# 4. Prerequisite cancellation propagation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_workflow_fails_when_prerequisite_is_canceled(
    mock_run_environment,
    monkeypatch,
):
    """When a prerequisite reaches terminal state 'canceled', the dependent
    workflow must fail with structured dependency failure metadata."""

    async def fake_reconcile(self, dependency_ids):
        pass  # Leave unresolved

    monkeypatch.setattr(
        workflow, "patched",
        lambda patch_id: patch_id == DEPENDENCY_GATE_PATCH,
    )
    monkeypatch.setattr(
        MoonMindRunWorkflow, "_reconcile_dependencies", fake_reconcile
    )

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="test-dep-canceled",
            workflows=[MoonMindRunWorkflow],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            handle = await _start_dep_workflow(
                env, "test-dep-canceled", "wf-dep-canceled",
                depends_on=["dep-canceled-1"],
            )

            await _wait_for_state(handle, "waiting_on_dependencies")

            # Simulate prerequisite reaching "canceled" terminal state.
            await handle.signal(
                "DependencyResolved",
                _make_dependency_resolved_payload(
                    "dep-canceled-1",
                    terminal_state="canceled",
                    close_status="canceled",
                    failure_category="dependency_canceled",
                    message="Prerequisite was canceled.",
                ),
            )

            with pytest.raises(WorkflowFailureError):
                await handle.result()


# ---------------------------------------------------------------------------
# 5. Prerequisite termination propagation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_workflow_fails_when_prerequisite_is_terminated(
    mock_run_environment,
    monkeypatch,
):
    """When a prerequisite reaches terminal state 'terminated', the dependent
    workflow must fail."""

    async def fake_reconcile(self, dependency_ids):
        pass

    monkeypatch.setattr(
        workflow, "patched",
        lambda patch_id: patch_id == DEPENDENCY_GATE_PATCH,
    )
    monkeypatch.setattr(
        MoonMindRunWorkflow, "_reconcile_dependencies", fake_reconcile
    )

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="test-dep-terminated",
            workflows=[MoonMindRunWorkflow],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            handle = await _start_dep_workflow(
                env, "test-dep-terminated", "wf-dep-terminated",
                depends_on=["dep-terminated-1"],
            )

            await _wait_for_state(handle, "waiting_on_dependencies")

            await handle.signal(
                "DependencyResolved",
                _make_dependency_resolved_payload(
                    "dep-terminated-1",
                    terminal_state="terminated",
                    close_status="terminated",
                    failure_category="prerequisite_terminated",
                    message="Prerequisite was terminated.",
                ),
            )

            with pytest.raises(WorkflowFailureError):
                await handle.result()


# ---------------------------------------------------------------------------
# 6. Prerequisite timed_out propagation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_workflow_fails_when_prerequisite_timed_out(
    mock_run_environment,
    monkeypatch,
):
    """When a prerequisite reaches terminal state 'timed_out', the dependent
    workflow must fail."""

    async def fake_reconcile(self, dependency_ids):
        pass

    monkeypatch.setattr(
        workflow, "patched",
        lambda patch_id: patch_id == DEPENDENCY_GATE_PATCH,
    )
    monkeypatch.setattr(
        MoonMindRunWorkflow, "_reconcile_dependencies", fake_reconcile
    )

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="test-dep-timedout",
            workflows=[MoonMindRunWorkflow],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            handle = await _start_dep_workflow(
                env, "test-dep-timedout", "wf-dep-timedout",
                depends_on=["dep-timedout-1"],
            )

            await _wait_for_state(handle, "waiting_on_dependencies")

            await handle.signal(
                "DependencyResolved",
                _make_dependency_resolved_payload(
                    "dep-timedout-1",
                    terminal_state="timed_out",
                    close_status="timed_out",
                    failure_category="prerequisite_timed_out",
                    message="Prerequisite timed out.",
                ),
            )

            with pytest.raises(WorkflowFailureError):
                await handle.result()


# ---------------------------------------------------------------------------
# 7. Chained dependencies (A → B → C)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_workflow_chained_dependencies(
    mock_run_environment,
    monkeypatch,
):
    """Three workflows in a chain: C depends on B, B depends on A.
    When A completes, B unblocks. When B completes, C unblocks."""

    async def fake_reconcile(self, dependency_ids):
        pass  # Leave all unresolved — signals will drive resolution.

    monkeypatch.setattr(
        workflow, "patched",
        lambda patch_id: patch_id == DEPENDENCY_GATE_PATCH,
    )
    monkeypatch.setattr(
        MoonMindRunWorkflow, "_reconcile_dependencies", fake_reconcile
    )

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="test-chain",
            workflows=[MoonMindRunWorkflow],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            # Start B (depends on A) and C (depends on B).
            handle_b = await _start_dep_workflow(
                env, "test-chain", "wf-chain-b",
                depends_on=["wf-chain-a"],
            )
            handle_c = await _start_dep_workflow(
                env, "test-chain", "wf-chain-c",
                depends_on=["wf-chain-b"],
            )

            await _wait_for_state(handle_b, "waiting_on_dependencies")
            await _wait_for_state(handle_c, "waiting_on_dependencies")

            # Resolve A → B: signal B that its dependency A is done.
            await handle_b.signal(
                "DependencyResolved",
                _make_dependency_resolved_payload("wf-chain-a"),
            )

            # B should complete.
            result_b = await handle_b.result()
            assert result_b["status"] == "success"

            # Now signal C that B is done.
            await handle_c.signal(
                "DependencyResolved",
                _make_dependency_resolved_payload("wf-chain-b"),
            )

            result_c = await handle_c.result()
            assert result_c["status"] == "success"


# ---------------------------------------------------------------------------
# 8. Multiple prerequisites — fan-in via signals
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_workflow_multi_dep_fan_in_via_signals(
    mock_run_environment,
    monkeypatch,
):
    """A dependent workflow with two prerequisites waits until BOTH receive
    DependencyResolved signals."""

    async def fake_reconcile(self, dependency_ids):
        pass

    monkeypatch.setattr(
        workflow, "patched",
        lambda patch_id: patch_id == DEPENDENCY_GATE_PATCH,
    )
    monkeypatch.setattr(
        MoonMindRunWorkflow, "_reconcile_dependencies", fake_reconcile
    )

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="test-fanin",
            workflows=[MoonMindRunWorkflow],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            handle = await _start_dep_workflow(
                env, "test-fanin", "wf-fanin",
                depends_on=["dep-fanin-1", "dep-fanin-2"],
            )

            await _wait_for_state(handle, "waiting_on_dependencies")

            # Signal only the first — should still be waiting.
            await handle.signal(
                "DependencyResolved",
                _make_dependency_resolved_payload("dep-fanin-1"),
            )
            await env.sleep(5)
            status = await handle.query("get_status")
            assert status["state"] == "waiting_on_dependencies"

            # Signal the second — now it should unblock.
            await handle.signal(
                "DependencyResolved",
                _make_dependency_resolved_payload("dep-fanin-2"),
            )

            result = await handle.result()
            assert result["status"] == "success"


# ---------------------------------------------------------------------------
# 9. Dependent pause → prerequisite failure → immediate fail on resume
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_workflow_pause_then_prerequisite_failure(
    mock_run_environment,
    monkeypatch,
):
    """While the dependent run is paused during dependency wait, a
    prerequisite fails. On resume, the workflow must fail immediately."""

    async def fake_reconcile(self, dependency_ids):
        pass

    monkeypatch.setattr(
        workflow, "patched",
        lambda patch_id: patch_id == DEPENDENCY_GATE_PATCH,
    )
    monkeypatch.setattr(
        MoonMindRunWorkflow, "_reconcile_dependencies", fake_reconcile
    )

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="test-pause-fail",
            workflows=[MoonMindRunWorkflow],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            handle = await _start_dep_workflow(
                env, "test-pause-fail", "wf-pause-fail",
                depends_on=["dep-pause-fail-1"],
            )

            await _wait_for_state(handle, "waiting_on_dependencies")

            # Pause the workflow.
            await handle.execute_update("Pause")
            status = await handle.query("get_status")
            assert status.get("paused") is True

            # While paused, the prerequisite fails.
            await handle.signal(
                "DependencyResolved",
                _make_dependency_resolved_payload(
                    "dep-pause-fail-1",
                    terminal_state="failed",
                    close_status="failed",
                    failure_category="dependency_failed",
                    message="Prerequisite failed while dependent was paused.",
                ),
            )

            # The current workflow logic fails immediately on dependency
            # failure, even while paused, so no Resume update is required.

            with pytest.raises(WorkflowFailureError):
                await handle.result()


# ---------------------------------------------------------------------------
# 10. Dependent pause → prerequisite succeeds → resume completes
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_workflow_pause_then_prerequisite_success(
    mock_run_environment,
    monkeypatch,
):
    """While the dependent run is paused during dependency wait, a
    prerequisite completes. On resume, the workflow should proceed."""

    async def fake_reconcile(self, dependency_ids):
        pass

    monkeypatch.setattr(
        workflow, "patched",
        lambda patch_id: patch_id == DEPENDENCY_GATE_PATCH,
    )
    monkeypatch.setattr(
        MoonMindRunWorkflow, "_reconcile_dependencies", fake_reconcile
    )

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="test-pause-ok",
            workflows=[MoonMindRunWorkflow],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            handle = await _start_dep_workflow(
                env, "test-pause-ok", "wf-pause-ok",
                depends_on=["dep-pause-ok-1"],
            )

            await _wait_for_state(handle, "waiting_on_dependencies")

            await handle.execute_update("Pause")

            # Signal success while paused.
            await handle.signal(
                "DependencyResolved",
                _make_dependency_resolved_payload(
                    "dep-pause-ok-1",
                ),
            )

            await handle.execute_update("Resume")

            result = await handle.result()
            assert result["status"] == "success"
