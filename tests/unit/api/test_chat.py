import time
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# Assuming the main app's router is imported correctly
# Ensure the path to chat_router is correct based on your project structure.
# If chat.py is in api_service.api.routers.chat, this should be fine.
from api_service.api.routers.chat import (
    _extract_openai_response_text,
    _normalize_openai_response_payload,
    _response_content_to_text,
    handle_anthropic_request,
    handle_google_request,
    handle_openai_request,
    responses_router,
    router as chat_router,
)
from api_service.api.routers.chat import (
    settings as settings_in_chat_router,  # For patch.object
)

# Updated import for the new auth dependency
from api_service.auth_providers import (  # Import the actual dependency used by the router
    get_current_user,
)
from api_service.db.base import get_async_session  # For overriding db session
from api_service.db.models import User  # For mock user type hint
from moonmind.config import settings  # Direct import for patching, used for teardown
from moonmind.schemas.chat_models import ChatCompletionRequest, Message

# Setup TestClient
app = FastAPI()
app.include_router(chat_router)  # Router paths are already prefixed
app.include_router(responses_router)

from api_service.api.dependencies import (  # noqa E402
    get_service_context,
    get_vector_index,
)

# --- Mocking Dependencies ---
# Mock user for authentication
mock_user_instance = User(
    id=uuid4(),
    email="test@example.com",
    is_active=True,
    is_superuser=False,
    is_verified=True,
    hashed_password="fake",
)

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
        return "test-google-key"  # Corrected this line
    elif provider == "Anthropic":
        return "test-anthropic-key"
    # If provider is None or any other unhandled case:
    return None

mock_user_api_key_dependency = AsyncMock(side_effect=mock_get_user_api_key_logic)

# Mock database session
mock_db_session = AsyncMock()

# Mock other app state dependencies
mock_vector_index_dependency = MagicMock()
mock_llama_settings_dependency = MagicMock()

# Override dependencies in the app
app.dependency_overrides[get_current_user] = (
    direct_override_get_current_user  # Restore direct override
)
app.dependency_overrides[get_async_session] = (
    lambda: mock_db_session
)  # This IS a FastAPI dependency for chat_completions
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

    monkeypatch.setattr(
        settings_instance_for_auth_providers.oidc, "AUTH_PROVIDER", "disabled"
    )
    # Also ensure the settings instance potentially used by the router itself is patched, if different.
    monkeypatch.setattr(settings_in_chat_router.oidc, "AUTH_PROVIDER", "disabled")
    monkeypatch.setattr(settings_in_chat_router.security, "high_security_mode", False)
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
    mock_response = MagicMock(
        spec=True
    )  # Using MagicMock, spec=True can help catch attribute errors
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
@patch("moonmind.models_cache.model_cache.get_model_provider")
@patch("api_service.api.routers.chat.get_user_api_key", new_callable=AsyncMock)
@patch("api_service.api.routers.chat.AsyncOpenAI")  # Corrected patch target
@patch("api_service.api.routers.chat.get_current_user")  # Added patch
def test_chat_completions_openai_via_cache(
    mock_chat_get_current_user,  # Added mock
    mock_async_openai_client,  # Patched AsyncOpenAI
    mock_get_user_api_key_helper,  # Patched get_user_api_key
    mock_get_provider,  # Patched model_cache.get_model_provider
    chat_request_openai_model,
    mock_openai_chat_response,  # This fixture might need update for new SDK response structure
):
    mock_chat_get_current_user.return_value = mock_user_instance  # Configure mock
    # settings.openai.openai_api_key = "fake_openai_key_for_test" # Global key not primary focus
    mock_get_provider.return_value = "OpenAI"
    mock_get_user_api_key_helper.return_value = "sk-test-user-key"

    # Configure the mock AsyncOpenAI client instance and its methods
    mock_client_instance = AsyncMock()
    mock_client_instance.chat.completions.create = AsyncMock(
        return_value=mock_openai_chat_response
    )
    mock_async_openai_client.return_value = (
        mock_client_instance  # AsyncOpenAI() call returns our mock_client_instance
    )

    response = client.post("/completions", json=chat_request_openai_model.model_dump())

    assert response.status_code == 200
    json_response = response.json()
    # Assertions need to align with mock_openai_chat_response structure (which should mimic new SDK)
    assert json_response["model"] == mock_openai_chat_response.model
    assert json_response["usage"]["cost_estimate_usd"] == 0.000045
    assert json_response["usage"]["pricing_source"] == "built_in"
    assert (
        json_response["choices"][0]["message"]["content"]
        == mock_openai_chat_response.choices[0].message.content
    )
    mock_get_provider.assert_called_once_with(chat_request_openai_model.model)
    mock_async_openai_client.assert_called_once_with(api_key="sk-test-user-key")
    mock_client_instance.chat.completions.create.assert_called_once()

