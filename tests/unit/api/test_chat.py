import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from fastapi import FastAPI
from fastapi.testclient import TestClient
from uuid import uuid4
import time

# Assuming the main app's router is imported correctly
# Ensure the path to chat_router is correct based on your project structure.
# If chat.py is in api_service.api.routers.chat, this should be fine.
from api_service.api.routers.chat import router as chat_router
from moonmind.config import settings  # Direct import for patching, used for teardown
from api_service.api.routers.chat import (
    settings as settings_in_chat_router,
)  # For patch.object
from moonmind.schemas.chat_models import ChatCompletionRequest, Message
# Updated import for the new auth dependency
from api_service.auth_providers import get_current_user # Import the actual dependency used by the router
from api_service.db.models import User # For mock user type hint
from api_service.db.base import get_async_session # For overriding db session

# Setup TestClient
app = FastAPI()
app.include_router(chat_router)  # Router paths are already prefixed

from api_service.api.dependencies import get_vector_index, get_service_context # noqa E402

# --- Mocking Dependencies ---
# Mock user for authentication
mock_user_instance = User(id=uuid4(), email="test@example.com", is_active=True, is_superuser=False, is_verified=True, hashed_password="fake")

# This function will be used to override the get_current_user dependency
def direct_override_get_current_user():
    return mock_user_instance

# Mock get_user_api_key
async def mock_get_user_api_key_logic(user: User, provider: str, db_session):
    # This print helps debug which provider is being asked for during tests.
    # print(f"mock_get_user_api_key_logic called for user {user.id}, provider {provider}")
    if provider == "OpenAI":
        # Allow specific tests to override this by changing the side_effect of mock_user_api_key_dependency
        return "sk-test-openai-key"
    elif provider == "Google":
        return "test-google-key" # Corrected this line
    elif provider == "Anthropic":
        return "test-anthropic-key"
    # Ollama doesn't need a key, so get_user_api_key shouldn't be called for it.
    # If provider is None or any other unhandled case:
    return None

mock_user_api_key_dependency = AsyncMock(side_effect=mock_get_user_api_key_logic)

# Mock database session
mock_db_session = AsyncMock()

# Mock other app state dependencies
mock_vector_index_dependency = MagicMock()
mock_llama_settings_dependency = MagicMock()

from api_service.auth import current_active_user as legacy_current_active_user # Import the actual legacy dependency

from api_service.auth import current_active_user as legacy_current_active_user_object_for_override

# Override dependencies in the app
app.dependency_overrides[get_current_user] = direct_override_get_current_user # Restore direct override
app.dependency_overrides[get_async_session] = lambda: mock_db_session # This IS a FastAPI dependency for chat_completions
app.dependency_overrides[get_vector_index] = lambda: mock_vector_index_dependency
app.dependency_overrides[get_service_context] = lambda: mock_llama_settings_dependency

client = TestClient(app)

# Store original API keys and restore them after tests
original_openai_api_key = settings.openai.openai_api_key
original_google_api_key = settings.google.google_api_key
# Attempt to get original cache refresh interval if it exists, otherwise default
original_model_cache_refresh_interval = getattr(
    settings, "model_cache_refresh_interval", 3600
)


def setup_module(module):
    """setup any state specific to the execution of the given module."""
    # Ensure model_cache is patched for all tests in this module if needed globally
    # Or patch it specifically within each test/fixture where it's used.
    pass


def teardown_module(module):
    """teardown any state that was previously setup with a setup_module
    method.
    """
    settings.openai.openai_api_key = original_openai_api_key
    settings.google.google_api_key = original_google_api_key
    settings.model_cache_refresh_interval = original_model_cache_refresh_interval
    # patch.stopall() # Avoid stopping all patches if some are managed by pytest-mock or context managers

@pytest.fixture(autouse=True)
def force_auth_provider_disabled_for_tests(monkeypatch):
    """Ensure AUTH_PROVIDER is 'disabled' for all tests in this module to use legacy auth path."""
    from moonmind.config import settings as settings_instance_for_auth_providers
    monkeypatch.setattr(settings_instance_for_auth_providers.oidc, "AUTH_PROVIDER", "disabled")
    # Also ensure the settings instance potentially used by the router itself is patched, if different.
    monkeypatch.setattr(settings_in_chat_router.oidc, "AUTH_PROVIDER", "disabled")
    yield

