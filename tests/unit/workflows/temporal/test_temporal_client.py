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
