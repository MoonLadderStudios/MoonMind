import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from fastapi import FastAPI
from fastapi.testclient import TestClient
from uuid import uuid4
import time

# Assuming the main app's router is imported correctly
from fastapi.api.routers.chat import router as chat_router
# Assuming settings are correctly imported and used by the application
from moonmind.config import settings # Direct import for patching
from moonmind.schemas.chat_models import ChatCompletionRequest, Message

# Setup TestClient
app = FastAPI()
app.include_router(chat_router, prefix="/v1") # Match the prefix
client = TestClient(app)

# Store original API keys and restore them after tests
original_openai_api_key = settings.openai.openai_api_key
original_google_api_key = settings.google.google_api_key

def setup_module(module):
    """ setup any state specific to the execution of the given module."""
    pass

def teardown_module(module):
    """ teardown any state that was previously setup with a setup_module
    method.
    """
    settings.openai.openai_api_key = original_openai_api_key
    settings.google.google_api_key = original_google_api_key
    patch.stopall()


@pytest.fixture
def chat_request_openai():
    return ChatCompletionRequest(
        model="gpt-3.5-turbo",
        messages=[Message(role="user", content="Hello OpenAI")],
        max_tokens=50,
        temperature=0.7,
    )

@pytest.fixture
def chat_request_google():
    return ChatCompletionRequest(
        model="gemini-pro",
        messages=[Message(role="user", content="Hello Google")],
        max_tokens=50,
        temperature=0.7,
    )

@pytest.fixture
def mock_openai_chat_response():
    response_id = f"chatcmpl-{uuid4().hex}"
    created_time = int(time.time())
    mock_choice = MagicMock()
    mock_choice.message.content = "This is a response from OpenAI."
    mock_choice.finish_reason = "stop"
    
    mock_usage = MagicMock()
    mock_usage.prompt_tokens = 10
    mock_usage.completion_tokens = 20
    mock_usage.total_tokens = 30

    mock_response = AsyncMock() # For acreate
    mock_response.id = response_id
    mock_response.created = created_time
    mock_response.model = "gpt-3.5-turbo" # Echoes the model used
    mock_response.choices = [mock_choice]
    mock_response.usage = mock_usage
    return mock_response

@pytest.fixture
def mock_google_chat_response():
    mock_part = MagicMock()
    mock_part.text = "This is a response from Google."
    
    mock_candidate = MagicMock()
    mock_candidate.content.parts = [mock_part]
    # Google's response doesn't directly map to finish_reason or detailed usage in the same way
    # The router currently estimates tokens for Google.

    mock_response = MagicMock() # generate_content is synchronous in the SDK v1
    mock_response.candidates = [mock_candidate]
    # mock_response.usage_metadata (if available and needed for token count)
    return mock_response


def test_chat_completions_openai_success(chat_request_openai, mock_openai_chat_response):
    settings.openai.openai_api_key = "fake_openai_key"
    settings.google.google_api_key = None # Ensure only OpenAI is configured for this test

    with patch('openai.ChatCompletion.acreate', new_callable=AsyncMock, return_value=mock_openai_chat_response) as mock_acreate:
        response = client.post("/v1/chat/completions", json=chat_request_openai.model_dump())

        assert response.status_code == 200
        json_response = response.json()
        
        assert json_response["id"] == mock_openai_chat_response.id
        assert json_response["object"] == "chat.completion"
        assert json_response["model"].startswith("gpt-3.5") # OpenAI might return a more specific version
        assert json_response["choices"][0]["message"]["content"] == mock_openai_chat_response.choices[0].message.content
        assert json_response["choices"][0]["finish_reason"] == "stop"
        assert json_response["usage"]["prompt_tokens"] == mock_openai_chat_response.usage.prompt_tokens
        assert json_response["usage"]["completion_tokens"] == mock_openai_chat_response.usage.completion_tokens
        mock_acreate.assert_called_once()


