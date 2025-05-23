import pytest
from unittest.mock import patch, MagicMock, Mock
from fastapi import FastAPI
from fastapi.testclient import TestClient
import time

# Assuming the main app's router is imported correctly
from fastapi.api.routers.models import router as models_router
# Assuming settings are correctly imported and used by the application
from moonmind.config.settings import settings

# Setup TestClient
app = FastAPI()
# Ensure the router prefix matches your application's setup
app.include_router(models_router, prefix="/v1") 
client = TestClient(app)

@pytest.fixture
def mock_google_models_data():
    gemini_pro = MagicMock()
    gemini_pro.name = "models/gemini-pro"
    gemini_pro.input_token_limit = 8192
    gemini_pro.supported_generation_methods = ['generateContent', 'countTokens']

    embedding_001 = MagicMock()
    embedding_001.name = "models/embedding-001"
    embedding_001.input_token_limit = 1024 # Specific to embedding
    embedding_001.supported_generation_methods = ['embedContent', 'countTokens']
    
    default_limit_model = MagicMock()
    default_limit_model.name = "models/default-limit-model"
    default_limit_model.input_token_limit = None # Test default assignment for generative
    default_limit_model.supported_generation_methods = ['generateContent']

    return [gemini_pro, embedding_001, default_limit_model]

@pytest.fixture
def mock_openai_models_data():
    # Mocking OpenAI's model structure (typically an object with an 'id' attribute)
    gpt_35_turbo = MagicMock()
    gpt_35_turbo.id = "gpt-3.5-turbo" 
    # Add other attributes if your factory/router logic uses them, e.g., context_window
    # For the current models endpoint, only 'id' is strictly necessary from list_openai_models

    gpt_4 = MagicMock()
    gpt_4.id = "gpt-4"

    return [gpt_35_turbo, gpt_4]

def test_get_models_google_only(mock_google_models_data):
    with patch('fastapi.api.routers.models.list_google_models', return_value=mock_google_models_data), \
         patch('fastapi.api.routers.models.list_openai_models', return_value=[]): # No OpenAI models
        
        response = client.get("/v1/models")
        assert response.status_code == 200
        json_response = response.json()
        assert json_response["object"] == "list"
        data = json_response["data"]
        assert len(data) == len(mock_google_models_data)

        for mock_model_obj in mock_google_models_data:
            found_model = next((item for item in data if item["id"] == mock_model_obj.name), None)
            assert found_model is not None
            assert found_model["owned_by"] == "Google"
            # Default context window for generative if None
            expected_context = mock_model_obj.input_token_limit
            if expected_context is None:
                 if 'embedContent' in mock_model_obj.supported_generation_methods:
                    expected_context = 1024
                 else:
                    expected_context = 8192 # Default for generative
            assert found_model["context_window"] == expected_context


def test_get_models_openai_only(mock_openai_models_data):
    with patch('fastapi.api.routers.models.list_google_models', return_value=[]), \
         patch('fastapi.api.routers.models.list_openai_models', return_value=mock_openai_models_data):
        
        response = client.get("/v1/models")
        assert response.status_code == 200
        json_response = response.json()
        assert json_response["object"] == "list"
        data = json_response["data"]
        assert len(data) == len(mock_openai_models_data)

        for mock_model_obj in mock_openai_models_data:
            found_model = next((item for item in data if item["id"] == mock_model_obj.id), None)
            assert found_model is not None
            assert found_model["owned_by"] == "OpenAI"
            assert "context_window" in found_model # Check if context_window is set (e.g. to default)
            # Example: Check default context window for OpenAI models if specified in router
            if "gpt-4" in mock_model_obj.id:
                 assert found_model["context_window"] == 8192
            else:
                 assert found_model["context_window"] == 4096


