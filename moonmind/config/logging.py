import json
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any, Mapping, Optional

_RESERVED_LOG_RECORD_FIELDS = {
    "name",
    "msg",
    "args",
    "levelname",
    "levelno",
    "pathname",
    "filename",
    "module",
    "exc_info",
    "exc_text",
    "stack_info",
    "lineno",
    "funcName",
    "created",
    "msecs",
    "relativeCreated",
    "thread",
    "threadName",
    "processName",
    "process",
    "taskName",
}


class StructuredLogFormatter(logging.Formatter):
    """JSON formatter that preserves extra fields for log aggregation."""

    def __init__(
        self,
        *,
        include_timestamp: bool = True,
        include_module: bool = True,
        default_fields: Optional[Mapping[str, Any]] = None,
    ) -> None:
        super().__init__()
        self._include_timestamp = include_timestamp
        self._include_module = include_module
        self._default_fields = dict(default_fields or {})

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            **self._default_fields,
            "level": record.levelname,
            "message": record.getMessage(),
        }

        if self._include_timestamp:
            payload["timestamp"] = datetime.fromtimestamp(
                record.created, tz=timezone.utc
            ).isoformat()

        if self._include_module:
            payload["logger"] = record.name

        extras: dict[str, Any] = {}
        for key, value in record.__dict__.items():
            if key in _RESERVED_LOG_RECORD_FIELDS:
                continue
            extras[key] = value

        if extras:
            payload["extra"] = extras

        return json.dumps(payload, default=str)


def configure_logging(
    level: str = "INFO",
    format_string: Optional[str] = None,
    include_timestamp: bool = True,
    include_module: bool = True,
    structured: Optional[bool] = None,
    default_fields: Optional[Mapping[str, Any]] = None,
) -> None:
    """
    Configure logging for the application.

    Args:
        level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        format_string: Custom format string for log messages
        include_timestamp: Whether to include timestamp in log messages
        include_module: Whether to include module name in log messages
        structured: When true, emit JSON logs that preserve ``extra`` fields.
        default_fields: Base fields appended to each structured log record
    """
    if structured is None:
        env_value = os.getenv("STRUCTURED_LOGS") or os.getenv(
            "SPEC_WORKFLOW_STRUCTURED_LOGS"
        )
        structured = env_value.lower() in {"1", "true", "yes"} if env_value else False

    if structured:
        formatter: logging.Formatter = StructuredLogFormatter(
            include_timestamp=include_timestamp,
            include_module=include_module,
            default_fields=default_fields,
        )
    elif format_string is None:
        parts = []
        if include_timestamp:
            parts.append("%(asctime)s")
        parts.append("%(levelname)s")
        if include_module:
            parts.append("%(name)s")
        parts.append("%(message)s")
        format_string = " - ".join(parts)
        formatter = logging.Formatter(format_string)
    else:
        formatter = logging.Formatter(format_string)

    logging.basicConfig(
        level=getattr(logging, level.upper()),
        handlers=[logging.StreamHandler(sys.stdout)],
        force=True,  # Override any existing configuration
    )

    root_logger = logging.getLogger()
    for handler in root_logger.handlers:
        handler.setFormatter(formatter)

    # Set specific loggers to appropriate levels
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)

    logger = logging.getLogger(__name__)
    logger.info(f"Logging configured with level: {level}")
