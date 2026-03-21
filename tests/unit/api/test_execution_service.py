from unittest.mock import AsyncMock, MagicMock

import pytest

from api_service.db.models import (
    MoonMindWorkflowState,
    TemporalExecutionCanonicalRecord,
    TemporalExecutionOwnerType,
    TemporalWorkflowType,
)
from moonmind.workflows.temporal.service import (
    TemporalExecutionService,
    TemporalExecutionValidationError,
)


@pytest.fixture
def mock_session():
    session = AsyncMock()
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
        workflow_type=TemporalWorkflowType.RUN,
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
        workflow_type=TemporalWorkflowType.RUN,
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
        workflow_type=TemporalWorkflowType.RUN,
        owner_type=TemporalExecutionOwnerType.USER,
        state=MoonMindWorkflowState.EXECUTING,
        entry="run",
    )
    service._require_source_execution = AsyncMock(return_value=record)
    service._sync_projection_best_effort = AsyncMock(return_value=record)

    await service.cancel_execution(
        workflow_id="mm:123", reason="testing", graceful=True
    )

    mock_client_adapter.cancel_workflow.assert_called_once_with("mm:123")
    mock_client_adapter.terminate_workflow.assert_not_called()


@pytest.mark.asyncio
async def test_force_terminate_routes_to_temporal_terminate(
    service, mock_session, mock_client_adapter
):
    record = TemporalExecutionCanonicalRecord(
        workflow_id="mm:123",
        run_id="run-1",
        workflow_type=TemporalWorkflowType.RUN,
        owner_type=TemporalExecutionOwnerType.USER,
        state=MoonMindWorkflowState.EXECUTING,
        entry="run",
    )
    service._require_source_execution = AsyncMock(return_value=record)
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
        workflow_type=TemporalWorkflowType.RUN,
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
            signal_name="Pause",
            payload={},
            payload_artifact_ref=None,
        )

    assert "Temporal signal failed: Temporal workflow is already closed" in str(
        exc.value
    )
