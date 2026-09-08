"""Unit tests for Temporal client worker-pause helpers (DOC-REQ-002, DOC-REQ-003)."""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock
from types import SimpleNamespace

import pytest

from moonmind.workflows.temporal.client import TemporalClientAdapter
from temporalio.client import WorkflowUpdateStage
from moonmind.workflows.temporal import client as temporal_client_module
from moonmind.workflows.temporal.data_converter import MOONMIND_TEMPORAL_DATA_CONVERTER
from moonmind.workflows.temporal.hard_switch_cutover import (
    resolve_user_workflow_start_contract,
)
from moonmind.config.settings import settings

pytestmark = [pytest.mark.asyncio]

class _FakeWorkflowExecution:
    """Stub mimicking a Temporal WorkflowExecution list result item."""

    def __init__(self, workflow_id: str) -> None:
        self.id = workflow_id
        self.run_id = "run-" + workflow_id

@pytest.fixture
def adapter() -> TemporalClientAdapter:
    mock_client = AsyncMock()
    return TemporalClientAdapter(client=mock_client)

async def test_temporal_client_uses_shared_pydantic_data_converter(monkeypatch):
    captured: dict[str, object] = {}

    async def fake_connect(address, *, namespace, data_converter, interceptors):
        captured["address"] = address
        captured["namespace"] = namespace
        captured["data_converter"] = data_converter
        captured["interceptors"] = interceptors
        return AsyncMock()

    monkeypatch.setattr(temporal_client_module.Client, "connect", fake_connect)

    await temporal_client_module.get_temporal_client("temporal:7233", "default")

    assert captured["data_converter"] is MOONMIND_TEMPORAL_DATA_CONVERTER
    assert captured["interceptors"] == []


async def test_default_adapter_refuses_implicit_live_connection_under_pytest(
    monkeypatch,
):
    monkeypatch.delenv("MOONMIND_ALLOW_LIVE_TEMPORAL_IN_TESTS", raising=False)
    adapter = TemporalClientAdapter()

    with pytest.raises(RuntimeError, match="implicit live Temporal connection"):
        await adapter.get_client()


async def test_default_adapter_allows_explicit_live_connection_opt_in(monkeypatch):
    captured: dict[str, object] = {}

    async def fake_connect(address, *, namespace, data_converter, interceptors):
        captured["address"] = address
        captured["namespace"] = namespace
        captured["data_converter"] = data_converter
        captured["interceptors"] = interceptors
        return AsyncMock()

    monkeypatch.setenv("MOONMIND_ALLOW_LIVE_TEMPORAL_IN_TESTS", "1")
    monkeypatch.setattr(temporal_client_module.Client, "connect", fake_connect)

    connected = await TemporalClientAdapter().get_client()

    assert connected is not None
    assert captured["data_converter"] is MOONMIND_TEMPORAL_DATA_CONVERTER
    assert captured["interceptors"] == []


async def test_explicit_task_queue_override_wins_over_default_topology():
    adapter = TemporalClientAdapter(client=AsyncMock())
    adapter._workflow_topology = SimpleNamespace(task_queues=["mm.workflow"])

    assert (
        adapter._get_task_queue(
            workflow_type="MoonMind.UserWorkflow",
            task_queue="mm.workflow.merge_automation",
        )
        == "mm.workflow.merge_automation"
    )
    assert adapter._get_task_queue(
        workflow_type="MoonMind.UserWorkflow"
    ) == resolve_user_workflow_start_contract(settings.temporal).task_queue

# ---- get_drain_metrics ----

async def test_get_drain_metrics_counts_running_workflows(adapter):
    """get_drain_metrics should use count_workflows and return the count."""

    adapter._client.count_workflows = AsyncMock(
        return_value=SimpleNamespace(count=5)
    )

    result = await adapter.get_drain_metrics()

    assert result["running"] == 5
    assert result["queued"] == 0
    assert result["stale_running"] == 0
    adapter._client.count_workflows.assert_awaited_once()

