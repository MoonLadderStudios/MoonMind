import logging
import pathlib
from typing import Optional


def read_text_file(file_path: str, safe_base_dir: Optional[str] = None):
    logger = logging.getLogger(__name__)
    if not file_path:
        logger.debug("No file path provided for loading text.")
        return None
    try:
        target_path = pathlib.Path(file_path).resolve()

        if safe_base_dir:
            base_path = pathlib.Path(safe_base_dir).resolve()
            if not target_path.is_relative_to(base_path):
                logger.warning("Path traversal detected for requested file.")
                return None

        if not target_path.exists():
            logger.warning("Requested file was not found.")
            return None
        if not target_path.is_file():
            logger.warning("Requested path is not a regular file.")
            return None

        with open(target_path, "r", encoding="utf-8") as f:
            content = f.read()
        logger.info("Successfully loaded requested text file.")
        return content
    except Exception as e:
        logger.error(f"Error loading text file: {e}")
        return None
