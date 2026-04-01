import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from typing import AsyncGenerator
import uuid
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock, AsyncMock

from api_service.main import app
from api_service.db.base import get_async_session
from api_service.api.routers.executions import _get_service, get_current_user

@pytest_asyncio.fixture
async def mock_execution_service():
    service_mock = MagicMock()
    service_mock.signal_execution = AsyncMock()
    yield service_mock

@pytest_asyncio.fixture
async def async_client(mock_execution_service) -> AsyncGenerator[AsyncClient, None]:
    transport = ASGITransport(app=app)
    async with AsyncClient(base_url="http://testserver", transport=transport) as client:
        yield client

@pytest.mark.asyncio
async def test_intervention_signal_without_logs(
    async_client: AsyncClient, mock_execution_service
):
    """
    Verify that intervention actions (Phase 5) route through backend signals
    independently of any active live log session.
    """
    task_run_id = str(uuid.uuid4())
    payload = {"signalName": "Pause", "payload": {}}
    
    # We bypass auth by overriding current_user dependency if necessary, but
    # _get_owned_execution is mocked anyway. For safety, we'll patch the dependency overrides:
    app.dependency_overrides.clear()
    app.dependency_overrides[get_async_session] = MagicMock
    
    app.dependency_overrides[_get_service] = lambda: mock_execution_service
    class MockRecord:
        id = 1
        workflow_id = task_run_id
        run_id = "run_id_1"
        state = MagicMock(value="executing")
        workflow_type = MagicMock(value="MoonMind.Run")
        close_status = None
        owner_id = "system"
        namespace = "default"
        manifest_artifact_ref = "test_ref"
        search_attributes = {}
        memo = {}
        parameters = {}
        artifact_refs = []
        started_at = datetime.now(timezone.utc)
        created_at = datetime.now(timezone.utc)
        closed_at = None
        updated_at = datetime.now(timezone.utc)

    mock_record = MockRecord()
    mock_record.memo = {}
    mock_record.parameters = {}
    mock_record.artifact_refs = []
    mock_execution_service.signal_execution.return_value = mock_record
    app.dependency_overrides[get_current_user] = lambda: MagicMock(id=1, email="test@test.com")
    
    # _get_owned_execution is called directly, not as a dependency
    with patch("api_service.api.routers.executions._get_owned_execution", new_callable=AsyncMock) as mock_get_owned:
        mock_get_owned.return_value = mock_record
        response = await async_client.post(f"/api/executions/{task_run_id}/signal", json=payload)
    app.dependency_overrides.clear()

    assert response.status_code == 202, response.text
    assert mock_execution_service.signal_execution.called
    assert mock_execution_service.signal_execution.call_args[1]["signal_name"] == "Pause"
