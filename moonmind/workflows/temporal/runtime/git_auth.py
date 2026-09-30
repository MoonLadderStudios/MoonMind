"""Shared host-side Git authentication helpers for managed runtimes."""

from __future__ import annotations

from typing import Mapping

from .github_auth_broker import AMBIENT_GH_CREDENTIAL_ENV_NAMES

_GITHUB_TOKEN_GIT_CREDENTIAL_HELPER = (
    '!f() { test "$1" = get || exit 0; '
    'echo username=x-access-token; echo password="$GITHUB_TOKEN"; }; f'
)
# Inherited Git selectors outside the per-process config list below: a parent
# ``git -c`` (GIT_CONFIG_PARAMETERS) applies after it, so its helpers would be
# asked for, and handed, the admitted credential; askpass programs answer when
# a helper does not (https://git-scm.com/docs/gitcredentials).
AMBIENT_GIT_CREDENTIAL_ENV_NAMES: tuple[str, ...] = (
    "GIT_CONFIG_PARAMETERS",
    "GIT_ASKPASS",
    "SSH_ASKPASS",
)


def build_github_token_git_environment(
    token: str | None,
    *,
    base_env: Mapping[str, str] | None = None,
    terminal_prompt: str = "0",
    host: str = "github.com",
) -> dict[str, str]:
    """Return a Git command environment that authenticates GitHub HTTPS.

    Plain ``git`` does not consume ``gh`` auth state or ``GITHUB_TOKEN`` by
    itself. This environment installs an in-memory credential helper through
    Git's per-process config variables so host-side clone/fetch operations use
    the same resolved GitHub token without writing the token to disk or argv.
    ``host`` scopes the helper to one trusted Git host (default
    ``github.com``); bound App flows pass the deployment endpoint host.

    The token is the admitted authority for both clients: empty entries reset
    the credential helpers and extra headers inherited configuration layers
    supply, askpass is disabled, and GitHub CLI selectors that outrank
    ``GITHUB_TOKEN`` or retarget another host are dropped
    (https://cli.github.com/manual/gh_help_environment).
    """

    env = {str(key): str(value) for key, value in (base_env or {}).items()}
    normalized_token = str(token or "").strip()
    if not normalized_token:
        return env

    normalized_host = str(host or "").strip().lower() or "github.com"
    for name in (*AMBIENT_GIT_CREDENTIAL_ENV_NAMES, *AMBIENT_GH_CREDENTIAL_ENV_NAMES):
        if name == "GH_HOST" and env.get(name, "").strip().lower() == normalized_host:
            continue
        env.pop(name, None)
    env["GITHUB_TOKEN"] = normalized_token
    env["GIT_TERMINAL_PROMPT"] = str(
        env.get("GIT_TERMINAL_PROMPT") or terminal_prompt
    )
    git_config = (
        (f"credential.https://{normalized_host}.helper", ""),
        (
            f"credential.https://{normalized_host}.helper",
            _GITHUB_TOKEN_GIT_CREDENTIAL_HELPER,
        ),
        (f"http.https://{normalized_host}/.extraHeader", ""),
        ("core.askPass", ""),
    )
    env["GIT_CONFIG_COUNT"] = str(len(git_config))
    for index, (key, value) in enumerate(git_config):
        env[f"GIT_CONFIG_KEY_{index}"] = key
        env[f"GIT_CONFIG_VALUE_{index}"] = value
    return env


__all__ = [
    "AMBIENT_GIT_CREDENTIAL_ENV_NAMES",
    "build_github_token_git_environment",
]
