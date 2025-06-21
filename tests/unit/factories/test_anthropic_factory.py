import pytest
from unittest.mock import patch, MagicMock

from llama_index.llms.anthropic import Anthropic as LlamaAnthropic

from moonmind.factories.anthropic_factory import AnthropicFactory
from moonmind.config.settings import AppSettings


@pytest.fixture
def mock_settings():
    """Fixture to mock application settings."""
    settings = AppSettings()
    settings.ANTHROPIC_API_KEY = "test_anthropic_api_key"
    settings.ANTHROPIC_MODEL_NAME = "claude-test-model"
    settings.ANTHROPIC_ENABLED = True
    return settings

def test_create_anthropic_model_success(mock_settings):
    """Test successful creation of an Anthropic model instance."""
    with patch("moonmind.factories.anthropic_factory.settings", mock_settings):
        with patch("moonmind.factories.anthropic_factory.Anthropic") as mock_anthropic_sdk:
            mock_anthropic_instance = MagicMock(spec=LlamaAnthropic)
            mock_anthropic_sdk.return_value = mock_anthropic_instance

            model = AnthropicFactory.create_anthropic_model()

            assert model is not None
            assert model == mock_anthropic_instance
            mock_anthropic_sdk.assert_called_once_with(
                api_key="test_anthropic_api_key",
                model="claude-test-model",
            )

def test_create_anthropic_model_no_api_key(mock_settings):
    """Test ValueError is raised if Anthropic API key is not configured."""
    mock_settings.ANTHROPIC_API_KEY = None
    with patch("moonmind.factories.anthropic_factory.settings", mock_settings):
        with pytest.raises(ValueError) as excinfo:
            AnthropicFactory.create_anthropic_model()
        assert "Anthropic API key not configured" in str(excinfo.value)

def test_create_anthropic_model_provider_disabled(mock_settings):
    """
    Test behavior when Anthropic provider is disabled.
    The factory itself doesn't check settings.ANTHROPIC_ENABLED,
    so it should still attempt to create a model if API key is present.
    The enabled check is usually done at a higher level (e.g., in the API router or model cache).
    """
    mock_settings.ANTHROPIC_ENABLED = False # This setting is not directly used by the factory
    with patch("moonmind.factories.anthropic_factory.settings", mock_settings):
        with patch("moonmind.factories.anthropic_factory.Anthropic") as mock_anthropic_sdk:
            mock_anthropic_instance = MagicMock(spec=LlamaAnthropic)
            mock_anthropic_sdk.return_value = mock_anthropic_instance

            model = AnthropicFactory.create_anthropic_model()
            assert model is not None # Factory should still proceed if API key is there
            mock_anthropic_sdk.assert_called_once_with(
                api_key="test_anthropic_api_key",
                model="claude-test-model",
            )

@patch("moonmind.factories.anthropic_factory.settings")
def test_factory_uses_default_model_name_if_not_overridden(mock_global_settings):
    """Test that the factory uses the default model name from settings if not overridden."""
    # Simulate global settings object
    mock_global_settings.ANTHROPIC_API_KEY = "a_valid_key"
    # The AppSettings class initializes anthropic_chat_model to "claude-3-opus-20240229" by default
    # So, we ensure our mock_global_settings reflects that default for ANTHROPIC_MODEL_NAME
    # if we are to test the default behavior accurately.
    # However, the factory uses settings.ANTHROPIC_MODEL_NAME which is what we set in AppSettings.anthropic.anthropic_chat_model
    # So, we need to access it via the nested structure if AppSettings is used directly,
    # or ensure the flattened version (if any) is correctly mocked.

    # For this test, let's assume settings.ANTHROPIC_MODEL_NAME is directly available
    # and is set to the default.
    # The AnthropicSettings class has `anthropic_chat_model`
    # The AnthropicFactory uses `settings.ANTHROPIC_MODEL_NAME` which should map to `settings.anthropic.anthropic_chat_model`
    # Let's make sure the mock_settings passed to the factory reflects this.

    # Create a fresh AppSettings instance to get the true default
    default_settings = AppSettings()
    default_anthropic_model_name = default_settings.anthropic.anthropic_chat_model

    # Configure the mock_global_settings to reflect how the factory would see it
    mock_global_settings.ANTHROPIC_API_KEY = "a_valid_key"
    mock_global_settings.ANTHROPIC_MODEL_NAME = default_anthropic_model_name # This is what the factory expects

    with patch("moonmind.factories.anthropic_factory.Anthropic") as mock_anthropic_sdk:
        AnthropicFactory.create_anthropic_model()
        mock_anthropic_sdk.assert_called_once_with(
            api_key="a_valid_key",
            model=default_anthropic_model_name # Check it uses the default from settings
        )