@pytest.fixture(autouse=True)  # Apply to all tests in this module
def cleanup_singleton_cache():
    # This fixture ensures that the ModelCache singleton is reset before each test.
    # It addresses the issue of state leakage between tests due to the singleton nature.
    with patch("moonmind.models_cache.ModelCache._instance", None):
        yield  # Test runs here
    # No explicit teardown needed here as the patch resets _instance for the next test's setup phase.


@pytest.fixture
def chat_request_openai_model():
    return ChatCompletionRequest(
        model="gpt-3.5-turbo",  # A model ID that model_cache.get_model_provider would identify as OpenAI
        messages=[Message(role="user", content="Hello OpenAI from cache test")],
        max_tokens=50,
        temperature=0.7,
    )


@pytest.fixture
def chat_request_google_model():
    return ChatCompletionRequest(
        model="models/gemini-pro",  # A model ID that model_cache.get_model_provider would identify as Google
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

    # Replicate the structure of openai.types.chat.ChatCompletion
    mock_response = MagicMock(spec=True) # Using MagicMock, spec=True can help catch attribute errors
    mock_response.id = response_id
    mock_response.created = created_time
    mock_response.model = "gpt-3.5-turbo-mocked"

    # Replicate openai.types.chat.chat_completion.Choice
    choice_mock = MagicMock()
    choice_mock.finish_reason = "stop"
    choice_mock.index = 0
    # Replicate openai.types.chat.chat_completion_message.ChatCompletionMessage
    message_mock = MagicMock()
    message_mock.content = "This is a response from OpenAI (mocked)."
    message_mock.role = "assistant"
    message_mock.function_call = None
    message_mock.tool_calls = None
    choice_mock.message = message_mock
    choice_mock.logprobs = None
    mock_response.choices = [choice_mock]

    # Replicate openai.types.completion_usage.CompletionUsage
    usage_mock = MagicMock()
    usage_mock.prompt_tokens = 15
    usage_mock.completion_tokens = 25
    usage_mock.total_tokens = 40
    mock_response.usage = usage_mock

    mock_response.object = "chat.completion"
    mock_response.system_fingerprint = None
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
@patch("api_service.api.routers.chat.model_cache.get_model_provider")
@patch("api_service.api.routers.chat.get_user_api_key", new_callable=AsyncMock)
@patch("api_service.api.routers.chat.AsyncOpenAI") # Corrected patch target
@patch("api_service.api.routers.chat.get_current_user") # Added patch
def test_chat_completions_openai_via_cache(
    mock_chat_get_current_user, # Added mock
    mock_async_openai_client, # Patched AsyncOpenAI
    mock_get_user_api_key_helper, # Patched get_user_api_key
    mock_get_provider, # Patched model_cache.get_model_provider
    chat_request_openai_model,
    mock_openai_chat_response, # This fixture might need update for new SDK response structure
):
    mock_chat_get_current_user.return_value = mock_user_instance # Configure mock
    # settings.openai.openai_api_key = "fake_openai_key_for_test" # Global key not primary focus
    mock_get_provider.return_value = "OpenAI"
    mock_get_user_api_key_helper.return_value = "sk-test-user-key"

    # Configure the mock AsyncOpenAI client instance and its methods
    mock_client_instance = AsyncMock()
    mock_client_instance.chat.completions.create = AsyncMock(return_value=mock_openai_chat_response)
    mock_async_openai_client.return_value = mock_client_instance # AsyncOpenAI() call returns our mock_client_instance

    response = client.post("/completions", json=chat_request_openai_model.model_dump())

    assert response.status_code == 200
    json_response = response.json()
    # Assertions need to align with mock_openai_chat_response structure (which should mimic new SDK)
    assert json_response["model"] == mock_openai_chat_response.model
    assert (
        json_response["choices"][0]["message"]["content"]
        == mock_openai_chat_response.choices[0].message.content
    )
    mock_get_provider.assert_called_once_with(chat_request_openai_model.model)
    mock_async_openai_client.assert_called_once_with(api_key="sk-test-user-key")
    mock_client_instance.chat.completions.create.assert_called_once()


# Test with model_cache integration and corrected path
@patch("api_service.api.routers.chat.model_cache.get_model_provider")
@patch("api_service.api.routers.chat.get_user_api_key", new_callable=AsyncMock)
@patch("api_service.api.routers.chat.AsyncOpenAI") # Corrected patch target
def test_chat_completions_endpoint_success_corrected_path(
    mock_async_openai_client, # Patched AsyncOpenAI
    mock_get_user_api_key_helper, # Patched get_user_api_key
    mock_get_provider, # Patched model_cache.get_model_provider
    chat_request_openai_model,
    mock_openai_chat_response, # This fixture might need update for new SDK response structure
):
    settings_in_chat_router.openai.openai_api_key = "fake_openai_key_for_test" # Can remain for server enabled check if any
    mock_get_provider.return_value = "OpenAI"
    mock_get_user_api_key_helper.return_value = "sk-test-user-key"

    # Configure the mock AsyncOpenAI client instance and its methods
    mock_client_instance = AsyncMock()
    mock_client_instance.chat.completions.create = AsyncMock(return_value=mock_openai_chat_response)
    mock_async_openai_client.return_value = mock_client_instance

    response = client.post(
        "/completions", json=chat_request_openai_model.model_dump()
    )

    assert response.status_code == 200
    json_response = response.json()
    assert json_response["model"] == mock_openai_chat_response.model
    assert (
        json_response["choices"][0]["message"]["content"]
        == mock_openai_chat_response.choices[0].message.content
    )
    mock_get_provider.assert_called_once_with(chat_request_openai_model.model)
    mock_async_openai_client.assert_called_once_with(api_key="sk-test-user-key")
    mock_client_instance.chat.completions.create.assert_called_once()


@patch("google.generativeai.configure")
@patch("api_service.api.routers.chat.get_google_model")
@patch("api_service.api.routers.chat.model_cache.get_model_provider")
@patch("api_service.api.routers.chat.get_user_api_key", new_callable=AsyncMock) # Patching the helper
def test_chat_completions_google_via_cache(
    mock_get_user_api_key_helper, # Innermost due to decorator order
    mock_get_model_provider,
    mock_router_get_google_model,
    mock_genai_configure,
    chat_request_google_model,
    mock_google_chat_response,
):
    # Directly set attributes on the imported settings instance
    settings_in_chat_router.google.google_enabled = True
    settings_in_chat_router.google.google_api_key = "fake_google_key_for_test" # This is for the global key, not user specific

    # Setup the new mock for get_user_api_key helper
    mock_get_user_api_key_helper.return_value = "test-google-key"

    mock_get_model_provider.return_value = "Google"
    mock_google_chat_model_instance = MagicMock()
    mock_google_chat_model_instance.generate_content.return_value = (
        mock_google_chat_response
    )
    mock_router_get_google_model.return_value = mock_google_chat_model_instance

    response = client.post("/completions", json=chat_request_google_model.model_dump())

    assert response.status_code == 200
    json_response = response.json()
    assert json_response["model"] == chat_request_google_model.model
    assert (
        json_response["choices"][0]["message"]["content"]
        == mock_google_chat_response.candidates[0].content.parts[0].text.strip()
    )
    mock_get_model_provider.assert_called_once_with(chat_request_google_model.model)
    # Adjust the assertion to expect the api_key argument
    mock_router_get_google_model.assert_called_once_with(
        model_name=chat_request_google_model.model, api_key="test-google-key" # Expected from mock_get_user_api_key_logic
    )
    mock_google_chat_model_instance.generate_content.assert_called_once()
    # No mock_is_enabled_google to assert anymore


# Refined Google Error Handling Tests
@patch("google.generativeai.configure")
@patch("api_service.api.routers.chat.get_google_model")  # CORRECTED TARGET
@patch("api_service.api.routers.chat.model_cache.get_model_provider")
# Removed patch for get_user_api_key
def test_chat_completions_google_value_error_invalid_role(
    # mock_get_user_api_key, # Removed
    mock_get_model_provider,
    mock_router_get_google_model,
    mock_genai_configure,
    chat_request_google_model,
):
    # mock_get_user_api_key.return_value = "fake-user-api-key"
    settings_in_chat_router.google.google_enabled = True
    settings_in_chat_router.google.google_api_key = "fake_google_key_for_test"

    mock_get_model_provider.return_value = "Google"
    mock_google_chat_model_instance = MagicMock()
    error_message = "Invalid role: 'system' is not a valid role for this model. Valid roles are 'user', 'model'."
    mock_google_chat_model_instance.generate_content.side_effect = ValueError(
        error_message
    )
    mock_router_get_google_model.return_value = mock_google_chat_model_instance

    response = client.post("/completions", json=chat_request_google_model.model_dump())

    assert response.status_code == 400
    json_response = response.json()
    assert "Role or turn order error with Gemini API" in json_response["detail"]


@patch("google.generativeai.configure")
@patch("api_service.api.routers.chat.get_google_model")  # CORRECTED TARGET
@patch("api_service.api.routers.chat.model_cache.get_model_provider")
@patch("api_service.api.routers.chat.get_user_api_key", new_callable=AsyncMock) # Patch get_user_api_key
def test_chat_completions_google_value_error_other_argument(
    mock_get_user_api_key, # Add mock argument
    mock_get_model_provider,
    mock_router_get_google_model,
    mock_genai_configure,
    chat_request_google_model,
):
    mock_get_user_api_key.return_value = "fake-user-api-key" # Configure it
    settings_in_chat_router.google.google_enabled = True
    settings_in_chat_router.google.google_api_key = "fake_google_key_for_test"

    mock_get_model_provider.return_value = "Google"
    mock_google_chat_model_instance = MagicMock()
    error_message = "Some other argument error not related to roles."
    mock_google_chat_model_instance.generate_content.side_effect = ValueError(
        error_message
    )
    mock_router_get_google_model.return_value = mock_google_chat_model_instance

    response = client.post("/completions", json=chat_request_google_model.model_dump())

    assert response.status_code == 500
    json_response = response.json()
    assert f"Google Gemini API error: {error_message}" in json_response["detail"]


# Cache behavior for unknown models
@patch("api_service.api.routers.chat.model_cache.get_model_provider")
@patch(
    "api_service.api.routers.chat.model_cache.refresh_models_sync"
)  # Mock the sync refresh
def test_chat_completions_model_not_found_initial_and_after_refresh(
    mock_refresh_sync, mock_get_provider, chat_request_unknown_model
):
    # Simulate provider returning None for the first call, and also for the second call after refresh
    mock_get_provider.side_effect = [None, None]

    response = client.post("/completions", json=chat_request_unknown_model.model_dump())

    assert response.status_code == 404
    assert (
        f"Model '{chat_request_unknown_model.model}' not found or provider unknown."
        in response.json()["detail"]
    )
    mock_refresh_sync.assert_called_once()  # Ensure refresh was attempted
    assert mock_get_provider.call_count == 2


@patch("api_service.api.routers.chat.model_cache.get_model_provider")
@patch("api_service.api.routers.chat.model_cache.refresh_models_sync")
@patch("openai.ChatCompletion.acreate", new_callable=AsyncMock)  # For the retry path
def test_chat_completions_model_found_after_refresh_openai(
    mock_acreate,
    mock_refresh_sync,
    mock_get_provider,
    chat_request_openai_model,
    mock_openai_chat_response,
):
    settings.openai.openai_api_key = "fake_openai_key_for_test"
    # First call to get_model_provider returns None, second call returns "OpenAI"
    mock_get_provider.side_effect = [None, "OpenAI"]
    mock_acreate.return_value = mock_openai_chat_response  # Mock the OpenAI call that would happen after successful refresh

    response = client.post("/completions", json=chat_request_openai_model.model_dump())

    # The current code in chat.py raises a specific 404 for "retry logic needs full implementation"
    # This test verifies that behavior. If full retry were implemented, this would be a 200.
    assert response.status_code == 404
    assert (
        f"Model '{chat_request_openai_model.model}' found after refresh, but retry logic needs full implementation."
        in response.json()["detail"]
    )
    mock_refresh_sync.assert_called_once()
    assert mock_get_provider.call_count == 2
    # mock_acreate.assert_called_once() # This would be called if retry was fully implemented and led to a 200


# API Key Checks (still relevant with cache)
@patch("api_service.api.routers.chat.model_cache.get_model_provider")
@patch("api_service.api.routers.chat.get_user_api_key", new_callable=AsyncMock)
def test_chat_completions_openai_no_api_key_with_cache(
    mock_get_user_api_key_helper, mock_get_provider, chat_request_openai_model
):
    # settings.openai.openai_api_key = None # Global key not relevant here for user-specific check
    mock_get_provider.return_value = "OpenAI"
    mock_get_user_api_key_helper.return_value = None # Simulate user has no key for OpenAI

    response = client.post("/completions", json=chat_request_openai_model.model_dump())
    assert response.status_code == 400
    assert "Provide a key" in response.json()["detail"]


@patch("api_service.api.routers.chat.model_cache.get_model_provider")
@patch("api_service.api.routers.chat.get_user_api_key", new_callable=AsyncMock)
def test_chat_completions_google_no_api_key_with_cache(
    mock_get_user_api_key_helper, mock_get_provider, chat_request_google_model
):
    # settings.google.google_api_key = None # Global key not relevant
    mock_get_provider.return_value = "Google"
    mock_get_user_api_key_helper.return_value = None # Simulate user has no key for Google

    response = client.post("/completions", json=chat_request_google_model.model_dump())
    assert response.status_code == 400
    assert "Provide a key" in response.json()["detail"]


# General API errors after successful routing by cache
@patch("api_service.api.routers.chat.model_cache.get_model_provider")
@patch("api_service.api.routers.chat.get_user_api_key", new_callable=AsyncMock)
@patch("api_service.api.routers.chat.AsyncOpenAI") # Corrected patch target
def test_chat_completions_openai_api_error_with_cache(
    mock_async_openai_client, # Patched AsyncOpenAI
    mock_get_user_api_key_helper, # Patched get_user_api_key
    mock_get_provider, # Patched model_cache.get_model_provider
    chat_request_openai_model
):
    # settings.openai.openai_api_key = "fake_openai_key_for_test" # Global key not primary
    mock_get_provider.return_value = "OpenAI"
    mock_get_user_api_key_helper.return_value = "sk-test-user-key"

    # Configure the mock AsyncOpenAI client instance to raise an error
    mock_client_instance = AsyncMock()
    mock_client_instance.chat.completions.create = AsyncMock(side_effect=Exception("OpenAI API Communication Error"))
    mock_async_openai_client.return_value = mock_client_instance

    response = client.post("/completions", json=chat_request_openai_model.model_dump())
    assert response.status_code == 500
    assert (
        "OpenAI API error: OpenAI API Communication Error" in response.json()["detail"] # This error message comes from the handler
    )
    mock_async_openai_client.assert_called_once_with(api_key="sk-test-user-key")


@patch("google.generativeai.configure")
@patch("api_service.api.routers.chat.get_google_model")  # CORRECTED TARGET
@patch("api_service.api.routers.chat.model_cache.get_model_provider")
@patch("api_service.api.routers.chat.get_user_api_key", new_callable=AsyncMock) # Patch get_user_api_key
def test_chat_completions_google_api_error_with_cache(
    mock_get_user_api_key, # Add mock argument
    mock_get_model_provider,
    mock_router_get_google_model,
    mock_genai_configure,
    chat_request_google_model,
):
    mock_get_user_api_key.return_value = "fake-user-api-key" # Configure it
    settings_in_chat_router.google.google_enabled = True
    settings_in_chat_router.google.google_api_key = "fake_google_key_for_test"

    mock_get_model_provider.return_value = "Google"
    mock_google_chat_model_instance = MagicMock()
    mock_google_chat_model_instance.generate_content.side_effect = Exception(
        "Google API Communication Error"
    )  # Corrected typo
    mock_router_get_google_model.return_value = mock_google_chat_model_instance

    response = client.post("/completions", json=chat_request_google_model.model_dump())
    assert response.status_code == 500
    assert (
        "Google Gemini API error: Google API Communication Error"
        in response.json()["detail"]
    )


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
        model="llama2",  # A model ID that model_cache.get_model_provider would identify as Ollama
        messages=[Message(role="user", content="Hello Ollama from cache test")],
        max_tokens=50,
        temperature=0.7,
    )


