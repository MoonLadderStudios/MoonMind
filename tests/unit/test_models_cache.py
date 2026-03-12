from threading import Lock
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

from moonmind.config import settings
from moonmind.config.settings import AppSettings
from moonmind.models_cache import (
    ModelCache,
    force_refresh_model_cache,
    refresh_model_cache_for_user,
)


# ---------------------------------------------------------------------------
# Shared fixtures (function-scoped by default, auto-applied to all tests)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_model_cache_singleton():
    """Reset the ModelCache singleton before and after each test."""
    ModelCache._lock = Lock()
    ModelCache._instance = None
    yield
    ModelCache._instance = None


@pytest.fixture()
def provider_settings(monkeypatch):
    """Patch all provider settings to known test values via monkeypatch (auto-reverted)."""
    monkeypatch.setattr(settings.google, "google_api_key", "fake_google_key_for_test")
    monkeypatch.setattr(settings.google, "google_enabled", True)
    monkeypatch.setattr(settings.openai, "openai_api_key", "fake_openai_key_for_test")
    monkeypatch.setattr(settings.openai, "openai_enabled", True)
    monkeypatch.setattr(settings.ollama, "ollama_enabled", True)
    monkeypatch.setattr(settings.anthropic, "anthropic_api_key", "fake_anthropic_key_for_test")
    monkeypatch.setattr(settings.anthropic, "anthropic_enabled", True)
    monkeypatch.setattr(settings.anthropic, "anthropic_chat_model", "claude-test-cache-model")


@pytest.fixture()
def model_mocks(provider_settings):
    """
    Start all provider-listing patches and return mock objects.
    The ``provider_settings`` fixture is applied first so settings are consistent.
    """
    google_model = MagicMock(name="gemini-pro-raw")
    google_model.name = "models/gemini-pro"
    google_model.input_token_limit = 8192
    google_model.supported_generation_methods = ["generateContent"]

    openai_model = MagicMock(name="gpt-3.5-turbo-raw")
    openai_model.id = "gpt-3.5-turbo"
    import time
    openai_model.created = int(time.time()) - 1000
    openai_model.owned_by = "openai"

    ollama_model = {"name": "test-ollama-model", "details": {"parameter_size": "7B"}}

    def _is_provider_enabled(provider_name):
        provider_name = provider_name.lower()
        if provider_name == "google":
            return settings.google.google_enabled and bool(settings.google.google_api_key)
        elif provider_name == "openai":
            return settings.openai.openai_enabled and bool(settings.openai.openai_api_key)
        elif provider_name == "ollama":
            return settings.ollama.ollama_enabled
        elif provider_name == "anthropic":
            return settings.anthropic.anthropic_enabled and bool(settings.anthropic.anthropic_api_key)
        return False

    with (
        patch("moonmind.models_cache.list_google_models", return_value=[google_model]) as mock_google,
        patch("moonmind.models_cache.list_openai_models", return_value=[openai_model]) as mock_openai,
        patch("moonmind.models_cache.list_ollama_models", new_callable=AsyncMock) as mock_ollama,
        patch.object(AppSettings, "is_provider_enabled", side_effect=_is_provider_enabled),
        patch("moonmind.models_cache.Thread") as mock_thread_class,
        patch("moonmind.models_cache.ModelCache._periodic_refresh", return_value=None),
    ):
        mock_ollama.return_value = [ollama_model]
        mock_thread_instance = MagicMock()
        mock_thread_class.return_value = mock_thread_instance

        yield {
            "google": mock_google,
            "openai": mock_openai,
            "ollama": mock_ollama,
            "thread_class": mock_thread_class,
            "thread_instance": mock_thread_instance,
            "google_model_raw": google_model,
            "openai_model_raw": openai_model,
            "ollama_model_raw": ollama_model,
        }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_singleton_behavior(model_mocks):
    m = model_mocks
    cache1 = ModelCache(refresh_interval_seconds=1000)
    m["thread_class"].assert_called_once()
    m["thread_instance"].start.assert_called_once()

    cache2 = ModelCache(refresh_interval_seconds=1000)
    assert cache1 is cache2


def test_default_refresh_interval_from_settings(model_mocks, monkeypatch):
    monkeypatch.setattr(settings, "model_cache_refresh_interval_seconds", 43200)
    cache = ModelCache()
    assert cache.refresh_interval_seconds == 43200


def test_override_refresh_interval_via_patched_settings(model_mocks, monkeypatch):
    monkeypatch.setattr(settings, "model_cache_refresh_interval_seconds", 100)
    cache = ModelCache()
    assert cache.refresh_interval_seconds == 100


