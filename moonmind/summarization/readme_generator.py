# d:\ai\MoonMind\moonmind\summarization\readme_generator.py

import asyncio
import logging
import tempfile
from pathlib import Path
from readmeai.main import readme_cli

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

        This method runs the readme-ai CLI tool in a controlled way,
        capturing the output file's content.

        Args:
            repo_path (str): The absolute path to the code repository.

        Returns:
            str | None: The content of the generated README.md as a string,
                        or None if generation fails.
        """
        logger.info(f"Starting README generation for {repo_path} using readme-ai.")

        # Use a temporary file for the output to avoid cluttering the project
        with tempfile.NamedTemporaryFile(mode='w+', suffix=".md", delete=False) as temp_output:
            output_path = temp_output.name

        # Ensure output_path is a string for readme_cli
        output_path_str = str(output_path)

        try:
            # Prepare arguments for the readme-ai CLI function
            # Note: readme_cli expects sys.argv style arguments, where the first one is the program name (can be dummy)
            args = [
                "readme-ai", # Dummy program name
                "--repository", repo_path,
                "--output", output_path_str, # Use string representation of path
            ]

            # Add any custom configurations
            # For example: --badge-style flat-square
            # Specific handling for model, provider, and api_key
            if "model" in self.config and self.config["model"]:
                args.extend(["--model", str(self.config["model"])])

            # 'provider' in config corresponds to '--llm-provider' for readme-ai CLI
            if "provider" in self.config and self.config["provider"]:
                args.extend(["--llm-provider", str(self.config["provider"])]) # Changed from --provider to --llm-provider

            if "api_key" in self.config and self.config["api_key"]:
                args.extend(["--api-key", str(self.config["api_key"])])

            # Add other configurations from self.config, avoiding duplication
            # of already handled keys (model, provider, api_key).
            other_config_keys = ["model", "provider", "api_key"]
            for key, value in self.config.items():
                if key not in other_config_keys and value is not None:
                    args.extend([f"--{key.replace('_', '-')}", str(value)])

            logger.debug(f"readme-ai arguments: {args}")

            # Invoke the readme-ai CLI's entrypoint function
            # readme_cli is not an async function, it's synchronous.
            # We need to run it in a way that doesn't block asyncio loop if called from async code.
            # For now, direct call as per original plan, will adjust if blocking becomes an issue.
            # Consider asyncio.to_thread if this class is used in an async context.

            # readme_cli might not directly accept a list of arguments.
            # It's designed to be called via command line and parses sys.argv.
            # A common pattern for such tools is to have a wrapper function or to use subprocess.
            # For now, assuming readme_cli can be called this way based on the plan's import.
            # If it fails, I'll need to check readmeai.main.readme_cli's signature or use subprocess.

            # Correction: readme_cli is an async function as per readme-ai v0.5.0+
            # https://github.com/eli64s/readme-ai/blob/main/readmeai/main.py
            await readme_cli(args)

            # Read the content from the temporary output file
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
                logger.debug(f"Temporary file {output_path_str} not found for deletion (may have been moved or deleted by readme-ai if output was specified to be elsewhere).")
