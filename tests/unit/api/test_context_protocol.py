import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock

from api_service.api.routers.context_protocol import ContextRequest, ContextMessage
from qdrant_client.http.exceptions import UnexpectedResponse # For mocking
from qdrant_client.http.models import Distance # For mocking, if needed for detailed get_collection mock

from api_service.main import app

# client = TestClient(app) # Removed global client
from llama_index.core.embeddings import BaseEmbedding # For spec (corrected path)

@pytest.fixture
def client():
    # Patch build_embed_model in the context of api_service.main where the app's startup sequence calls it.
    with patch('api_service.main.build_embed_model') as MockBuildEmbedModel:
        # Configure the mock embed_model instance that build_embed_model will return
        mock_embed_instance = MagicMock(spec=BaseEmbedding)

        # Mock the specific methods that will be called on this instance
        # _get_query_embedding is often called by other methods in BaseEmbedding or its users
        mock_embed_instance._get_query_embedding = MagicMock(return_value=[0.1] * 100) # Example dimension

        # Explicitly add other common embedding methods if they are directly accessed on the mock
        # or if the spec doesn't create them automatically as callable mocks.
        mock_embed_instance.embed_query = MagicMock(return_value=[0.1] * 100)
        mock_embed_instance.embed_documents = MagicMock(return_value=[[0.1] * 100])
        mock_embed_instance.get_text_embedding = MagicMock(return_value=[0.1] * 100) # Alias for query embedding often

        # build_embed_model returns a tuple: (embedding_model, dimensions)
        MockBuildEmbedModel.return_value = (mock_embed_instance, 100)

        # Use TestClient as a context manager to run startup/shutdown events
        # Now, when TestClient(app) starts up, it will use our mocked build_embed_model
        with TestClient(app) as c:
            yield c

import httpx # For creating a mock httpx.Response

@pytest.fixture(autouse=True) # Apply to all tests in this module
def mock_qdrant_client_autouse():
    with patch("moonmind.factories.vector_store_factory.QdrantClient") as mock_qdrant:
        mock_client_instance = MagicMock()
        
        # Mock a CollectionInfo object
        mock_collection_info = MagicMock()
        mock_collection_info.config.params.vectors.size = 100 # Match dimension from client fixture
        mock_collection_info.config.params.vectors.distance = Distance.COSINE # Match expected distance

        mock_client_instance.get_collection.return_value = mock_collection_info
        
        mock_qdrant.return_value = mock_client_instance
        yield mock_qdrant

@pytest.fixture
def mock_google_model():
    # Patch where get_google_model is looked up by the context_protocol router
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

def test_context_protocol_endpoint(mock_google_model, client): # Added client fixture
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
    
    response = client.post("/context", json=request_data) # Using client from fixture
    
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

def test_context_protocol_invalid_role(client): # Added client fixture
    """Test the context protocol endpoint with an invalid role."""
    request_data = {
        "messages": [
            {"role": "invalid_role", "content": "This should fail."},
            {"role": "user", "content": "What is the Model Context Protocol?"}
        ],
        "model": "gemini-pro"
    }
    
    response = client.post("/context", json=request_data) # Using client from fixture
    
    assert response.status_code == 400
    data = response.json()
    assert "detail" in data
    assert "Invalid message role" in data["detail"]