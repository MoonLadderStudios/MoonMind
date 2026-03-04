
# Important to ignore env file validation errors if AppSettings is accidentally loaded
import os
import unittest
from unittest.mock import patch, MagicMock

os.environ["IGNORE_ENV_FILE"] = "1"

from moonmind.summarization.readme_generator import ReadmeAiGenerator


class TestReadmeAiGenerator(unittest.IsolatedAsyncioTestCase):

    def test_init(self):
        """Test the initialization of ReadmeAiGenerator with and without config."""
        # Default initialization
        generator = ReadmeAiGenerator()
        self.assertEqual(generator.config, {})

        # Initialization with custom config
        custom_config = {"model": "gpt-4", "provider": "openai", "api_key": "test_key"}
        generator_with_config = ReadmeAiGenerator(config=custom_config)
        self.assertEqual(generator_with_config.config, custom_config)

    @patch("moonmind.summarization.readme_generator.logger.error")
    @patch("moonmind.summarization.readme_generator.ConfigLoader", new=None)
    async def test_generate_missing_dependencies(self, mock_logger_error):
        """Test generation fails gracefully when dependencies are missing."""
        generator = ReadmeAiGenerator()

        # Call generate with mock ConfigLoader as None
        result = await generator.generate("/dummy/repo/path")

        # Verify it returns None and logs an error
        self.assertIsNone(result)
        mock_logger_error.assert_called_once_with(
            "readme-ai library is not installed; cannot generate"
        )

    @patch("moonmind.summarization.readme_generator.Path.unlink")
    @patch("moonmind.summarization.readme_generator.Path.exists")
    @patch(
        "moonmind.summarization.readme_generator.open",
        new_callable=unittest.mock.mock_open,
        read_data="# Dummy README Content",
    )
    @patch("moonmind.summarization.readme_generator.asyncio.to_thread")
    @patch("moonmind.summarization.readme_generator.tempfile.NamedTemporaryFile")
    @patch("moonmind.summarization.readme_generator.readme_agent")
    @patch("moonmind.summarization.readme_generator.ConfigLoader")
    async def test_generate_success(
        self,
        mock_config_loader_cls,
        mock_readme_agent,
        mock_tempfile,
        mock_to_thread,
        mock_file_open,
        mock_exists,
        mock_unlink,
    ):
        """Test successful generation with mocked dependencies."""
        # Setup mocks
        mock_config_instance = MagicMock()
        mock_config_loader_cls.return_value = mock_config_instance

        mock_temp_file = MagicMock()
        mock_temp_file.name = "/tmp/dummy_readme.md"
        mock_tempfile.return_value.__enter__.return_value = mock_temp_file

        # Generator initialization
        generator = ReadmeAiGenerator()

        repo_path = "/dummy/repo/path"

        # Execute
        result = await generator.generate(repo_path)

        # Verify
        self.assertEqual(result, "# Dummy README Content")

        # Verify config setup
        mock_config_loader_cls.assert_called_once()
        self.assertEqual(mock_config_instance.config.git.repository, repo_path)

        # Verify asyncio.to_thread was called with the right arguments
        mock_to_thread.assert_called_once_with(
            mock_readme_agent, mock_config_instance, "/tmp/dummy_readme.md"
        )

        # Verify file read
        mock_file_open.assert_called_once_with(
            "/tmp/dummy_readme.md", "r", encoding="utf-8"
        )

    @patch("moonmind.summarization.readme_generator.Path.unlink")
    @patch("moonmind.summarization.readme_generator.Path.exists")
    @patch(
        "moonmind.summarization.readme_generator.open",
        new_callable=unittest.mock.mock_open,
        read_data="# Output",
    )
    @patch("moonmind.summarization.readme_generator.asyncio.to_thread")
    @patch("moonmind.summarization.readme_generator.tempfile.NamedTemporaryFile")
    @patch("moonmind.summarization.readme_generator.readme_agent")
    @patch("moonmind.summarization.readme_generator.ConfigLoader")
    async def test_generate_with_custom_config_providers(
        self,
        mock_config_loader_cls,
        mock_readme_agent,
        mock_tempfile,
        mock_to_thread,
        mock_file_open,
        mock_exists,
        mock_unlink,
    ):
        """Test custom configuration is applied correctly for all providers."""
        test_cases = [
            {"model": "gpt-4", "provider": "openai", "api_key": "test_openai_key"},
            {
                "model": "claude-3-opus",
                "provider": "anthropic",
                "api_key": "test_anthropic_key",
            },
            {
                "model": "gemini-1.5-pro",
                "provider": "google",
                "api_key": "test_google_key",
            },
        ]

        for custom_config in test_cases:
            with self.subTest(provider=custom_config["provider"]):
                mock_config_instance = MagicMock()
                mock_config_loader_cls.return_value = mock_config_instance

                generator = ReadmeAiGenerator(config=custom_config)
                await generator.generate("/dummy/path")

                self.assertEqual(
                    mock_config_instance.config.llm.model, custom_config["model"]
                )
                self.assertEqual(
                    mock_config_instance.config.llm.api, custom_config["provider"]
                )
                self.assertEqual(
                    mock_config_instance.config.llm.api_key, custom_config["api_key"]
                )

    @patch("moonmind.summarization.readme_generator.logger.exception")
    @patch("moonmind.summarization.readme_generator.Path.unlink")
    @patch("moonmind.summarization.readme_generator.Path.exists", return_value=True)
    @patch("moonmind.summarization.readme_generator.asyncio.to_thread")
    @patch("moonmind.summarization.readme_generator.tempfile.NamedTemporaryFile")
    @patch("moonmind.summarization.readme_generator.readme_agent")
    @patch("moonmind.summarization.readme_generator.ConfigLoader")
    async def test_generate_exception(
        self,
        mock_config_loader_cls,
        mock_readme_agent,
        mock_tempfile,
        mock_to_thread,
        mock_exists,
        mock_unlink,
        mock_logger_exception,
    ):
        """Test exception handling during generation."""
        mock_config_instance = MagicMock()
        mock_config_loader_cls.return_value = mock_config_instance

        # Setup mock to raise exception
        test_exception = Exception("Test generation error")
        mock_to_thread.side_effect = test_exception

        generator = ReadmeAiGenerator()
        repo_path = "/dummy/error/repo"

        result = await generator.generate(repo_path)

        # Verify it handled the exception and returned None
        self.assertIsNone(result)
        mock_logger_exception.assert_called_once()

        # Also test the file cleanup logic when file exists in finally block
        mock_unlink.assert_called_once()

    @patch("moonmind.summarization.readme_generator.Path.unlink")
    @patch("moonmind.summarization.readme_generator.Path.exists", return_value=False)
    @patch("moonmind.summarization.readme_generator.logger.debug")
    @patch("moonmind.summarization.readme_generator.asyncio.to_thread")
    @patch("moonmind.summarization.readme_generator.tempfile.NamedTemporaryFile")
    @patch("moonmind.summarization.readme_generator.readme_agent")
    @patch("moonmind.summarization.readme_generator.ConfigLoader")
    async def test_generate_file_cleanup_not_found(
        self,
        mock_config_loader_cls,
        mock_readme_agent,
        mock_tempfile,
        mock_to_thread,
        mock_logger_debug,
        mock_exists,
        mock_unlink,
    ):
        """Test cleanup when temporary file doesn't exist."""
        mock_config_instance = MagicMock()
        mock_config_loader_cls.return_value = mock_config_instance

        mock_temp_file = MagicMock()
        mock_temp_file.name = "/tmp/dummy_missing.md"
        mock_tempfile.return_value.__enter__.return_value = mock_temp_file

        # Setup mock so to_thread succeeds, but we mock exists to False
        # To just trigger the finally block correctly
        generator = ReadmeAiGenerator()

        with patch(
            "moonmind.summarization.readme_generator.open",
            unittest.mock.mock_open(read_data="# Dummy"),
        ):
            await generator.generate("/dummy/path")

        # Verify unlink was NOT called because exists was False
        mock_unlink.assert_not_called()

        # Verify appropriate debug log was written
        mock_logger_debug.assert_any_call(
            "Temporary file /tmp/dummy_missing.md not found for deletion."
        )


if __name__ == "__main__":
    unittest.main()