async def test_get_drain_metrics_with_task_queue_filter(adapter):
    """get_drain_metrics should include TaskQueue filter in Visibility query."""

    adapter._client.count_workflows = AsyncMock(
        return_value=SimpleNamespace(count=0)
    )

    await adapter.get_drain_metrics(task_queues=["mm.workflow", "mm.activity"])

    called_query = adapter._client.count_workflows.await_args.kwargs["query"]
    assert 'TaskQueue IN ("mm.workflow", "mm.activity")' in called_query

async def test_get_drain_metrics_empty_namespace(adapter):
    """With no running workflows, counts should all be zero."""

    adapter._client.count_workflows = AsyncMock(
        return_value=SimpleNamespace(count=0)
    )

    result = await adapter.get_drain_metrics()

    assert result == {"running": 0, "queued": 0, "stale_running": 0}

# ---- send_batch_pause_update / send_batch_resume_update ----

async def test_send_batch_pause_update_starts_all_with_canonical_pause(adapter):
    """Batch pause should update each running workflow with 'Pause'."""

    executions = [_FakeWorkflowExecution(f"wf-{i}") for i in range(3)]
    mock_handles = {}
    for ex in executions:
        handle = AsyncMock()
        handle.get_update_handle = Mock(return_value=SimpleNamespace(result=AsyncMock(side_effect=TimeoutError)))
        mock_handles[ex.id] = handle

    async def _fake_list(query):
        for ex in executions:
            yield ex

    adapter._client.list_workflows = _fake_list
    adapter._client.get_workflow_handle = lambda wid, **_kwargs: mock_handles[wid]

    signaled = await adapter.send_batch_pause_update()

    assert len(signaled.targets) == 3
    assert signaled.status == "pending"
    for handle in mock_handles.values():
        handle.start_update.assert_awaited_once()
        assert handle.start_update.await_args.args == ("Pause",)
        assert handle.start_update.await_args.kwargs["wait_for_stage"] is (
            WorkflowUpdateStage.ACCEPTED
        )
        handle.execute_update.assert_not_awaited()

async def test_send_batch_resume_update_starts_all_with_canonical_resume(adapter):
    """Batch resume should update each running workflow with 'Resume'."""

    executions = [_FakeWorkflowExecution("wf-1")]
    mock_handle = AsyncMock()
    mock_handle.get_update_handle = Mock(return_value=SimpleNamespace(result=AsyncMock(side_effect=TimeoutError)))

    async def _fake_list(query):
        for ex in executions:
            yield ex

    adapter._client.list_workflows = _fake_list
    adapter._client.get_workflow_handle = lambda wid, **_kwargs: mock_handle

    signaled = await adapter.send_batch_resume_update()

    assert len(signaled.targets) == 1
    assert signaled.status == "pending"
    mock_handle.start_update.assert_awaited_once()
    assert mock_handle.start_update.await_args.args == ("Resume",)
    assert mock_handle.start_update.await_args.kwargs["wait_for_stage"] is (
        WorkflowUpdateStage.ACCEPTED
    )
    mock_handle.execute_update.assert_not_awaited()

