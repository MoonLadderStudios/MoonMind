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


# ---- Bounded enumeration and truthful dispositions (MoonMind#3953) ----

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


async def _fake_empty_list(query):
    for _execution in ():
        yield _execution


async def test_control_empty_enumeration_reports_empty_not_succeeded(adapter):
    """An enumerated request with no eligible runs must not read as quiesced."""

    adapter._client.list_workflows = _fake_empty_list

    batch = await adapter.send_batch_pause_update(request_id="empty-request")

    assert batch.enumerated is True
    assert batch.targets == []
    assert batch.status == "empty"
    assert batch.enumeration_error is None
    assert batch.enumeration_cursor is None
    assert batch.enumeration_policy is not None
    assert "non-atomic" in batch.enumeration_policy


def _confirmed_handle(*, run_id: str, payload: dict) -> AsyncMock:
    handle = AsyncMock()
    handle.get_update_handle = Mock(
        return_value=SimpleNamespace(result=AsyncMock(return_value=None))
    )
    handle.query.return_value = {"runId": run_id, **payload}
    return handle


async def test_control_enumeration_resumes_and_deduplicates(adapter):
    """A retry keeps already-persisted targets and skips re-listed identities."""

    from moonmind.schemas.workflow_control_models import (
        WorkflowControlBatch,
        WorkflowControlTarget,
    )

    partial = WorkflowControlBatch(
        requestId="resume-enumeration", action="Pause",
        targets=[WorkflowControlTarget(
            workflowId="wf-0", runId="run-wf-0", updateId="update-0",
        )],
    )
    executions = [_FakeWorkflowExecution("wf-0"), _FakeWorkflowExecution("wf-1")]

    async def _fake_list(query):
        for ex in executions:
            yield ex

    handles = {
        "wf-0": _confirmed_handle(run_id="run-wf-0", payload={"safePoint": True}),
        "wf-1": _confirmed_handle(run_id="run-wf-1", payload={"safePoint": True}),
    }
    adapter._client.list_workflows = _fake_list
    adapter._client.get_workflow_handle = lambda workflow_id, **kwargs: handles[workflow_id]

    result = await adapter.send_batch_pause_update(batch=partial)

    assert result.enumerated is True
    assert [target.workflow_id for target in result.targets] == ["wf-0", "wf-1"]
    # The re-listed identity keeps its original stable Update ID.
    assert result.targets[0].update_id == "update-0"
    assert result.targets[1].update_id != "update-0"
    assert result.enumeration_cursor is None
    assert result.status == "succeeded"


async def test_control_enumeration_truncation_never_confirms(adapter, monkeypatch):
    """Hitting the enumeration budget leaves the request unconfirmed."""

    monkeypatch.setattr(temporal_client_module, "_WORKFLOW_CONTROL_ENUMERATION_LIMIT", 2)
    executions = [_FakeWorkflowExecution(f"wf-{i}") for i in range(3)]

    async def _fake_list(query):
        for ex in executions:
            yield ex

    adapter._client.list_workflows = _fake_list

    batch = await adapter.send_batch_pause_update(request_id="truncated-request")

    assert batch.enumerated is False
    assert batch.enumeration_error == "control_enumeration_truncated"
    assert len(batch.targets) == 2
    assert batch.enumeration_cursor == "wf-1"
    assert batch.status == "unknown"


async def test_control_visibility_failure_checkpoints_partial_targets(adapter):
    """A later-page failure keeps the partial list instead of discarding it."""

    executions = [_FakeWorkflowExecution("wf-0"), _FakeWorkflowExecution("wf-1")]

    async def _fake_failing_list(query):
        yield executions[0]
        raise RuntimeError("visibility page lost")

    adapter._client.list_workflows = _fake_failing_list

    batch = await adapter.send_batch_pause_update(request_id="failing-request")

    assert batch.enumerated is False
    assert batch.enumeration_error == "control_visibility_unavailable"
    assert [target.workflow_id for target in batch.targets] == ["wf-0"]
    assert batch.enumeration_cursor == "wf-0"
    assert batch.status == "unknown"


async def test_control_generation_mismatch_is_superseded(adapter):
    """A newer generation owns the run; the old request must not confirm."""

    from moonmind.schemas.workflow_control_models import (
        WorkflowControlBatch,
        WorkflowControlTarget,
    )

    batch = WorkflowControlBatch(
        requestId="generation-request", action="Pause", generation=4, enumerated=True,
        targets=[WorkflowControlTarget(
            workflowId="wf-gen", runId="run-gen", updateId="update-gen",
        )],
    )
    handle = _confirmed_handle(
        run_id="run-gen", payload={"controlGeneration": 5, "safePoint": True},
    )
    adapter._client.get_workflow_handle = Mock(return_value=handle)
    adapter._client.list_workflows = Mock(
        side_effect=AssertionError("Enumerated targets must not be re-listed")
    )

    result = await adapter.send_batch_pause_update(batch=batch)

    assert result.targets[0].state == "superseded"
    assert result.targets[0].reason == "control_generation_superseded"
    assert result.targets[0].run_id == "run-gen"
    assert result.status == "unknown"


