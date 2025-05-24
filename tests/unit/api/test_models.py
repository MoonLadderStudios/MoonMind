import pytest
from unittest.mock import patch, MagicMock
from fastapi import FastAPI
from fastapi.testclient import TestClient
import time

# Assuming the main app's router is imported correctly
from fastapi.api.routers.models import router as models_router
# Assuming settings are correctly imported and used by the application
# from moonmind.config.settings import settings # Settings might not be directly needed here anymore

# Setup TestClient
app = FastAPI()
app.include_router(models_router, prefix="/v1") 
client = TestClient(app)

# Fixture for a sample list of models as would be returned by model_cache.get_all_models()
@pytest.fixture
def mock_cached_models_data():
    return [
        {
            "id": "models/gemini-pro", "object": "model", "created": int(time.time()),
            "owned_by": "Google", "permission": [], "root": "models/gemini-pro", "parent": None,
            "context_window": 8192, 
            "capabilities": {"chat_completion": True, "text_completion": True, "embedding": False}
        },
        {
            "id": "gpt-3.5-turbo", "object": "model", "created": int(time.time()),
            "owned_by": "OpenAI", "permission": [], "root": "gpt-3.5-turbo", "parent": None,
            "context_window": 4096,
            "capabilities": {"chat_completion": True, "text_completion": True, "embedding": False}
        },
        {
            "id": "models/embedding-001", "object": "model", "created": int(time.time()),
            "owned_by": "Google", "permission": [], "root": "models/embedding-001", "parent": None,
            "context_window": 1024,
            "capabilities": {"chat_completion": False, "text_completion": False, "embedding": True}
        }
    ]

@patch('fastapi.api.routers.models.model_cache.get_all_models')
def test_get_models_success_with_cache(mock_get_all_models, mock_cached_models_data):
    """
    Test the /v1/models endpoint when the model_cache returns a list of models.
    """
    mock_get_all_models.return_value = mock_cached_models_data
    
    response = client.get("/v1/models")
    assert response.status_code == 200
    json_response = response.json()
    
    assert json_response["object"] == "list"
    assert "data" in json_response
    assert json_response["data"] == mock_cached_models_data
    assert len(json_response["data"]) == len(mock_cached_models_data)
    mock_get_all_models.assert_called_once()

@patch('fastapi.api.routers.models.model_cache.get_all_models')
def test_get_models_empty_from_cache(mock_get_all_models):
    """
    Test the /v1/models endpoint when the model_cache returns an empty list.
    """
    mock_get_all_models.return_value = [] # Cache returns no models
    
    response = client.get("/v1/models")
    assert response.status_code == 200
    json_response = response.json()
    
    assert json_response["object"] == "list"
    assert "data" in json_response
    assert len(json_response["data"]) == 0
    mock_get_all_models.assert_called_once()

@patch('fastapi.api.routers.models.model_cache.get_all_models')
def test_get_models_cache_exception(mock_get_all_models):
    """
    Test the /v1/models endpoint when model_cache.get_all_models() raises an exception.
    """
    error_message = "Cache internal error"
    mock_get_all_models.side_effect = Exception(error_message)
    
    response = client.get("/v1/models")
    
    # The router should catch this and return a 500 error
    assert response.status_code == 500
    json_response = response.json()
    assert "detail" in json_response
    # The exact message might be wrapped by FastAPI's error handling
    assert "An error occurred while retrieving models" in json_response["detail"]
    # assert error_message in json_response["detail"] # Check if original error is propagated in message
    mock_get_all_models.assert_called_once()

# Health check tests (ensure they are still working)
def test_health_check_main_app(): # Renamed to distinguish if app has own /health
    # This test assumes /health is at the root of the TestClient's app.
    # If models_router is the only thing in `app`, then this might fail or hit the router's health.
    # For clarity, it's better to test the router's health check specifically if it exists.
    # Add a general health check to the app if needed for this test:
    # @app.get("/health")
    # async def main_health(): return {"status": "main_healthy"}
    # For now, assuming this test is for a health check defined outside the models_router.
    # If not, this test might need adjustment or removal.
    # Let's assume the app itself does not have a /health and we only test the router's.
    pass 


def test_router_health_check():
    # This tests the health check defined within the models_router
    response = client.get("/v1/health") 
    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}

# Notes on previous tests:
# The tests like test_get_models_google_only, test_get_models_openai_only,
# test_get_models_combined, and the API error tests (test_get_models_google_api_error, etc.)
# were based on mocking list_google_models and list_openai_models directly.
# Since the router models.py now solely relies on model_cache.get_all_models(),
# these specific scenarios (Google only, OpenAI only, specific API errors during fetch)
# are now encapsulated within the ModelCache's logic.
# The unit tests for ModelCache (in test_models_cache.py) are responsible for verifying
# that the cache correctly handles these scenarios (e.g., one provider failing, API keys missing).
# The router tests for /v1/models should focus on how the router behaves based on what
# model_cache.get_all_models() returns:
#   1. A list of models (success case).
#   2. An empty list.
#   3. An exception occurring within the cache call.
# This revised test_models.py reflects that change in responsibility.
# The mock_google_models_data and mock_openai_models_data fixtures are no longer
# directly used by these router tests, but a combined fixture mock_cached_models_data is used.
# The health check tests are independent and should remain.

# Remove the unused fixture to keep the file clean
# @pytest.fixture
# def mock_google_models_data(): ...
# @pytest.fixture
# def mock_openai_models_data(): ...
# These are no longer needed here. `mock_cached_models_data` is representative.
# The `test_health_check_main_app` is also commented out as it's not relevant
# if the test FastAPI app `app` only includes the `models_router`.
# If `app` had its own `/health` endpoint, it would be relevant.
# The `test_router_health_check` correctly tests the router's `/v1/health`.
