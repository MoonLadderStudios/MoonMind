import json
import pytest
from unittest.mock import patch, AsyncMock
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api_service.api.routers.proxy import router
from api_service.db.base import get_async_session

app = FastAPI()
app.include_router(router, prefix="/api/v1")

mock_db_session = AsyncMock()
app.dependency_overrides[get_async_session] = lambda: mock_db_session

client = TestClient(app)

@pytest.fixture
def mock_encryption(monkeypatch):
    """Bypass decryption to just return a dict payload for testing."""
    async def fake_decode(token: str):
        if token == "valid-token":
            return {
                "provider": "anthropic",
                "secret_refs": {"anthropic_api_key": "db://anthropic"},
                "exp": 9999999999
            }
        elif token == "valid-openai":
            return {
                "provider": "openai",
                "secret_refs": {"openai_api_key": "db://openai"},
                "exp": 9999999999
            }
        from fastapi import HTTPException
        raise HTTPException(status_code=401, detail="Invalid or expired proxy token")
        
    monkeypatch.setattr("api_service.api.routers.proxy._verify_and_decode_proxy_token", fake_decode)
    return fake_decode

@pytest.fixture
def mock_resolver(monkeypatch):
    async def fake_resolve(provider, refs, db):
        if provider == "anthropic":
            return "sk-ant-12345"
        elif provider == "openai":
            return "sk-opn-12345"
        raise ValueError("Resolution failed")
        
    monkeypatch.setattr("api_service.api.routers.proxy._resolve_provider_key", fake_resolve)
    return fake_resolve

@pytest.fixture
def mock_httpx(monkeypatch):
    class MockResponse:
        content = b'{"success": True}'
        status_code = 200
        headers = {"content-type": "application/json", "x-custom": "test"}

    async def fake_request(*args, **kwargs):
        # We can assert headers here if we want by attaching logic to the mock.
        return MockResponse()

    mock = AsyncMock()
    mock.request.side_effect = fake_request
    
    class MockClientContext:
        async def __aenter__(self):
            return mock
            
        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass
            
    monkeypatch.setattr("httpx.AsyncClient", lambda **kwargs: MockClientContext())
    return mock

def test_proxy_missing_auth():
    resp = client.post("/api/v1/proxy/anthropic/v1/messages", json={})
    assert resp.status_code == 401
    assert "Missing proxy credential header" in resp.json()["detail"]

def test_proxy_invalid_token(mock_encryption):
    resp = client.post(
        "/api/v1/proxy/anthropic/v1/messages", 
        headers={"Authorization": "Bearer bad-token"}
    )
    assert resp.status_code == 401
    
def test_proxy_expired_token(mock_encryption):
    resp = client.post(
        "/api/v1/proxy/anthropic/v1/messages", 
        headers={"Authorization": "Bearer expired"}
    )
    assert resp.status_code == 401

def test_proxy_mismatch_provider(mock_encryption):
    resp = client.post(
        "/api/v1/proxy/openai/v1/chat/completions", 
        headers={"Authorization": "Bearer valid-token"} # This token has provider="anthropic"
    )
    assert resp.status_code == 403
    assert "provider mismatch" in resp.json()["detail"].lower()

@pytest.mark.asyncio
def test_proxy_success_anthropic(mock_encryption, mock_resolver, mock_httpx):
    resp = client.post(
        "/api/v1/proxy/anthropic/v1/messages", 
        headers={"Authorization": "Bearer valid-token", "x-my-header": "value"}
    )
    assert resp.status_code == 200
    assert resp.headers.get("x-custom") == "test"
    
@pytest.mark.asyncio
def test_proxy_success_openai(mock_encryption, mock_resolver, mock_httpx):
    resp = client.post(
        "/api/v1/proxy/openai/v1/chat/completions", 
        headers={"Authorization": "Bearer valid-openai"}
    )
    assert resp.status_code == 200
