"""Runtime output parser protocol and default implementations."""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

_GEMINI_RATE_LIMIT_MARKERS: tuple[str, ...] = (
    "model_capacity_exhausted",
    "no capacity available for model",
    "resource_exhausted",
    "status: 429",
    "status 429",
    "too many requests",
    "ratelimitexceeded",
    "retrying with backoff",
)
_CODEX_HARD_BLOCKER_MARKERS: tuple[str, ...] = (
    "blocked on the workspace tooling constraint",
    "apply_patch executable or tool is not available",
    "no actual `apply_patch` tool is available",
    "no actual 'apply_patch' tool is available",
)
_CODEX_TERMINAL_BLOCKER_MARKERS: tuple[str, ...] = (
    "i'll start by exploring the codebase",
    "let me search more broadly",
    "let me search more specifically",
    "strict compliance with the repo rule, i should stop here",
    "want me to keep going",
    "is there something specific you'd like me to work on",
)

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

    @abstractmethod
    def parse_stream_chunk(self, chunk: str) -> list[dict]:
        """Parse a chunk of streamed text and extract structured events."""

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

    def parse_stream_chunk(self, chunk: str) -> list[dict]:
        return []

def _extract_matching_lines(
    markers: tuple[str, ...],
    *texts: str,
) -> list[str]:
    matches: list[str] = []
    seen: set[str] = set()
    for text in texts:
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            lower = stripped.lower()
            if any(marker in lower for marker in markers) and lower not in seen:
                seen.add(lower)
                matches.append(stripped)
    return matches

def _last_nonempty_line(text: str) -> str | None:
    for line in reversed(text.splitlines()):
        stripped = line.strip()
        if stripped:
            return stripped
    return None

class CodexCliOutputParser(PlainTextOutputParser):
    """Plain-text parser with Codex-specific managed-runtime blocker detection."""

    @staticmethod
    def extract_blocker_lines(*texts: str) -> list[str]:
        matches = _extract_matching_lines(_CODEX_HARD_BLOCKER_MARKERS, *texts)
        seen = {line.lower() for line in matches}
        for text in texts:
            line = _last_nonempty_line(text)
            if not line:
                continue
            lower = line.lower()
            if lower in seen:
                continue
            if any(marker in lower for marker in _CODEX_TERMINAL_BLOCKER_MARKERS):
                seen.add(lower)
                matches.append(line)
        return matches

    def parse(self, stdout: str, stderr: str) -> ParsedOutput:
        base = super().parse(stdout, stderr)
        blocker_lines = self.extract_blocker_lines(stdout, stderr)
        error_messages = list(base.error_messages)
        for line in blocker_lines:
            if line not in error_messages:
                error_messages.append(line)
        return ParsedOutput(
            raw_text=base.raw_text,
            events=base.events,
            error_messages=error_messages,
            rate_limited=base.rate_limited,
            has_structured_output=base.has_structured_output,
        )

class GeminiCliOutputParser(PlainTextOutputParser):
    """Plain-text parser with Gemini-specific 429/capacity detection."""

    @staticmethod
    def _extract_rate_limit_lines(*texts: str) -> list[str]:
        matches: list[str] = []
        for text in texts:
            for line in text.splitlines():
                stripped = line.strip()
                if not stripped:
                    continue
                lower = stripped.lower()
                if any(marker in lower for marker in _GEMINI_RATE_LIMIT_MARKERS):
                    matches.append(stripped)
        return matches

    def parse(self, stdout: str, stderr: str) -> ParsedOutput:
        base = super().parse(stdout, stderr)
        rate_limit_lines = self._extract_rate_limit_lines(stdout, stderr)
        error_messages = list(base.error_messages)
        for line in rate_limit_lines:
            if line not in error_messages:
                error_messages.append(line)
        return ParsedOutput(
            raw_text=base.raw_text,
            events=[],
            error_messages=error_messages,
            rate_limited=bool(rate_limit_lines),
            has_structured_output=False,
        )

    def parse_stream_chunk(self, chunk: str) -> list[dict]:
        events: list[dict] = []
        for line in self._extract_rate_limit_lines(chunk):
            events.append(
                {
                    "type": "rate_limit",
                    "provider": "gemini_cli",
                    "status_code": 429,
                    "message": line,
                }
            )
        return events

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
            if not stripped:
                continue
            lower = stripped.lower()
            is_rate_limit = "rate limit" in lower or "429" in lower
            is_error = any(
                ind in lower for ind in ("error:", "fatal:")
            )
            if is_rate_limit:
                rate_limited = True
                error_messages.append(stripped)
            elif is_error:
                error_messages.append(stripped)

        return ParsedOutput(
            raw_text=stdout + stderr,
            events=events,
            error_messages=error_messages,
            rate_limited=rate_limited,
            has_structured_output=bool(events),
        )

    def parse_stream_chunk(self, chunk: str) -> list[dict]:
        events: list[dict] = []
        for line in chunk.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            try:
                events.append(json.loads(stripped))
            except json.JSONDecodeError:
                continue
        return events
