import pytest
from unittest.mock import AsyncMock, patch

from moonmind.schemas.temporal_activity_models import ArtifactReadInput, ArtifactWriteCompleteInput
from moonmind.workflows.temporal.artifacts import TemporalArtifactActivities, TemporalArtifactValidationError

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
async def test_artifact_read_fallback_logic(activities, mock_service):
    # Pass a dict that fails validation (e.g. missing principal)
    request = {
        "artifact_ref": "test-ref",
    }
    # Provide principal via kwargs to satisfy the fallback and requirement
    payload = await activities.artifact_read(request, principal="test-principal")
    assert payload == b"test payload"
    mock_service.read.assert_called_once_with(artifact_id="test-ref", principal="test-principal")

@pytest.mark.asyncio
async def test_artifact_read_kwargs_path(activities, mock_service):
    payload = await activities.artifact_read(artifact_ref="test-ref", principal="test-principal")
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
async def test_artifact_write_complete_fallback_logic(activities, mock_service, patch_build_artifact_ref):
    # A dict that fails validation (e.g., payload is un-decodable object or just missing from dict but provided via kwargs)
    request = {
        "artifact_id": "test-id",
    }
    await activities.artifact_write_complete(request, principal="test-principal", payload=b"test payload", content_type="text/plain")
    mock_service.write_complete.assert_called_once_with(
        artifact_id="test-id",
        principal="test-principal",
        payload=b"test payload",
        content_type="text/plain"
    )

@pytest.mark.asyncio
async def test_artifact_write_complete_kwargs_path(activities, mock_service, patch_build_artifact_ref):
    await activities.artifact_write_complete(
        artifact_id="test-id",
        payload=b"test payload",
        principal="test-principal",
        content_type="text/plain"
    )
    mock_service.write_complete.assert_called_once_with(
        artifact_id="test-id",
        principal="test-principal",
        payload=b"test payload",
        content_type="text/plain"
    )