async def test_control_run_mismatch_keeps_pinned_identity(adapter):
    """A successor execution sharing the workflow id never inherits the request."""

    from moonmind.schemas.workflow_control_models import (
        WorkflowControlBatch,
        WorkflowControlTarget,
    )

    batch = WorkflowControlBatch(
        requestId="successor-request", action="Pause", generation=4, enumerated=True,
        targets=[WorkflowControlTarget(
            workflowId="wf-run", runId="run-old", updateId="update-run",
        )],
    )
    handle = _confirmed_handle(
        run_id="run-new", payload={"controlGeneration": 4, "safePoint": True},
    )
    adapter._client.get_workflow_handle = Mock(return_value=handle)
    adapter._client.list_workflows = Mock(
        side_effect=AssertionError("Enumerated targets must not be re-listed")
    )

    result = await adapter.send_batch_pause_update(batch=batch)

    assert result.targets[0].state == "superseded"
    assert result.targets[0].reason == "run_superseded"
    # Pinned identity is preserved; the successor run id is not adopted.
    assert result.targets[0].run_id == "run-old"
    assert result.status == "unknown"


async def test_control_unknown_query_is_unsupported(adapter):
    """Runs without the control protocol need attention, not unknown-outage."""

    from moonmind.schemas.workflow_control_models import (
        WorkflowControlBatch,
        WorkflowControlTarget,
    )

    batch = WorkflowControlBatch(
        requestId="protocol-request", action="Pause", enumerated=True,
        targets=[WorkflowControlTarget(
            workflowId="wf-proto", runId="run-proto", updateId="update-proto",
        )],
    )
    handle = AsyncMock()
    handle.get_update_handle = Mock(
        return_value=SimpleNamespace(result=AsyncMock(return_value=None))
    )
    handle.query = AsyncMock(
        side_effect=RuntimeError("unknown query 'control_state' for workflow")
    )
    adapter._client.get_workflow_handle = Mock(return_value=handle)
    adapter._client.list_workflows = Mock(
        side_effect=AssertionError("Enumerated targets must not be re-listed")
    )

    result = await adapter.send_batch_pause_update(batch=batch)

    assert result.targets[0].state == "unsupported"
    assert result.targets[0].reason == "control_protocol_unsupported"
    assert result.status == "unknown"


async def test_control_terminated_run_is_already_terminal(adapter):
    """Positive close evidence resolves the target without claiming cleanup."""

    from moonmind.schemas.workflow_control_models import (
        WorkflowControlBatch,
        WorkflowControlTarget,
    )

    batch = WorkflowControlBatch(
        requestId="terminal-request", action="Pause", enumerated=True,
        targets=[WorkflowControlTarget(
            workflowId="wf-gone", runId="run-gone", updateId="update-gone",
        )],
    )
    handle = AsyncMock()
    handle.start_update = AsyncMock(side_effect=RuntimeError("workflow closed"))
    handle.describe = AsyncMock(
        return_value=SimpleNamespace(status=SimpleNamespace(name="TERMINATED"))
    )
    adapter._client.get_workflow_handle = Mock(return_value=handle)
    adapter._client.list_workflows = Mock(
        side_effect=AssertionError("Enumerated targets must not be re-listed")
    )

    result = await adapter.send_batch_pause_update(batch=batch)

    assert result.targets[0].state == "already_terminal"
    assert result.targets[0].reason == "workflow_already_terminal"
    assert result.status == "succeeded"


async def test_control_absent_lookup_without_evidence_stays_unknown(adapter):
    """A failed describe cannot promote a missing run to already_terminal."""

    from moonmind.schemas.workflow_control_models import (
        WorkflowControlBatch,
        WorkflowControlTarget,
    )

    batch = WorkflowControlBatch(
        requestId="absent-request", action="Pause", enumerated=True,
        targets=[WorkflowControlTarget(
            workflowId="wf-absent", runId="run-absent", updateId="update-absent",
        )],
    )
    handle = AsyncMock()
    handle.start_update = AsyncMock(side_effect=RuntimeError("unavailable"))
    handle.describe = AsyncMock(side_effect=RuntimeError("not found"))
    adapter._client.get_workflow_handle = Mock(return_value=handle)
    adapter._client.list_workflows = Mock(
        side_effect=AssertionError("Enumerated targets must not be re-listed")
    )

    result = await adapter.send_batch_pause_update(batch=batch)

    assert result.targets[0].state == "unknown"
    assert result.targets[0].reason == "update_acceptance_unavailable"
    assert result.status == "unknown"


