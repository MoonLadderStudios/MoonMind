"""Tests for OAuth provider registry."""

from __future__ import annotations

import pytest

from moonmind.workflows.temporal.runtime.providers.registry import (
    OAUTH_PROVIDERS,
    get_provider_bootstrap_command,
    get_provider,
    get_provider_default,
    supported_runtime_ids,
)


class TestOAuthProviderRegistry:
    """Verify provider registry entries and lookup."""

    def test_gemini_provider_exists(self) -> None:
        spec = get_provider("gemini_cli")
        assert spec is not None
        assert spec["runtime_id"] == "gemini_cli"
        assert spec["auth_mode"] == "oauth"
        assert spec["session_transport"] == "none"
        assert spec["default_volume_name"] == "gemini_auth_volume"
        assert spec["default_mount_path"] == "/var/lib/gemini-auth"

    def test_codex_provider_exists(self) -> None:
        spec = get_provider("codex_cli")
        assert spec is not None
        assert spec["runtime_id"] == "codex_cli"
        assert spec["default_volume_name"] == "codex_auth_volume"
        assert spec["default_mount_path"] == "/home/app/.codex"

    def test_claude_provider_exists(self) -> None:
        spec = get_provider("claude_code")
        assert spec is not None
        assert spec["runtime_id"] == "claude_code"
        assert spec["default_volume_name"] == "claude_auth_volume"
        assert spec["default_mount_path"] == "/home/app/.claude"



    def test_unknown_runtime_returns_none(self) -> None:
        assert get_provider("unknown_runtime") is None

    def test_supported_runtime_ids(self) -> None:
        ids = supported_runtime_ids()
        assert "gemini_cli" in ids
        assert "codex_cli" in ids
        assert "claude_code" in ids
        assert len(ids) == 3

    def test_all_providers_have_required_keys(self) -> None:
        required_keys = {
            "runtime_id",
            "auth_mode",
            "session_transport",
            "default_volume_name",
            "default_mount_path",
            "default_volume_name_env",
            "default_mount_path_env",
            "provider_id",
            "provider_label",
            "bootstrap_command",
            "success_check",
            "account_label_prefix",
        }
        for runtime_id, spec in OAUTH_PROVIDERS.items():
            for key in required_keys:
                assert key in spec, f"Missing key '{key}' in provider '{runtime_id}'"

    def test_non_codex_providers_use_none_transport(self) -> None:
        for runtime_id, spec in OAUTH_PROVIDERS.items():
            if runtime_id == "codex_cli":
                continue
            assert spec["session_transport"] == "none", (
                f"Provider '{runtime_id}' should use none transport until a replacement exists"
            )

    def test_session_transport_default_is_exposed_for_runtime_boundaries(self) -> None:
        assert get_provider_default("codex_cli", "session_transport") == "moonmind_pty_ws"

    def test_all_providers_use_oauth_mode(self) -> None:
        for runtime_id, spec in OAUTH_PROVIDERS.items():
            assert spec["auth_mode"] == "oauth", (
                f"Provider '{runtime_id}' should use oauth auth mode"
            )

    def test_bootstrap_commands_are_lists(self) -> None:
        for runtime_id, spec in OAUTH_PROVIDERS.items():
            assert isinstance(spec["bootstrap_command"], list), (
                f"Provider '{runtime_id}' bootstrap_command should be a list"
            )
            assert len(spec["bootstrap_command"]) > 0, (
                f"Provider '{runtime_id}' bootstrap_command should not be empty"
            )

    def test_codex_bootstrap_command_is_not_placeholder(self) -> None:
        assert get_provider_bootstrap_command("codex_cli") == (
            "codex",
            "login",
            "--device-auth",
        )

    def test_bootstrap_command_rejects_unknown_runtime(self) -> None:
        with pytest.raises(ValueError, match="Unsupported OAuth runtime"):
            get_provider_bootstrap_command("unknown_runtime")

    def test_bootstrap_command_rejects_blank_command(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        providers = dict(OAUTH_PROVIDERS)
        providers["codex_cli"] = dict(providers["codex_cli"], bootstrap_command=[" "])
        monkeypatch.setattr(
            "moonmind.workflows.temporal.runtime.providers.registry.OAUTH_PROVIDERS",
            providers,
        )

        with pytest.raises(ValueError, match="bootstrap command is not configured"):
            get_provider_bootstrap_command("codex_cli")
