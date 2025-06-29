# d:\ai\MoonMind\moonmind\summarization\readme_generator.py

import asyncio
import logging
import tempfile
from pathlib import Path

from readmeai.config.settings import ConfigLoader
from readmeai.core.pipeline import readme_agent

logger = logging.getLogger(__name__)

class ReadmeAiGenerator:
    """
    A wrapper class to programmatically invoke the readme-ai library
    for generating high-quality README.md files.
    """

    def __init__(self, config: dict = None):
        """
        Initializes the ReadmeAiGenerator.

        Args:
            config (dict, optional): A dictionary of configuration options
                                     to pass to readme-ai. Defaults to None.
        """
        self.config = config or {}

    async def generate(self, repo_path: str) -> str | None:
        """
        Generates a README.md file for a given repository using readme-ai.

        This method runs the readme-ai programmatically using its core pipeline.

        Args:
            repo_path (str): The absolute path to the code repository.

        Returns:
            str | None: The content of the generated README.md as a string,
                        or None if generation fails.
        """
        logger.info(f"Starting README generation for {repo_path} using readme-ai.")

        # Use a temporary file for the output
        with tempfile.NamedTemporaryFile(mode='w+', suffix=".md", delete=False) as temp_output:
            output_path = temp_output.name

        output_path_str = str(output_path)

        try:
            # Create readme-ai configuration
            config_loader = ConfigLoader()

            # Set the repository path
            config_loader.config.git.repository = repo_path

            # Apply custom configurations
            if "model" in self.config and self.config["model"]:
                config_loader.config.llm.model = self.config["model"]

            if "provider" in self.config and self.config["provider"]:
                config_loader.config.llm.api = self.config["provider"]

            if "api_key" in self.config and self.config["api_key"]:
                # Set the API key based on provider
                if self.config["provider"].lower() == "openai":
                    config_loader.config.llm.api_key = self.config["api_key"]
                elif self.config["provider"].lower() == "anthropic":
                    config_loader.config.llm.api_key = self.config["api_key"]
                elif self.config["provider"].lower() == "google":
                    config_loader.config.llm.api_key = self.config["api_key"]
                # Ollama typically doesn't use an API key

            logger.debug(f"readme-ai config: repository={repo_path}, model={config_loader.config.llm.model}, provider={config_loader.config.llm.api}")

            # Use asyncio.to_thread to run the synchronous readme_agent function
            await asyncio.to_thread(readme_agent, config_loader, output_path_str)

            # Read the content from the output file
            with open(output_path_str, 'r', encoding='utf-8') as f:
                readme_content = f.read()

            logger.info(f"Successfully generated README for {repo_path} at {output_path_str}.")
            return readme_content

        except Exception as e:
            logger.exception(f"An error occurred while running readme-ai for {repo_path}: {e}")
            return None

        finally:
            # Clean up the temporary file
            temp_file_path = Path(output_path_str)
            if temp_file_path.exists():
                temp_file_path.unlink()
                logger.debug(f"Temporary file {output_path_str} deleted.")
            else:
                logger.debug(f"Temporary file {output_path_str} not found for deletion.")
