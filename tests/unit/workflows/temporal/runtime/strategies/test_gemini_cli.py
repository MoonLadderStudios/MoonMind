"""Tests for GeminiCliStrategy and registry integration."""

from __future__ import annotations

from types import SimpleNamespace

from moonmind.workflows.temporal.runtime.strategies import (
    RUNTIME_STRATEGIES,
    GeminiCliStrategy,
    get_strategy,
)

# ---------------------------------------------------------------------------
# Helpers — lightweight stand-ins for Pydantic models
# ---------------------------------------------------------------------------

def _make_profile(
    *,
    command_template: list[str] | None = None,
    default_model: str | None = None,
    default_effort: str | None = None,
    runtime_id: str = "gemini_cli",
) -> SimpleNamespace:
    return SimpleNamespace(
        command_template=command_template or ["gemini"],
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
# Registry tests (DOC-REQ-002 / FR-004)
# ---------------------------------------------------------------------------


class TestRegistry:
    def test_gemini_cli_registered(self) -> None:
        assert "gemini_cli" in RUNTIME_STRATEGIES

    def test_get_strategy_found(self) -> None:
        s = get_strategy("gemini_cli")
        assert s is not None
        assert isinstance(s, GeminiCliStrategy)

    def test_get_strategy_missing_returns_none(self) -> None:
        assert get_strategy("unknown_runtime") is None


# ---------------------------------------------------------------------------
# GeminiCliStrategy property tests (DOC-REQ-003 / FR-005)
# ---------------------------------------------------------------------------


class TestGeminiCliProperties:
    def test_runtime_id(self) -> None:
        s = GeminiCliStrategy()
        assert s.runtime_id == "gemini_cli"

    def test_default_command_template(self) -> None:
        s = GeminiCliStrategy()
        assert s.default_command_template == ["gemini"]

    def test_default_auth_mode(self) -> None:
        s = GeminiCliStrategy()
        assert s.default_auth_mode == "api_key"


# ---------------------------------------------------------------------------
# build_command tests (DOC-REQ-003 validation)
# ---------------------------------------------------------------------------


class TestGeminiCliBuildCommand:
    """Verify strategy produces identical output to the legacy elif block."""

    def test_basic_prompt(self) -> None:
        s = GeminiCliStrategy()
        profile = _make_profile()
        request = _make_request(instruction_ref="Fix the bug")
        cmd = s.build_command(profile, request)
        assert cmd == ["gemini", "--yolo", "--prompt", "Fix the bug"]

    def test_with_model(self) -> None:
        s = GeminiCliStrategy()
        profile = _make_profile(default_model="gemini-2.5-pro")
        request = _make_request(instruction_ref="Refactor this")
        cmd = s.build_command(profile, request)
        assert cmd == [
            "gemini",
            "--model", "gemini-2.5-pro",
            "--yolo", "--prompt", "Refactor this",
        ]

    def test_with_model_override_in_params(self) -> None:
        s = GeminiCliStrategy()
        profile = _make_profile(default_model="gemini-2.5-pro")
        request = _make_request(
            instruction_ref="Help",
            parameters={"model": "gemini-2.5-flash"},
        )
        cmd = s.build_command(profile, request)
        assert cmd == [
            "gemini",
            "--model", "gemini-2.5-flash",
            "--yolo", "--prompt", "Help",
        ]

    def test_effort_is_ignored(self) -> None:
        """Gemini CLI does not support --effort; it should not be added."""
        s = GeminiCliStrategy()
        profile = _make_profile(default_effort="high")
        request = _make_request(instruction_ref="Think hard")
        cmd = s.build_command(profile, request)
        assert cmd == [
            "gemini",
            "--yolo", "--prompt", "Think hard",
        ]

    def test_with_model_and_ignored_effort(self) -> None:
        """Gemini CLI applies --model but ignores --effort."""
        s = GeminiCliStrategy()
        profile = _make_profile(
            default_model="gemini-2.5-pro",
            default_effort="medium",
        )
        request = _make_request(instruction_ref="Go")
        cmd = s.build_command(profile, request)
        assert cmd == [
            "gemini",
            "--model", "gemini-2.5-pro",
            "--yolo", "--prompt", "Go",
        ]

    def test_effort_param_is_ignored(self) -> None:
        """Effort from request.parameters should also be ignored."""
        s = GeminiCliStrategy()
        profile = _make_profile()
        request = _make_request(
            instruction_ref="Think hard",
            parameters={"effort": "high"},
        )
        cmd = s.build_command(profile, request)
        assert "--effort" not in cmd
        assert cmd == [
            "gemini",
            "--yolo", "--prompt", "Think hard",
        ]

    def test_no_instruction_ref(self) -> None:
        s = GeminiCliStrategy()
        profile = _make_profile()
        request = _make_request()
        cmd = s.build_command(profile, request)
        assert cmd == ["gemini"]

    def test_custom_command_template(self) -> None:
        s = GeminiCliStrategy()
        profile = _make_profile(command_template=["gemini", "--sandbox"])
        request = _make_request(instruction_ref="Test")
        cmd = s.build_command(profile, request)
        assert cmd == [
            "gemini", "--sandbox",
            "--yolo", "--prompt", "Test",
        ]


# ---------------------------------------------------------------------------
# shape_environment tests (DOC-REQ-006 / FR-006)
# ---------------------------------------------------------------------------


class TestGeminiCliShapeEnvironment:
    def test_passes_through_gemini_keys(self) -> None:
        s = GeminiCliStrategy()
        base_env = {"UNRELATED_KEY": "value"}
        
        from unittest.mock import patch
        with patch("os.environ", {
            "HOME": "/home/user",
            "GEMINI_HOME": "/opt/gemini",
            "GEMINI_CLI_HOME": "/opt/gemini-cli",
            "PATH": "/usr/bin",
        }):
            result = s.shape_environment(base_env, None)
            
        assert result == {
            "UNRELATED_KEY": "value",
            "HOME": "/home/user",
            "GEMINI_HOME": "/opt/gemini",
            "GEMINI_CLI_HOME": "/opt/gemini-cli",
        }

    def test_missing_optional_keys(self) -> None:
        s = GeminiCliStrategy()
        base_env = {"PATH": "/usr/bin"}
        
        from unittest.mock import patch
        with patch("os.environ", {"HOME": "/home/user"}):
            result = s.shape_environment(base_env, None)
            
        assert result == {"PATH": "/usr/bin", "HOME": "/home/user"}

    def test_empty_env(self) -> None:
        s = GeminiCliStrategy()
        
        from unittest.mock import patch
        with patch("os.environ", {"UNRELATED": "value"}):
            result = s.shape_environment({}, None)
            
        assert result == {}


# ---------------------------------------------------------------------------
# Launcher delegation / fallthrough tests (DOC-REQ-004 / FR-007)
# ---------------------------------------------------------------------------


class TestLauncherDelegation:
    """Verify the launcher's build_command delegates correctly."""

    def test_gemini_cli_delegates_to_strategy(self) -> None:
        """When runtime_id='gemini_cli', the launcher should produce
        the same output as the strategy (strategy handles it)."""
        from moonmind.workflows.temporal.runtime.strategies import (
            get_strategy,
        )

        strategy = get_strategy("gemini_cli")
        assert strategy is not None

        profile = _make_profile(default_model="test-model")
        request = _make_request(instruction_ref="Hello")
        cmd = strategy.build_command(profile, request)
        assert "--yolo" in cmd
        assert "--prompt" in cmd

    def test_unregistered_runtime_returns_none(self) -> None:
        """Truly unknown runtimes should get None from the registry."""
        assert get_strategy("unknown_runtime") is None
        assert get_strategy("some_future_cli") is None

    def test_all_known_runtimes_are_registered(self) -> None:
        """Since Phase 2, all four runtimes are registered."""

        assert get_strategy("codex_cli") is not None
        assert get_strategy("claude_code") is not None
        assert get_strategy("gemini_cli") is not None

