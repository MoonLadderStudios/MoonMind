"""Unit tests for Jules runtime gate helpers."""

from __future__ import annotations

from moonmind.jules.runtime import (
    JULES_RUNTIME_DISABLED_MESSAGE,
    RuntimeGateState,
    build_runtime_gate_state,
    is_jules_runtime_enabled,
)

def test_is_jules_runtime_enabled_with_api_key_only(
    monkeypatch,
) -> None:
    """Jules auto-enables when JULES_API_KEY is present (no explicit JULES_ENABLED)."""
    monkeypatch.delenv("JULES_ENABLED", raising=False)
    monkeypatch.delenv("JULES_API_URL", raising=False)
    monkeypatch.setenv("JULES_API_KEY", "test-key")

    assert is_jules_runtime_enabled()

def test_is_jules_runtime_enabled_with_enabled_and_credentials(
    monkeypatch,
) -> None:
    monkeypatch.setenv("JULES_ENABLED", "true")
    monkeypatch.setenv("JULES_API_URL", "https://jules.example.test")
    monkeypatch.setenv("JULES_API_KEY", "test-key")

    assert is_jules_runtime_enabled()

def test_is_jules_runtime_disabled_by_explicit_false(
    monkeypatch,
) -> None:
    """JULES_ENABLED=false disables even when API key is present."""
    monkeypatch.setenv("JULES_ENABLED", "false")
    monkeypatch.setenv("JULES_API_KEY", "test-key")

    assert is_jules_runtime_enabled() is False

def test_is_jules_runtime_disabled_without_api_key(
    monkeypatch,
) -> None:
    """No API key → disabled regardless of JULES_ENABLED."""
    monkeypatch.delenv("JULES_ENABLED", raising=False)
    monkeypatch.delenv("JULES_API_URL", raising=False)
    monkeypatch.delenv("JULES_API_KEY", raising=False)

    assert is_jules_runtime_enabled() is False

def test_build_runtime_gate_state_reports_missing_key(monkeypatch) -> None:
    monkeypatch.delenv("JULES_ENABLED", raising=False)
    monkeypatch.delenv("JULES_API_URL", raising=False)
    monkeypatch.delenv("JULES_API_KEY", raising=False)

    state = build_runtime_gate_state()

    assert isinstance(state, RuntimeGateState)
    assert state.enabled is False
    assert state.missing == ("JULES_API_KEY",)
    assert state.error_message == JULES_RUNTIME_DISABLED_MESSAGE

def test_build_runtime_gate_state_explicit_disabled(monkeypatch) -> None:
    monkeypatch.setenv("JULES_ENABLED", "false")
    monkeypatch.setenv("JULES_API_KEY", "test-key")

    state = build_runtime_gate_state()

    assert state.enabled is False
    assert "JULES_ENABLED" in state.missing

def test_build_runtime_gate_state_honors_explicit_values() -> None:
    state = build_runtime_gate_state(
        enabled=True,
        api_url="https://jules.example.test",
        api_key="test-key",
    )

    assert state.enabled is True
    assert state.missing == ()

def test_build_runtime_gate_state_auto_enables_with_key_only() -> None:
    """Just providing api_key is sufficient — uses default URL, no enabled flag needed."""
    state = build_runtime_gate_state(api_key="test-key")

    assert state.enabled is True
    assert state.missing == ()
