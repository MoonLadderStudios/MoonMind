from unittest.mock import patch

import pytest
from pydantic import ValidationError

from moonmind.config.settings import (AppSettings, AtlassianSettings,
                                      GoogleSettings, OllamaSettings,
                                      OpenAISettings)


# Fixture for default settings, can be customized in tests
@pytest.fixture
def app_settings_defaults():
    return {
        "google": GoogleSettings(
            google_chat_model="test-google-chat",
            google_embedding_model="test-google-embed",
            google_api_key="test_google_key" # Required for is_provider_enabled
        ),
        "openai": OpenAISettings(
            openai_chat_model="test-openai-chat",
            openai_api_key="test_openai_key" # Required for is_provider_enabled
        ),
        "ollama": OllamaSettings(
            ollama_chat_model="test-ollama-chat",
            ollama_embedding_model="test-ollama-embed"
        ),
        # Ensure other required fields for AppSettings have defaults if not provided
        "default_chat_provider": "google", # Default to google for fixture
        "default_embedding_provider": "google", # Default to google for fixture
        "qdrant": {"qdrant_enabled": False}, # Disable things not under test
        "rag": {},
        "github": {"github_enabled": False},
        "google_drive": {"google_drive_enabled": False},
        "atlassian": {
            "confluence": {"confluence_enabled": False},
            "jira": {"jira_enabled": False}
        },

    }

# Test that the removed fields are indeed gone
def test_default_model_fields_removed(app_settings_defaults):
    with pytest.raises(ValidationError):
        AppSettings(**app_settings_defaults, default_chat_model="some-model") # This should fail
    with pytest.raises(ValidationError):
        AppSettings(**app_settings_defaults, default_embed_model="some-model") # This should fail

    # Check that they are not present as attributes either
    settings = AppSettings(**app_settings_defaults)
    assert not hasattr(settings, "default_chat_model")
    assert not hasattr(settings, "default_embed_model")


class TestAtlassianSettings:
    def test_atlassian_url_correction(self, monkeypatch):
        # Test case 1: URL with "https://https://"
        monkeypatch.setenv("ATLASSIAN_URL", "https://https://example.atlassian.net")
        settings = AtlassianSettings()
        assert settings.atlassian_url == "https://example.atlassian.net"

        # Test case 2: Correct HTTPS URL
        monkeypatch.setenv("ATLASSIAN_URL", "https://example.atlassian.net")
        settings2 = AtlassianSettings()
        assert settings2.atlassian_url == "https://example.atlassian.net"

        # Test case 3: HTTP URL (should remain unchanged)
        monkeypatch.setenv("ATLASSIAN_URL", "http://example.atlassian.net")
        settings3 = AtlassianSettings()
        assert settings3.atlassian_url == "http://example.atlassian.net"

        # Test case 4: Empty URL (should remain None or empty)
        monkeypatch.delenv("ATLASSIAN_URL", raising=False)
        settings4 = AtlassianSettings()
        assert settings4.atlassian_url is None

        # Test case 5: URL that only starts with "https://" once
        monkeypatch.setenv("ATLASSIAN_URL", "https://another.example.com")
        settings5 = AtlassianSettings()
        assert settings5.atlassian_url == "https://another.example.com"

        # Test case 6: URL with no scheme
        monkeypatch.setenv("ATLASSIAN_URL", "example.atlassian.net")
        settings6 = AtlassianSettings()
        assert settings6.atlassian_url == "example.atlassian.net"

        # Clean up environment variable
        monkeypatch.delenv("ATLASSIAN_URL", raising=False)