async def test_send_batch_update_skips_failed_workflows(adapter):
    """Update dispatch should continue when individual workflows fail."""

    executions = [
        _FakeWorkflowExecution("wf-ok"),
        _FakeWorkflowExecution("wf-fail"),
        _FakeWorkflowExecution("wf-ok2"),
    ]
    ok_handle = AsyncMock()
    ok_handle.get_update_handle = Mock(return_value=SimpleNamespace(result=AsyncMock(side_effect=TimeoutError)))
    fail_handle = AsyncMock()
    fail_handle.start_update = AsyncMock(side_effect=RuntimeError("gone"))
    ok2_handle = AsyncMock()
    ok2_handle.get_update_handle = Mock(return_value=SimpleNamespace(result=AsyncMock(side_effect=TimeoutError)))

    handles = {"wf-ok": ok_handle, "wf-fail": fail_handle, "wf-ok2": ok2_handle}

    async def _fake_list(query):
        for ex in executions:
            yield ex

    adapter._client.list_workflows = _fake_list
    adapter._client.get_workflow_handle = lambda wid, **_kwargs: handles[wid]

    signaled = await adapter.send_batch_pause_update()

    # 2 succeeded, 1 failed
    assert [target.state for target in signaled.targets] == ["pending", "unknown", "pending"]
    assert signaled.status == "partial"
    ok_handle.start_update.assert_awaited_once()
    assert ok_handle.start_update.await_args.args == ("Pause",)
    ok2_handle.start_update.assert_awaited_once()
    assert ok2_handle.start_update.await_args.args == ("Pause",)


@pytest.mark.parametrize("action", ["Pause", "Resume"])
@pytest.mark.parametrize("task_queues", [None, [], ["custom-workflow-queue"]])
async def test_control_enumeration_requires_complete_protocol(adapter, action, task_queues):
    """Shared queues contain operator/manifest runs without control evidence."""
    queries = []

    async def list_workflows(query):
        queries.append(query)
        # Model the server's WorkflowType filter on a mixed queue.
        for workflow_type in ("MoonMind.UserWorkflow", "MoonMind.ManifestIngest", "MoonMind.ManagedSessionReconcile"):
            if f'WorkflowType="{workflow_type}"' in query:
                yield _FakeWorkflowExecution(workflow_type)

    handle = AsyncMock()
    handle.get_update_handle = Mock(return_value=SimpleNamespace(result=AsyncMock(return_value=None)))
    handle.query.return_value = {
        "runId": "run-MoonMind.UserWorkflow", "safePoint": True, "resumed": True,
    }
    adapter._client.list_workflows = list_workflows
    adapter._client.get_workflow_handle = Mock(return_value=handle)

    batch = await adapter._send_update_to_running_workflows(
        update_name=action, task_queues=task_queues,
    )

    assert batch.status == "succeeded"
    assert [target.workflow_id for target in batch.targets] == ["MoonMind.UserWorkflow"]
    assert 'WorkflowType="MoonMind.UserWorkflow"' in queries[0]
    if task_queues is None:
        assert 'TaskQueue IN ("mm.workflow",' in queries[0]
    elif not task_queues:
        assert "TaskQueue" not in queries[0]
    else:
        assert 'TaskQueue IN ("custom-workflow-queue")' in queries[0]


async def test_control_dispatch_is_bounded_parallel_and_persistence_serialized(adapter, monkeypatch):
    import asyncio
    from moonmind.schemas.workflow_control_models import WorkflowControlBatch, WorkflowControlTarget

    monkeypatch.setattr(temporal_client_module, "_WORKFLOW_CONTROL_CONCURRENCY", 2)
    batch = WorkflowControlBatch(
        requestId="parallel", action="Pause", generation=4, enumerated=True,
        targets=[WorkflowControlTarget(workflowId=f"wf-{i}", runId=f"run-{i}", updateId=f"update-{i}") for i in range(5)],
    )
    active = peak = committing = 0
    persisted = []
    handles = {}

    async def progress(observed):
        nonlocal committing
        assert committing == 0, "An AsyncSession cannot commit concurrently"
        committing += 1
        await asyncio.sleep(0)
        persisted.append(observed)
        # Exercise the production callback's merge into the live batch while
        # other target RPCs complete. No local observation may be overwritten.
        for target, committed in zip(batch.targets, observed.targets):
            target.state, target.reason = committed.state, committed.reason
        committing -= 1

    async def start(*args, **kwargs):
        nonlocal active, peak
        assert kwargs["args"] == [{"controlGeneration": 4}]
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0)
        active -= 1

    for target in batch.targets:
        handle = AsyncMock()
        handle.start_update.side_effect = start
        handle.get_update_handle = Mock(return_value=SimpleNamespace(result=AsyncMock(return_value=None)))
        handle.query.return_value = {"runId": target.run_id, "controlGeneration": 4, "safePoint": True}
        handles[target.workflow_id] = handle
    adapter._client.list_workflows = Mock(side_effect=AssertionError("Persisted targets must not be re-enumerated"))
    adapter._client.get_workflow_handle = lambda workflow_id, **kwargs: handles[workflow_id]

    result = await adapter.send_batch_pause_update(batch=batch, on_progress=progress)

    assert peak == 2
    assert result.status == persisted[-1].status == "succeeded"
    assert len(persisted) == 10
    for index in range(len(batch.targets)):
        states = [snapshot.targets[index].state for snapshot in persisted]
        assert "accepted" in states
        confirmed = states.index("safe_point")
        assert all(state == "safe_point" for state in states[confirmed:])