def test_override_refresh_interval_via_constructor_argument(model_mocks, monkeypatch):
    monkeypatch.setattr(settings, "model_cache_refresh_interval_seconds", 9999)
    cache = ModelCache(refresh_interval_seconds=50)
    assert cache.refresh_interval_seconds == 50


def test_initial_refresh_populates_data(model_mocks):
    m = model_mocks
    cache = ModelCache(refresh_interval_seconds=36000)
    cache.refresh_models_sync()

    m["google"].assert_called_once()
    m["openai"].assert_called_once()
    m["ollama"].assert_called_once()

    assert len(cache.models_data) == 4
    assert cache.model_to_provider["models/gemini-pro"] == "Google"
    assert cache.model_to_provider["gpt-3.5-turbo"] == "OpenAI"
    assert cache.model_to_provider["test-ollama-model"] == "Ollama"
    assert cache.model_to_provider["claude-test-cache-model"] == "Anthropic"

    gemini = next(m for m in cache.models_data if m["id"] == "models/gemini-pro")
    assert gemini["owned_by"] == "Google"
    assert gemini["context_window"] == 8192

    openai_data = next(m for m in cache.models_data if m["id"] == "gpt-3.5-turbo")
    assert openai_data["owned_by"] == "OpenAI"
    assert openai_data["context_window"] == 4096

    ollama_data = next(m for m in cache.models_data if m["id"] == "test-ollama-model")
    assert ollama_data["owned_by"] == "Ollama"

    anthropic_data = next(m for m in cache.models_data if m["id"] == "claude-test-cache-model")
    assert anthropic_data["owned_by"] == "Anthropic"
    assert anthropic_data["context_window"] == 200000


def test_get_all_models_after_refresh(model_mocks):
    cache = ModelCache(refresh_interval_seconds=36000)
    cache.refresh_models_sync()

    models = cache.get_all_models()
    assert len(models) == 4
    assert any(m["id"] == "models/gemini-pro" for m in models)
    assert any(m["id"] == "gpt-3.5-turbo" for m in models)
    assert any(m["id"] == "test-ollama-model" for m in models)
    assert any(m["id"] == "claude-test-cache-model" for m in models)


def test_get_model_provider(model_mocks):
    cache = ModelCache(refresh_interval_seconds=36000)
    cache.refresh_models_sync()

    assert cache.get_model_provider("models/gemini-pro") == "Google"
    assert cache.get_model_provider("gpt-3.5-turbo") == "OpenAI"
    assert cache.get_model_provider("test-ollama-model") == "Ollama"
    assert cache.get_model_provider("claude-test-cache-model") == "Anthropic"
    assert cache.get_model_provider("non-existent-model") is None


def test_cache_refresh_logic_manual_trigger(model_mocks):
    m = model_mocks
    with patch("time.time", return_value=1000.0):
        cache = ModelCache(refresh_interval_seconds=3600)
        cache.refresh_models_sync()

        m["google"].assert_called_once()
        m["openai"].assert_called_once()
        m["ollama"].assert_called_once()

        m["google"].reset_mock()
        m["openai"].reset_mock()
        m["ollama"].reset_mock()

        cache.refresh_models_sync()
        m["google"].assert_called_once()
        m["openai"].assert_called_once()
        m["ollama"].assert_called_once()
        assert cache.last_refresh_time == 1000.0


def test_cache_refresh_logic_stale_get_all_models(model_mocks):
    m = model_mocks
    initial_time = 1000.0
    refresh_interval = 60

    with patch("time.time", return_value=initial_time):
        cache = ModelCache(refresh_interval_seconds=refresh_interval)
        cache.refresh_models_sync()
        assert cache.last_refresh_time == initial_time

        m["google"].reset_mock()
        m["openai"].reset_mock()
        m["ollama"].reset_mock()

    with patch("time.time", return_value=initial_time + refresh_interval + 1):
        cache.get_all_models()

        m["google"].assert_called_once()
        m["openai"].assert_called_once()
        m["ollama"].assert_called_once()
        assert cache.last_refresh_time == initial_time + refresh_interval + 1


