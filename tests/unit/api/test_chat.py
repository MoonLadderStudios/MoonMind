import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from fastapi import FastAPI
from fastapi.testclient import TestClient
from uuid import uuid4
import time

# Assuming the main app's router is imported correctly
# Ensure the path to chat_router is correct based on your project structure.
# If chat.py is in fastapi.api.routers.chat, this should be fine.
from fastapi.api.routers.chat import router as chat_router
from moonmind.config import settings # Direct import for patching
from moonmind.schemas.chat_models import ChatCompletionRequest, Message

# Setup TestClient
app = FastAPI()
app.include_router(chat_router, prefix="/v1") # Match the prefix
client = TestClient(app)

# Store original API keys and restore them after tests
original_openai_api_key = settings.openai.openai_api_key
original_google_api_key = settings.google.google_api_key
# Attempt to get original cache refresh interval if it exists, otherwise default
original_model_cache_refresh_interval = getattr(settings, 'model_cache_refresh_interval', 3600)


def setup_module(module):
    """ setup any state specific to the execution of the given module."""
    # Ensure model_cache is patched for all tests in this module if needed globally
    # Or patch it specifically within each test/fixture where it's used.
    pass

def teardown_module(module):
    """ teardown any state that was previously setup with a setup_module
    method.
    """
    settings.openai.openai_api_key = original_openai_api_key
    settings.google.google_api_key = original_google_api_key
    settings.model_cache_refresh_interval = original_model_cache_refresh_interval
    patch.stopall() # Stop all patches


@pytest.fixture(autouse=True) # Apply to all tests in this module
def cleanup_singleton_cache():
    # This fixture ensures that the ModelCache singleton is reset before each test.
    # It addresses the issue of state leakage between tests due to the singleton nature.
    with patch('moonmind.models_cache.ModelCache._instance', None):
        yield # Test runs here
    # No explicit teardown needed here as the patch resets _instance for the next test's setup phase.


@pytest.fixture
def chat_request_openai_model():
    return ChatCompletionRequest(
        model="gpt-3.5-turbo", # A model ID that model_cache.get_model_provider would identify as OpenAI
        messages=[Message(role="user", content="Hello OpenAI from cache test")],
        max_tokens=50,
        temperature=0.7,
    )

@pytest.fixture
def chat_request_google_model():
    return ChatCompletionRequest(
        model="models/gemini-pro", # A model ID that model_cache.get_model_provider would identify as Google
        messages=[Message(role="user", content="Hello Google from cache test")],
        max_tokens=50,
        temperature=0.7,
    )

@pytest.fixture
def chat_request_unknown_model():
    return ChatCompletionRequest(
        model="unknown-model-123",
        messages=[Message(role="user", content="Hello Unknown")],
    )


@pytest.fixture
def mock_openai_chat_response():
    response_id = f"chatcmpl-{uuid4().hex}"
    created_time = int(time.time())
    mock_choice = MagicMock()
    mock_choice.message.content = "This is a response from OpenAI (mocked)."
    mock_choice.finish_reason = "stop"
    
    mock_usage = MagicMock()
    mock_usage.prompt_tokens = 15
    mock_usage.completion_tokens = 25
    mock_usage.total_tokens = 40

    mock_response = AsyncMock() 
    mock_response.id = response_id
    mock_response.created = created_time
    mock_response.model = "gpt-3.5-turbo-mocked"
    mock_response.choices = [mock_choice]
    mock_response.usage = mock_usage
    return mock_response

@pytest.fixture
def mock_google_chat_response():
    mock_part = MagicMock()
    mock_part.text = "This is a response from Google (mocked)."
    
    mock_candidate = MagicMock()
    mock_candidate.content.parts = [mock_part]

    mock_response = MagicMock() 
    mock_response.candidates = [mock_candidate]
    return mock_response

