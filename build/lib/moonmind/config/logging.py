import logging
import sys
from typing import Optional


def configure_logging(
    level: str = "INFO",
    format_string: Optional[str] = None,
    include_timestamp: bool = True,
    include_module: bool = True
) -> None:
    """
    Configure logging for the application.

    Args:
        level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        format_string: Custom format string for log messages
        include_timestamp: Whether to include timestamp in log messages
        include_module: Whether to include module name in log messages
    """
    if format_string is None:
        parts = []
        if include_timestamp:
            parts.append("%(asctime)s")
        parts.append("%(levelname)s")
        if include_module:
            parts.append("%(name)s")
        parts.append("%(message)s")
        format_string = " - ".join(parts)

    logging.basicConfig(
        level=getattr(logging, level.upper()),
        format=format_string,
        handlers=[
            logging.StreamHandler(sys.stdout)
        ],
        force=True  # Override any existing configuration
    )

    # Set specific loggers to appropriate levels
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)

    logger = logging.getLogger(__name__)
    logger.info(f"Logging configured with level: {level}")