def test_error_handling_google_fetch_fails(model_mocks, monkeypatch):
    m = model_mocks
    m["google"].side_effect = Exception("Google API Error")

    ModelCache._instance = None
    cache = ModelCache(refresh_interval_seconds=36000)

    with patch.object(cache, "logger") as mock_logger:
        cache.refresh_models_sync()

    assert any(m["id"] == "gpt-3.5-turbo" for m in cache.models_data)
    assert any(m["id"] == "test-ollama-model" for m in cache.models_data)
    assert any(m["id"] == "claude-test-cache-model" for m in cache.models_data)
    assert len(cache.models_data) == 3
    assert cache.get_model_provider("models/gemini-pro") is None
    assert cache.get_model_provider("gpt-3.5-turbo") == "OpenAI"
    assert cache.get_model_provider("test-ollama-model") == "Ollama"
    assert any(
        "Error fetching Google models: Google API Error" in str(arg)
        for arg_list in mock_logger.exception.call_args_list
        for arg in arg_list[0]
    )


def test_missing_api_keys_skips_providers(model_mocks, monkeypatch):
    monkeypatch.setattr(settings.google, "google_api_key", None)
    monkeypatch.setattr(settings.openai, "openai_api_key", None)
    monkeypatch.setattr(settings.anthropic, "anthropic_api_key", None)

    cache = ModelCache(refresh_interval_seconds=36000)
    with patch.object(cache, "logger") as mock_logger:
        cache.refresh_models_sync()

    assert len(cache.models_data) == 1
    assert any(m["id"] == "test-ollama-model" for m in cache.models_data)
    assert len(cache.model_to_provider) == 1
    assert cache.get_model_provider("test-ollama-model") == "Ollama"

    warnings_logged = [str(args[0]) for args, kwargs in mock_logger.warning.call_args_list]
    assert any("Google API key not set." in w for w in warnings_logged)
    assert any("OpenAI API key not set." in w for w in warnings_logged)


def test_force_refresh_model_cache_function(model_mocks):
    m = model_mocks
    cache = ModelCache(refresh_interval_seconds=36000)
    cache.refresh_models_sync()

    m["google"].assert_called_once()
    m["openai"].assert_called_once()
    m["ollama"].assert_called_once()

    cache._refresh_in_progress = False
    force_refresh_model_cache()

    assert m["google"].call_count == 2
    assert m["openai"].call_count == 2
    assert m["ollama"].call_count == 2


@pytest.mark.asyncio
async def test_refresh_model_cache_for_user_does_not_mutate_singleton_keys(model_mocks):
    from unittest.mock import AsyncMock
    m = model_mocks
    cache = ModelCache(refresh_interval_seconds=36000)
    cache.google_api_key = "global_google_key"
    cache.openai_api_key = "global_openai_key"

    with patch(
        "api_service.api.routers.chat.get_user_api_key",
        new=AsyncMock(side_effect=["user_google_key", "user_openai_key"]),
    ):
        user_models = await refresh_model_cache_for_user(
            user=MagicMock(), db_session=MagicMock()
        )

    assert isinstance(user_models, list)
    assert cache.google_api_key == "global_google_key"
    assert cache.openai_api_key == "global_openai_key"
    m["google"].assert_called_with(api_key="user_google_key")
    m["openai"].assert_called_with(api_key="user_openai_key")



def test_periodic_refresh_thread_execution(provider_settings):
    """Verify Thread is created with the correct target and the refresh produces data."""
    with (
        patch("moonmind.models_cache.list_google_models") as mock_google,
        patch("moonmind.models_cache.list_openai_models") as mock_openai,
        patch("moonmind.models_cache.list_ollama_models", new_callable=AsyncMock) as mock_ollama,
        patch.object(AppSettings, "is_provider_enabled", return_value=True),
        patch("moonmind.models_cache.Thread") as mock_thread_ctor,
    ):
        google_model = MagicMock()
        google_model.name = "models/gemini-pro"
        google_model.input_token_limit = 8192
        google_model.supported_generation_methods = ["generateContent"]

        openai_model = MagicMock()
        openai_model.id = "gpt-3.5-turbo"
        import time
        openai_model.created = int(time.time()) - 1000
        openai_model.owned_by = "openai"

        mock_google.return_value = [google_model]
        mock_openai.return_value = [openai_model]
        mock_ollama.return_value = [{"name": "test-ollama-model", "details": {"parameter_size": "7B"}}]

        mock_thread_instance = Mock()
        mock_thread_ctor.return_value = mock_thread_instance

        cache = ModelCache(refresh_interval_seconds=1)

        mock_thread_ctor.assert_called_once_with(target=cache._periodic_refresh, daemon=True)
        mock_thread_instance.start.assert_called_once()

        cache.refresh_models_sync()

        assert mock_google.call_count == 1
        assert mock_openai.call_count == 1
        assert mock_ollama.call_count == 1
        assert cache.last_refresh_time > 0
