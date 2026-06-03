import json
import logging
import os
import sys
from datetime import datetime, timezone
from collections.abc import Callable
from typing import Any, Mapping, Optional

import structlog
from structlog.stdlib import ProcessorFormatter

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

def _first_env_text(*names: str) -> str:
    for name in names:
        value = os.getenv(name)
        if value is not None and value.strip():
            return value.strip()
    return ""

def _service_name_from_env() -> str:
    configured = _first_env_text(
        "MOONMIND_SERVICE_NAME",
        "MOONMIND_SERVICE",
        "OTEL_SERVICE_NAME",
        "SERVICE_NAME",
    )
    if configured:
        return configured

    worker_fleet = _first_env_text("MOONMIND_WORKER_FLEET", "TEMPORAL_WORKER_FLEET")
    if worker_fleet:
        return f"temporal-worker-{worker_fleet.replace('_', '-')}"

    worker_runtime = _first_env_text("MOONMIND_WORKER_RUNTIME")
    if worker_runtime:
        return f"moonmind-{worker_runtime.replace('_', '-')}-worker"

    return "moonmind"

def _component_name_from_env() -> str:
    return (
        _first_env_text(
            "MOONMIND_COMPONENT",
            "MOONMIND_COMPONENT_NAME",
            "TEMPORAL_WORKER_FLEET",
            "MOONMIND_WORKER_RUNTIME",
        )
        or "application"
    )

def _worker_fleet_from_env() -> str:
    return _first_env_text(
        "MOONMIND_WORKER_FLEET",
        "TEMPORAL_WORKER_FLEET",
        "MOONMIND_WORKER_RUNTIME",
    )

def default_log_fields_from_env() -> dict[str, str]:
    """Return stable log enrichment fields shared by MoonMind services."""

    return {
        "service": _service_name_from_env(),
        "component": _component_name_from_env(),
        "worker_fleet": _worker_fleet_from_env(),
        "worker_id": _first_env_text("MOONMIND_WORKER_ID", "HOSTNAME"),
    }

def _merge_default_fields(
    default_fields: Mapping[str, Any],
) -> Callable[[Any, str, dict[str, Any]], dict[str, Any]]:
    def processor(
        _logger: Any, _method_name: str, event_dict: dict[str, Any]
    ) -> dict[str, Any]:
        for key, value in default_fields.items():
            event_dict.setdefault(key, value)
        return event_dict

    return processor

def _configure_structlog(default_fields: Mapping[str, Any]) -> ProcessorFormatter:
    shared_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        _merge_default_fields(default_fields),
        structlog.processors.TimeStamper(fmt="iso", utc=True, key="timestamp"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    structlog.configure(
        processors=[
            *shared_processors,
            ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )
    return ProcessorFormatter(
        foreign_pre_chain=[
            *shared_processors,
            structlog.stdlib.ExtraAdder(),
        ],
        processors=[
            ProcessorFormatter.remove_processors_meta,
            structlog.processors.JSONRenderer(),
        ],
    )

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
        env_value = (
            os.getenv("WORKFLOW_STRUCTURED_LOGS")
            or os.getenv("STRUCTURED_LOGS")
            or os.getenv("MOONMIND_STRUCTURED_LOGS")
        )
        structured = env_value.lower() in {"1", "true", "yes"} if env_value else True

    resolved_default_fields = {
        **default_log_fields_from_env(),
        **dict(default_fields or {}),
    }

    if structured:
        formatter: logging.Formatter = _configure_structlog(resolved_default_fields)
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
