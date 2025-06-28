import pytest
from unittest.mock import patch, MagicMock

from llama_index.llms.anthropic import Anthropic as LlamaAnthropic

from moonmind.factories.anthropic_factory import AnthropicFactory
from moonmind.config.settings import AppSettings


@pytest.fixture
def mock_settings():
    """Fixture to mock application settings."""
    settings_instance = AppSettings()
    settings_instance.anthropic.anthropic_api_key = "test_anthropic_api_key"
    settings_instance.anthropic.anthropic_chat_model = "claude-test-model"
    settings_instance.anthropic.anthropic_enabled = True
    return settings_instance


def test_create_anthropic_model_success(mock_settings):
    """Test successful creation of an Anthropic model instance."""
    with patch("moonmind.factories.anthropic_factory.settings", mock_settings):
        with patch(
            "moonmind.factories.anthropic_factory.Anthropic"
        ) as mock_anthropic_sdk:
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
    mock_settings.anthropic.anthropic_api_key = None
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
    mock_settings.anthropic.anthropic_enabled = (
        False  # This setting is not directly used by the factory
    )
    with patch("moonmind.factories.anthropic_factory.settings", mock_settings):
        with patch(
            "moonmind.factories.anthropic_factory.Anthropic"
        ) as mock_anthropic_sdk:
            mock_anthropic_instance = MagicMock(spec=LlamaAnthropic)
            mock_anthropic_sdk.return_value = mock_anthropic_instance

            model = AnthropicFactory.create_anthropic_model()
            assert model is not None  # Factory should still proceed if API key is there
            mock_anthropic_sdk.assert_called_once_with(
                api_key="test_anthropic_api_key",
                model="claude-test-model",
            )


@patch("moonmind.factories.anthropic_factory.settings")
def test_factory_uses_default_model_name_if_not_overridden(mock_global_settings):
    """Test that the factory uses the default model name from settings if not overridden."""
    # Simulate global settings object
    # mock_global_settings is an instance of AppSettings passed by the @patch decorator

    # Create a fresh AppSettings instance to get the true default values
    default_settings = AppSettings()
    default_api_key = "a_valid_key"  # Assign a valid key for the test
    default_anthropic_model_name = default_settings.anthropic.anthropic_chat_model

    # Configure the mock_global_settings (which is an AppSettings instance)
    # to reflect how the factory would see it.
    # The factory accesses settings.anthropic.anthropic_api_key and settings.anthropic.anthropic_chat_model
    mock_global_settings.anthropic.anthropic_api_key = default_api_key
    # anthropic_chat_model should already be its default from AppSettings, but we can be explicit
    mock_global_settings.anthropic.anthropic_chat_model = default_anthropic_model_name
    # Ensure provider is enabled for the factory to proceed (though factory doesn't check this directly,
    # it's good practice for mocking the settings state accurately)
    mock_global_settings.anthropic.anthropic_enabled = True

    with patch("moonmind.factories.anthropic_factory.Anthropic") as mock_anthropic_sdk:
        # The factory will use the `mock_global_settings` due to the decorator @patch("moonmind.factories.anthropic_factory.settings")
        AnthropicFactory.create_anthropic_model()
        mock_anthropic_sdk.assert_called_once_with(
            api_key=default_api_key,  # Check it uses the key from settings
            model=default_anthropic_model_name,  # Check it uses the default model name from settings
        )
