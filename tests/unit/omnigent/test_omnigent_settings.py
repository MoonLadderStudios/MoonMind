"""Unit tests for Omnigent runtime gate settings."""

from __future__ import annotations

from moonmind.omnigent.settings import build_omnigent_gate, resolved_server_url


def test_omnigent_gate_disabled_when_env_missing() -> None:
    gate = build_omnigent_gate(env={})

    assert gate.enabled is False
    assert gate.missing == ("OMNIGENT_ENABLED", "OMNIGENT_SERVER_URL")


def test_omnigent_gate_requires_server_url_when_enabled() -> None:
    gate = build_omnigent_gate(env={"OMNIGENT_ENABLED": "1"})

    assert gate.enabled is False
    assert gate.missing == ("OMNIGENT_SERVER_URL",)


def test_omnigent_gate_preserves_explicit_false_values() -> None:
    for raw_enabled in ("false", "0", False, 0):
        gate = build_omnigent_gate(env={"OMNIGENT_ENABLED": raw_enabled})

        assert gate.enabled is False
        assert gate.missing == ()


def test_omnigent_gate_enabled_with_flag_and_server_url() -> None:
    env = {
        "OMNIGENT_ENABLED": "true",
        "OMNIGENT_SERVER_URL": " https://omnigent.example.test ",
        "OMNIGENT_API_TOKEN": "activity-boundary-only",
    }

    gate = build_omnigent_gate(env=env)

    assert gate.enabled is True
    assert gate.missing == ()
    assert resolved_server_url(env=env) == "https://omnigent.example.test"