def test_get_models_combined(mock_google_models_data, mock_openai_models_data):
    with patch('fastapi.api.routers.models.list_google_models', return_value=mock_google_models_data), \
         patch('fastapi.api.routers.models.list_openai_models', return_value=mock_openai_models_data):
        
        response = client.get("/v1/models")
        assert response.status_code == 200
        json_response = response.json()
        assert json_response["object"] == "list"
        data = json_response["data"]
        assert len(data) == len(mock_google_models_data) + len(mock_openai_models_data)

        # Check Google Models
        for mock_model_obj in mock_google_models_data:
            found_model = next((item for item in data if item["id"] == mock_model_obj.name), None)
            assert found_model is not None
            assert found_model["owned_by"] == "Google"

        # Check OpenAI Models
        for mock_model_obj in mock_openai_models_data:
            found_model = next((item for item in data if item["id"] == mock_model_obj.id), None)
            assert found_model is not None
            assert found_model["owned_by"] == "OpenAI"


def test_get_models_empty():
    with patch('fastapi.api.routers.models.list_google_models', return_value=[]), \
         patch('fastapi.api.routers.models.list_openai_models', return_value=[]):
        
        response = client.get("/v1/models")
        assert response.status_code == 200
        json_response = response.json()
        assert json_response["object"] == "list"
        assert len(json_response["data"]) == 0


def test_get_models_google_api_error(mock_openai_models_data):
    with patch('fastapi.api.routers.models.list_google_models', side_effect=Exception("Google API Error")), \
         patch('fastapi.api.routers.models.list_openai_models', return_value=mock_openai_models_data) as mock_list_openai:
        
        response = client.get("/v1/models")
        assert response.status_code == 200 # The endpoint handles Google API error gracefully
        json_response = response.json()
        # Should still return OpenAI models
        assert len(json_response["data"]) == len(mock_openai_models_data)
        # Verify only OpenAI models are present
        openai_ids = {model.id for model in mock_openai_models_data}
        returned_ids = {item["id"] for item in json_response["data"]}
        assert openai_ids == returned_ids


def test_get_models_openai_api_error(mock_google_models_data):
    with patch('fastapi.api.routers.models.list_google_models', return_value=mock_google_models_data) as mock_list_google, \
         patch('fastapi.api.routers.models.list_openai_models', side_effect=Exception("OpenAI API Error")):
        
        response = client.get("/v1/models")
        assert response.status_code == 200 # The endpoint handles OpenAI API error gracefully
        json_response = response.json()
        # Should still return Google models
        assert len(json_response["data"]) == len(mock_google_models_data)
        google_ids = {model.name for model in mock_google_models_data}
        returned_ids = {item["id"] for item in json_response["data"]}
        assert google_ids == returned_ids

def test_get_models_both_api_errors():
    with patch('fastapi.api.routers.models.list_google_models', side_effect=Exception("Google API Error")), \
         patch('fastapi.api.routers.models.list_openai_models', side_effect=Exception("OpenAI API Error")):
        
        response = client.get("/v1/models")
        assert response.status_code == 200
        json_response = response.json()
        assert len(json_response["data"]) == 0 # No models should be returned
        
# Health check test (already present in the original file, good to keep)
def test_health_check():
    response = client.get("/health") # Assuming /health is at the root of the app or router
    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}

# Note: The original file had an `app_name` setting. If your application truly relies on this
# for a default model ID, you might need to ensure `settings.app_name` is appropriately set
# during test setup (e.g., via environment variable or directly patching `settings`).
# For these tests, I've focused on the behavior when actual model providers are called or mocked.
# If `settings.app_name` was intended for a default/fallback model entry when no others are available,
# the tests `test_get_models_empty` and error handling tests would need to assert its presence.
# However, the current router logic for `/models` in `fastapi/api/routers/models.py`
# doesn't seem to add such a default model explicitly if both `list_google_models` and
# `list_openai_models` return empty or raise errors; it would just return an empty `data` list.
# This revised test suite aligns with that behavior.
# If a default app model is a requirement, the router logic and tests would need adjustment.
# For now, assuming no such default model is added by the `/models` endpoint itself.

# Ensure the prefix is correct for the health check if it's part of the same router
# If health_check is on the main app: client.get("/health")
# If health_check is on the models_router: client.get("/v1/health")
# The provided code for models.py has `@router.get("/health")`, so it's part of models_router
# and thus should be accessed via the prefix.
def test_router_health_check():
    response = client.get("/v1/health") 
    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}