# Test with model_cache integration and corrected path
@patch("moonmind.models_cache.model_cache.get_model_provider")
@patch("api_service.api.routers.chat.get_user_api_key", new_callable=AsyncMock)
@patch("api_service.api.routers.chat.AsyncOpenAI")  # Corrected patch target
def test_chat_completions_endpoint_success_corrected_path(
    mock_async_openai_client,  # Patched AsyncOpenAI
    mock_get_user_api_key_helper,  # Patched get_user_api_key
    mock_get_provider,  # Patched model_cache.get_model_provider
    chat_request_openai_model,
    mock_openai_chat_response,  # This fixture might need update for new SDK response structure
):
    settings_in_chat_router.openai.openai_api_key = (
        "fake_openai_key_for_test"  # Can remain for server enabled check if any
    )
    mock_get_provider.return_value = "OpenAI"
    mock_get_user_api_key_helper.return_value = "sk-test-user-key"

    # Configure the mock AsyncOpenAI client instance and its methods
    mock_client_instance = AsyncMock()
    mock_client_instance.chat.completions.create = AsyncMock(
        return_value=mock_openai_chat_response
    )
    mock_async_openai_client.return_value = mock_client_instance

    response = client.post("/completions", json=chat_request_openai_model.model_dump())

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

@patch("moonmind.factories.openai_factory.get_openai_model", return_value="gpt-3.5-turbo")
@patch("moonmind.models_cache.model_cache.get_model_provider")
@patch("api_service.api.routers.chat.get_user_api_key", new_callable=AsyncMock)
@patch("api_service.api.routers.chat.AsyncOpenAI")
def test_chat_completions_blocks_secret_before_provider_send(
    mock_async_openai_client,
    mock_get_user_api_key_helper,
    mock_get_provider,
    _mock_get_openai_model,
    monkeypatch,
    chat_request_openai_model,
):
    monkeypatch.setattr(settings_in_chat_router.security, "high_security_mode", True)
    mock_get_provider.return_value = "OpenAI"
    mock_get_user_api_key_helper.return_value = "sk-test-user-key"
    chat_request_openai_model.messages[0].content = "Use token=super-secret-value"

    response = client.post("/completions", json=chat_request_openai_model.model_dump())

    assert response.status_code == 422
    assert "chat.openai.messages[0].content" in response.json()["detail"]
    mock_async_openai_client.assert_not_called()


@patch("moonmind.factories.openai_factory.get_openai_model", return_value="gpt-3.5-turbo")
@patch("moonmind.models_cache.model_cache.get_model_provider")
@patch("api_service.api.routers.chat.get_user_api_key", new_callable=AsyncMock)
@patch("api_service.api.routers.chat.AsyncOpenAI")
def test_chat_completions_blocks_secret_before_provider_send(
    mock_async_openai_client,
    mock_get_user_api_key_helper,
    mock_get_provider,
    _mock_get_openai_model,
    monkeypatch,
    chat_request_openai_model,
):
    monkeypatch.setattr(settings_in_chat_router.security, "high_security_mode", True)
    mock_get_provider.return_value = "OpenAI"
    mock_get_user_api_key_helper.return_value = "sk-test-user-key"
    chat_request_openai_model.messages[0].content = "Use token=super-secret-value"

    response = client.post("/completions", json=chat_request_openai_model.model_dump())

    assert response.status_code == 422
    assert "chat.openai.messages[0].content" in response.json()["detail"]
    mock_async_openai_client.assert_not_called()


