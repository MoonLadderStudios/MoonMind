import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock

from api_service.api.routers.context_protocol import ContextRequest, ContextMessage
from api_service.main import app

client = TestClient(app)

@pytest.fixture
def mock_google_model():
    with patch("api_service.api.routers.context_protocol.get_google_model") as mock:
        model_mock = MagicMock()
        response_mock = MagicMock()
        candidate_mock = MagicMock()
        content_mock = MagicMock()
        part_mock = MagicMock()
        
        part_mock.text = "This is a test response from the model."
        content_mock.parts = [part_mock]
        candidate_mock.content = content_mock
        response_mock.candidates = [candidate_mock]
        model_mock.generate_content.return_value = response_mock
        
        mock.return_value = model_mock
        yield mock

def test_context_protocol_endpoint(mock_google_model):
    """Test the context protocol endpoint with a mocked Google model."""
    request_data = {
        "messages": [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "What is the Model Context Protocol?"}
        ],
        "model": "gemini-pro",
        "temperature": 0.7,
        "max_tokens": 100
    }
    
    response = client.post("/context", json=request_data)
    
    assert response.status_code == 200
    data = response.json()
    
    # Check that the response has the expected structure
    assert "id" in data
    assert "content" in data
    assert "model" in data
    assert "created_at" in data
    assert "metadata" in data
    
    # Check that the model was called with the correct parameters
    mock_google_model.assert_called_once_with("gemini-pro")
    
    # Check that the content matches our mock
    assert data["content"] == "This is a test response from the model."
    assert data["model"] == "gemini-pro"
    
    # Check that metadata contains usage information
    assert "usage" in data["metadata"]
    assert "prompt_tokens" in data["metadata"]["usage"]
    assert "completion_tokens" in data["metadata"]["usage"]
    assert "total_tokens" in data["metadata"]["usage"]

def test_context_protocol_invalid_role():
    """Test the context protocol endpoint with an invalid role."""
    request_data = {
        "messages": [
            {"role": "invalid_role", "content": "This should fail."},
            {"role": "user", "content": "What is the Model Context Protocol?"}
        ],
        "model": "gemini-pro"
    }
    
    response = client.post("/context", json=request_data)
    
    assert response.status_code == 400
    data = response.json()
    assert "detail" in data
    assert "Invalid message role" in data["detail"]