def test_chat_completions_google_success(chat_request_google, mock_google_chat_response):
    settings.google.google_api_key = "fake_google_key"
    settings.openai.openai_api_key = None # Ensure only Google is configured

    # Patch the factory function that returns the model, then mock the model's method
    with patch('moonmind.factories.google_factory.get_google_model') as mock_get_google_model:
        mock_chat_model = MagicMock()
        mock_chat_model.generate_content.return_value = mock_google_chat_response
        mock_get_google_model.return_value = mock_chat_model

        response = client.post("/v1/chat/completions", json=chat_request_google.model_dump())

        assert response.status_code == 200
        json_response = response.json()

        assert json_response["object"] == "chat.completion"
        assert json_response["model"] == chat_request_google.model
        assert json_response["choices"][0]["message"]["content"] == mock_google_chat_response.candidates[0].content.parts[0].text.strip()
        assert json_response["choices"][0]["finish_reason"] == "stop" # Default for Google in current router
        # Token estimation for Google is done in the router, so we can check if they are present
        assert "prompt_tokens" in json_response["usage"]
        assert "completion_tokens" in json_response["usage"]
        assert "total_tokens" in json_response["usage"]
        mock_get_google_model.assert_called_once_with(chat_request_google.model)
        mock_chat_model.generate_content.assert_called_once()

def test_chat_completions_openai_no_api_key(chat_request_openai):
    settings.openai.openai_api_key = None
    response = client.post("/v1/chat/completions", json=chat_request_openai.model_dump())
    assert response.status_code == 400
    assert "OpenAI API key not configured" in response.json()["detail"]

def test_chat_completions_google_no_api_key(chat_request_google):
    settings.google.google_api_key = None
    # Ensure it's not trying to use OpenAI by mistake if model name implies it
    # For a model like "gemini-pro", it should clearly go to the Google path.
    response = client.post("/v1/chat/completions", json=chat_request_google.model_dump())
    assert response.status_code == 400
    assert "Google API key not configured" in response.json()["detail"]


@patch('openai.ChatCompletion.acreate', new_callable=AsyncMock)
def test_chat_completions_openai_api_error(mock_acreate, chat_request_openai):
    settings.openai.openai_api_key = "fake_openai_key"
    mock_acreate.side_effect = Exception("OpenAI API Error")
    
    response = client.post("/v1/chat/completions", json=chat_request_openai.model_dump())
    assert response.status_code == 500
    assert "OpenAI API error: OpenAI API Error" in response.json()["detail"]

@patch('moonmind.factories.google_factory.get_google_model')
def test_chat_completions_google_api_error(mock_get_google_model, chat_request_google):
    settings.google.google_api_key = "fake_google_key"
    mock_chat_model = MagicMock()
    mock_chat_model.generate_content.side_effect = Exception("Google API Error")
    mock_get_google_model.return_value = mock_chat_model
    
    response = client.post("/v1/chat/completions", json=chat_request_google.model_dump())
    assert response.status_code == 500
    assert "Google Gemini API error: Google API Error" in response.json()["detail"]

# Test for invalid role in Google model request (example from original chat.py)
def test_chat_completions_google_invalid_role(chat_request_google):
    settings.google.google_api_key = "fake_google_key"
    
    invalid_request_data = chat_request_google.model_dump()
    invalid_request_data["messages"] = [{"role": "invalid_role", "content": "test"}]

    # We expect a 400 if Pydantic validation on roles is strict, or if the mapping logic raises it.
    # The current router code maps unknown roles to 'user' with a warning, 
    # so the API call might proceed. If Gemini itself rejects it, that's an API error.
    # For this test, let's assume the mapping handles it, and the API call is made.
    # If Gemini API rejects the role (e.g., if 'model' role has no preceding 'user' message),
    # that would be caught by the general API error handler.
    # The specific "Please use a valid role" is caught if the error string matches.

    with patch('moonmind.factories.google_factory.get_google_model') as mock_get_google_model:
        mock_chat_model = MagicMock()
        # Simulate an error from Gemini that includes the role information
        mock_chat_model.generate_content.side_effect = Exception("Role error with Gemini API: Please use a valid role for your messages.")
        mock_get_google_model.return_value = mock_chat_model

        response = client.post("/v1/chat/completions", json=invalid_request_data)
        assert response.status_code == 400 # Due to the specific error message check
        assert "Role error with Google Gemini API" in response.json()["detail"]

# Restore original settings after all tests in this file are done
def teardown_function(function):
    settings.openai.openai_api_key = original_openai_api_key
    settings.google.google_api_key = original_google_api_key
