"""Tests for CursorCliStrategy, ClaudeCodeStrategy, and CodexCliStrategy."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from moonmind.workflows.temporal.runtime.strategies import (
    RUNTIME_STRATEGIES,
)
from moonmind.workflows.temporal.runtime.strategies.claude_code import (
    ClaudeCodeStrategy,
)
from moonmind.workflows.temporal.runtime.strategies.codex_cli import (
    CodexCliStrategy,
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
    env_overrides: dict | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        command_template=command_template or ["test-cmd"],
        default_model=default_model,
        default_effort=default_effort,
        runtime_id=runtime_id,
        env_overrides=env_overrides or {},
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
    def test_three_strategies_registered(self) -> None:
        assert len(RUNTIME_STRATEGIES) == 3

    def test_all_ids_present(self) -> None:
        expected = {"gemini_cli", "claude_code", "codex_cli"}
        assert set(RUNTIME_STRATEGIES.keys()) == expected


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
        # When no profile default_model is set, the claude_code runtime default applies.
        assert cmd == [
            "claude",
            "--model", "Sonnet 4.6",
            "-p", "--dangerously-skip-permissions", "Refactor this",
        ]

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
            "-p", "--dangerously-skip-permissions", "Do it",
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
        # No instruction_ref but runtime default model still applies.
        assert cmd == ["claude", "--model", "Sonnet 4.6", "-p", "--dangerously-skip-permissions"]

    def test_anthropic_model_env_suppresses_model_flag(self) -> None:
        """When ANTHROPIC_MODEL is in env_overrides (MiniMax profile), --model must be omitted."""
        s = ClaudeCodeStrategy()
        profile = _make_profile(
            command_template=["claude"],
            default_model="MiniMax-M2.7",
            env_overrides={"ANTHROPIC_MODEL": "MiniMax-M2.7"},
        )
        request = _make_request(
            instruction_ref="Do something",
            parameters={"model": "MiniMax-M2.7"},
        )
        cmd = s.build_command(profile, request)
        assert "--model" not in cmd
        assert "MiniMax-M2.7" not in cmd
        assert cmd == ["claude", "-p", "--dangerously-skip-permissions", "Do something"]

    def test_blank_anthropic_model_env_does_not_suppress_model_flag(self) -> None:
        """A blank ANTHROPIC_MODEL override must NOT suppress --model."""
        s = ClaudeCodeStrategy()
        profile = _make_profile(
            command_template=["claude"],
            default_model="claude-sonnet-4-6",
            env_overrides={"ANTHROPIC_MODEL": ""},
        )
        request = _make_request(instruction_ref="Go")
        cmd = s.build_command(profile, request)
        assert "--model" in cmd
        assert "claude-sonnet-4-6" in cmd

    def test_model_flag_still_passed_without_env_override(self) -> None:
        """Without ANTHROPIC_MODEL in env_overrides, --model is passed as usual."""
        s = ClaudeCodeStrategy()
        profile = _make_profile(
            command_template=["claude"],
            default_model="claude-sonnet-4-6",
        )
        request = _make_request(instruction_ref="Go")
        cmd = s.build_command(profile, request)
        assert "--model" in cmd
        assert "claude-sonnet-4-6" in cmd


# ---------------------------------------------------------------------------
# ClaudeCodeStrategy.prepare_workspace
# ---------------------------------------------------------------------------


class TestClaudeCodePrepareWorkspace:
    @pytest.mark.asyncio
    async def test_creates_claude_md_when_absent(self, tmp_path) -> None:
        """When CLAUDE.md does not exist, it is created with the instruction ref."""
        s = ClaudeCodeStrategy()
        request = _make_request(instruction_ref="Do the task")
        await s.prepare_workspace(tmp_path, request)
        claude_md = tmp_path / "CLAUDE.md"
        assert claude_md.is_file() and not claude_md.is_symlink()
        assert claude_md.read_text() == "Do the task"

    @pytest.mark.asyncio
    async def test_does_not_overwrite_existing_regular_file(self, tmp_path) -> None:
        """When CLAUDE.md already exists as a regular file, it is left unchanged."""
        s = ClaudeCodeStrategy()
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text("existing project context")
        request = _make_request(instruction_ref="New task instructions")
        await s.prepare_workspace(tmp_path, request)
        assert claude_md.read_text() == "existing project context"

    @pytest.mark.asyncio
    async def test_does_not_follow_symlink_to_agents_md(self, tmp_path) -> None:
        """When CLAUDE.md is a symlink (e.g. -> AGENTS.md), it must not be followed.
        AGENTS.md must remain intact after prepare_workspace runs."""
        s = ClaudeCodeStrategy()
        agents_md = tmp_path / "AGENTS.md"
        agents_md.write_text("# Agent coding standards\n\nDo not break me.")
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.symlink_to("AGENTS.md")
        request = _make_request(instruction_ref="Task instructions")
        await s.prepare_workspace(tmp_path, request)
        # AGENTS.md must be untouched
        assert agents_md.read_text() == "# Agent coding standards\n\nDo not break me."
        # CLAUDE.md symlink must still point to AGENTS.md
        assert claude_md.is_symlink()

    @pytest.mark.asyncio
    async def test_no_op_without_instruction_ref(self, tmp_path) -> None:
        """When instruction_ref is absent, nothing is written."""
        s = ClaudeCodeStrategy()
        request = _make_request()
        await s.prepare_workspace(tmp_path, request)
        assert not (tmp_path / "CLAUDE.md").exists()


# ---------------------------------------------------------------------------
# CodexCliStrategy
# ---------------------------------------------------------------------------


class TestCodexCliProperties:
    def test_runtime_id(self) -> None:
        assert CodexCliStrategy().runtime_id == "codex_cli"

    def test_default_command_template(self) -> None:
        assert CodexCliStrategy().default_command_template == [
            "codex", "exec",
        ]

    def test_default_auth_mode(self) -> None:
        assert CodexCliStrategy().default_auth_mode == "api_key"


class TestCodexCliBuildCommand:
    def test_basic_prompt(self) -> None:
        s = CodexCliStrategy()
        profile = _make_profile(
            command_template=["codex", "exec"],
        )
        request = _make_request(instruction_ref="Fix the bug")
        cmd = s.build_command(profile, request)
        # When no profile default_model set, codex_cli runtime default applies.
        assert cmd == ["codex", "exec", "-m", "gpt-5.4", "Fix the bug"]

    def test_with_model(self) -> None:
        s = CodexCliStrategy()
        profile = _make_profile(
            command_template=["codex", "exec"],
            default_model="o3-mini",
        )
        request = _make_request(instruction_ref="Hello")
        cmd = s.build_command(profile, request)
        assert cmd == [
            "codex", "exec",
            "-m", "o3-mini", "Hello",
        ]

    def test_model_from_params(self) -> None:
        s = CodexCliStrategy()
        profile = _make_profile(
            command_template=["codex", "exec"],
            default_model="o3-mini",
        )
        request = _make_request(
            instruction_ref="Go",
            parameters={"model": "gpt-4.1"},
        )
        cmd = s.build_command(profile, request)
        assert "-m" in cmd
        assert "gpt-4.1" in cmd

    def test_strips_managed_policy_flags_from_legacy_template(self) -> None:
        s = CodexCliStrategy()
        profile = _make_profile(
            command_template=[
                "codex",
                "exec",
                "--full-auto",
                "--sandbox",
                "workspace-write",
                "--ask-for-approval",
                "on-request",
            ],
        )
        request = _make_request(instruction_ref="Go")
        cmd = s.build_command(profile, request)
        assert cmd == ["codex", "exec", "-m", "gpt-5.4", "Go"]

    def test_no_prompt(self) -> None:
        s = CodexCliStrategy()
        profile = _make_profile(
            command_template=["codex", "exec"],
        )
        request = _make_request()
        cmd = s.build_command(profile, request)
        # No instruction_ref but runtime default model still applies.
        assert cmd == ["codex", "exec", "-m", "gpt-5.4"]


class TestCodexCliShapeEnvironment:
    def test_passes_through_codex_keys(self) -> None:
        s = CodexCliStrategy()
        base_env = {"UNRELATED": "value"}
        
        from unittest.mock import patch
        with patch("os.environ", {
            "HOME": "/home/user",
            "CODEX_HOME": "/opt/codex",
            "CODEX_CONFIG_HOME": "/opt/codex-config",
            "CODEX_CONFIG_PATH": "/opt/codex-config/path",
            "PATH": "/usr/bin",
        }):
            result = s.shape_environment(base_env, None)
            
        assert result == {
            "UNRELATED": "value",
            "HOME": "/home/user",
            "CODEX_HOME": "/opt/codex",
            "CODEX_CONFIG_HOME": "/opt/codex-config",
            "CODEX_CONFIG_PATH": "/opt/codex-config/path",
        }

    def test_empty_env(self) -> None:
        s = CodexCliStrategy()
        from unittest.mock import patch
        with patch("os.environ", {"UNRELATED": "value"}):
            result = s.shape_environment({}, None)
        assert result == {}


class TestCodexCliPrepareWorkspace:
    @pytest.mark.asyncio
    @patch("moonmind.rag.context_injection.ContextInjectionService")
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
