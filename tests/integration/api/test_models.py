from unittest.mock import Mock, patch

import pytest

from fastapi import FastAPI
from fastapi.testclient import TestClient
from moonmind.config.settings import settings
from moonmind.fastapi.api.routers.models import router as models_router

# Initialize settings if necessary (e.g., for app_name)
# settings.app_name = "test_app" # Ensure this is set for tests if not loaded automatically

# Setup TestClient
app = FastAPI()
app.include_router(models_router, prefix="/v1") # Match the prefix used in the main app if any
client = TestClient(app)

# Helper function or fixture for mock Google models
@pytest.fixture
def mock_google_models_data():
    # Generative model
    gemini_pro = Mock()
    gemini_pro.name = "models/gemini-pro"
    gemini_pro.input_token_limit = 8192
    gemini_pro.supported_generation_methods = ['generateContent', 'countTokens']

    # Embedding model
    embedding_001 = Mock()
    embedding_001.name = "models/embedding-001"
    embedding_001.input_token_limit = 1024
    embedding_001.supported_generation_methods = ['embedContent', 'countTokens']

    # Model with no input_token_limit (should get default)
    default_limit_model = Mock()
    default_limit_model.name = "models/default-limit-model"
    default_limit_model.input_token_limit = None # Test default assignment
    default_limit_model.supported_generation_methods = ['generateContent']


    return [gemini_pro, embedding_001, default_limit_model]

def test_get_models_with_google_models(mock_google_models_data):
    """
    Test the /v1/models endpoint when Google models are available.
    """
    with patch('moonmind.fastapi.api.routers.models.list_google_models') as mock_list_google:
        mock_list_google.return_value = mock_google_models_data

        response = client.get("/v1/models")

        assert response.status_code == 200
        json_response = response.json()

        assert json_response["object"] == "list"
        assert "data" in json_response

        data = json_response["data"]
        assert len(data) == 1 + len(mock_google_models_data) # Default model + Google models

        # Check for default model
        default_model_present = any(item["id"] == settings.app_name for item in data)
        assert default_model_present, f"Default model '{settings.app_name}' not found."

        # Check for Google models transformation
        for mock_model_obj in mock_google_models_data:
            found_model = next((item for item in data if item["id"] == mock_model_obj.name), None)
            assert found_model is not None, f"Model {mock_model_obj.name} not found in response."
            assert found_model["owned_by"] == "Google"
            assert found_model["object"] == "model"

            expected_context_window = mock_model_obj.input_token_limit
            if expected_context_window is None:
                if 'embedContent' in mock_model_obj.supported_generation_methods:
                    expected_context_window = 1024
                else: # Assumed generative
                    expected_context_window = 8192
            assert found_model["context_window"] == expected_context_window

            expected_capabilities = {
                "chat_completion": 'generateContent' in mock_model_obj.supported_generation_methods,
                "text_completion": 'generateContent' in mock_model_obj.supported_generation_methods,
                "embedding": 'embedContent' in mock_model_obj.supported_generation_methods,
            }
            assert found_model["capabilities"] == expected_capabilities

def test_get_models_without_google_models():
    """
    Test the /v1/models endpoint when no Google models are available (e.g., API key not set).
    """
    with patch('moonmind.fastapi.api.routers.models.list_google_models') as mock_list_google:
        mock_list_google.return_value = [] # No Google models

        response = client.get("/v1/models")

        assert response.status_code == 200
        json_response = response.json()

        assert json_response["object"] == "list"
        assert "data" in json_response

        data = json_response["data"]
        assert len(data) == 1 # Only the default model

        # Check that only the default model is present
        assert data[0]["id"] == settings.app_name
        assert data[0]["owned_by"] == settings.app_name

def test_get_models_google_api_error():
    """
    Test the /v1/models endpoint when list_google_models raises an exception.
    """
    with patch('moonmind.fastapi.api.routers.models.list_google_models') as mock_list_google:
        mock_list_google.side_effect = Exception("Google API Error")

        response = client.get("/v1/models")

        # The current implementation in models.py catches the exception and logs it,
        # then returns the default model. So, the behavior is similar to no Google models.
        assert response.status_code == 200
        json_response = response.json()

        assert json_response["object"] == "list"
        assert "data" in json_response

        data = json_response["data"]
        assert len(data) == 1 # Only the default model due to the error

        assert data[0]["id"] == settings.app_name
        assert data[0]["owned_by"] == settings.app_name
        # Optionally, check logs if possible, but that's outside the scope of an API test typically.

# To ensure settings are loaded if they have a specific load mechanism:
# from moonmind.config import load_settings # Hypothetical load function
# load_settings() # Call it if it exists and is necessary before tests run

# Or, if settings rely on environment variables that pytest can set:
# (No specific code here, but pytest-dotenv or similar might be used in a real project)

# Ensure app_name is set for the default model ID.
# If settings.app_name is None or not set, the tests might fail when checking default model.
if not settings.app_name:
    settings.app_name = "default_test_app"

print(f"Test running with app_name: {settings.app_name}") # For debugging
print(f"Models router prefix: {models_router.prefix}") # For debugging
print(f"App routers: {app.routes}") # For debugging client issues
# The client's base_url will be http://testserver. If router has /v1 prefix and client calls /v1/models,
# the full URL becomes http://testserver/v1/models which should match the app's routing.
# If models_router was added without a prefix to app, then client.get("/models") would be used.
# Given app.include_router(models_router, prefix="/v1"), client.get("/v1/models") is correct.

# Final check on mock model capabilities logic
# Gemini Pro: generateContent -> chat_completion: True, text_completion: True, embedding: False
# Embedding-001: embedContent -> chat_completion: False, text_completion: False, embedding: True
# Default-limit-model: generateContent -> chat_completion: True, text_completion: True, embedding: False
# This logic seems correctly implemented in the test assertions.
