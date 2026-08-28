from unittest.mock import AsyncMock, MagicMock

import pytest

from api_service.db.models import (
    MoonMindWorkflowState,
    TemporalExecutionCanonicalRecord,
    TemporalExecutionOwnerType,
    TemporalWorkflowType,
)
from moonmind.workflows.temporal.service import (
    TemporalExecutionCancelUndeliverableError,
    TemporalExecutionService,
    TemporalExecutionValidationError,
)

@pytest.fixture
def mock_session():
    session = MagicMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    session.refresh = AsyncMock()
    session.execute = AsyncMock()
    session.get = AsyncMock()
    return session

@pytest.fixture
def mock_client_adapter():
    adapter = AsyncMock()
    return adapter

@pytest.fixture
def service(mock_session, mock_client_adapter, monkeypatch):
    svc = TemporalExecutionService(session=mock_session)
    # Monkeypatch the internal client adapter
    svc._client_adapter = mock_client_adapter
    return svc

@pytest.mark.asyncio
async def test_describe_execution_syncs_from_temporal(
    service, mock_session, mock_client_adapter
):
    # DOC-REQ-002: Write test for syncing execution details from Temporal
    # T004: Write test for syncing execution details from Temporal
    record = TemporalExecutionCanonicalRecord(
        workflow_id="mm:123",
        run_id="run-1",
        workflow_type=TemporalWorkflowType.USER_WORKFLOW,
        owner_type=TemporalExecutionOwnerType.USER,
        state=MoonMindWorkflowState.INITIALIZING,
        entry="run",
    )
    mock_session.get.return_value = record

    # We mock _load_source_execution since describe_execution calls it
    service._load_source_execution = AsyncMock(return_value=record)
    service._sync_projection_best_effort = AsyncMock(return_value=record)

    result = await service.describe_execution("mm:123")

    mock_client_adapter.describe_workflow.assert_called_once_with("mm:123")
    assert result == record

@pytest.mark.asyncio
async def test_list_executions_sourced_from_temporal(
    service, mock_session, mock_client_adapter
):
    # DOC-REQ-002: Write test for listing executions sourced from Temporal
    # T005: Write test for listing executions sourced from Temporal

    # Mocking session execute to return a list of records
    mock_result = MagicMock()
    record = TemporalExecutionCanonicalRecord(
        workflow_id="mm:123",
        run_id="run-1",
        workflow_type=TemporalWorkflowType.USER_WORKFLOW,
        owner_type=TemporalExecutionOwnerType.USER,
        state=MoonMindWorkflowState.INITIALIZING,
        entry="run",
    )
    mock_result.scalars().all.return_value = [record]

    # Need another execute for the count
    mock_count_result = MagicMock()
    mock_count_result.scalar_one.return_value = 1

    mock_session.execute.side_effect = [mock_result, mock_count_result]

    service._sync_projections_best_effort = AsyncMock(return_value=[record])

    res = await service.list_executions(page_size=10)

    service._sync_projections_best_effort.assert_called_once()
    assert len(res.items) == 1
    assert res.count == 1

@pytest.mark.asyncio
async def test_cancel_action_routes_to_temporal(
    service, mock_session, mock_client_adapter
):
    # DOC-REQ-001: Write test for routing cancel action to Temporal
    # T009: Write test for routing cancel action to Temporal
    record = TemporalExecutionCanonicalRecord(
        workflow_id="mm:123",
        run_id="run-1",
        workflow_type=TemporalWorkflowType.USER_WORKFLOW,
        owner_type=TemporalExecutionOwnerType.USER,
        state=MoonMindWorkflowState.EXECUTING,
        entry="run",
    )
    service._require_cancel_target_execution = AsyncMock(return_value=record)
    service._sync_projection_best_effort = AsyncMock(return_value=record)

    await service.cancel_execution(
        workflow_id="mm:123", reason="testing", graceful=True
    )

    mock_client_adapter.update_workflow.assert_not_called()
    mock_client_adapter.cancel_workflow.assert_awaited_once_with("mm:123")
    mock_client_adapter.terminate_workflow.assert_not_called()

