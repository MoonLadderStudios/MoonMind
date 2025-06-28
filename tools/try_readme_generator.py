import asyncio
import logging
import os
from pathlib import Path

# Configure basic logging for the example
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Adjust the Python path to include the project root if running from the 'tools' directory
# This allows importing moonmind.summarization.readme_generator
import sys
tools_dir = Path(__file__).parent.resolve()
project_root = tools_dir.parent
sys.path.insert(0, str(project_root))

from moonmind.summarization.readme_generator import ReadmeAiGenerator

async def main():
    """
    Example function to demonstrate and test ReadmeAiGenerator.
    """
    logger.info("Starting ReadmeAiGenerator example usage.")

    # Define the path to the repository for which to generate a README.
    # For this example, we'll try to generate a README for the MoonMind project itself.
    # Assumes this script is run from the 'tools' directory or project root.
    repo_to_summarize = project_root # Path to the MoonMind repository

    # Ensure the path is an absolute path string
    repo_path_str = str(repo_to_summarize.resolve())
    logger.info(f"Target repository path: {repo_path_str}")

    # Example of passing custom configuration to readme-ai
    # These are hypothetical, refer to readme-ai documentation for actual options
    custom_config = {
        "badge_style": "flat-square",
        # "header_style": "classic", # Example, if supported
        # "llm_provider": "ollama", # Example, if supported and configured in readme-ai
        # "api_key": "YOUR_API_KEY_IF_NEEDED" # Example for specific LLM providers
        "model": "gpt-3.5-turbo", # Specify a model if required by readme-ai or if you want a specific one
        "tree_depth": "2", # Limit tree depth for faster processing in test
    }

    # Check for OpenAI API key if 'openai' is the intended LLM provider (default for readme-ai)
    # readme-ai might use OPENAI_API_KEY environment variable by default.
    # For explicit configuration or other providers, readme-ai has its own config system.
    # This example assumes readme-ai will pick up necessary configs from its environment or defaults.

    # It's good practice to ensure the target path exists
    if not Path(repo_path_str).is_dir():
        logger.error(f"Repository path {repo_path_str} does not exist or is not a directory.")
        return

    generator = ReadmeAiGenerator(config=custom_config)

    logger.info(f"Instantiated ReadmeAiGenerator with config: {custom_config}")
    logger.info(f"Calling generate() for repository: {repo_path_str}")

    generated_readme_content = await generator.generate(repo_path_str)

    if generated_readme_content:
        logger.info("README.md generated successfully by ReadmeAiGenerator!")
        print("\n--- Generated README Snippet (First 500 chars) ---")
        print(generated_readme_content[:500] + "...")
        print("\n--- End of Snippet ---")

        # Optionally, save the full README to a file for inspection
        try:
            output_filename = "generated_readme_example.md"
            with open(output_filename, "w", encoding="utf-8") as f:
                f.write(generated_readme_content)
            logger.info(f"Full generated README saved to: {output_filename}")
        except IOError as e:
            logger.error(f"Failed to save the full README to file: {e}")
    else:
        logger.error("Failed to generate README.md using ReadmeAiGenerator.")

if __name__ == "__main__":
    # To run this async function:
    # Ensure you are in an environment where 'moonmind' can be imported
    # and 'readme-ai' is installed and configured (e.g., API keys if needed).
    asyncio.run(main())
