"""OAuth provider registry — maps runtime_id to provider spec."""

from __future__ import annotations

import os

from moonmind.workflows.temporal.runtime.providers.base import OAuthProviderSpec

OAUTH_PROVIDERS: dict[str, OAuthProviderSpec] = {
    "gemini_cli": OAuthProviderSpec(
        runtime_id="gemini_cli",
        auth_mode="oauth",
        session_transport="none",
        default_volume_name="gemini_auth_volume",
        default_mount_path="/var/lib/gemini-auth",
        default_volume_name_env="GEMINI_VOLUME_NAME",
        default_mount_path_env="GEMINI_VOLUME_PATH",
        provider_id="google",
        provider_label="Google",
        bootstrap_command=["true"],
        success_check="gemini_config_exists",
        account_label_prefix="Gemini",
    ),
    "codex_cli": OAuthProviderSpec(
        runtime_id="codex_cli",
        auth_mode="oauth",
        session_transport="moonmind_pty_ws",
        default_volume_name="codex_auth_volume",
        default_mount_path="/home/app/.codex",
        default_volume_name_env="CODEX_VOLUME_NAME",
        default_mount_path_env="CODEX_VOLUME_PATH",
        provider_id="openai",
        provider_label="OpenAI",
        bootstrap_command=["codex", "login", "--device-auth"],
        success_check="codex_config_exists",
        account_label_prefix="Codex",
    ),
    "claude_code": OAuthProviderSpec(
        runtime_id="claude_code",
        auth_mode="oauth",
        session_transport="moonmind_pty_ws",
        default_volume_name="claude_auth_volume",
        default_mount_path="/home/app/.claude",
        default_volume_name_env="CLAUDE_VOLUME_NAME",
        default_mount_path_env="CLAUDE_VOLUME_PATH",
        provider_id="anthropic",
        provider_label="Anthropic",
        bootstrap_command=["claude", "auth", "login"],
        success_check="claude_config_exists",
        account_label_prefix="Claude",
    ),

}

def get_provider(runtime_id: str) -> OAuthProviderSpec | None:
    """Look up the OAuth provider spec for a runtime.

    Returns ``None`` if the runtime is not registered.
    """
    return OAUTH_PROVIDERS.get(runtime_id)

def get_provider_default(runtime_id: str, key: str) -> str | None:
    """Return a configured OAuth provider default for a runtime.

    Volume defaults honor deployment env overrides at call time. Provider
    metadata is static registry data.
    """
    spec = get_provider(runtime_id)
    if spec is None:
        return None

    if key == "volume_ref":
        return os.environ.get(
            spec["default_volume_name_env"], spec["default_volume_name"]
        )
    if key == "volume_mount_path":
        return os.environ.get(
            spec["default_mount_path_env"], spec["default_mount_path"]
        )
    if key == "provider_id":
        return spec["provider_id"]
    if key == "provider_label":
        return spec["provider_label"]
    if key == "session_transport":
        return spec["session_transport"]
    return None

def get_provider_bootstrap_command(runtime_id: str) -> tuple[str, ...]:
    """Return a validated provider bootstrap command for OAuth enrollment."""
    spec = get_provider(runtime_id)
    if spec is None:
        raise ValueError(f"Unsupported OAuth runtime: {runtime_id}")

    command = spec.get("bootstrap_command")
    if not isinstance(command, list):
        raise ValueError(
            f"OAuth provider '{runtime_id}' bootstrap command is not configured"
        )

    normalized = tuple(str(part).strip() for part in command)
    if not normalized or any(not part for part in normalized):
        raise ValueError(
            f"OAuth provider '{runtime_id}' bootstrap command is not configured"
        )
    return normalized

def supported_runtime_ids() -> list[str]:
    """Return the list of runtime IDs with registered OAuth providers."""
    return list(OAUTH_PROVIDERS.keys())
