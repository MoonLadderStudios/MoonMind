"""Unit tests for Codex Cloud runtime settings and gate."""

from __future__ import annotations

from moonmind.codex_cloud.settings import (
    build_codex_cloud_gate,
    is_codex_cloud_enabled,
)


class TestCodexCloudGate:
    def test_disabled_when_nothing_set(self):
        gate = build_codex_cloud_gate(env={})
        assert gate.enabled is False
        assert "CODEX_CLOUD_ENABLED" in gate.missing

    def test_disabled_when_only_enabled_flag_set(self):
        gate = build_codex_cloud_gate(env={"CODEX_CLOUD_ENABLED": "true"})
        assert gate.enabled is False
        assert "CODEX_CLOUD_API_URL" in gate.missing
        assert "CODEX_CLOUD_API_KEY" in gate.missing

    def test_enabled_when_all_set(self):
        gate = build_codex_cloud_gate(
            env={
                "CODEX_CLOUD_ENABLED": "true",
                "CODEX_CLOUD_API_URL": "https://codex.test",
                "CODEX_CLOUD_API_KEY": "key-123",
            }
        )
        assert gate.enabled is True
        assert gate.missing == ()

    def test_disabled_when_enabled_is_false(self):
        gate = build_codex_cloud_gate(
            env={
                "CODEX_CLOUD_ENABLED": "false",
                "CODEX_CLOUD_API_URL": "https://codex.test",
                "CODEX_CLOUD_API_KEY": "key-123",
            }
        )
        assert gate.enabled is False
        assert "CODEX_CLOUD_ENABLED" in gate.missing

    def test_enabled_with_explicit_args(self):
        gate = build_codex_cloud_gate(
            enabled=True,
            api_url="https://codex.test",
            api_key="key-456",
            env={},
        )
        assert gate.enabled is True

    def test_enabled_accepts_alternate_true_values(self):
        for value in ("1", "yes", "on", "True", "TRUE"):
            gate = build_codex_cloud_gate(
                env={
                    "CODEX_CLOUD_ENABLED": value,
                    "CODEX_CLOUD_API_URL": "https://codex.test",
                    "CODEX_CLOUD_API_KEY": "key-123",
                }
            )
            assert gate.enabled is True, f"Expected enabled for value={value!r}"


class TestIsCodexCloudEnabled:
    def test_returns_false_for_empty_env(self):
        assert is_codex_cloud_enabled(env={}) is False

    def test_returns_true_when_fully_configured(self):
        assert (
            is_codex_cloud_enabled(
                env={
                    "CODEX_CLOUD_ENABLED": "true",
                    "CODEX_CLOUD_API_URL": "https://codex.test",
                    "CODEX_CLOUD_API_KEY": "key-789",
                }
            )
            is True
        )