async def test_control_terminal_dispositions_skip_reconciliation(adapter):
    """Terminal observations never re-enter Temporal fan-out on reads."""

    from moonmind.schemas.workflow_control_models import (
        WorkflowControlBatch,
        WorkflowControlTarget,
    )

    batch = WorkflowControlBatch(
        requestId="settled-request", action="Pause", enumerated=True,
        targets=[WorkflowControlTarget(
            workflowId="wf-settled", runId="run-settled",
            updateId="update-settled", state="already_terminal",
            reason="workflow_already_terminal",
        )],
    )
    adapter._client.get_workflow_handle = Mock(
        side_effect=AssertionError("Settled targets must not be re-queried")
    )
    adapter._client.list_workflows = Mock(
        side_effect=AssertionError("Enumerated targets must not be re-listed")
    )

    result = await adapter.send_batch_pause_update(batch=batch)

    assert result.targets[0].state == "already_terminal"
    assert result.status == "succeeded"


async def test_control_truncated_resume_never_grows_past_limit(adapter, monkeypatch):
    """A resumed pass caps the accumulated total instead of growing one per read."""

    from moonmind.schemas.workflow_control_models import (
        WorkflowControlBatch,
        WorkflowControlTarget,
    )

    monkeypatch.setattr(temporal_client_module, "_WORKFLOW_CONTROL_ENUMERATION_LIMIT", 2)
    partial = WorkflowControlBatch(
        requestId="resume-truncated", action="Pause",
        targets=[WorkflowControlTarget(
            workflowId="wf-0", runId="run-wf-0", updateId="update-0",
        ), WorkflowControlTarget(
            workflowId="wf-1", runId="run-wf-1", updateId="update-1",
        )],
    )
    executions = [_FakeWorkflowExecution("wf-0"), _FakeWorkflowExecution("wf-1"),
                  _FakeWorkflowExecution("wf-2")]

    async def _fake_list(query):
        for ex in executions:
            yield ex

    adapter._client.list_workflows = _fake_list

    result = await adapter.send_batch_pause_update(batch=partial)

    assert result.enumerated is False
    assert result.enumeration_error == "control_enumeration_truncated"
    assert len(result.targets) == 2
    assert [target.workflow_id for target in result.targets] == ["wf-0", "wf-1"]
    assert result.status == "unknown"


async def test_control_malformed_control_evidence_stays_retryable(adapter):
    """A blank or runId-less control_state observation never parks as superseded."""

    from moonmind.schemas.workflow_control_models import (
        WorkflowControlBatch,
        WorkflowControlTarget,
    )

    for observed in (None, "not-a-dict", {}, {"safePoint": True}):
        batch = WorkflowControlBatch(
            requestId="malformed-request", action="Pause", enumerated=True,
            targets=[WorkflowControlTarget(
                workflowId="wf-malformed", runId="run-malformed",
                updateId="update-malformed",
            )],
        )
        handle = _confirmed_handle(run_id="run-malformed", payload={})
        handle.query = AsyncMock(return_value=observed)
        adapter._client.get_workflow_handle = Mock(return_value=handle)
        adapter._client.list_workflows = Mock(
            side_effect=AssertionError("Enumerated targets must not be re-listed")
        )

        result = await adapter.send_batch_pause_update(batch=batch)

        assert result.targets[0].state == "unknown"
        assert result.targets[0].reason == "control_query_unavailable"
        assert result.status == "unknown"


async def test_control_generic_not_found_stays_retryable(adapter):
    """A generic NOT_FOUND during control_state query is not proof of no protocol."""

    from moonmind.schemas.workflow_control_models import (
        WorkflowControlBatch,
        WorkflowControlTarget,
    )

    batch = WorkflowControlBatch(
        requestId="not-found-request", action="Pause", enumerated=True,
        targets=[WorkflowControlTarget(
            workflowId="wf-vanished", runId="run-vanished",
            updateId="update-vanished",
        )],
    )
    handle = AsyncMock()
    handle.get_update_handle = Mock(
        return_value=SimpleNamespace(result=AsyncMock(return_value=None))
    )
    handle.query = AsyncMock(
        side_effect=RuntimeError("workflow execution not found")
    )
    adapter._client.get_workflow_handle = Mock(return_value=handle)
    adapter._client.list_workflows = Mock(
        side_effect=AssertionError("Enumerated targets must not be re-listed")
    )

    result = await adapter.send_batch_pause_update(batch=batch)

    assert result.targets[0].state == "unknown"
    assert result.targets[0].reason == "control_query_unavailable"
    assert result.status == "unknown"