async def test_control_persistence_failure_cancels_parallel_dispatch(adapter, monkeypatch):
    import asyncio
    from moonmind.schemas.workflow_control_models import WorkflowControlBatch, WorkflowControlTarget

    monkeypatch.setattr(temporal_client_module, "_WORKFLOW_CONTROL_CONCURRENCY", 2)
    batch = WorkflowControlBatch(
        requestId="commit-failure", action="Pause", enumerated=True,
        targets=[WorkflowControlTarget(workflowId=f"wf-{i}", runId=f"run-{i}", updateId=f"update-{i}") for i in range(2)],
    )
    second_started = asyncio.Event()
    second_cancelled = asyncio.Event()

    async def first(*args, **kwargs):
        await second_started.wait()

    async def second(*args, **kwargs):
        second_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            second_cancelled.set()

    async def progress(observed):
        raise RuntimeError("audit commit failed")

    handles = [AsyncMock(), AsyncMock()]
    handles[0].start_update.side_effect = first
    handles[1].start_update.side_effect = second
    adapter._client.get_workflow_handle = lambda workflow_id, **kwargs: handles[int(workflow_id[-1])]

    with pytest.raises(ExceptionGroup) as error:
        await asyncio.wait_for(adapter.send_batch_pause_update(batch=batch, on_progress=progress), timeout=2)

    assert "audit commit failed" in str(error.value.exceptions[0])
    assert second_cancelled.is_set()
    for handle in handles:
        handle.query.assert_not_awaited()


async def test_control_resumes_legacy_persisted_batch_without_generation(adapter):
    from moonmind.schemas.workflow_control_models import WorkflowControlBatch

    batch = WorkflowControlBatch.model_validate({
        "requestId": "legacy", "action": "Resume", "enumerated": True,
        "targets": [{"workflowId": "legacy-workflow", "runId": "old-run", "updateId": "old-update"}],
    })
    handle = AsyncMock()
    handle.get_update_handle = Mock(return_value=SimpleNamespace(result=AsyncMock(return_value=None)))
    handle.query.return_value = {"runId": "old-run", "resumed": True}
    adapter._client.get_workflow_handle = Mock(return_value=handle)
    adapter._client.list_workflows = Mock(side_effect=AssertionError("Existing identities are immutable"))

    result = await adapter.send_batch_resume_update(batch=batch)

    assert result.status == "succeeded"
    assert handle.start_update.await_args.kwargs["args"] == []
    assert handle.start_update.await_args.kwargs["id"] == "old-update"
    adapter._client.get_workflow_handle.assert_called_once_with("legacy-workflow", run_id="old-run")


async def test_control_empty_enumeration_reports_empty_not_succeeded(adapter):
    """An enumerated empty target list means no eligible runs, not quiescence."""

    async def _fake_list(query):
        return
        yield  # pragma: no cover - empty Visibility selection

    adapter._client.list_workflows = _fake_list

    batch = await adapter.send_batch_pause_update(request_id="empty-scope")

    assert batch.enumerated is True
    assert batch.targets == []
    assert batch.status == "empty"
    assert batch.selection_policy is not None
    assert "not an atomic snapshot" in batch.selection_policy


