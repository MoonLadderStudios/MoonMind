"""Tests for RuntimeOutputParser implementations and exit classification."""

from __future__ import annotations

import json

from moonmind.workflows.temporal.runtime.output_parser import (
    NdjsonOutputParser,
    PlainTextOutputParser,
)
from moonmind.workflows.temporal.runtime.strategies.cursor_cli import (
    CursorCliStrategy,
)
from moonmind.workflows.temporal.runtime.strategies.gemini_cli import (
    GeminiCliStrategy,
)
from moonmind.workflows.temporal.runtime.strategies.claude_code import (
    ClaudeCodeStrategy,
)
from moonmind.workflows.temporal.runtime.strategies.codex_cli import (
    CodexCliStrategy,
)


# ---------------------------------------------------------------------------
# PlainTextOutputParser
# ---------------------------------------------------------------------------


class TestPlainTextOutputParser:
    def test_empty_output(self) -> None:
        parser = PlainTextOutputParser()
        result = parser.parse("", "")
        assert result.raw_text == ""
        assert result.error_messages == []
        assert not result.has_structured_output
        assert not result.rate_limited

    def test_extracts_error_lines(self) -> None:
        parser = PlainTextOutputParser()
        result = parser.parse(
            "some output",
            "Error: something went wrong\nWarning: be careful\nFatal: crash",
        )
        assert len(result.error_messages) == 2  # Error: and Fatal:
        assert any("Error:" in m for m in result.error_messages)
        assert any("Fatal:" in m for m in result.error_messages)

    def test_ignores_non_error_stderr(self) -> None:
        parser = PlainTextOutputParser()
        result = parser.parse("output", "just some debug info")
        assert result.error_messages == []


# ---------------------------------------------------------------------------
# NdjsonOutputParser
# ---------------------------------------------------------------------------


class TestNdjsonOutputParser:
    def test_empty_output(self) -> None:
        parser = NdjsonOutputParser()
        result = parser.parse("", "")
        assert result.events == []
        assert result.error_messages == []
        assert not result.has_structured_output
        assert not result.rate_limited

    def test_parses_valid_ndjson(self) -> None:
        parser = NdjsonOutputParser()
        events = [
            {"type": "progress", "message": "Working..."},
            {"type": "result", "message": "Done"},
        ]
        stdout = "\n".join(json.dumps(e) for e in events)
        result = parser.parse(stdout, "")
        assert len(result.events) == 2
        assert result.has_structured_output
        assert not result.rate_limited

    def test_detects_rate_limit_429(self) -> None:
        parser = NdjsonOutputParser()
        events = [
            {"type": "error", "status_code": 429, "message": "Too many requests"},
        ]
        stdout = json.dumps(events[0])
        result = parser.parse(stdout, "")
        assert result.rate_limited
        assert len(result.error_messages) > 0

    def test_detects_rate_limit_text(self) -> None:
        parser = NdjsonOutputParser()
        events = [
            {"type": "error", "error": "Rate limit exceeded"},
        ]
        stdout = json.dumps(events[0])
        result = parser.parse(stdout, "")
        assert result.rate_limited

    def test_detects_rate_limit_in_stderr(self) -> None:
        parser = NdjsonOutputParser()
        result = parser.parse("", "Error: 429 rate limit hit")
        assert result.rate_limited

    def test_skips_non_json_lines(self) -> None:
        parser = NdjsonOutputParser()
        stdout = 'not json\n{"type": "ok"}\nalso not json'
        result = parser.parse(stdout, "")
        assert len(result.events) == 1

    def test_error_event_type(self) -> None:
        parser = NdjsonOutputParser()
        events = [{"type": "error", "message": "Something failed"}]
        stdout = json.dumps(events[0])
        result = parser.parse(stdout, "")
        assert len(result.error_messages) > 0


# ---------------------------------------------------------------------------
# CursorCliStrategy exit classification
# ---------------------------------------------------------------------------


class TestCursorCliClassifyExit:
    def test_success(self) -> None:
        s = CursorCliStrategy()
        status, failure = s.classify_exit(0, "", "")
        assert status == "completed"
        assert failure is None

    def test_failure(self) -> None:
        s = CursorCliStrategy()
        status, failure = s.classify_exit(1, "", "")
        assert status == "failed"
        assert failure == "execution_error"

    def test_rate_limited_via_ndjson(self) -> None:
        s = CursorCliStrategy()
        stdout_line = json.dumps(
            {"type": "error", "status_code": 429, "message": "Rate limited"}
        )
        status, failure = s.classify_exit(1, stdout_line, "")
        assert status == "failed"
        assert failure == "rate_limited"

    def test_rate_limited_via_stderr(self) -> None:
        s = CursorCliStrategy()
        status, failure = s.classify_exit(1, "", "Error: 429 rate limit exceeded")
        assert status == "failed"
        assert failure == "rate_limited"


class TestCursorCliOutputParser:
    def test_returns_ndjson_parser(self) -> None:
        s = CursorCliStrategy()
        parser = s.create_output_parser()
        assert isinstance(parser, NdjsonOutputParser)


# ---------------------------------------------------------------------------
# Default strategy exit classification (Gemini, Claude, Codex)
# ---------------------------------------------------------------------------


class TestDefaultStrategyClassifyExit:
    def test_gemini_success(self) -> None:
        s = GeminiCliStrategy()
        status, failure = s.classify_exit(0, "", "")
        assert status == "completed"
        assert failure is None

    def test_gemini_failure(self) -> None:
        s = GeminiCliStrategy()
        status, failure = s.classify_exit(1, "", "")
        assert status == "failed"
        assert failure == "execution_error"

    def test_claude_success(self) -> None:
        s = ClaudeCodeStrategy()
        status, failure = s.classify_exit(0, "", "")
        assert status == "completed"
        assert failure is None

    def test_codex_success(self) -> None:
        s = CodexCliStrategy()
        status, failure = s.classify_exit(0, "", "")
        assert status == "completed"
        assert failure is None


class TestDefaultOutputParser:
    def test_gemini_returns_none(self) -> None:
        s = GeminiCliStrategy()
        assert s.create_output_parser() is None

    def test_claude_returns_none(self) -> None:
        s = ClaudeCodeStrategy()
        assert s.create_output_parser() is None

    def test_codex_returns_none(self) -> None:
        s = CodexCliStrategy()
        assert s.create_output_parser() is None
