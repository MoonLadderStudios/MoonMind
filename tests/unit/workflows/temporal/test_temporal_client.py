"""Unit tests for Temporal client worker-pause helpers (DOC-REQ-002, DOC-REQ-003)."""

from __future__ import annotations

from unittest.mock import AsyncMock
from types import SimpleNamespace

import pytest

from moonmind.workflows.temporal.client import TemporalClientAdapter
from moonmind.workflows.temporal import client as temporal_client_module
from moonmind.workflows.temporal.data_converter import MOONMIND_TEMPORAL_DATA_CONVERTER

pytestmark = [pytest.mark.asyncio]

class _FakeWorkflowExecution:
    """Stub mimicking a Temporal WorkflowExecution list result item."""

    def __init__(self, workflow_id: str) -> None:
        self.id = workflow_id

@pytest.fixture
def adapter() -> TemporalClientAdapter:
    mock_client = AsyncMock()
    return TemporalClientAdapter(client=mock_client)

async def test_temporal_client_uses_shared_pydantic_data_converter(monkeypatch):
    captured: dict[str, object] = {}

    async def fake_connect(address, *, namespace, data_converter):
        captured["address"] = address
        captured["namespace"] = namespace
        captured["data_converter"] = data_converter
        return AsyncMock()

    monkeypatch.setattr(temporal_client_module.Client, "connect", fake_connect)

    await temporal_client_module.get_temporal_client("temporal:7233", "default")

    assert captured["data_converter"] is MOONMIND_TEMPORAL_DATA_CONVERTER

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

# ---- send_batch_pause_signal / send_batch_resume_signal ----

async def test_send_batch_pause_signal_sends_to_all(adapter):
    """Batch pause signal should signal each running workflow with 'pause'."""

    executions = [_FakeWorkflowExecution(f"wf-{i}") for i in range(3)]
    mock_handles = {}
    for ex in executions:
        handle = AsyncMock()
        mock_handles[ex.id] = handle

    async def _fake_list(query):
        for ex in executions:
            yield ex

    adapter._client.list_workflows = _fake_list
    adapter._client.get_workflow_handle = lambda wid: mock_handles[wid]

    signaled = await adapter.send_batch_pause_signal()

    assert signaled == 3
    for handle in mock_handles.values():
        handle.signal.assert_awaited_once_with("pause")

async def test_send_batch_resume_signal_sends_to_all(adapter):
    """Batch resume signal should signal each running workflow with 'resume'."""

    executions = [_FakeWorkflowExecution("wf-1")]
    mock_handle = AsyncMock()

    async def _fake_list(query):
        for ex in executions:
            yield ex

    adapter._client.list_workflows = _fake_list
    adapter._client.get_workflow_handle = lambda wid: mock_handle

    signaled = await adapter.send_batch_resume_signal()

    assert signaled == 1
    mock_handle.signal.assert_awaited_once_with("resume")

async def test_send_batch_signal_skips_failed_workflows(adapter):
    """Signal dispatch should continue when individual workflows fail."""

    executions = [
        _FakeWorkflowExecution("wf-ok"),
        _FakeWorkflowExecution("wf-fail"),
        _FakeWorkflowExecution("wf-ok2"),
    ]
    ok_handle = AsyncMock()
    fail_handle = AsyncMock()
    fail_handle.signal = AsyncMock(side_effect=RuntimeError("gone"))
    ok2_handle = AsyncMock()

    handles = {"wf-ok": ok_handle, "wf-fail": fail_handle, "wf-ok2": ok2_handle}

    async def _fake_list(query):
        for ex in executions:
            yield ex

    adapter._client.list_workflows = _fake_list
    adapter._client.get_workflow_handle = lambda wid: handles[wid]

    signaled = await adapter.send_batch_pause_signal()

    # 2 succeeded, 1 failed
    assert signaled == 2
    ok_handle.signal.assert_awaited_once_with("pause")
    ok2_handle.signal.assert_awaited_once_with("pause")
