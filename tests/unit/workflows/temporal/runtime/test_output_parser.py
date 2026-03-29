"""Tests for RuntimeOutputParser implementations and exit classification."""

from __future__ import annotations

import json

from moonmind.workflows.temporal.runtime.output_parser import (
    GeminiCliOutputParser,
    PlainTextOutputParser,
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
    def test_parse_stream_chunk_returns_empty(self) -> None:
        parser = PlainTextOutputParser()
        assert parser.parse_stream_chunk("line1\nline2") == []

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

    def test_gemini_rate_limited(self) -> None:
        s = GeminiCliStrategy()
        stderr = "Error 429: Too many requests\nreason: MODEL_CAPACITY_EXHAUSTED"
        status, failure = s.classify_exit(143, "", stderr)
        assert status == "failed"
        assert failure == "integration_error"

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
    def test_gemini_returns_plain_text(self) -> None:
        s = GeminiCliStrategy()
        assert isinstance(s.create_output_parser(), GeminiCliOutputParser)

    def test_claude_returns_plain_text(self) -> None:
        s = ClaudeCodeStrategy()
        assert isinstance(s.create_output_parser(), PlainTextOutputParser)

    def test_codex_returns_plain_text(self) -> None:
        s = CodexCliStrategy()
        assert isinstance(s.create_output_parser(), PlainTextOutputParser)


class TestGeminiCliOutputParser:
    def test_parse_stream_chunk_detects_capacity_rate_limit(self) -> None:
        parser = GeminiCliOutputParser()
        events = parser.parse_stream_chunk(
            'Attempt 6 failed with status 429. Retrying with backoff...\n'
        )
        assert len(events) == 1
        assert events[0]["type"] == "rate_limit"
        assert events[0]["status_code"] == 429

    def test_parse_detects_capacity_markers(self) -> None:
        parser = GeminiCliOutputParser()
        result = parser.parse(
            "",
            'message: No capacity available for model gemini-3.1-pro-preview\n'
            'reason: MODEL_CAPACITY_EXHAUSTED\n',
        )
        assert result.rate_limited
        assert any("capacity" in message.lower() for message in result.error_messages)
