"""Runtime output parser protocol and default implementations."""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ParsedOutput:
    """Structured result from parsing runtime stdout/stderr."""

    raw_text: str = ""
    events: list[dict] = field(default_factory=list)
    error_messages: list[str] = field(default_factory=list)
    rate_limited: bool = False
    has_structured_output: bool = False


class RuntimeOutputParser(ABC):
    """Protocol for parsing runtime-specific structured output."""

    @abstractmethod
    def parse(self, stdout: str, stderr: str) -> ParsedOutput:
        """Parse stdout/stderr into a structured ParsedOutput."""


class PlainTextOutputParser(RuntimeOutputParser):
    """Default parser that treats output as plain text.

    Used by Gemini CLI, Claude Code, and Codex CLI which produce
    unstructured text output.
    """

    def parse(self, stdout: str, stderr: str) -> ParsedOutput:
        combined = stdout + stderr
        error_messages: list[str] = []

        # Extract error-like lines from stderr
        for line in stderr.splitlines():
            stripped = line.strip()
            if stripped and any(
                indicator in stripped.lower()
                for indicator in ("error:", "fatal:", "exception:", "traceback")
            ):
                error_messages.append(stripped)

        return ParsedOutput(
            raw_text=combined,
            error_messages=error_messages,
            has_structured_output=False,
        )


class NdjsonOutputParser(RuntimeOutputParser):
    """Parser for Cursor CLI's NDJSON (``--output-format stream-json``) output.

    Extracts structured events, detects rate-limiting (HTTP 429),
    and collects error messages from the event stream.
    """

    def parse(self, stdout: str, stderr: str) -> ParsedOutput:
        events: list[dict] = []
        error_messages: list[str] = []
        rate_limited = False

        for line in stdout.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            try:
                event = json.loads(stripped)
                events.append(event)

                # Check for rate limiting markers
                event_type = str(event.get("type", "")).lower()
                status_code = event.get("status_code") or event.get("statusCode")
                error_text = str(event.get("error", "") or event.get("message", ""))

                if status_code == 429 or "rate limit" in error_text.lower():
                    rate_limited = True
                    error_messages.append(f"Rate limited: {error_text or 'HTTP 429'}")

                if event_type in ("error", "fatal"):
                    error_messages.append(error_text or f"Event type: {event_type}")

            except json.JSONDecodeError:
                # Non-JSON line in stdout — skip silently
                continue

        # Also scan stderr for error indicators
        for line in stderr.splitlines():
            stripped = line.strip()
            if stripped and any(
                indicator in stripped.lower()
                for indicator in ("error:", "fatal:", "429", "rate limit")
            ):
                if "rate limit" in stripped.lower() or "429" in stripped:
                    rate_limited = True
                error_messages.append(stripped)

        return ParsedOutput(
            raw_text=stdout + stderr,
            events=events,
            error_messages=error_messages,
            rate_limited=rate_limited,
            has_structured_output=len(events) > 0,
        )
