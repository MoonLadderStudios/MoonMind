"""Tests for RuntimeOutputParser implementations and exit classification."""

from __future__ import annotations

from unittest.mock import patch

from moonmind.workflows.temporal.runtime.output_parser import (
    ClaudeCodeOutputParser,
    CodexCliOutputParser,
    GeminiCliOutputParser,
    ParsedOutput,
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
        assert len(result.error_messages) == 2
        assert any("Error:" in m for m in result.error_messages)
        assert any("Fatal:" in m for m in result.error_messages)

    def test_ignores_non_error_stderr(self) -> None:
        parser = PlainTextOutputParser()
        result = parser.parse("output", "just some debug info")
        assert result.error_messages == []

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

    def test_claude_failure(self) -> None:
        s = ClaudeCodeStrategy()
        status, failure = s.classify_exit(1, "", "")
        assert status == "failed"
        assert failure == "execution_error"

    def test_claude_rate_limited(self) -> None:
        s = ClaudeCodeStrategy()
        stdout = "You've hit your limit · resets 1pm (UTC)"
        status, failure = s.classify_exit(1, stdout, "")
        assert status == "failed"
        assert failure == "integration_error"

    def test_claude_rate_limited_usage_variant(self) -> None:
        s = ClaudeCodeStrategy()
        stderr = "You've hit your usage limit. Contact your admin."
        status, failure = s.classify_exit(1, "", stderr)
        assert status == "failed"
        assert failure == "integration_error"

    def test_codex_success(self) -> None:
        s = CodexCliStrategy()
        status, failure = s.classify_exit(0, "", "")
        assert status == "completed"
        assert failure is None

    def test_codex_rate_limited(self) -> None:
        s = CodexCliStrategy()
        stdout = (
            "You've hit your usage limit. To get more access now, send a "
            "request to your admin or try again at 4:45 AM."
        )
        status, failure = s.classify_exit(1, stdout, "")
        assert status == "failed"
        assert failure == "integration_error"

class TestDefaultOutputParser:
    def test_gemini_returns_plain_text(self) -> None:
        s = GeminiCliStrategy()
        assert isinstance(s.create_output_parser(), GeminiCliOutputParser)

    def test_claude_returns_claude_code_output_parser(self) -> None:
        s = ClaudeCodeStrategy()
        assert isinstance(s.create_output_parser(), ClaudeCodeOutputParser)

    def test_codex_returns_codex_cli_output_parser(self) -> None:
        s = CodexCliStrategy()
        assert isinstance(s.create_output_parser(), CodexCliOutputParser)

class TestCodexCliOutputParser:
    def test_parse_detects_managed_runtime_blocker_message(self) -> None:
        parser = CodexCliOutputParser()
        result = parser.parse(
            "Blocked on the workspace tooling constraint.\n",
            "",
        )
        assert result.error_messages == ["Blocked on the workspace tooling constraint."]

    def test_parse_detects_terminal_interactive_handoff_message(self) -> None:
        parser = CodexCliOutputParser()
        result = parser.parse(
            "Want me to keep going with that, or is there something specific you'd like me to work on?\n",
            "",
        )
        assert result.error_messages == [
            "Want me to keep going with that, or is there something specific you'd like me to work on?"
        ]

    def test_parse_ignores_intermediate_progress_message_when_output_finishes_cleanly(self) -> None:
        parser = CodexCliOutputParser()
        result = parser.parse(
            "Let me search more specifically for frontend components and provider-related code.\n"
            "Implemented provider profile details in task history and added unit coverage.\n",
            "",
        )
        assert result.error_messages == []

    def test_parse_deduplicates_blocker_lines_case_insensitively(self) -> None:
        parser = CodexCliOutputParser()
        result = parser.parse(
            "Blocked on the workspace tooling constraint.\n",
            "blocked on the workspace tooling constraint.\n",
        )
        assert result.error_messages == ["Blocked on the workspace tooling constraint."]

    def test_parse_ignores_non_blocker_stdout(self) -> None:
        parser = CodexCliOutputParser()
        result = parser.parse(
            "Implemented provider profile details in task history and added unit coverage.\n",
            "",
        )
        assert result.error_messages == []

    def test_parse_ignores_positive_apply_patch_availability_message(self) -> None:
        parser = CodexCliOutputParser()
        result = parser.parse(
            "The actual `apply_patch` tool is available in this environment.\n",
            "",
        )
        assert result.error_messages == []

    def test_parse_preserves_base_parser_fields(self) -> None:
        parser = CodexCliOutputParser()
        base = ParsedOutput(
            raw_text="stdoutstderr",
            events=[{"type": "event"}],
            error_messages=["Error: base"],
            rate_limited=True,
            has_structured_output=True,
        )
        with patch.object(PlainTextOutputParser, "parse", return_value=base):
            result = parser.parse("stdout", "stderr")
        assert result == base

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

class TestClaudeCodeOutputParser:
    def test_parse_detects_short_limit_message(self) -> None:
        parser = ClaudeCodeOutputParser()
        result = parser.parse("You've hit your limit · resets 1pm (UTC)\n", "")
        assert result.rate_limited
        assert result.error_messages == [
            "You've hit your limit · resets 1pm (UTC)"
        ]

    def test_parse_detects_usage_limit_message(self) -> None:
        parser = ClaudeCodeOutputParser()
        result = parser.parse(
            "",
            "You've hit your usage limit. Contact your admin.\n",
        )
        assert result.rate_limited
        assert any("usage limit" in message.lower() for message in result.error_messages)

    def test_parse_ignores_clean_output(self) -> None:
        parser = ClaudeCodeOutputParser()
        result = parser.parse(
            "Implemented the feature and added unit coverage.\n",
            "",
        )
        assert not result.rate_limited
        assert result.error_messages == []

    def test_parse_stream_chunk_emits_rate_limit_event(self) -> None:
        parser = ClaudeCodeOutputParser()
        events = parser.parse_stream_chunk(
            "You've hit your limit · resets 1pm (UTC)\n"
        )
        assert len(events) == 1
        assert events[0]["type"] == "rate_limit"
        assert events[0]["provider"] == "claude_code"
        assert events[0]["status_code"] == 429

class TestCodexCliOutputParserRateLimits:
    def test_parse_detects_codex_usage_limit(self) -> None:
        parser = CodexCliOutputParser()
        result = parser.parse(
            "You've hit your usage limit. To get more access now, send a "
            "request to your admin or try again at 4:45 AM.\n",
            "",
        )
        assert result.rate_limited
        assert any("usage limit" in message.lower() for message in result.error_messages)

    def test_parse_detects_429_status_code(self) -> None:
        parser = CodexCliOutputParser()
        result = parser.parse("", "Request failed with status: 429\n")
        assert result.rate_limited

    def test_parse_stream_chunk_emits_rate_limit_event(self) -> None:
        parser = CodexCliOutputParser()
        events = parser.parse_stream_chunk(
            "You've hit your usage limit. send a request to your admin.\n"
        )
        assert len(events) == 1
        assert events[0]["type"] == "rate_limit"
        assert events[0]["provider"] == "codex_cli"
        assert events[0]["status_code"] == 429

class TestStrategyClassifyResultRateLimit:
    """Workflow-boundary test: classify_result must emit provider_error_code='429'
    so AgentRun's provider_error_requires_cooldown() check fires the slot-release
    + cooldown path instead of Temporal activity retries.
    """

    def test_claude_code_emits_429_provider_error_code(self) -> None:
        from moonmind.workflows.provider_failures import (
            provider_error_requires_cooldown,
        )

        s = ClaudeCodeStrategy()
        result = s.classify_result(
            exit_code=1,
            stdout="You've hit your limit · resets 1pm (UTC)\n",
            stderr="",
        )
        assert result.status == "failed"
        assert result.failure_class == "integration_error"
        assert result.provider_error_code == "429"
        assert provider_error_requires_cooldown(
            provider_error_code=result.provider_error_code,
            retry_recommendation=None,
        )

    def test_codex_cli_emits_429_provider_error_code(self) -> None:
        from moonmind.workflows.provider_failures import (
            provider_error_requires_cooldown,
        )

        s = CodexCliStrategy()
        result = s.classify_result(
            exit_code=1,
            stdout=(
                "You've hit your usage limit. To get more access now, send a "
                "request to your admin or try again at 4:45 AM.\n"
            ),
            stderr="",
        )
        assert result.status == "failed"
        assert result.failure_class == "integration_error"
        assert result.provider_error_code == "429"
        assert provider_error_requires_cooldown(
            provider_error_code=result.provider_error_code,
            retry_recommendation=None,
        )

    def test_gemini_cli_emits_429_provider_error_code(self) -> None:
        s = GeminiCliStrategy()
        result = s.classify_result(
            exit_code=143,
            stdout="",
            stderr="Error 429: Too many requests\nreason: MODEL_CAPACITY_EXHAUSTED\n",
        )
        assert result.failure_class == "integration_error"
        assert result.provider_error_code == "429"

    def test_claude_code_clean_exit_does_not_set_provider_error_code(self) -> None:
        s = ClaudeCodeStrategy()
        result = s.classify_result(exit_code=0, stdout="ok\n", stderr="")
        assert result.status == "completed"
        assert result.provider_error_code is None

    def test_claude_code_terminate_on_live_rate_limit(self) -> None:
        assert ClaudeCodeStrategy().terminate_on_live_rate_limit() is True

    def test_codex_cli_terminate_on_live_rate_limit(self) -> None:
        assert CodexCliStrategy().terminate_on_live_rate_limit() is True

    def test_codex_cli_blocker_still_classified_when_rate_limit_absent(self) -> None:
        """Codex blocker detection must still fire when there's no 429."""
        s = CodexCliStrategy()
        result = s.classify_result(
            exit_code=0,
            stdout="Blocked on the workspace tooling constraint.\n",
            stderr="",
        )
        assert result.status == "failed"
        assert result.failure_class == "execution_error"
        assert result.provider_error_code is None
