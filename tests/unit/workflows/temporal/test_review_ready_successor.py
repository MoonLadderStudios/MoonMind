"""A successor never takes over the waiting parent's merge or issue outcome."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from temporalio.exceptions import CancelledError

from moonmind.workflows.temporal.workflows import run as run_module


def merge_parent(monkeypatch, *, enabled=True):
    parent = run_module.MoonMindRunWorkflow()
    parent._moonspec_gate_verdict = "FULLY_IMPLEMENTED"
    parent._trusted_issue_context = {"trustedSource": "moonmind.github.get_issue"}
    parent._build_merge_gate_start_payload = MagicMock(
        return_value={
            "idempotencyKey": "merge-owner",
            "mergeAutomationConfig": {"finishMode": "merge"},
        }
    )
    parent._workflow_child_task_queue = lambda: "mm.workflow.user.v2"
    parent._workflow_patch_enabled = lambda _name: False
    parent._patched_or_false_outside_workflow = lambda _name: enabled
    parent._set_state = MagicMock()
    parent._update_memo = MagicMock()
    parent._update_search_attributes = MagicMock()
    parent._record_failure_diagnostic = MagicMock()
    monkeypatch.setattr(
        run_module.workflow,
        "info",
        lambda: SimpleNamespace(workflow_id="parent", run_id="run"),
    )

    async def wait_condition(predicate, **_kwargs):
        while not predicate():
            await asyncio.sleep(0)

    monkeypatch.setattr(run_module.workflow, "wait_condition", wait_condition)
    return parent


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["merged", "failed", "canceled"])
async def test_review_ready_keeps_parent_waiting_for_its_own_terminal_outcome(
    monkeypatch, status
):
    parent = merge_parent(monkeypatch)
    child = asyncio.get_running_loop().create_future()
    started = asyncio.Event()
    monkeypatch.setattr(
        run_module.workflow, "start_child_workflow", AsyncMock(return_value=child)
    )

    async def successor(*args, **kwargs):
        assert args[0] == "recurring.review_ready_successor"
        assert (
            parent._publish_context["implementationAvailability"]["mergeWorkflowId"]
            == "merge-owner"
        )
        started.set()
        return {"status": "enqueued", "workflowId": "independent-successor"}

    monkeypatch.setattr(run_module.workflow, "execute_activity", successor)
    task = asyncio.create_task(
        parent._maybe_start_merge_gate(
            parameters={"system": {"recurrence": {"definitionId": "definition"}}},
            pull_request_url="https://github.com/org/repo/pull/1",
        )
    )
    await asyncio.wait_for(started.wait(), 1)
    assert not task.done()
    assert parent._awaiting_external
    assert "mergeAutomationResult" not in parent._publish_context
    child.set_result({"status": status})
    if status == "failed":
        with pytest.raises(ValueError, match="merge automation failed"):
            await task
    else:
        await task
    assert parent._publish_context["mergeAutomationStatus"] == status
    assert "implementationAvailability" not in parent._publish_context
    assert not parent._awaiting_external
    assert parent._cancel_requested is (status == "canceled")


@pytest.mark.asyncio
async def test_old_history_keeps_recorded_child_sequence(monkeypatch):
    parent = merge_parent(monkeypatch, enabled=False)
    execute = AsyncMock(return_value={"status": "merged"})
    start = AsyncMock()
    activity = AsyncMock()
    monkeypatch.setattr(run_module.workflow, "execute_child_workflow", execute)
    monkeypatch.setattr(run_module.workflow, "start_child_workflow", start)
    monkeypatch.setattr(run_module.workflow, "execute_activity", activity)
    await parent._maybe_start_merge_gate(
        parameters={}, pull_request_url="https://github.com/org/repo/pull/1"
    )
    execute.assert_awaited_once()
    start.assert_not_awaited()
    activity.assert_not_awaited()


@pytest.mark.asyncio
async def test_successor_response_loss_and_restart_keep_journal_identity(monkeypatch):
    parent = merge_parent(monkeypatch)
    parent._publish_context["implementationAvailability"] = {
        "state": "review_ready",
        "definitionId": "definition",
        "mergeWorkflowId": "merge-owner",
    }
    activity = AsyncMock(
        side_effect=[ConnectionError("response lost"), {"status": "enqueued"}]
    )
    monkeypatch.setattr(run_module.workflow, "execute_activity", activity)
    await parent._maybe_admit_review_ready_successor()
    restored = merge_parent(monkeypatch)
    restored._publish_context = dict(parent._publish_context)
    await restored._maybe_admit_review_ready_successor()
    await restored._maybe_admit_review_ready_successor()
    assert activity.await_count == 2
    assert activity.await_args_list[0].args[1] == activity.await_args_list[1].args[1]
    assert activity.await_args.args[1]["ordinal"] == 0


@pytest.mark.asyncio
async def test_successor_does_not_swallow_cancellation(monkeypatch):
    parent = merge_parent(monkeypatch)
    parent._publish_context["implementationAvailability"] = {
        "state": "review_ready",
        "definitionId": "definition",
    }
    monkeypatch.setattr(
        run_module.workflow, "execute_activity", AsyncMock(side_effect=CancelledError())
    )
    with pytest.raises(CancelledError):
        await parent._maybe_admit_review_ready_successor()


@pytest.mark.asyncio
async def test_confirmed_successor_can_release_another_slot_after_out_of_order_completion(
    monkeypatch,
):
    parent = merge_parent(monkeypatch)
    parent._publish_context["implementationAvailability"] = {
        "state": "review_ready",
        "definitionId": "definition",
    }
    activity = AsyncMock(
        side_effect=[
            {"status": "enqueued"},
            {"status": "skipped"},
            {"status": "enqueued"},
        ]
    )
    monkeypatch.setattr(run_module.workflow, "execute_activity", activity)
    await parent._maybe_admit_review_ready_successor()
    await parent._maybe_admit_review_ready_successor()
    assert activity.await_count == 1
    # Only the owner's five-minute observation cadence can look for another
    # slot. The adapter refuses a third parent while the successor is active;
    # if that successor finishes first, the original merge owner can fill it.
    await parent._maybe_admit_review_ready_successor(allow_next_observation=True)
    await parent._maybe_admit_review_ready_successor(allow_next_observation=True)
    assert [call.args[1]["ordinal"] for call in activity.await_args_list] == [0, 1, 2]
    assert parent._publish_context["implementationSuccessor"]["status"] == "enqueued"


@pytest.mark.asyncio
async def test_successor_activity_uses_executing_parent_identity(monkeypatch):
    from contextlib import asynccontextmanager
    from uuid import UUID

    from temporalio import activity

    from api_service.db import base
    from api_service.services import recurring_workflows_service
    from moonmind.workflows.temporal.activity_runtime import (
        TemporalIntegrationActivities,
    )

    definition = "20f53104-5635-4463-85ab-49e8a0040b58"

    @asynccontextmanager
    async def session():
        yield "isolated-session"

    start = AsyncMock(return_value={"status": "enqueued"})
    monkeypatch.setattr(base, "get_async_session_context", session)
    monkeypatch.setattr(
        recurring_workflows_service,
        "RecurringWorkflowsService",
        lambda _session: SimpleNamespace(start_review_ready_successor=start),
    )
    monkeypatch.setattr(
        activity,
        "info",
        lambda: SimpleNamespace(
            workflow_id="actual-parent", workflow_run_id="actual-run"
        ),
    )
    assert await TemporalIntegrationActivities().recurring_review_ready_successor(
        {
            "definitionId": definition,
            "workflowId": "forged",
            "runId": "forged",
            "ordinal": 2,
        }
    ) == {"status": "enqueued"}
    start.assert_awaited_once_with(
        definition_id=UUID(definition),
        workflow_id="actual-parent",
        run_id="actual-run",
        ordinal=2,
    )
