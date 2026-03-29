"""OAuth provider registry — maps runtime_id to provider spec."""

from __future__ import annotations

from moonmind.workflows.temporal.runtime.providers.base import OAuthProviderSpec

OAUTH_PROVIDERS: dict[str, OAuthProviderSpec] = {
    "gemini_cli": OAuthProviderSpec(
        runtime_id="gemini_cli",
        auth_mode="oauth",
        session_transport="none",
        default_volume_name="gemini_auth_volume",
        default_mount_path="/var/lib/gemini-auth",
        bootstrap_command=["true"],
        success_check="gemini_config_exists",
        account_label_prefix="Gemini",
    ),
    "codex_cli": OAuthProviderSpec(
        runtime_id="codex_cli",
        auth_mode="oauth",
        session_transport="none",
        default_volume_name="codex_auth_volume",
        default_mount_path="/home/app/.codex",
        bootstrap_command=["true"],
        success_check="codex_config_exists",
        account_label_prefix="Codex",
    ),
    "claude_code": OAuthProviderSpec(
        runtime_id="claude_code",
        auth_mode="oauth",
        session_transport="none",
        default_volume_name="claude_auth_volume",
        default_mount_path="/home/app/.claude",
        bootstrap_command=["true"],
        success_check="claude_config_exists",
        account_label_prefix="Claude",
    ),

}


def get_provider(runtime_id: str) -> OAuthProviderSpec | None:
    """Look up the OAuth provider spec for a runtime.

    Returns ``None`` if the runtime is not registered.
    """
    return OAUTH_PROVIDERS.get(runtime_id)


def supported_runtime_ids() -> list[str]:
    """Return the list of runtime IDs with registered OAuth providers."""
    return list(OAUTH_PROVIDERS.keys())