# Test with model_cache integration
@patch('fastapi.api.routers.chat.model_cache.get_model_provider')
@patch('openai.ChatCompletion.acreate', new_callable=AsyncMock)
def test_chat_completions_openai_via_cache(mock_acreate, mock_get_provider, chat_request_openai_model, mock_openai_chat_response):
    settings.openai.openai_api_key = "fake_openai_key_for_test"
    mock_get_provider.return_value = "OpenAI"
    mock_acreate.return_value = mock_openai_chat_response
    
    response = client.post("/v1/chat/completions", json=chat_request_openai_model.model_dump())
    
    assert response.status_code == 200
    json_response = response.json()
    assert json_response["model"] == mock_openai_chat_response.model
    assert json_response["choices"][0]["message"]["content"] == mock_openai_chat_response.choices[0].message.content
    mock_get_provider.assert_called_once_with(chat_request_openai_model.model)
    mock_acreate.assert_called_once()


@patch('fastapi.api.routers.chat.model_cache.get_model_provider')
@patch('moonmind.factories.google_factory.get_google_model') # Still need to mock the factory that returns the model instance
def test_chat_completions_google_via_cache(mock_get_google_model_factory, mock_get_provider, chat_request_google_model, mock_google_chat_response):
    settings.google.google_api_key = "fake_google_key_for_test"
    mock_get_provider.return_value = "Google"

    mock_google_chat_model_instance = MagicMock()
    mock_google_chat_model_instance.generate_content.return_value = mock_google_chat_response
    mock_get_google_model_factory.return_value = mock_google_chat_model_instance

    response = client.post("/v1/chat/completions", json=chat_request_google_model.model_dump())

    assert response.status_code == 200
    json_response = response.json()
    assert json_response["model"] == chat_request_google_model.model
    assert json_response["choices"][0]["message"]["content"] == mock_google_chat_response.candidates[0].content.parts[0].text.strip()
    mock_get_provider.assert_called_once_with(chat_request_google_model.model)
    mock_get_google_model_factory.assert_called_once_with(chat_request_google_model.model)
    mock_google_chat_model_instance.generate_content.assert_called_once()


# Refined Google Error Handling Tests
@patch('fastapi.api.routers.chat.model_cache.get_model_provider')
@patch('moonmind.factories.google_factory.get_google_model')
def test_chat_completions_google_value_error_invalid_role(mock_get_google_model_factory, mock_get_provider, chat_request_google_model):
    settings.google.google_api_key = "fake_google_key_for_test"
    mock_get_provider.return_value = "Google"
    
    mock_google_chat_model_instance = MagicMock()
    error_message = "Invalid role: 'system' is not a valid role for this model. Valid roles are 'user', 'model'."
    mock_google_chat_model_instance.generate_content.side_effect = ValueError(error_message)
    mock_get_google_model_factory.return_value = mock_google_chat_model_instance

    response = client.post("/v1/chat/completions", json=chat_request_google_model.model_dump())
    
    assert response.status_code == 400
    json_response = response.json()
    assert "Invalid role in request" in json_response["detail"]
    assert error_message in json_response["detail"]


@patch('fastapi.api.routers.chat.model_cache.get_model_provider')
@patch('moonmind.factories.google_factory.get_google_model')
def test_chat_completions_google_value_error_other_argument(mock_get_google_model_factory, mock_get_provider, chat_request_google_model):
    settings.google.google_api_key = "fake_google_key_for_test"
    mock_get_provider.return_value = "Google"
    
    mock_google_chat_model_instance = MagicMock()
    error_message = "Some other argument error not related to roles."
    mock_google_chat_model_instance.generate_content.side_effect = ValueError(error_message)
    mock_get_google_model_factory.return_value = mock_google_chat_model_instance

    response = client.post("/v1/chat/completions", json=chat_request_google_model.model_dump())
    
    assert response.status_code == 400
    json_response = response.json()
    assert "Invalid argument in request" in json_response["detail"]
    assert error_message in json_response["detail"]


