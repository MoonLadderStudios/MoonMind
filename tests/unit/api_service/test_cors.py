import pytest
from fastapi.testclient import TestClient
from api_service.main import app
from moonmind.config.settings import settings

@pytest.fixture
def client():
    return TestClient(app)

def test_cors_allowed_origin(client):
    # Test with an origin that is in the default allowed list
    allowed_origin = "http://localhost:3000"
    response = client.get("/healthz", headers={"Origin": allowed_origin})
    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") == allowed_origin
    assert response.headers.get("access-control-allow-credentials") == "true"

def test_cors_disallowed_origin(client):
    # Test with an origin that is NOT in the allowed list
    disallowed_origin = "http://malicious-site.com"
    response = client.get("/healthz", headers={"Origin": disallowed_origin})
    assert response.status_code == 200
    # FastAPI's CORSMiddleware typically doesn't include the header if origin not allowed
    assert "access-control-allow-origin" not in response.headers

def test_cors_preflight_allowed(client):
    allowed_origin = "http://localhost:5000"
    response = client.options(
        "/healthz",
        headers={
            "Origin": allowed_origin,
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "X-Requested-With",
        },
    )
    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") == allowed_origin
    assert "GET" in response.headers.get("access-control-allow-methods", "")

def test_cors_preflight_disallowed(client):
    disallowed_origin = "http://another-malicious-site.com"
    response = client.options(
        "/healthz",
        headers={
            "Origin": disallowed_origin,
            "Access-Control-Request-Method": "GET",
        },
    )
    # CORSMiddleware returns 400 for invalid preflight? No, it returns 200 but without CORS headers or 400 depending on version/config
    # Actually, standard CORSMiddleware returns a simple response without CORS headers if origin not allowed.
    assert "access-control-allow-origin" not in response.headers
