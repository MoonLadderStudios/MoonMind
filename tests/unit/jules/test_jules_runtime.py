"""Unit tests for Jules runtime gate helpers."""

from __future__ import annotations

from moonmind.jules.runtime import (
    JULES_RUNTIME_DISABLED_MESSAGE,
    RuntimeGateState,
    build_runtime_gate_state,
    is_jules_runtime_enabled,
)


def test_is_jules_runtime_enabled_requires_enabled_and_credentials(
    monkeypatch,
) -> None:
    monkeypatch.setenv("JULES_ENABLED", "true")
    monkeypatch.setenv("JULES_API_URL", "https://jules.example.test")
    monkeypatch.setenv("JULES_API_KEY", "test-key")

    assert is_jules_runtime_enabled()

    monkeypatch.setenv("JULES_ENABLED", "false")
    assert is_jules_runtime_enabled() is False


def test_build_runtime_gate_state_reports_missing_fields(monkeypatch) -> None:
    monkeypatch.delenv("JULES_ENABLED", raising=False)
    monkeypatch.delenv("JULES_API_URL", raising=False)
    monkeypatch.delenv("JULES_API_KEY", raising=False)

    state = build_runtime_gate_state()

    assert isinstance(state, RuntimeGateState)
    assert state.enabled is False
    assert state.missing == ("JULES_ENABLED", "JULES_API_URL", "JULES_API_KEY")
    assert state.error_message == JULES_RUNTIME_DISABLED_MESSAGE


def test_build_runtime_gate_state_honors_explicit_values() -> None:
    state = build_runtime_gate_state(
        enabled=True,
        api_url="https://jules.example.test",
        api_key="test-key",
    )

    assert state.enabled is True
    assert state.missing == ()