# Cache behavior for unknown models
@patch('fastapi.api.routers.chat.model_cache.get_model_provider')
@patch('fastapi.api.routers.chat.model_cache.refresh_models_sync') # Mock the sync refresh
def test_chat_completions_model_not_found_initial_and_after_refresh(mock_refresh_sync, mock_get_provider, chat_request_unknown_model):
    # Simulate provider returning None for the first call, and also for the second call after refresh
    mock_get_provider.side_effect = [None, None] 
    
    response = client.post("/v1/chat/completions", json=chat_request_unknown_model.model_dump())
    
    assert response.status_code == 404
    assert f"Model '{chat_request_unknown_model.model}' not found or provider unknown." in response.json()["detail"]
    mock_refresh_sync.assert_called_once() # Ensure refresh was attempted
    assert mock_get_provider.call_count == 2


@patch('fastapi.api.routers.chat.model_cache.get_model_provider')
@patch('fastapi.api.routers.chat.model_cache.refresh_models_sync')
@patch('openai.ChatCompletion.acreate', new_callable=AsyncMock) # For the retry path
def test_chat_completions_model_found_after_refresh_openai(mock_acreate, mock_refresh_sync, mock_get_provider, chat_request_openai_model, mock_openai_chat_response):
    settings.openai.openai_api_key = "fake_openai_key_for_test"
    # First call to get_model_provider returns None, second call returns "OpenAI"
    mock_get_provider.side_effect = [None, "OpenAI"]
    mock_acreate.return_value = mock_openai_chat_response # Mock the OpenAI call that would happen after successful refresh

    response = client.post("/v1/chat/completions", json=chat_request_openai_model.model_dump())
    
    # The current code in chat.py raises a specific 404 for "retry logic needs full implementation"
    # This test verifies that behavior. If full retry were implemented, this would be a 200.
    assert response.status_code == 404 
    assert f"Model '{chat_request_openai_model.model}' found after refresh, but retry logic needs full implementation." in response.json()["detail"]
    mock_refresh_sync.assert_called_once()
    assert mock_get_provider.call_count == 2
    # mock_acreate.assert_called_once() # This would be called if retry was fully implemented and led to a 200


# API Key Checks (still relevant with cache)
@patch('fastapi.api.routers.chat.model_cache.get_model_provider')
def test_chat_completions_openai_no_api_key_with_cache(mock_get_provider, chat_request_openai_model):
    settings.openai.openai_api_key = None # Key not set
    mock_get_provider.return_value = "OpenAI" # Cache says it's an OpenAI model
    
    response = client.post("/v1/chat/completions", json=chat_request_openai_model.model_dump())
    assert response.status_code == 400
    assert "OpenAI API key not configured" in response.json()["detail"]


@patch('fastapi.api.routers.chat.model_cache.get_model_provider')
def test_chat_completions_google_no_api_key_with_cache(mock_get_provider, chat_request_google_model):
    settings.google.google_api_key = None # Key not set
    mock_get_provider.return_value = "Google" # Cache says it's a Google model
    
    response = client.post("/v1/chat/completions", json=chat_request_google_model.model_dump())
    assert response.status_code == 400
    assert "Google API key not configured" in response.json()["detail"]


# General API errors after successful routing by cache
@patch('fastapi.api.routers.chat.model_cache.get_model_provider')
@patch('openai.ChatCompletion.acreate', new_callable=AsyncMock)
def test_chat_completions_openai_api_error_with_cache(mock_acreate, mock_get_provider, chat_request_openai_model):
    settings.openai.openai_api_key = "fake_openai_key_for_test"
    mock_get_provider.return_value = "OpenAI"
    mock_acreate.side_effect = Exception("OpenAI API Communication Error")
    
    response = client.post("/v1/chat/completions", json=chat_request_openai_model.model_dump())
    assert response.status_code == 500
    assert "OpenAI API error: OpenAI API Communication Error" in response.json()["detail"]