def test_openai_response_normalization_preserves_zero_token_usage() -> None:
    payload = _normalize_openai_response_payload(
        {
            "model": "gpt-4o-mini",
            "output_text": "ok",
            "usage": {
                "input_tokens": 0,
                "prompt_tokens": 1_000,
                "output_tokens": 0,
                "completion_tokens": 1_000,
            },
        },
        fallback_model="gpt-4o-mini",
        metadata={},
    )

    assert payload["usage"]["cost_estimate_usd"] == 0
    assert payload["metadata"]["billing"]["inputTokens"] == 0
    assert payload["metadata"]["billing"]["outputTokens"] == 0


@patch("google.generativeai.configure")
@patch("moonmind.factories.google_factory.get_google_model")
@patch("moonmind.models_cache.model_cache.get_model_provider")
@patch(
    "api_service.api.routers.chat.get_user_api_key", new_callable=AsyncMock
)  # Patching the helper
def test_chat_completions_google_via_cache(
    mock_get_user_api_key_helper,  # Innermost due to decorator order
    mock_get_model_provider,
    mock_router_get_google_model,
    mock_genai_configure,
    chat_request_google_model,
    mock_google_chat_response,
):
    # Directly set attributes on the imported settings instance
    settings_in_chat_router.google.google_enabled = True
    settings_in_chat_router.google.google_api_key = (
        "fake_google_key_for_test"  # This is for the global key, not user specific
    )

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
    assert json_response["usage"]["cost_estimate_usd"] is not None
    assert json_response["usage"]["pricing_source"] == "built_in"
    assert (
        json_response["choices"][0]["message"]["content"]
        == mock_google_chat_response.candidates[0].content.parts[0].text.strip()
    )
    mock_get_model_provider.assert_called_once_with(chat_request_google_model.model)
    # Adjust the assertion to expect the api_key argument
    mock_router_get_google_model.assert_called_once_with(
        model_name=chat_request_google_model.model,
        api_key="test-google-key",  # Expected from mock_get_user_api_key_logic
    )
    mock_google_chat_model_instance.generate_content.assert_called_once()
    # No mock_is_enabled_google to assert anymore


@pytest.mark.asyncio
@patch("api_service.api.routers.chat.AsyncOpenAI")
async def test_handle_openai_request_blocks_secret_before_provider_call(
    mock_async_openai_client,
    monkeypatch,
):
    monkeypatch.setattr(settings_in_chat_router.security, "high_security_mode", True)
    settings_in_chat_router.openai.openai_enabled = True
    request = ChatCompletionRequest(
        model="gpt-3.5-turbo",
        messages=[Message(role="user", content="Use token=blocked-secret-value")],
    )

    with pytest.raises(Exception) as exc_info:
        await handle_openai_request(
            request,
            request.messages,
            "gpt-3.5-turbo",
            "sk-test-openai-key",
        )

    assert "chat.openai.messages[0].content" in str(exc_info.value)
    assert "blocked-secret-value" not in str(exc_info.value)
    mock_async_openai_client.assert_not_called()


@pytest.mark.asyncio
@patch("moonmind.factories.google_factory.get_google_model")
async def test_handle_google_request_blocks_secret_before_provider_call(
    mock_get_google_model,
    monkeypatch,
):
    monkeypatch.setattr(settings_in_chat_router.security, "high_security_mode", True)
    settings_in_chat_router.google.google_enabled = True
    request = ChatCompletionRequest(
        model="models/gemini-pro",
        messages=[Message(role="user", content="Use token=blocked-secret-value")],
    )

    with pytest.raises(Exception) as exc_info:
        await handle_google_request(
            request,
            request.messages,
            "models/gemini-pro",
            "test-google-key",
        )

    assert "chat.google.messages[0].content" in str(exc_info.value)
    assert "blocked-secret-value" not in str(exc_info.value)
    mock_get_google_model.assert_not_called()


