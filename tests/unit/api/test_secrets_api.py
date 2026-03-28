import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock

from api_service.api.routers.secrets import router
from api_service.db.base import get_async_session
from api_service.auth_providers import get_current_user

app = FastAPI()
app.include_router(router, prefix="/api/v1/secrets")

mock_db_session = AsyncMock()
app.dependency_overrides[get_async_session] = lambda: mock_db_session
app.dependency_overrides[get_current_user] = lambda: lambda: None

client = TestClient(app)

@pytest.fixture
def mock_secrets_service(monkeypatch):
    mock = AsyncMock()
    monkeypatch.setattr("api_service.api.routers.secrets.SecretsService", mock)
    return mock

def test_create_secret(mock_secrets_service):
    from api_service.db.models import ManagedSecret
    from datetime import datetime
    
    # Mock return value
    mock_secret = ManagedSecret(slug="NEW_KEY", status="active", details={}, created_at=datetime.utcnow())
    mock_secrets_service.create_secret.return_value = mock_secret
    mock_secrets_service.get_secret.return_value = None
    
    resp = client.post(
        "/api/v1/secrets",
        json={"slug": "NEW_KEY", "plaintext": "new-secret-val"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["slug"] == "NEW_KEY"
    assert data["status"] == "active"
    assert "ciphertext" not in data

def test_create_secret_conflict(mock_secrets_service):
    from api_service.db.models import ManagedSecret
    
    mock_secret = ManagedSecret(slug="TEST_API_KEY", status="active", details={})
    mock_secrets_service.get_secret.return_value = mock_secret
    
    resp = client.post(
        "/api/v1/secrets",
        json={"slug": "TEST_API_KEY", "plaintext": "conflict-val"},
    )
    assert resp.status_code == 409

def test_list_secrets(mock_secrets_service):
    from api_service.db.models import ManagedSecret
    from datetime import datetime
    
    mock_secret = ManagedSecret(slug="TEST_API_KEY", status="active", details={}, created_at=datetime.utcnow())
    mock_secrets_service.list_metadata.return_value = [mock_secret]
    
    resp = client.get("/api/v1/secrets")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["items"]) == 1
    assert data["items"][0]["slug"] == "TEST_API_KEY"

def test_update_secret(mock_secrets_service):
    from api_service.db.models import ManagedSecret
    from datetime import datetime
    
    mock_secret = ManagedSecret(slug="TEST_API_KEY", status="active", details={}, created_at=datetime.utcnow())
    mock_secrets_service.update_secret.return_value = mock_secret
    
    resp = client.put(
        "/api/v1/secrets/TEST_API_KEY",
        json={"plaintext": "updated-val"},
    )
    assert resp.status_code == 200

def test_rotate_secret(mock_secrets_service):
    from api_service.db.models import ManagedSecret
    from datetime import datetime
    
    mock_secret = ManagedSecret(slug="TEST_API_KEY", status="rotated", details={}, created_at=datetime.utcnow())
    mock_secrets_service.rotate_secret.return_value = mock_secret
    
    resp = client.post(
        "/api/v1/secrets/TEST_API_KEY/rotate",
        json={"plaintext": "rotated-val"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "rotated"

def test_update_secret_status(mock_secrets_service):
    from api_service.db.models import ManagedSecret
    from datetime import datetime
    
    mock_secret = ManagedSecret(slug="TEST_API_KEY", status="disabled", details={}, created_at=datetime.utcnow())
    mock_secrets_service.set_status.return_value = mock_secret
    
    resp = client.put(
        "/api/v1/secrets/TEST_API_KEY/status",
        json={"status": "disabled"},
    )
    assert resp.status_code == 200, resp.json()

def test_delete_secret(mock_secrets_service):
    mock_secrets_service.delete_secret.return_value = True
    
    resp = client.delete("/api/v1/secrets/TEST_API_KEY")
    assert resp.status_code == 204

def test_validate_secret(mock_secrets_service):
    from api_service.db.models import ManagedSecret
    
    mock_secret = ManagedSecret()
    mock_secrets_service.get_secret.return_value = mock_secret
    
    resp = client.get("/api/v1/secrets/TEST_API_KEY/validate")
    assert resp.status_code == 200
    assert resp.json()["valid"] is True
