import logging
import os


def read_text_file(file_path):
    logger = logging.getLogger(__name__)
    if not file_path:
        logger.debug("No file path provided for loading text.")
        return None
    try:
        if not os.path.exists(file_path):
            logger.warning(f"File not found: {file_path}")
            return None
        if not os.path.isfile(file_path):
            logger.warning(f"Path is not a file: {file_path}")
            return None

        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
        logger.info(f"Successfully loaded text from file: {file_path}")
        return content
    except Exception as e:
        logger.error(f"Error loading text from file {file_path}: {e}")
        return None