@pytest.mark.asyncio
@patch("moonmind.factories.anthropic_factory.AnthropicFactory.create_anthropic_model")
async def test_handle_anthropic_request_blocks_secret_before_provider_call(
    mock_create_anthropic_model,
    monkeypatch,
):
    monkeypatch.setattr(settings_in_chat_router.security, "high_security_mode", True)
    settings_in_chat_router.anthropic.anthropic_enabled = True
    request = ChatCompletionRequest(
        model="claude-test",
        messages=[Message(role="user", content="Use token=blocked-secret-value")],
    )

    with pytest.raises(Exception) as exc_info:
        await handle_anthropic_request(
            request,
            request.messages,
            "claude-test",
            "test-anthropic-key",
        )

    assert "chat.anthropic.messages[0].content" in str(exc_info.value)
    assert "blocked-secret-value" not in str(exc_info.value)
    mock_create_anthropic_model.assert_not_called()


def test_chat_outbound_scan_allows_clean_messages_with_high_security(monkeypatch):
    from api_service.api.routers.chat import _scan_chat_messages_before_send

    monkeypatch.setattr(settings_in_chat_router.security, "high_security_mode", True)

    _scan_chat_messages_before_send(
        [Message(role="user", content="Use the documented provider profile.")],
        surface="chat.openai",
    )


def test_chat_outbound_scan_blocks_dict_messages_with_high_security(monkeypatch):
    from api_service.api.routers.chat import _scan_chat_messages_before_send

    monkeypatch.setattr(settings_in_chat_router.security, "high_security_mode", True)

    with pytest.raises(Exception) as exc_info:
        _scan_chat_messages_before_send(
            [{"role": "user", "content": "Please use token=blocked-secret-value"}],
            surface="chat.openai",
        )

    assert "chat.openai.messages[0].content" in str(exc_info.value)
    assert "blocked-secret-value" not in str(exc_info.value)


@patch("api_service.api.routers.chat.get_rag_context", new_callable=AsyncMock)
@patch("moonmind.models_cache.model_cache.get_model_provider")
@patch("api_service.api.routers.chat.get_user_api_key", new_callable=AsyncMock)
@patch("moonmind.factories.openai_factory.get_openai_model")
@patch("api_service.api.routers.chat.AsyncOpenAI")
def test_responses_endpoint_blocks_secret_before_openai_provider_call(
    mock_async_openai_client,
    mock_get_openai_model,
    mock_get_user_api_key_helper,
    mock_get_provider,
    mock_get_rag_context,
    monkeypatch,
):
    monkeypatch.setattr(settings_in_chat_router.security, "high_security_mode", True)
    settings_in_chat_router.openai.openai_enabled = True
    mock_get_provider.return_value = "OpenAI"
    mock_get_user_api_key_helper.return_value = "sk-test-user-key"
    mock_get_openai_model.return_value = "gpt-4.1"
    mock_get_rag_context.return_value = ""

    response = client.post(
        "/responses",
        json={
            "model": "gpt-4.1",
            "input": "Use token=blocked-secret-value",
        },
    )

    assert response.status_code == 422
    assert "chat.openai.responses.messages[0].content" in response.json()["detail"]
    assert "blocked-secret-value" not in response.json()["detail"]
    mock_async_openai_client.assert_not_called()