async def test_control_enumeration_scopes_to_user_workflow_only(adapter):
    """Enumeration selects running UserWorkflows; operator/manifest stay out.

    Operator and manifest workflows share the task queues, so queue membership
    alone cannot scope the pause. The Visibility query pins the UserWorkflow
    type, and the recorded selection policy carries that query plus the
    not-atomic disclaimer instead of claiming a system snapshot.
    """
    captured: dict[str, str] = {}

    async def _fake_list(query):
        captured["query"] = query
        return
        yield  # pragma: no cover - empty Visibility selection

    adapter._client.list_workflows = _fake_list

    batch = await adapter.send_batch_pause_update(request_id="scoped-selection")

    assert 'ExecutionStatus="Running"' in captured["query"]
    assert 'WorkflowType="MoonMind.UserWorkflow"' in captured["query"]
    assert batch.selection_policy is not None
    assert 'WorkflowType="MoonMind.UserWorkflow"' in batch.selection_policy
    assert "not an atomic snapshot" in batch.selection_policy


async def test_control_enumeration_dedupes_checkpoint_and_bounds_pages(adapter, monkeypatch):
    """Paged enumeration dedupes runs, checkpoints pages, and honors budgets."""
    from moonmind.schemas.workflow_control_models import WorkflowControlBatch
    from moonmind.workflows.temporal import client as temporal_client_module

    monkeypatch.setattr(temporal_client_module, "_WORKFLOW_CONTROL_ENUMERATION_PAGE_SIZE", 2)
    monkeypatch.setattr(temporal_client_module, "_WORKFLOW_CONTROL_ENUMERATION_BUDGET", 3)

    executions = [
        _FakeWorkflowExecution("wf-a"),
        _FakeWorkflowExecution("wf-a"),  # duplicate Visibility entry
        _FakeWorkflowExecution("wf-b"),
        _FakeWorkflowExecution("wf-c"),
        _FakeWorkflowExecution("wf-d"),  # beyond budget
    ]

    async def _fake_list(query):
        for ex in executions:
            yield ex

    adapter._client.list_workflows = _fake_list
    handle = AsyncMock()
    handle.get_update_handle = Mock(return_value=SimpleNamespace(result=AsyncMock(side_effect=TimeoutError)))
    adapter._client.get_workflow_handle = Mock(return_value=handle)

    checkpoints = []

    async def progress(observed):
        checkpoints.append(observed.model_copy(deep=True))

    batch = await adapter.send_batch_pause_update(request_id="paged", on_progress=progress)

    assert batch.enumerated is True
    assert batch.enumeration_error == "control_enumeration_budget_exceeded"
    assert [target.workflow_id for target in batch.targets] == ["wf-a", "wf-b", "wf-c"]
    # Incomplete enumeration cannot produce a fully confirmed result.
    assert batch.status != "succeeded"
    # At least one page checkpoint persisted before the budget error.
    assert any(snapshot.enumerated is False for snapshot in checkpoints)
    assert batch.enumeration_cursor is not None


async def test_control_enumeration_failure_checkpoints_partial_targets(adapter):
    """A later-page Visibility failure keeps partial targets and the cursor."""
    from moonmind.schemas.workflow_control_models import WorkflowControlBatch

    async def _fake_list(query):
        yield _FakeWorkflowExecution("wf-ok")
        raise RuntimeError("visibility page failed")

    adapter._client.list_workflows = _fake_list

    batch = await adapter.send_batch_pause_update(request_id="partial-page")

    assert batch.enumerated is False
    assert batch.enumeration_error == "control_visibility_unavailable"
    assert [target.workflow_id for target in batch.targets] == ["wf-ok"]
    assert batch.enumeration_cursor is not None
    assert batch.status == "unknown"

    # A retry resumes from the checkpointed partial targets.
    retried = await adapter.send_batch_pause_update(
        batch=batch.model_copy(deep=True),
    )
    assert ("wf-ok", "run-wf-ok") in {
        (target.workflow_id, target.run_id) for target in retried.targets
    }