# Tests for provider enablement functionality
@patch("api_service.api.routers.chat.model_cache.get_model_provider")
@patch("api_service.api.routers.chat.get_user_api_key", new_callable=AsyncMock)
def test_chat_completions_openai_provider_disabled(
    mock_get_user_api_key_helper, mock_get_provider, chat_request_openai_model
):
    """Test that disabled OpenAI provider returns appropriate error"""
    settings_in_chat_router.openai.openai_enabled = False
    settings_in_chat_router.openai.openai_api_key = "fake_key" # Global key
    # settings.openai.openai_api_key = "fake_openai_key_for_test" # Original line for teardown reference
    mock_get_user_api_key_helper.return_value = "sk-test-openai-key" # User has a key, but provider is off


    mock_get_provider.return_value = "OpenAI"

    response = client.post("/completions", json=chat_request_openai_model.model_dump())
    assert response.status_code == 400
    json_response = response.json()
    # The error message comes from handle_openai_request in chat.py
    assert "OpenAI provider is disabled on the server." in json_response["detail"]


@patch("api_service.api.routers.chat.model_cache.get_model_provider")
# Removed patch for get_user_api_key
def test_chat_completions_google_provider_disabled(
    # mock_get_user_api_key, # Removed
    mock_get_provider, chat_request_google_model
):
    """Test that disabled Google provider returns appropriate error"""
    # mock_get_user_api_key.return_value = "fake-user-api-key"
    settings_in_chat_router.google.google_enabled = False
    settings_in_chat_router.google.google_api_key = "fake_key"
    # settings.google.google_api_key = "fake_google_key_for_test" # Original line for teardown reference

    mock_get_provider.return_value = "Google"

    response = client.post("/completions", json=chat_request_google_model.model_dump())
    assert response.status_code == 400
    json_response = response.json()
    # The error message comes from handle_google_request in chat.py
    assert "Google provider is disabled on the server." in json_response["detail"]


