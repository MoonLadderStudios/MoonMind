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
                logger.warning(
                    f"Path traversal detected: {file_path} is outside of {safe_base_dir}"
                )
                return None
        else:
            # If no explicit safe base directory is provided, ensure the path does not contain explicit
            # relative path traversal sequences (like '..') to prevent directory traversal attacks.
            if ".." in pathlib.Path(file_path).parts:
                logger.warning(
                    f"Path traversal detected: {file_path} contains '..' sequences."
                )
                return None

        if not target_path.exists():
            logger.warning(f"File not found: {file_path}")
            return None
        if not target_path.is_file():
            logger.warning(f"Path is not a file: {file_path}")
            return None

        with open(target_path, "r", encoding="utf-8") as f:
            content = f.read()
        logger.info(f"Successfully loaded text from file: {file_path}")
        return content
    except Exception as e:
        logger.error(f"Error loading text from file {file_path}: {e}")
        return None