# Refined Google Error Handling Tests
@patch("google.generativeai.configure")
@patch("moonmind.factories.google_factory.get_google_model")  # CORRECTED TARGET
@patch("moonmind.models_cache.model_cache.get_model_provider")
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
@patch("moonmind.factories.google_factory.get_google_model")  # CORRECTED TARGET
@patch("moonmind.models_cache.model_cache.get_model_provider")
@patch(
    "api_service.api.routers.chat.get_user_api_key", new_callable=AsyncMock
)  # Patch get_user_api_key
def test_chat_completions_google_value_error_other_argument(
    mock_get_user_api_key,  # Add mock argument
    mock_get_model_provider,
    mock_router_get_google_model,
    mock_genai_configure,
    chat_request_google_model,
):
    mock_get_user_api_key.return_value = "fake-user-api-key"  # Configure it
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
@patch("moonmind.models_cache.model_cache.get_model_provider")
@patch(
    "moonmind.models_cache.model_cache.refresh_models_sync"
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

@patch("moonmind.models_cache.model_cache.get_model_provider")
@patch("moonmind.models_cache.model_cache.refresh_models_sync")
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
@patch("moonmind.models_cache.model_cache.get_model_provider")
@patch("api_service.api.routers.chat.get_user_api_key", new_callable=AsyncMock)
def test_chat_completions_openai_no_api_key_with_cache(
    mock_get_user_api_key_helper, mock_get_provider, chat_request_openai_model
):
    # settings.openai.openai_api_key = None # Global key not relevant here for user-specific check
    mock_get_provider.return_value = "OpenAI"
    mock_get_user_api_key_helper.return_value = (
        None  # Simulate user has no key for OpenAI
    )

    response = client.post("/completions", json=chat_request_openai_model.model_dump())
    assert response.status_code == 400
    assert "Provide a key" in response.json()["detail"]

@patch("moonmind.models_cache.model_cache.get_model_provider")
@patch("api_service.api.routers.chat.get_user_api_key", new_callable=AsyncMock)
def test_chat_completions_google_no_api_key_with_cache(
    mock_get_user_api_key_helper, mock_get_provider, chat_request_google_model
):
    # settings.google.google_api_key = None # Global key not relevant
    mock_get_provider.return_value = "Google"
    mock_get_user_api_key_helper.return_value = (
        None  # Simulate user has no key for Google
    )

    response = client.post("/completions", json=chat_request_google_model.model_dump())
    assert response.status_code == 400
    assert "Provide a key" in response.json()["detail"]

# General API errors after successful routing by cache
@patch("moonmind.models_cache.model_cache.get_model_provider")
@patch("api_service.api.routers.chat.get_user_api_key", new_callable=AsyncMock)
@patch("api_service.api.routers.chat.AsyncOpenAI")  # Corrected patch target
def test_chat_completions_openai_api_error_with_cache(
    mock_async_openai_client,  # Patched AsyncOpenAI
    mock_get_user_api_key_helper,  # Patched get_user_api_key
    mock_get_provider,  # Patched model_cache.get_model_provider
    chat_request_openai_model,
):
    # settings.openai.openai_api_key = "fake_openai_key_for_test" # Global key not primary
    mock_get_provider.return_value = "OpenAI"
    mock_get_user_api_key_helper.return_value = "sk-test-user-key"

    # Configure the mock AsyncOpenAI client instance to raise an error
    mock_client_instance = AsyncMock()
    mock_client_instance.chat.completions.create = AsyncMock(
        side_effect=Exception("OpenAI API Communication Error")
    )
    mock_async_openai_client.return_value = mock_client_instance

    response = client.post("/completions", json=chat_request_openai_model.model_dump())
    assert response.status_code == 500
    assert (
        "OpenAI API error: OpenAI API Communication Error"
        in response.json()["detail"]  # This error message comes from the handler
    )
    mock_async_openai_client.assert_called_once_with(api_key="sk-test-user-key")

@patch("google.generativeai.configure")
@patch("moonmind.factories.google_factory.get_google_model")  # CORRECTED TARGET
@patch("moonmind.models_cache.model_cache.get_model_provider")
@patch(
    "api_service.api.routers.chat.get_user_api_key", new_callable=AsyncMock
)  # Patch get_user_api_key
def test_chat_completions_google_api_error_with_cache(
    mock_get_user_api_key,  # Add mock argument
    mock_get_model_provider,
    mock_router_get_google_model,
    mock_genai_configure,
    chat_request_google_model,
):
    mock_get_user_api_key.return_value = "fake-user-api-key"  # Configure it
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

# Tests for provider enablement functionality
@patch("moonmind.models_cache.model_cache.get_model_provider")
@patch("api_service.api.routers.chat.get_user_api_key", new_callable=AsyncMock)
def test_chat_completions_openai_provider_disabled(
    mock_get_user_api_key_helper, mock_get_provider, chat_request_openai_model
):
    """Test that disabled OpenAI provider returns appropriate error"""
    settings_in_chat_router.openai.openai_enabled = False
    settings_in_chat_router.openai.openai_api_key = "fake_key"  # Global key
    # settings.openai.openai_api_key = "fake_openai_key_for_test" # Original line for teardown reference
    mock_get_user_api_key_helper.return_value = (
        "sk-test-openai-key"  # User has a key, but provider is off
    )

    mock_get_provider.return_value = "OpenAI"

    response = client.post("/completions", json=chat_request_openai_model.model_dump())
    assert response.status_code == 400
    json_response = response.json()
    # The error message comes from handle_openai_request in chat.py
    assert "OpenAI provider is disabled on the server." in json_response["detail"]

@patch("moonmind.models_cache.model_cache.get_model_provider")
# Removed patch for get_user_api_key
def test_chat_completions_google_provider_disabled(
    # mock_get_user_api_key, # Removed
    mock_get_provider,
    chat_request_google_model,
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

# Test default model functionality
@patch("moonmind.models_cache.model_cache.get_model_provider")
@patch("api_service.api.routers.chat.get_user_api_key", new_callable=AsyncMock)
@patch("api_service.api.routers.chat.AsyncOpenAI")  # Corrected patch target
def test_chat_completions_uses_default_model(
    mock_async_openai_client,  # Patched AsyncOpenAI
    mock_get_user_api_key_helper,  # Patched get_user_api_key
    mock_get_provider,  # Patched model_cache.get_model_provider
    chat_request_no_model,
    mock_openai_chat_response,  # Fixture for response
):
    """Test that default model is used when no model is specified"""

    settings_in_chat_router.default_chat_provider = "openai"
    # Default model (e.g., "gpt-3.5-turbo") will be fetched by settings.get_default_chat_model()

    settings_in_chat_router.openai.openai_enabled = True
    # settings_in_chat_router.openai.openai_api_key = "fake_openai_key_for_test" # Global key not primary

    mock_get_provider.return_value = (
        "OpenAI"  # Model cache identifies the default model as OpenAI
    )
    mock_get_user_api_key_helper.return_value = "sk-test-user-key"  # User has a key

    # Configure the mock AsyncOpenAI client
    mock_client_instance = AsyncMock()
    mock_client_instance.chat.completions.create = AsyncMock(
        return_value=mock_openai_chat_response
    )
    mock_async_openai_client.return_value = mock_client_instance

    response = client.post("/completions", json=chat_request_no_model.model_dump())

    assert response.status_code == 200
    # Assert that get_model_provider was called with the actual default model name
    # The actual default model name comes from settings.get_default_chat_model()
    # For this test, let's assume it's "gpt-3.5-turbo" as per current settings default.
    # If settings.get_default_chat_model() is complex, this might need adjustment or specific mocking of settings.
    actual_default_model = settings.get_default_chat_model()  # Get it dynamically
    mock_get_provider.assert_called_once_with(actual_default_model)
    mock_async_openai_client.assert_called_once_with(api_key="sk-test-user-key")
    mock_client_instance.chat.completions.create.assert_called_once()

@patch("moonmind.models_cache.model_cache.get_model_provider")
@patch("api_service.api.routers.chat.get_user_api_key", new_callable=AsyncMock)
@patch("moonmind.factories.openai_factory.get_openai_model")
@patch("api_service.api.routers.chat.AsyncOpenAI")
def test_responses_endpoint_uses_openai_responses_api(
    mock_async_openai_client,
    mock_get_openai_model,
    mock_get_user_api_key_helper,
    mock_get_provider,
):
    mock_get_provider.return_value = "OpenAI"
    mock_get_user_api_key_helper.return_value = "sk-test-user-key"
    mock_get_openai_model.return_value = "gpt-4.1"
    mock_response = {
        "id": "resp_test",
        "object": "response",
        "created_at": 123,
        "status": "completed",
        "model": "gpt-4.1",
        "output": [
            {
                "id": "msg_test",
                "type": "message",
                "status": "completed",
                "role": "assistant",
                "content": [
                    {
                        "type": "output_text",
                        "text": "response text",
                        "annotations": [],
                    }
                ],
            }
        ],
        "usage": {"input_tokens": 4, "output_tokens": 2, "total_tokens": 6},
    }
    mock_client_instance = AsyncMock()
    mock_client_instance.responses.create = AsyncMock(return_value=mock_response)
    mock_async_openai_client.return_value = mock_client_instance

    response = client.post(
        "/responses",
        json={
            "model": "gpt-4.1",
            "instructions": "Be concise.",
            "input": "Say hello.",
            "max_output_tokens": 20,
            "temperature": 0.2,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["id"] == "resp_test"
    assert payload["output_text"] == "response text"
    mock_async_openai_client.assert_called_once_with(api_key="sk-test-user-key")
    mock_client_instance.responses.create.assert_awaited_once_with(
        model="gpt-4.1",
        input=[{"role": "user", "content": "Say hello."}],
        instructions="Be concise.",
        max_output_tokens=20,
        temperature=0.2,
    )

@patch("api_service.api.routers.chat.get_rag_context", new_callable=AsyncMock)
@patch("moonmind.models_cache.model_cache.get_model_provider")
@patch("api_service.api.routers.chat.get_user_api_key", new_callable=AsyncMock)
@patch("moonmind.factories.openai_factory.get_openai_model")
@patch("api_service.api.routers.chat.AsyncOpenAI")
def test_responses_endpoint_sends_processed_rag_context_to_openai(
    mock_async_openai_client,
    mock_get_openai_model,
    mock_get_user_api_key_helper,
    mock_get_provider,
    mock_get_rag_context,
):
    mock_get_provider.return_value = "OpenAI"
    mock_get_user_api_key_helper.return_value = "sk-test-user-key"
    mock_get_openai_model.return_value = "gpt-4.1"
    mock_get_rag_context.return_value = "--- Retrieved Context ---\nKnown fact.\n"
    mock_client_instance = AsyncMock()
    mock_client_instance.responses.create = AsyncMock(
        return_value={"output_text": "response text", "model": "gpt-4.1"}
    )
    mock_async_openai_client.return_value = mock_client_instance

    response = client.post(
        "/responses",
        json={
            "model": "gpt-4.1",
            "instructions": "Be concise.",
            "input": "Use context.",
        },
    )

    assert response.status_code == 200
    call_kwargs = mock_client_instance.responses.create.await_args.kwargs
    assert call_kwargs["instructions"] == "Be concise."
    assert call_kwargs["input"][0]["role"] == "user"
    assert "--- Retrieved Context ---\nKnown fact." in call_kwargs["input"][0]["content"]
    assert "User's question: Use context." in call_kwargs["input"][0]["content"]


def test_response_content_to_text_preserves_falsy_non_none_values():
    assert _response_content_to_text(0) == "0"
    assert _response_content_to_text(False) == "False"
    assert (
        _response_content_to_text(
            [{"type": "input_text", "text": 0}, {"type": "text", "text": False}]
        )
        == "0\nFalse"
    )


def test_extract_openai_response_text_accepts_text_content_items():
    assert (
        _extract_openai_response_text(
            {
                "output": [
                    {
                        "content": [
                            {"type": "text", "text": "plain text response"},
                        ]
                    }
                ]
            }
        )
        == "plain text response"
    )


def test_responses_endpoint_rejects_streaming_requests():
    response = client.post(
        "/responses",
        json={"model": "gpt-4.1", "input": "Say hello.", "stream": True},
    )

    assert response.status_code == 400
    assert "Streaming Responses API requests" in response.json()["detail"]