async def test_control_generation_mismatch_is_superseded_not_unknown(adapter):
    """A newer command's generation must not report the old request confirmed."""

    async def _fake_list(query):
        yield _FakeWorkflowExecution("wf-1")

    handle = AsyncMock()
    handle.get_update_handle = Mock(return_value=SimpleNamespace(result=AsyncMock(return_value=None)))
    handle.query.return_value = {"runId": "run-wf-1", "controlGeneration": 9, "safePoint": True}
    adapter._client.list_workflows = _fake_list
    adapter._client.get_workflow_handle = Mock(return_value=handle)

    from moonmind.schemas.workflow_control_models import WorkflowControlBatch

    batch = await adapter.send_batch_pause_update(
        request_id="old-generation",
        batch=WorkflowControlBatch(requestId="old-generation", action="Pause", generation=4),
    )

    assert batch.targets[0].state == "superseded"
    assert batch.targets[0].reason == "control_generation_superseded"
    assert batch.status == "failed"


async def test_control_run_mismatch_is_superseded_successor_policy(adapter):
    """Continue-As-New/reset runs never inherit the pinned control request."""

    async def _fake_list(query):
        yield _FakeWorkflowExecution("wf-1")

    handle = AsyncMock()
    handle.get_update_handle = Mock(return_value=SimpleNamespace(result=AsyncMock(return_value=None)))
    # The server now runs a successor execution under the same workflow ID.
    handle.query.return_value = {"runId": "run-successor", "controlGeneration": 0, "safePoint": True}
    adapter._client.list_workflows = _fake_list
    adapter._client.get_workflow_handle = Mock(return_value=handle)

    batch = await adapter.send_batch_pause_update(request_id="successor-run")

    assert batch.targets[0].state == "superseded"
    assert batch.targets[0].reason == "control_run_superseded"


async def test_control_unsupported_query_protocol_is_explicit(adapter):
    """Workflows without the control query are unsupported, not unknown."""

    async def _fake_list(query):
        yield _FakeWorkflowExecution("wf-legacy")

    handle = AsyncMock()
    handle.get_update_handle = Mock(return_value=SimpleNamespace(result=AsyncMock(return_value=None)))

    async def _query(*args, **kwargs):
        raise RuntimeError("unknown query control_state for workflow")

    handle.query = _query
    adapter._client.list_workflows = _fake_list
    adapter._client.get_workflow_handle = Mock(return_value=handle)

    batch = await adapter.send_batch_pause_update(request_id="unsupported-protocol")

    assert batch.targets[0].state == "unsupported"
    assert batch.targets[0].reason == "control_query_unsupported"


async def test_control_already_terminal_satisfies_pause_scope(adapter):
    """A run that closed before observation satisfies the scope without claiming host cleanup."""

    async def _fake_list(query):
        yield _FakeWorkflowExecution("wf-gone")

    handle = AsyncMock()

    async def _start_update(*args, **kwargs):
        raise RuntimeError("workflow execution already completed")

    handle.start_update = _start_update
    describe = SimpleNamespace(status=SimpleNamespace(name="COMPLETED"))
    handle.describe = AsyncMock(return_value=describe)
    adapter._client.list_workflows = _fake_list
    adapter._client.get_workflow_handle = Mock(return_value=handle)

    batch = await adapter.send_batch_pause_update(request_id="terminal-run")

    assert batch.targets[0].state == "already_terminal"
    assert batch.targets[0].reason == "control_target_terminal"
    assert batch.status == "succeeded"
