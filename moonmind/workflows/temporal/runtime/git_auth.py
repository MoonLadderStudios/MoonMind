"""Shared host-side Git authentication helpers for managed runtimes."""

from __future__ import annotations

from typing import Mapping

_GITHUB_TOKEN_GIT_CREDENTIAL_HELPER = (
    '!f() { test "$1" = get || exit 0; '
    'echo username=x-access-token; echo password="$GITHUB_TOKEN"; }; f'
)


def build_github_token_git_environment(
    token: str | None,
    *,
    base_env: Mapping[str, str] | None = None,
    terminal_prompt: str = "0",
) -> dict[str, str]:
    """Return a Git command environment that authenticates GitHub HTTPS.

    Plain ``git`` does not consume ``gh`` auth state or ``GITHUB_TOKEN`` by
    itself. This environment installs an in-memory credential helper through
    Git's per-process config variables so host-side clone/fetch operations use
    the same resolved GitHub token without writing the token to disk or argv.
    """

    env = {str(key): str(value) for key, value in (base_env or {}).items()}
    normalized_token = str(token or "").strip()
    if not normalized_token:
        return env

    env["GITHUB_TOKEN"] = normalized_token
    env["GIT_TERMINAL_PROMPT"] = str(
        env.get("GIT_TERMINAL_PROMPT") or terminal_prompt
    )
    env["GIT_CONFIG_COUNT"] = "2"
    env["GIT_CONFIG_KEY_0"] = "credential.https://github.com.helper"
    env["GIT_CONFIG_VALUE_0"] = ""
    env["GIT_CONFIG_KEY_1"] = "credential.https://github.com.helper"
    env["GIT_CONFIG_VALUE_1"] = _GITHUB_TOKEN_GIT_CREDENTIAL_HELPER
    return env


__all__ = [
    "build_github_token_git_environment",
]
