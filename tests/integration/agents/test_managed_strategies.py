"""Integration tests: managed runtime strategy command building & workspace prep.

Per-strategy tests exercising real file I/O and command construction for all
four managed runtimes.  No API keys, Docker workers, or Temporal needed.

Run::

    ./tools/test_unit.sh tests/integration/agents/test_managed_strategies.py -v
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from moonmind.workflows.temporal.runtime.strategies import (
    RUNTIME_STRATEGIES,
    ClaudeCodeStrategy,
    CodexCliStrategy,
    CursorCliStrategy,
    GeminiCliStrategy,
    get_strategy,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fake_profile(
    runtime_id: str,
    *,
    command_template: list[str] | None = None,
    default_model: str | None = None,
    default_effort: str | None = None,
) -> SimpleNamespace:
    """Build a lightweight profile-like object for strategy tests."""
    strategy = get_strategy(runtime_id)
    return SimpleNamespace(
        runtime_id=runtime_id,
        command_template=command_template or (list(strategy.default_command_template) if strategy else [runtime_id]),
        default_model=default_model,
        default_effort=default_effort,
    )


def _fake_request(
    *,
    instruction_ref: str | None = "Do something",
    parameters: dict | None = None,
) -> SimpleNamespace:
    """Build a lightweight request-like object for strategy tests."""
    return SimpleNamespace(
        instruction_ref=instruction_ref,
        parameters=parameters or {},
        approval_policy={"level": "full_autonomy"},
    )


# ---------------------------------------------------------------------------
# Registry completeness
# ---------------------------------------------------------------------------


class TestStrategyRegistry:
    """Verify all four runtimes are registered and discoverable."""

    def test_all_runtimes_registered(self) -> None:
        expected = {"gemini_cli", "codex_cli", "cursor_cli", "claude_code"}
        assert set(RUNTIME_STRATEGIES.keys()) == expected

    @pytest.mark.parametrize(
        "runtime_id",
        ["gemini_cli", "codex_cli", "cursor_cli", "claude_code"],
    )
    def test_get_strategy_returns_instance(self, runtime_id: str) -> None:
        strategy = get_strategy(runtime_id)
        assert strategy is not None
        assert strategy.runtime_id == runtime_id

    def test_unknown_runtime_returns_none(self) -> None:
        assert get_strategy("unknown_runtime") is None


# ---------------------------------------------------------------------------
# Gemini CLI Strategy
# ---------------------------------------------------------------------------


class TestGeminiCliStrategy:
    def test_defaults(self) -> None:
        s = GeminiCliStrategy()
        assert s.runtime_id == "gemini_cli"
        assert s.default_command_template == ["gemini"]
        assert s.default_auth_mode == "api_key"

    def test_build_command_basic(self) -> None:
        s = GeminiCliStrategy()
        profile = _fake_profile("gemini_cli")
        request = _fake_request(instruction_ref="Write tests")
        cmd = s.build_command(profile, request)

        assert cmd[0] == "gemini"
        assert "--yolo" in cmd
        assert "--prompt" in cmd
        idx = cmd.index("--prompt")
        assert cmd[idx + 1] == "Write tests"

    def test_build_command_with_model_and_effort(self) -> None:
        s = GeminiCliStrategy()
        profile = _fake_profile("gemini_cli")
        request = _fake_request(
            parameters={"model": "gemini-2.5-pro", "effort": "high"}
        )
        cmd = s.build_command(profile, request)

        assert "--model" in cmd
        assert "gemini-2.5-pro" in cmd
        assert "--effort" in cmd
        assert "high" in cmd

    def test_shape_environment_preserves_gemini_keys(self) -> None:
        s = GeminiCliStrategy()
        profile = _fake_profile("gemini_cli")
        with patch.dict(os.environ, {"HOME": "/home/user", "GEMINI_HOME": "/gemini"}):
            env = s.shape_environment({}, profile)
            assert env.get("HOME") == "/home/user"
            assert env.get("GEMINI_HOME") == "/gemini"

    def test_classify_exit_success(self) -> None:
        s = GeminiCliStrategy()
        status, failure_class = s.classify_exit(0, "", "")
        assert status == "completed"
        assert failure_class is None

    def test_classify_exit_failure(self) -> None:
        s = GeminiCliStrategy()
        status, failure_class = s.classify_exit(1, "", "error")
        assert status == "failed"
        assert failure_class == "execution_error"


# ---------------------------------------------------------------------------
# Codex CLI Strategy
# ---------------------------------------------------------------------------


class TestCodexCliStrategy:
    def test_defaults(self) -> None:
        s = CodexCliStrategy()
        assert s.runtime_id == "codex_cli"
        assert s.default_command_template == ["codex", "exec", "--full-auto"]
        assert s.default_auth_mode == "api_key"

    def test_build_command_basic(self) -> None:
        s = CodexCliStrategy()
        profile = _fake_profile("codex_cli")
        request = _fake_request(instruction_ref="Fix the build")
        cmd = s.build_command(profile, request)

        assert cmd[:3] == ["codex", "exec", "--full-auto"]
        assert "Fix the build" in cmd

    def test_build_command_with_model(self) -> None:
        s = CodexCliStrategy()
        profile = _fake_profile("codex_cli")
        request = _fake_request(parameters={"model": "codex-mini-latest"})
        cmd = s.build_command(profile, request)

        assert "-m" in cmd
        assert "codex-mini-latest" in cmd
        # Codex does NOT support --effort
        assert "--effort" not in cmd

    def test_shape_environment_preserves_codex_keys(self) -> None:
        s = CodexCliStrategy()
        profile = _fake_profile("codex_cli")
        with patch.dict(os.environ, {"HOME": "/home/user", "CODEX_HOME": "/codex"}):
            env = s.shape_environment({}, profile)
            assert env.get("HOME") == "/home/user"
            assert env.get("CODEX_HOME") == "/codex"

    async def test_prepare_workspace_calls_context_injection(self, tmp_path: Path) -> None:
        """Codex workspace prep should invoke ContextInjectionService."""
        s = CodexCliStrategy()
        request = _fake_request(instruction_ref="Test task")

        with patch(
            "moonmind.rag.context_injection.ContextInjectionService"
        ) as MockCIS:
            mock_service = MockCIS.return_value
            mock_service.inject_context = AsyncMock()
            await s.prepare_workspace(tmp_path, request)
            mock_service.inject_context.assert_awaited_once()


# ---------------------------------------------------------------------------
# Cursor CLI Strategy
# ---------------------------------------------------------------------------


class TestCursorCliStrategy:
    def test_defaults(self) -> None:
        s = CursorCliStrategy()
        assert s.runtime_id == "cursor_cli"
        assert s.default_command_template == ["cursor"]
        assert s.default_auth_mode == "oauth"

    def test_build_command_includes_stream_json(self) -> None:
        s = CursorCliStrategy()
        profile = _fake_profile("cursor_cli")
        request = _fake_request(instruction_ref="Refactor module")
        cmd = s.build_command(profile, request)

        assert "--output-format" in cmd
        assert "stream-json" in cmd
        assert "--force" in cmd
        assert "-p" in cmd
        idx = cmd.index("-p")
        assert cmd[idx + 1] == "Refactor module"

    def test_build_command_with_model(self) -> None:
        s = CursorCliStrategy()
        profile = _fake_profile("cursor_cli")
        request = _fake_request(parameters={"model": "gpt-4"})
        cmd = s.build_command(profile, request)
        assert "--model" in cmd
        assert "gpt-4" in cmd

    def test_build_command_with_sandbox_mode(self) -> None:
        s = CursorCliStrategy()
        profile = _fake_profile("cursor_cli")
        request = _fake_request(parameters={"sandbox_mode": "strict"})
        cmd = s.build_command(profile, request)
        assert "--sandbox" in cmd
        assert "strict" in cmd

    async def test_prepare_workspace_writes_files(self, tmp_path: Path) -> None:
        """Cursor workspace prep should create .cursor/rules/ and .cursor/cli.json."""
        s = CursorCliStrategy()
        request = _fake_request(instruction_ref="Test task for cursor")

        await s.prepare_workspace(tmp_path, request)

        # Verify rule file was created
        rules_dir = tmp_path / ".cursor" / "rules"
        assert rules_dir.exists(), ".cursor/rules/ should exist"
        rule_files = list(rules_dir.glob("*.mdc"))
        assert len(rule_files) > 0, "At least one .mdc rule file should be created"

        # Verify cli.json was created
        cli_json = tmp_path / ".cursor" / "cli.json"
        assert cli_json.exists(), ".cursor/cli.json should exist"
        cli_data = json.loads(cli_json.read_text())
        assert isinstance(cli_data, dict)

    def test_classify_exit_success(self) -> None:
        s = CursorCliStrategy()
        status, failure_class = s.classify_exit(0, "", "")
        assert status == "completed"
        assert failure_class is None

    def test_classify_exit_failure(self) -> None:
        s = CursorCliStrategy()
        status, failure_class = s.classify_exit(1, "", "error occurred")
        assert status == "failed"
        assert failure_class == "execution_error"

    def test_classify_exit_rate_limited(self) -> None:
        """429 in NDJSON output should be classified as rate limited."""
        s = CursorCliStrategy()
        ndjson_429 = '{"type": "error", "error": {"code": 429, "message": "Rate limited"}}\n'
        status, failure_class = s.classify_exit(1, ndjson_429, "")
        assert status == "failed"
        assert failure_class == "integration_error"

    def test_output_parser_is_ndjson(self) -> None:
        from moonmind.workflows.temporal.runtime.output_parser import NdjsonOutputParser

        s = CursorCliStrategy()
        parser = s.create_output_parser()
        assert isinstance(parser, NdjsonOutputParser)


# ---------------------------------------------------------------------------
# Claude Code Strategy
# ---------------------------------------------------------------------------


class TestClaudeCodeStrategy:
    def test_defaults(self) -> None:
        s = ClaudeCodeStrategy()
        assert s.runtime_id == "claude_code"
        assert s.default_command_template == ["claude"]
        assert s.default_auth_mode == "api_key"

    def test_build_command_basic(self) -> None:
        s = ClaudeCodeStrategy()
        profile = _fake_profile("claude_code")
        request = _fake_request(instruction_ref="Write a module")
        cmd = s.build_command(profile, request)

        assert cmd[0] == "claude"
        assert "--prompt" in cmd
        idx = cmd.index("--prompt")
        assert cmd[idx + 1] == "Write a module"

    def test_build_command_with_model_and_effort(self) -> None:
        s = ClaudeCodeStrategy()
        profile = _fake_profile("claude_code")
        request = _fake_request(
            parameters={"model": "claude-sonnet-4-20250514", "effort": "medium"}
        )
        cmd = s.build_command(profile, request)

        assert "--model" in cmd
        assert "claude-sonnet-4-20250514" in cmd
        assert "--effort" in cmd
        assert "medium" in cmd

    def test_build_command_no_instruction(self) -> None:
        """Without instruction_ref, prompt flags should be absent."""
        s = ClaudeCodeStrategy()
        profile = _fake_profile("claude_code")
        request = _fake_request(instruction_ref=None)
        cmd = s.build_command(profile, request)

        assert "--prompt" not in cmd

    async def test_prepare_workspace_is_noop(self, tmp_path: Path) -> None:
        """Claude workspace prep is currently a no-op stub."""
        s = ClaudeCodeStrategy()
        request = _fake_request()

        # Should not raise and should not create any files
        await s.prepare_workspace(tmp_path, request)

        # Only our tmp_path should exist, no new subdirectories
        entries = list(tmp_path.iterdir())
        assert len(entries) == 0, (
            "Claude prepare_workspace should be a no-op (no files created)"
        )

    def test_output_parser_is_plain_text(self) -> None:
        from moonmind.workflows.temporal.runtime.output_parser import PlainTextOutputParser

        s = ClaudeCodeStrategy()
        parser = s.create_output_parser()
        assert isinstance(parser, PlainTextOutputParser)