@patch('fastapi.api.routers.chat.model_cache.get_model_provider')
@patch('moonmind.factories.google_factory.get_google_model')
def test_chat_completions_google_api_error_with_cache(mock_get_google_model_factory, mock_get_provider, chat_request_google_model):
    settings.google.google_api_key = "fake_google_key_for_test"
    mock_get_provider.return_value = "Google"
    
    mock_google_chat_model_instance = MagicMock()
    mock_google_chat_model_instance.generate_content.side_effect = Exception("Google API Communication Error")
    mock_get_google_model_factory.return_value = mock_google_chat_model_instance
    
    response = client.post("/v1/chat/completions", json=chat_request_google_model.model_dump())
    assert response.status_code == 500
    assert "Google Gemini API error: Google API Communication Error" in response.json()["detail"]


# Test fixtures for provider enablement tests
@pytest.fixture
def chat_request_no_model():
    """Chat request without specifying a model - should use default"""
    return ChatCompletionRequest(
        messages=[Message(role="user", content="Hello with default model")],
        max_tokens=50,
        temperature=0.7,
    )

@pytest.fixture
def chat_request_ollama_model():
    return ChatCompletionRequest(
        model="llama2", # A model ID that model_cache.get_model_provider would identify as Ollama
        messages=[Message(role="user", content="Hello Ollama from cache test")],
        max_tokens=50,
        temperature=0.7,
    )


# Tests for provider enablement functionality
@patch('fastapi.api.routers.chat.model_cache.get_model_provider')
@patch('moonmind.config.settings.settings.is_provider_enabled')
def test_chat_completions_openai_provider_disabled(mock_is_enabled, mock_get_provider, chat_request_openai_model):
    """Test that disabled OpenAI provider returns appropriate error"""
    settings.openai.openai_api_key = "fake_openai_key_for_test"
    mock_get_provider.return_value = "OpenAI"
    mock_is_enabled.return_value = False
    
    response = client.post("/v1/chat/completions", json=chat_request_openai_model.model_dump())
    assert response.status_code == 400
    json_response = response.json()
    assert "OpenAI provider is disabled" in json_response["detail"]

@patch('fastapi.api.routers.chat.model_cache.get_model_provider')
@patch('moonmind.config.settings.settings.is_provider_enabled')
def test_chat_completions_google_provider_disabled(mock_is_enabled, mock_get_provider, chat_request_google_model):
    """Test that disabled Google provider returns appropriate error"""
    settings.google.google_api_key = "fake_google_key_for_test"
    mock_get_provider.return_value = "Google"
    mock_is_enabled.return_value = False
    
    response = client.post("/v1/chat/completions", json=chat_request_google_model.model_dump())
    assert response.status_code == 400
    json_response = response.json()
    assert "Google provider is disabled" in json_response["detail"]

@patch('fastapi.api.routers.chat.model_cache.get_model_provider')
@patch('moonmind.config.settings.settings.is_provider_enabled')
@patch('moonmind.factories.ollama_factory.chat_with_ollama', new_callable=AsyncMock)
def test_chat_completions_ollama_provider_disabled(mock_chat_ollama, mock_is_enabled, mock_get_provider, chat_request_ollama_model):
    """Test that disabled Ollama provider returns appropriate error"""
    mock_get_provider.return_value = "Ollama"
    mock_is_enabled.return_value = False
    
    response = client.post("/v1/chat/completions", json=chat_request_ollama_model.model_dump())
    assert response.status_code == 400
    json_response = response.json()
    assert "Ollama provider is disabled" in json_response["detail"]

