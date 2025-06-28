import logging
import unittest
from unittest.mock import patch, MagicMock

# Assuming settings are accessible for modification in tests, or mock them appropriately
from moonmind.config import settings
from moonmind.factories.openai_factory import list_openai_models, get_openai_model

# Configure basic logging for tests to see warnings if needed
logging.basicConfig(level=logging.INFO)


class TestOpenAIFactory(unittest.TestCase):
    def setUp(self):
        # Store original settings to restore them after tests
        self.original_openai_api_key = settings.openai.openai_api_key
        self.original_openai_chat_model = settings.openai.openai_chat_model

    def tearDown(self):
        # Restore original settings
        settings.openai.openai_api_key = self.original_openai_api_key
        settings.openai.openai_chat_model = self.original_openai_chat_model
        patch.stopall()  # Stop all active patches

    @patch("openai.Model.list")
    def test_list_openai_models_success(self, mock_model_list):
        # Mock the OpenAI API response
        mock_model_data_1 = MagicMock()
        mock_model_data_1.id = "gpt-3.5-turbo"
        mock_model_data_1.name = (
            "GPT-3.5 Turbo"  # some models might have a name attribute
        )

        mock_model_data_2 = MagicMock()
        mock_model_data_2.id = "gpt-4"
        mock_model_data_2.name = "GPT-4"

        mock_model_data_3 = MagicMock()  # A model that shouldn't be included
        mock_model_data_3.id = "text-davinci-003"
        mock_model_data_3.name = "Text Davinci"

        mock_api_response = MagicMock()
        mock_api_response.data = [
            mock_model_data_1,
            mock_model_data_2,
            mock_model_data_3,
        ]
        mock_model_list.return_value = mock_api_response

        settings.openai.openai_api_key = "fake_api_key"
        models = list_openai_models()

        self.assertEqual(len(models), 2)
        self.assertTrue(any(model.id == "gpt-3.5-turbo" for model in models))
        self.assertTrue(any(model.id == "gpt-4" for model in models))
        self.assertFalse(any(model.id == "text-davinci-003" for model in models))
        mock_model_list.assert_called_once()

    @patch("openai.Model.list")
    @patch("moonmind.factories.openai_factory.logger.warning")
    def test_list_openai_models_no_api_key(self, mock_logger_warning, mock_model_list):
        settings.openai.openai_api_key = None
        models = list_openai_models()
        self.assertEqual(models, [])
        mock_logger_warning.assert_called_with(
            "OpenAI models are not available because the API key is not set in settings"
        )
        mock_model_list.assert_not_called()

    @patch("openai.Model.list")
    @patch("moonmind.factories.openai_factory.logger.error")
    def test_list_openai_models_api_error(self, mock_logger_error, mock_model_list):
        settings.openai.openai_api_key = "fake_api_key"
        mock_model_list.side_effect = Exception("API connection error")

        models = list_openai_models()
        self.assertEqual(models, [])
        mock_logger_error.assert_called_with(
            "Error listing OpenAI models: API connection error"
        )

    def test_get_openai_model_with_name(self):
        settings.openai.openai_api_key = (
            "fake_api_key"  # Needs to be set for the model to be "available"
        )
        model_name = get_openai_model("gpt-4-test")
        self.assertEqual(model_name, "gpt-4-test")

    def test_get_openai_model_default_from_settings(self):
        settings.openai.openai_api_key = "fake_api_key"
        settings.openai.openai_chat_model = "gpt-default-from-settings"
        model_name = get_openai_model()
        self.assertEqual(model_name, "gpt-default-from-settings")

    @patch("moonmind.factories.openai_factory.logger.warning")
    def test_get_openai_model_no_api_key_logs_warning(self, mock_logger_warning):
        settings.openai.openai_api_key = None
        settings.openai.openai_chat_model = "gpt-default"  # Default model

        model_name = get_openai_model()  # Get default model
        self.assertEqual(model_name, "gpt-default")
        mock_logger_warning.assert_called_with(
            "OpenAI API key is not set in settings. OpenAI model initialization might fail."
        )

        mock_logger_warning.reset_mock()  # Reset mock for the next call
        model_name_specific = get_openai_model("gpt-specific")  # Get specific model
        self.assertEqual(model_name_specific, "gpt-specific")
        mock_logger_warning.assert_called_with(
            "OpenAI API key is not set in settings. OpenAI model initialization might fail."
        )


if __name__ == "__main__":
    unittest.main()