@patch("api_service.api.routers.chat.model_cache.get_model_provider")
@patch("moonmind.factories.ollama_factory.chat_with_ollama", new_callable=AsyncMock)
def test_chat_completions_ollama_provider_disabled(
    mock_chat_ollama, mock_get_provider, chat_request_ollama_model
):
    """Test that disabled Ollama provider returns appropriate error"""
    settings_in_chat_router.ollama.ollama_enabled = False

    mock_get_provider.return_value = "Ollama"

    response = client.post("/completions", json=chat_request_ollama_model.model_dump())
    assert response.status_code == 400
    json_response = response.json()
    # The error message comes from handle_ollama_request in chat.py
    assert "Ollama provider is disabled or not available." in json_response["detail"]


# Test default model functionality
@patch("api_service.api.routers.chat.model_cache.get_model_provider")
@patch("api_service.api.routers.chat.get_user_api_key", new_callable=AsyncMock)
@patch("api_service.api.routers.chat.AsyncOpenAI") # Corrected patch target
def test_chat_completions_uses_default_model(
    mock_async_openai_client, # Patched AsyncOpenAI
    mock_get_user_api_key_helper, # Patched get_user_api_key
    mock_get_provider, # Patched model_cache.get_model_provider
    chat_request_no_model,
    mock_openai_chat_response # Fixture for response
):
    """Test that default model is used when no model is specified"""

    settings_in_chat_router.default_chat_provider = "openai"
    # Default model (e.g., "gpt-3.5-turbo") will be fetched by settings.get_default_chat_model()

    settings_in_chat_router.openai.openai_enabled = True
    # settings_in_chat_router.openai.openai_api_key = "fake_openai_key_for_test" # Global key not primary

    mock_get_provider.return_value = "OpenAI" # Model cache identifies the default model as OpenAI
    mock_get_user_api_key_helper.return_value = "sk-test-user-key" # User has a key

    # Configure the mock AsyncOpenAI client
    mock_client_instance = AsyncMock()
    mock_client_instance.chat.completions.create = AsyncMock(return_value=mock_openai_chat_response)
    mock_async_openai_client.return_value = mock_client_instance

    response = client.post("/completions", json=chat_request_no_model.model_dump())

    assert response.status_code == 200
    # Assert that get_model_provider was called with the actual default model name
    # The actual default model name comes from settings.get_default_chat_model()
    # For this test, let's assume it's "gpt-3.5-turbo" as per current settings default.
    # If settings.get_default_chat_model() is complex, this might need adjustment or specific mocking of settings.
    actual_default_model = settings.get_default_chat_model() # Get it dynamically
    mock_get_provider.assert_called_once_with(actual_default_model)
    mock_async_openai_client.assert_called_once_with(api_key="sk-test-user-key")
    mock_client_instance.chat.completions.create.assert_called_once()