# Test default model functionality
@patch('fastapi.api.routers.chat.model_cache.get_model_provider')
@patch('moonmind.config.settings.settings.get_default_chat_model')
@patch('openai.ChatCompletion.acreate', new_callable=AsyncMock)
def test_chat_completions_uses_default_model(mock_acreate, mock_get_default, mock_get_provider, chat_request_no_model, mock_openai_chat_response):
    """Test that default model is used when no model is specified"""
    settings.openai.openai_api_key = "fake_openai_key_for_test"
    mock_get_default.return_value = "gpt-3.5-turbo"
    mock_get_provider.return_value = "OpenAI"
    mock_acreate.return_value = mock_openai_chat_response
    
    response = client.post("/v1/chat/completions", json=chat_request_no_model.model_dump())
    
    assert response.status_code == 200
    mock_get_default.assert_called_once()
    mock_get_provider.assert_called_once_with("gpt-3.5-turbo")

# Test Ollama integration
@pytest.fixture
def mock_ollama_chat_response():
    return {
        "message": {
            "content": "This is a response from Ollama (mocked).",
            "role": "assistant"
        },
        "done": True
    }

@patch('fastapi.api.routers.chat.model_cache.get_model_provider')
@patch('moonmind.config.settings.settings.is_provider_enabled')
@patch('moonmind.factories.ollama_factory.chat_with_ollama', new_callable=AsyncMock)
@patch('moonmind.factories.ollama_factory.get_ollama_model')
def test_chat_completions_ollama_success(mock_get_ollama_model, mock_chat_ollama, mock_is_enabled, mock_get_provider, chat_request_ollama_model, mock_ollama_chat_response):
    """Test successful Ollama chat completion"""
    mock_get_provider.return_value = "Ollama"
    mock_is_enabled.return_value = True
    mock_get_ollama_model.return_value = "llama2"
    mock_chat_ollama.return_value = mock_ollama_chat_response
    
    response = client.post("/v1/chat/completions", json=chat_request_ollama_model.model_dump())
    
    assert response.status_code == 200
    json_response = response.json()
    assert json_response["model"] == "llama2"
    assert json_response["choices"][0]["message"]["content"] == mock_ollama_chat_response["message"]["content"]
    mock_chat_ollama.assert_called_once()

@patch('fastapi.api.routers.chat.model_cache.get_model_provider')
@patch('moonmind.config.settings.settings.is_provider_enabled')
@patch('moonmind.factories.ollama_factory.chat_with_ollama', new_callable=AsyncMock)
@patch('moonmind.factories.ollama_factory.get_ollama_model')
def test_chat_completions_ollama_api_error(mock_get_ollama_model, mock_chat_ollama, mock_is_enabled, mock_get_provider, chat_request_ollama_model):
    """Test Ollama API error handling"""
    mock_get_provider.return_value = "Ollama"
    mock_is_enabled.return_value = True
    mock_get_ollama_model.return_value = "llama2"
    mock_chat_ollama.side_effect = Exception("Ollama connection error")
    
    response = client.post("/v1/chat/completions", json=chat_request_ollama_model.model_dump())
    
    assert response.status_code == 500
    json_response = response.json()
    assert "Ollama API error: Ollama connection error" in json_response["detail"]

@patch('fastapi.api.routers.chat.model_cache.get_model_provider')
@patch('moonmind.config.settings.settings.is_provider_enabled')
@patch('moonmind.factories.ollama_factory.chat_with_ollama', new_callable=AsyncMock)
@patch('moonmind.factories.ollama_factory.get_ollama_model')
def test_chat_completions_ollama_invalid_response(mock_get_ollama_model, mock_chat_ollama, mock_is_enabled, mock_get_provider, chat_request_ollama_model):
    """Test Ollama invalid response handling"""
    mock_get_provider.return_value = "Ollama"
    mock_is_enabled.return_value = True
    mock_get_ollama_model.return_value = "llama2"
    mock_chat_ollama.return_value = {"invalid": "response"}  # Missing message field
    
    response = client.post("/v1/chat/completions", json=chat_request_ollama_model.model_dump())
    
    assert response.status_code == 500
    json_response = response.json()
    assert "Invalid response from Ollama API: No message returned" in json_response["detail"]
