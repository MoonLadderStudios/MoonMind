"""Unit tests for Anthropic Claude runtime gate helpers."""

from __future__ import annotations

from moonmind.claude.runtime import (
    CLAUDE_RUNTIME_DISABLED_MESSAGE,
    RuntimeGateState,
    build_runtime_gate_state,
    is_claude_runtime_enabled,
    resolve_anthropic_api_key,
)

def test_resolve_anthropic_api_key_prefers_argument(monkeypatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("CLAUDE_API_KEY", raising=False)

    key = resolve_anthropic_api_key(api_key="  provided-key  ")

    assert key == "provided-key"

def test_resolve_anthropic_api_key_prefers_anthropic_env(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-env")
    monkeypatch.setenv("CLAUDE_API_KEY", "claude-env")

    key = resolve_anthropic_api_key()

    assert key == "anthropic-env"

def test_resolve_anthropic_api_key_falls_back_to_alias(monkeypatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("CLAUDE_API_KEY", "alias-key")

    key = resolve_anthropic_api_key()

    assert key == "alias-key"

def test_is_claude_runtime_enabled_respects_alias(monkeypatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("CLAUDE_API_KEY", raising=False)

    assert is_claude_runtime_enabled()

def test_build_runtime_gate_state_reports_source(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "env-key")

    state = build_runtime_gate_state()

    assert isinstance(state, RuntimeGateState)
    assert state.enabled is True
    assert state.source_env == "ANTHROPIC_API_KEY"
    assert state.error_message == CLAUDE_RUNTIME_DISABLED_MESSAGE

def test_build_runtime_gate_state_honors_custom_error_message(monkeypatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("CLAUDE_API_KEY", raising=False)

    state = build_runtime_gate_state(error_message="custom-error")

    assert state.enabled is True
    assert state.source_env == "unconditional"
    assert state.error_message == "custom-error"
