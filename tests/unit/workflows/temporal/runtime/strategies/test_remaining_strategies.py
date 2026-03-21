"""Tests for CursorCliStrategy, ClaudeCodeStrategy, and CodexCliStrategy."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from moonmind.workflows.temporal.runtime.strategies import (
    RUNTIME_STRATEGIES,
    get_strategy,
)
from moonmind.workflows.temporal.runtime.strategies.claude_code import (
    ClaudeCodeStrategy,
)
from moonmind.workflows.temporal.runtime.strategies.codex_cli import (
    CodexCliStrategy,
)
from moonmind.workflows.temporal.runtime.strategies.cursor_cli import (
    CursorCliStrategy,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_profile(
    *,
    command_template: list[str] | None = None,
    default_model: str | None = None,
    default_effort: str | None = None,
    runtime_id: str = "test",
) -> SimpleNamespace:
    return SimpleNamespace(
        command_template=command_template or ["test-cmd"],
        default_model=default_model,
        default_effort=default_effort,
        runtime_id=runtime_id,
    )


def _make_request(
    *,
    instruction_ref: str | None = None,
    parameters: dict | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        instruction_ref=instruction_ref,
        parameters=parameters or {},
    )


# ---------------------------------------------------------------------------
# Registry completeness
# ---------------------------------------------------------------------------


class TestAllStrategiesRegistered:
    def test_four_strategies_registered(self) -> None:
        assert len(RUNTIME_STRATEGIES) == 4

    def test_all_ids_present(self) -> None:
        expected = {"gemini_cli", "cursor_cli", "claude_code", "codex_cli"}
        assert set(RUNTIME_STRATEGIES.keys()) == expected


# ---------------------------------------------------------------------------
# CursorCliStrategy
# ---------------------------------------------------------------------------


class TestCursorCliProperties:
    def test_runtime_id(self) -> None:
        assert CursorCliStrategy().runtime_id == "cursor_cli"

    def test_default_command_template(self) -> None:
        assert CursorCliStrategy().default_command_template == ["cursor"]

    def test_default_auth_mode(self) -> None:
        assert CursorCliStrategy().default_auth_mode == "oauth"


class TestCursorCliBuildCommand:
    def test_basic_prompt(self) -> None:
        s = CursorCliStrategy()
        profile = _make_profile(command_template=["cursor"])
        request = _make_request(instruction_ref="Fix bug")
        cmd = s.build_command(profile, request)
        assert cmd == [
            "cursor", "-p", "Fix bug",
            "--output-format", "stream-json", "--force",
        ]

    def test_with_model(self) -> None:
        s = CursorCliStrategy()
        profile = _make_profile(command_template=["cursor"], default_model="gpt-4")
        request = _make_request(instruction_ref="Help")
        cmd = s.build_command(profile, request)
        assert "--model" in cmd
        assert "gpt-4" in cmd

    def test_with_sandbox(self) -> None:
        s = CursorCliStrategy()
        profile = _make_profile(command_template=["cursor"])
        request = _make_request(
            instruction_ref="Test",
            parameters={"sandbox_mode": "strict"},
        )
        cmd = s.build_command(profile, request)
        assert "--sandbox" in cmd
        assert "strict" in cmd

    def test_no_prompt(self) -> None:
        s = CursorCliStrategy()
        profile = _make_profile(command_template=["cursor"])
        request = _make_request()
        cmd = s.build_command(profile, request)
        assert cmd == ["cursor", "--output-format", "stream-json", "--force"]

    def test_always_has_force_and_stream_json(self) -> None:
        s = CursorCliStrategy()
        profile = _make_profile(command_template=["cursor"])
        request = _make_request(instruction_ref="Go")
        cmd = s.build_command(profile, request)
        assert "--force" in cmd
        assert "--output-format" in cmd
        assert "stream-json" in cmd


# ---------------------------------------------------------------------------
# ClaudeCodeStrategy
# ---------------------------------------------------------------------------


class TestClaudeCodeProperties:
    def test_runtime_id(self) -> None:
        assert ClaudeCodeStrategy().runtime_id == "claude_code"

    def test_default_command_template(self) -> None:
        assert ClaudeCodeStrategy().default_command_template == ["claude"]

    def test_default_auth_mode(self) -> None:
        assert ClaudeCodeStrategy().default_auth_mode == "api_key"


class TestClaudeCodeBuildCommand:
    def test_basic_prompt(self) -> None:
        s = ClaudeCodeStrategy()
        profile = _make_profile(command_template=["claude"])
        request = _make_request(instruction_ref="Refactor this")
        cmd = s.build_command(profile, request)
        assert cmd == ["claude", "--prompt", "Refactor this"]

    def test_with_model_and_effort(self) -> None:
        s = ClaudeCodeStrategy()
        profile = _make_profile(
            command_template=["claude"],
            default_model="claude-4-opus",
            default_effort="high",
        )
        request = _make_request(instruction_ref="Do it")
        cmd = s.build_command(profile, request)
        assert cmd == [
            "claude",
            "--model", "claude-4-opus",
            "--effort", "high",
            "--prompt", "Do it",
        ]

    def test_param_override(self) -> None:
        s = ClaudeCodeStrategy()
        profile = _make_profile(
            command_template=["claude"],
            default_model="claude-4-opus",
        )
        request = _make_request(
            instruction_ref="Go",
            parameters={"model": "claude-4-sonnet"},
        )
        cmd = s.build_command(profile, request)
        assert "--model" in cmd
        assert "claude-4-sonnet" in cmd

    def test_no_prompt(self) -> None:
        s = ClaudeCodeStrategy()
        profile = _make_profile(command_template=["claude"])
        request = _make_request()
        cmd = s.build_command(profile, request)
        assert cmd == ["claude"]


# ---------------------------------------------------------------------------
# CodexCliStrategy
# ---------------------------------------------------------------------------


class TestCodexCliProperties:
    def test_runtime_id(self) -> None:
        assert CodexCliStrategy().runtime_id == "codex_cli"

    def test_default_command_template(self) -> None:
        assert CodexCliStrategy().default_command_template == [
            "codex", "exec", "--full-auto",
        ]

    def test_default_auth_mode(self) -> None:
        assert CodexCliStrategy().default_auth_mode == "api_key"


class TestCodexCliBuildCommand:
    def test_basic_prompt(self) -> None:
        s = CodexCliStrategy()
        profile = _make_profile(
            command_template=["codex", "exec", "--full-auto"],
        )
        request = _make_request(instruction_ref="Fix the bug")
        cmd = s.build_command(profile, request)
        assert cmd == ["codex", "exec", "--full-auto", "Fix the bug"]

    def test_with_model(self) -> None:
        s = CodexCliStrategy()
        profile = _make_profile(
            command_template=["codex", "exec", "--full-auto"],
            default_model="o3-mini",
        )
        request = _make_request(instruction_ref="Hello")
        cmd = s.build_command(profile, request)
        assert cmd == [
            "codex", "exec", "--full-auto",
            "-m", "o3-mini", "Hello",
        ]

    def test_model_from_params(self) -> None:
        s = CodexCliStrategy()
        profile = _make_profile(
            command_template=["codex", "exec", "--full-auto"],
            default_model="o3-mini",
        )
        request = _make_request(
            instruction_ref="Go",
            parameters={"model": "gpt-4.1"},
        )
        cmd = s.build_command(profile, request)
        assert "-m" in cmd
        assert "gpt-4.1" in cmd

    def test_no_prompt(self) -> None:
        s = CodexCliStrategy()
        profile = _make_profile(
            command_template=["codex", "exec", "--full-auto"],
        )
        request = _make_request()
        cmd = s.build_command(profile, request)
        assert cmd == ["codex", "exec", "--full-auto"]


class TestCodexCliShapeEnvironment:
    def test_passes_through_codex_keys(self) -> None:
        s = CodexCliStrategy()
        env = {
            "HOME": "/home/user",
            "CODEX_HOME": "/opt/codex",
            "CODEX_CONFIG_HOME": "/opt/codex-config",
            "CODEX_CONFIG_PATH": "/opt/codex-config/path",
            "UNRELATED": "value",
            "PATH": "/usr/bin",
        }
        result = s.shape_environment(env, None)
        assert result == {
            "HOME": "/home/user",
            "CODEX_HOME": "/opt/codex",
            "CODEX_CONFIG_HOME": "/opt/codex-config",
            "CODEX_CONFIG_PATH": "/opt/codex-config/path",
        }

    def test_empty_env(self) -> None:
        s = CodexCliStrategy()
        result = s.shape_environment({}, None)
        assert result == {}


class TestCodexCliPrepareWorkspace:
    @pytest.mark.asyncio
    @patch("moonmind.workflows.temporal.runtime.strategies.codex_cli.ContextInjectionService")
    async def test_prepare_workspace_calls_injection(self, mock_service_class, tmp_path) -> None:
        mock_service = mock_service_class.return_value
        mock_service.inject_context = AsyncMock()
        
        s = CodexCliStrategy()
        request = _make_request(instruction_ref="Do work")
        await s.prepare_workspace(workspace_path=tmp_path, request=request)
        
        mock_service.inject_context.assert_called_once_with(
            request=request,
            workspace_path=tmp_path,
        )
