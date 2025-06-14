from unittest.mock import patch

import pytest
from pydantic import ValidationError

# Assuming AppSettings is in moonmind.config.settings
# Adjust the import path if necessary based on your project structure
from moonmind.config.settings import (AppSettings, GoogleSettings,
                                      OllamaSettings, OpenAISettings)


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
        "confluence_enabled": False,
        "jira_enabled": False,

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