# Test Ollama integration
@pytest.fixture
def mock_ollama_chat_response():
    return {
        "message": {
            "content": "This is a response from Ollama (mocked).",
            "role": "assistant",
        },
        "done": True,
    }


@patch(
    "moonmind.factories.ollama_factory.list_ollama_models", new_callable=AsyncMock
)  # Outermost
@patch("api_service.api.routers.chat.model_cache.get_model_provider")  # Middle
@patch(
    "api_service.api.routers.chat.chat_with_ollama", new_callable=AsyncMock
)  # Middle - CORRECTED TARGET
@patch("api_service.api.routers.chat.get_ollama_model")  # Innermost - CORRECTED TARGET
def test_chat_completions_ollama_success(
    mock_router_get_ollama_model,  # Innermost
    mock_router_chat_with_ollama,  # Middle
    mock_get_provider,  # Middle
    mock_list_ollama_models,  # Outermost
    chat_request_ollama_model,
    mock_ollama_chat_response,
):
    """Test successful Ollama chat completion"""
    settings_in_chat_router.ollama.ollama_enabled = True

    mock_list_ollama_models.return_value = []  # Prevent ModelCache network call
    mock_get_provider.return_value = "Ollama"
    mock_router_get_ollama_model.return_value = "llama2"
    mock_router_chat_with_ollama.return_value = mock_ollama_chat_response

    response = client.post("/completions", json=chat_request_ollama_model.model_dump())

    assert response.status_code == 200
    json_response = response.json()
    assert json_response["model"] == "llama2"
    assert (
        json_response["choices"][0]["message"]["content"]
        == mock_ollama_chat_response["message"]["content"]
    )
    mock_router_chat_with_ollama.assert_called_once()