def _describe_with_pending_workflow_task_attempt(attempt: int) -> MagicMock:
    """Build a Temporal description whose pending workflow task is on ``attempt``.

    Temporal reports attempt 1 for a healthy first attempt; a higher attempt
    means the previous workflow task failed and the server is retrying it.
    """

    pending = MagicMock()
    pending.attempt = attempt
    raw_description = MagicMock()
    raw_description.pending_workflow_task = pending
    description = MagicMock()
    description.raw_description = raw_description
    return description


def _wedged_execution_record() -> TemporalExecutionCanonicalRecord:
    return TemporalExecutionCanonicalRecord(
        workflow_id="mm:123",
        run_id="run-1",
        workflow_type=TemporalWorkflowType.USER_WORKFLOW,
        owner_type=TemporalExecutionOwnerType.USER,
        state=MoonMindWorkflowState.AWAITING_SLOT,
        entry="run",
    )


@pytest.mark.asyncio
async def test_graceful_cancel_submits_request_then_reports_it_unprocessable(
    service, mock_session, mock_client_adapter
):
    """The request must still be sent, but must not be reported as done.

    Graceful cancellation is delivered through a workflow task. An execution
    stuck retrying its workflow task (nondeterministic history, unloadable
    definition) never observes the retained request, so reporting success makes
    the operator's cancel a silent no-op. Withholding the request instead would
    be worse: a task failing now can retry successfully after a worker restart,
    and Temporal honors the retained request without the operator asking twice.
    """

    record = _wedged_execution_record()
    service._require_cancel_target_execution = AsyncMock(return_value=record)
    service._sync_projection_best_effort = AsyncMock(return_value=record)
    mock_client_adapter.describe_workflow.return_value = (
        _describe_with_pending_workflow_task_attempt(95)
    )

    with pytest.raises(TemporalExecutionCancelUndeliverableError) as excinfo:
        await service.cancel_execution(
            workflow_id="mm:123", reason=None, graceful=True
        )

    assert "Force cancel" in str(excinfo.value)
    # The operator's intent reaches Temporal and is retained there...
    mock_client_adapter.cancel_workflow.assert_awaited_once_with("mm:123")
    mock_client_adapter.terminate_workflow.assert_not_called()
    # ...but the record keeps its live state rather than a canceled state the
    # execution has not reached.
    assert record.state is MoonMindWorkflowState.AWAITING_SLOT
    mock_session.commit.assert_not_called()


@pytest.mark.asyncio
async def test_transient_temporal_cancel_failure_stays_retryable(
    service, mock_session, mock_client_adapter
):
    """A transport failure is not an undeliverable-cancel refusal.

    Callers distinguish the two by type: a conflict answer would tell clients
    the request can never succeed, when a retry may well reach Temporal.
    """

    record = _wedged_execution_record()
    service._require_cancel_target_execution = AsyncMock(return_value=record)
    service._sync_projection_best_effort = AsyncMock(return_value=record)
    mock_client_adapter.cancel_workflow.side_effect = RuntimeError("connection reset")

    with pytest.raises(TemporalExecutionValidationError) as excinfo:
        await service.cancel_execution(
            workflow_id="mm:123", reason=None, graceful=True
        )

    assert not isinstance(excinfo.value, TemporalExecutionCancelUndeliverableError)
    assert record.state is MoonMindWorkflowState.AWAITING_SLOT


@pytest.mark.asyncio
async def test_force_cancel_still_terminates_a_wedged_workflow(
    service, mock_session, mock_client_adapter
):
    """Terminate is the escape hatch: it must not depend on the workflow running."""

    record = _wedged_execution_record()
    service._require_cancel_target_execution = AsyncMock(return_value=record)
    service._sync_projection_best_effort = AsyncMock(return_value=record)
    mock_client_adapter.describe_workflow.return_value = (
        _describe_with_pending_workflow_task_attempt(95)
    )

    await service.cancel_execution(
        workflow_id="mm:123", reason=None, graceful=False
    )

    mock_client_adapter.terminate_workflow.assert_awaited_once_with(
        "mm:123", reason="Force canceled by operator."
    )
    assert record.state is MoonMindWorkflowState.FAILED


