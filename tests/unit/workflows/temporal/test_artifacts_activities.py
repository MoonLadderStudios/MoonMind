import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from moonmind.schemas.temporal_activity_models import ArtifactReadInput, ArtifactWriteCompleteInput
from moonmind.workflows.temporal.artifacts import TemporalArtifactActivities

@pytest.fixture
def mock_service():
    service = AsyncMock()
    # Mock read
    mock_artifact = AsyncMock()
    service.read.return_value = (mock_artifact, b"test payload")
    
    # Mock write_complete
    service.write_complete.return_value = mock_artifact
    return service

@pytest.fixture
def activities(mock_service):
    return TemporalArtifactActivities(mock_service)

@pytest.fixture
def patch_build_artifact_ref():
    with patch("moonmind.workflows.temporal.artifacts.build_artifact_ref") as mock_build:
        mock_build.return_value = {"artifact_id": "test-id"}
        yield mock_build

@pytest.mark.asyncio
async def test_artifact_read_pydantic_model(activities, mock_service):
    request = ArtifactReadInput(
        artifact_ref="test-ref",
        principal="test-principal"
    )
    payload = await activities.artifact_read(request)
    assert payload == b"test payload"
    mock_service.read.assert_called_once_with(artifact_id="test-ref", principal="test-principal")

@pytest.mark.asyncio
async def test_artifact_read_legacy_dict_validation_path(activities, mock_service):
    request = {
        "artifact_ref": "test-ref",
        "principal": "test-principal"
    }
    payload = await activities.artifact_read(request)
    assert payload == b"test payload"
    mock_service.read.assert_called_once_with(artifact_id="test-ref", principal="test-principal")

@pytest.mark.asyncio
async def test_artifact_write_complete_pydantic_model(activities, mock_service, patch_build_artifact_ref):
    request = ArtifactWriteCompleteInput(
        artifact_id="test-id",
        payload=b"test payload",
        principal="test-principal",
        content_type="text/plain"
    )
    await activities.artifact_write_complete(request)
    mock_service.write_complete.assert_called_once_with(
        artifact_id="test-id",
        principal="test-principal",
        payload=b"test payload",
        content_type="text/plain"
    )

@pytest.mark.asyncio
async def test_artifact_write_complete_legacy_dict(activities, mock_service, patch_build_artifact_ref):
    request = {
        "artifact_id": "test-id",
        "payload": "dGVzdCBwYXlsb2Fk", # base64 for "test payload"
        "principal": "test-principal",
        "content_type": "text/plain"
    }
    await activities.artifact_write_complete(request)
    mock_service.write_complete.assert_called_once_with(
        artifact_id="test-id",
        principal="test-principal",
        payload=b"test payload",
        content_type="text/plain"
    )

@pytest.mark.asyncio
async def test_artifact_write_complete_payload_roundtrip_legacy_list_ints(activities, mock_service, patch_build_artifact_ref):
    request = {
        "artifact_id": "test-id",
        "payload": list(b"test payload"),
        "principal": "test-principal",
        "content_type": "text/plain"
    }
    await activities.artifact_write_complete(request)
    mock_service.write_complete.assert_called_once_with(
        artifact_id="test-id",
        principal="test-principal",
        payload=b"test payload",
        content_type="text/plain"
    )

@pytest.mark.asyncio
async def test_execution_record_terminal_state_indexes_run_digest_best_effort(
    activities,
    monkeypatch,
):
    """MM-762: terminal-state activity triggers Plane B run digest writeback."""

    import api_service.db.base as db_base
    import moonmind.workflows.temporal.service as temporal_service

    record = SimpleNamespace(
        workflow_id="mm:run:123",
        run_id="temporal-run-1",
        state="completed",
        close_status="completed",
    )
    service_calls: list[dict[str, object]] = []
    digest_calls: list[str] = []

    class _SessionContext:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class _TemporalExecutionService:
        def __init__(self, session):
            self.session = session

        async def record_terminal_state(self, **kwargs):
            service_calls.append(dict(kwargs))
            return record

    async def _fake_digest_writeback(target):
        digest_calls.append(target.workflow_id)

    monkeypatch.setattr(db_base, "get_async_session_context", lambda: _SessionContext())
    monkeypatch.setattr(
        temporal_service,
        "TemporalExecutionService",
        _TemporalExecutionService,
    )
    monkeypatch.setattr(
        activities,
        "_write_run_digest_best_effort",
        _fake_digest_writeback,
    )

    result = await activities.execution_record_terminal_state(
        {
            "workflowId": "mm:run:123",
            "state": "completed",
            "closeStatus": "completed",
            "summary": "Workflow completed successfully",
        }
    )

    assert service_calls == [
        {
            "workflow_id": "mm:run:123",
            "state": "completed",
            "close_status": "completed",
            "summary": "Workflow completed successfully",
            "error_category": None,
        }
    ]
    assert digest_calls == ["mm:run:123"]
    assert result == {
        "workflowId": "mm:run:123",
        "state": "completed",
        "closeStatus": "completed",
    }

@pytest.mark.asyncio
async def test_artifact_publish_report_bundle_delegates_to_service(
    activities, mock_service
):
    """MM-461: Activity facade should expose report bundle publication."""

    expected = {
        "report_bundle_v": 1,
        "primary_report_ref": {"artifact_ref_v": 1, "artifact_id": "art_primary"},
        "evidence_refs": [],
        "report_type": "unit_test_report",
        "report_scope": "final",
    }
    mock_service.publish_report_bundle.return_value = expected

    request = {
        "principal": "workflow-producer",
        "namespace": "moonmind",
        "workflow_id": "wf-report",
        "run_id": "run-report",
        "report_type": "unit_test_report",
        "report_scope": "final",
        "primary": {"payload": "# Final report", "content_type": "text/markdown"},
    }

    result = await activities.artifact_publish_report_bundle(request)

    assert result == expected
    mock_service.publish_report_bundle.assert_awaited_once_with(**request)