@patch(
    "moonmind.factories.ollama_factory.list_ollama_models", new_callable=AsyncMock
)  # Outermost
@patch("api_service.api.routers.chat.model_cache.get_model_provider")  # Middle
@patch(
    "api_service.api.routers.chat.chat_with_ollama", new_callable=AsyncMock
)  # Middle - CORRECTED TARGET
@patch("api_service.api.routers.chat.get_ollama_model")  # Innermost - CORRECTED TARGET
def test_chat_completions_ollama_api_error(
    mock_router_get_ollama_model,  # Innermost
    mock_router_chat_with_ollama,  # Middle
    mock_get_provider,  # Middle
    mock_list_ollama_models,  # Outermost
    chat_request_ollama_model,
):
    """Test Ollama API error handling"""
    settings_in_chat_router.ollama.ollama_enabled = True

    mock_list_ollama_models.return_value = []  # Prevent ModelCache network call
    mock_get_provider.return_value = "Ollama"
    mock_router_get_ollama_model.return_value = "llama2"
    mock_router_chat_with_ollama.side_effect = Exception("Ollama connection error")

    response = client.post("/completions", json=chat_request_ollama_model.model_dump())

    assert response.status_code == 500
    json_response = response.json()
    assert "Ollama API error: Ollama connection error" in json_response["detail"]


