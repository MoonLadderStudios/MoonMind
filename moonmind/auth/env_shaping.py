"""Environment shaping helpers for OAuth and API-key modes.

These helpers are shared across managed runtime strategies and the OAuth
Session orchestrator to ensure consistent handling of sensitive credentials
and environment overrides.
"""

from __future__ import annotations

# GitHub CLI authentication is required for workflows like pr-resolver.
# Env-var prefixes / names cleared when shaping OAuth environments (DOC-REQ-007).
# These are the sensitive keys that must NOT appear in child-process environments.
OAUTH_CLEARED_VARS: frozenset[str] = frozenset(
    {
        "GOOGLE_API_KEY",
        "GEMINI_API_KEY",
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "OPENAI_API_KEY",
        "CODEX_API_KEY",
        "GITHUB_TOKEN",
    }
)
_BASE_ENV_FILTER_FRAGMENTS: tuple[str, ...] = (
    "password",
    "token",
    "secret",
    "credential",
    "api_key",
    "private_key",
)

def _should_filter_base_env_var(key: str) -> bool:
    normalized_key = str(key or "").strip()
    if not normalized_key:
        return False
    lowered = normalized_key.lower()
    return any(fragment in lowered for fragment in _BASE_ENV_FILTER_FRAGMENTS)

def shape_environment_for_oauth(
    base_env: dict[str, str],
    *,
    volume_mount_path: str | None,
) -> dict[str, str]:
    """Return env dict shaped for OAuth volume-mount mode.

    Clears sensitive API-key vars and sets browser-auth helpers if a
    volume mount path is provided.  Does NOT expose secrets.
    """
    env = dict(base_env)
    for key in OAUTH_CLEARED_VARS:
        env.pop(key, None)
    if volume_mount_path:
        env["MANAGED_AUTH_VOLUME_PATH"] = volume_mount_path
    return env

def shape_environment_for_api_key(
    base_env: dict[str, str],
    *,
    api_key_ref: str | None,
    account_label: str | None,
) -> dict[str, str]:
    """Return env dict shaped for API-key mode.

    The api_key_ref is a *reference* (e.g. a secret store key name), not the
    raw credential.  The actual resolution of the reference into a real key
    is delegated to the runtime launcher (out of scope for Phase 5).
    """
    env = dict(base_env)
    for key in OAUTH_CLEARED_VARS:
        env.pop(key, None)
    if api_key_ref:
        # Pass only the reference, never the real value.
        env["MANAGED_API_KEY_REF"] = api_key_ref
    if account_label:
        env["MANAGED_ACCOUNT_LABEL"] = account_label
    return env

__all__ = [
    "OAUTH_CLEARED_VARS",
    "shape_environment_for_oauth",
    "shape_environment_for_api_key",
    "_should_filter_base_env_var",
]