@pytest.mark.asyncio
async def test_graceful_cancel_proceeds_on_first_workflow_task_attempt(
    service, mock_session, mock_client_adapter
):
    """A healthy execution keeps the ordinary graceful cancel path."""

    record = _wedged_execution_record()
    service._require_cancel_target_execution = AsyncMock(return_value=record)
    service._sync_projection_best_effort = AsyncMock(return_value=record)
    mock_client_adapter.describe_workflow.return_value = (
        _describe_with_pending_workflow_task_attempt(1)
    )

    await service.cancel_execution(workflow_id="mm:123", reason=None, graceful=True)

    mock_client_adapter.cancel_workflow.assert_awaited_once_with("mm:123")
    assert record.state is MoonMindWorkflowState.CANCELED


@pytest.mark.asyncio
async def test_graceful_cancel_proceeds_when_deliverability_cannot_be_checked(
    service, mock_session, mock_client_adapter
):
    """The pre-flight check is not the authority for the operator's command."""

    record = _wedged_execution_record()
    service._require_cancel_target_execution = AsyncMock(return_value=record)
    service._sync_projection_best_effort = AsyncMock(return_value=record)
    mock_client_adapter.describe_workflow.side_effect = RuntimeError("temporal down")

    await service.cancel_execution(workflow_id="mm:123", reason=None, graceful=True)

    mock_client_adapter.cancel_workflow.assert_awaited_once_with("mm:123")
    assert record.state is MoonMindWorkflowState.CANCELED


@pytest.mark.asyncio
async def test_force_terminate_routes_to_temporal_terminate(
    service, mock_session, mock_client_adapter
):
    record = TemporalExecutionCanonicalRecord(
        workflow_id="mm:123",
        run_id="run-1",
        workflow_type=TemporalWorkflowType.USER_WORKFLOW,
        owner_type=TemporalExecutionOwnerType.USER,
        state=MoonMindWorkflowState.EXECUTING,
        entry="run",
    )
    service._require_cancel_target_execution = AsyncMock(return_value=record)
    service._sync_projection_best_effort = AsyncMock(return_value=record)

    await service.cancel_execution(
        workflow_id="mm:123", reason="force stop", graceful=False
    )

    mock_client_adapter.terminate_workflow.assert_called_once_with(
        "mm:123", reason="force stop"
    )
    mock_client_adapter.cancel_workflow.assert_not_called()

@pytest.mark.asyncio
async def test_action_validation_relies_on_temporal(
    service, mock_session, mock_client_adapter
):
    # DOC-REQ-003: Write test for action validation relying on Temporal
    # T010: Write test for action validation relying on Temporal
    record = TemporalExecutionCanonicalRecord(
        workflow_id="mm:123",
        run_id="run-1",
        workflow_type=TemporalWorkflowType.USER_WORKFLOW,
        owner_type=TemporalExecutionOwnerType.USER,
        state=MoonMindWorkflowState.EXECUTING,
        entry="run",
    )
    service._require_source_execution = AsyncMock(return_value=record)

    # Simulate a Temporal error, which should be mapped to TemporalExecutionValidationError
    class MockTemporalError(Exception):
        pass

    mock_client_adapter.signal_workflow.side_effect = MockTemporalError(
        "Temporal workflow is already closed"
    )

    with pytest.raises(TemporalExecutionValidationError) as exc:
        await service.signal_execution(
            workflow_id="mm:123",
            signal_name="ExternalEvent",
            payload={},
            payload_artifact_ref=None,
        )
    assert "Temporal signal failed: Temporal workflow is already closed" in str(
        exc.value
    )
