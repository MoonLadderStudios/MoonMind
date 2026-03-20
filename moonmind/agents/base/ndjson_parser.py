"""NDJSON stream parser for Cursor CLI's ``stream-json`` output format.

Cursor CLI's ``--output-format stream-json`` produces newline-delimited JSON
(NDJSON) events.  Each line is an independent JSON object with at least a
``type`` field.  This module provides lightweight helpers to parse individual
lines and iterate over a stream of lines, yielding typed event objects.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Iterable, Iterator

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CursorStreamEvent:
    """A single parsed event from Cursor CLI's NDJSON output stream.

    Attributes:
        event_type: The ``type`` field from the JSON object (e.g. ``system``,
            ``assistant``, ``tool_call``, ``result``).
        timestamp: Optional ISO-8601 timestamp string from the event.
        data: The ``data`` payload dictionary.  Empty dict if not present.
    """

    event_type: str
    timestamp: str | None = None
    data: dict[str, Any] = field(default_factory=dict)


def parse_ndjson_line(line: str) -> CursorStreamEvent | None:
    """Parse a single NDJSON line into a :class:`CursorStreamEvent`.

    Returns ``None`` and logs a warning if the line is not valid JSON or is
    missing the required ``type`` field.
    """
    stripped = line.strip()
    if not stripped:
        return None

    try:
        obj = json.loads(stripped)
    except json.JSONDecodeError:
        logger.warning("Malformed NDJSON line (invalid JSON): %s", stripped[:200])
        return None

    if not isinstance(obj, dict):
        logger.warning("NDJSON line is not a JSON object: %s", stripped[:200])
        return None

    event_type = obj.get("type")
    if not event_type:
        logger.warning("NDJSON line missing 'type' field: %s", stripped[:200])
        return None

    return CursorStreamEvent(
        event_type=str(event_type),
        timestamp=obj.get("timestamp"),
        data=obj.get("data", {}),
    )


def parse_ndjson_stream(lines: Iterable[str]) -> Iterator[CursorStreamEvent]:
    """Yield :class:`CursorStreamEvent` objects from an iterable of NDJSON lines.

    Malformed or empty lines are silently skipped (with a warning log).
    """
    for line in lines:
        event = parse_ndjson_line(line)
        if event is not None:
            yield event


_RATE_LIMIT_PHRASES: tuple[str, ...] = (
    "rate limit",
    "rate_limit",
    "ratelimit",
    "too many requests",
)


def detect_rate_limit(event: CursorStreamEvent) -> dict[str, Any]:
    """Scan a :class:`CursorStreamEvent` for rate-limit / 429 indicators.

    Returns a dict with:
    - ``detected`` (bool): whether rate-limiting was detected.
    - ``retry_after_seconds`` (int | None): suggested wait time if available.

    Detection checks:
    1. ``event.data.status`` or ``event.data.statusCode`` equal to 429.
    2. ``event.data.error`` or ``event.data.message`` containing rate-limit
       phrases (case-insensitive).
    """
    data = event.data or {}

    # Check HTTP status codes.
    status = data.get("status") or data.get("statusCode")
    if status is not None:
        try:
            if int(status) == 429:
                retry_after = _extract_retry_after(data)
                return {"detected": True, "retry_after_seconds": retry_after}
        except (ValueError, TypeError):
            pass

    # Check error / message text for rate-limit phrases.
    for key in ("error", "message"):
        text = str(data.get(key, "")).lower()
        if any(phrase in text for phrase in _RATE_LIMIT_PHRASES):
            retry_after = _extract_retry_after(data)
            return {"detected": True, "retry_after_seconds": retry_after}

    return {"detected": False, "retry_after_seconds": None}


def _extract_retry_after(data: dict[str, Any]) -> int | None:
    """Try to extract a ``retry_after`` / ``retryAfter`` value from *data*."""
    for key in ("retry_after", "retryAfter", "Retry-After", "retry-after"):
        raw = data.get(key)
        if raw is not None:
            try:
                return int(raw)
            except (ValueError, TypeError):
                pass
    return None