@patch(
    "moonmind.factories.ollama_factory.list_ollama_models", new_callable=AsyncMock
)  # Outermost
@patch("api_service.api.routers.chat.model_cache.get_model_provider")  # Middle
@patch(
    "api_service.api.routers.chat.chat_with_ollama", new_callable=AsyncMock
)  # Middle - CORRECTED TARGET
@patch("api_service.api.routers.chat.get_ollama_model")  # Innermost - CORRECTED TARGET
def test_chat_completions_ollama_invalid_response(
    mock_router_get_ollama_model,  # Innermost
    mock_router_chat_with_ollama,  # Middle
    mock_get_provider,  # Middle
    mock_list_ollama_models,  # Outermost
    chat_request_ollama_model,
):
    """Test Ollama invalid response handling"""
    settings_in_chat_router.ollama.ollama_enabled = True

    mock_list_ollama_models.return_value = []  # Prevent ModelCache network call
    mock_get_provider.return_value = "Ollama"
    mock_router_get_ollama_model.return_value = "llama2"
    mock_router_chat_with_ollama.return_value = {
        "invalid": "response"
    }  # Missing message field

    response = client.post("/completions", json=chat_request_ollama_model.model_dump())

    assert response.status_code == 500
    json_response = response.json()
    assert (
        "Invalid response from Ollama API: No message returned"
        in json_response["detail"]
    )
