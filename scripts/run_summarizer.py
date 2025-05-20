import argparse
import logging
import os
import sys
import time # Keep time import as it was in original, though not directly used in this script's main logic after refactor

# Assuming moonmind.config.logging and configure_logging exist and are accessible
from moonmind.config.logging import configure_logging

from moonmind.factories.google_factory import get_google_model
from moonmind.summarization import summarize_text, update_summaries

# Call configure_logging early, as in the original script
configure_logging()
logger = logging.getLogger(__name__)


if __name__ == "__main__":
    logger.info("Summarizer job starting.")

    parser = argparse.ArgumentParser(description="Generate summaries for text files.")
    parser.add_argument("base_data_path", help="Base data path inside the container (e.g., /app/data). This is where the host directory is mounted.")
    parser.add_argument("--input-dir-suffix", default="BlueprintText", help="Suffix for the input directory, relative to base_data_path (e.g., BlueprintText).")
    parser.add_argument("--output-dir-suffix", default="BlueprintSummaries", help="Suffix for the output directory, relative to base_data_path (e.g., BlueprintSummaries).")
    parser.add_argument("--prompt-file", default="/app/prompts/atbtt-bp-summary.txt", help="Path to the prompt file inside the container.")
    parser.add_argument("--replace-existing", action="store_true", help="Replace existing summary files if they are found.")

    args = parser.parse_args()

    logger.info(f"Base data path received: {args.base_data_path}")
    logger.info(f"Input directory suffix: {args.input_dir_suffix}")
    logger.info(f"Output directory suffix: {args.output_dir_suffix}")
    logger.info(f"Prompt file: {args.prompt_file}")
    logger.info(f"Replace existing summaries: {args.replace_existing}")

    input_directory = os.path.join(args.base_data_path, args.input_dir_suffix)
    output_directory = os.path.join(args.base_data_path, args.output_dir_suffix)

    logger.info(f"Derived input directory: {input_directory}")
    logger.info(f"Derived output directory: {output_directory}")

    if not os.getenv("GOOGLE_API_KEY"):
        logger.error("GOOGLE_API_KEY environment variable is not set. The job may fail to access Google services.")

    try:
        update_summaries(
            input_dir=input_directory,
            output_dir=output_directory,
            prompt_file_path=args.prompt_file,
            model_factory=get_google_model,
            text_summarizer=summarize_text,
            replace_existing=args.replace_existing
        )
        logger.info("Summarizer job process completed.")
        sys.exit(0)
    except Exception as e:
        logger.exception(f"An unexpected error occurred during the summarizer job: {e}")
        sys.exit(1)
