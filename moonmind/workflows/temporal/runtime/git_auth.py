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
    host: str = "github.com",
) -> dict[str, str]:
    """Return a Git command environment that authenticates GitHub HTTPS.

    Plain ``git`` does not consume ``gh`` auth state or ``GITHUB_TOKEN`` by
    itself. This environment installs an in-memory credential helper through
    Git's per-process config variables so host-side clone/fetch operations use
    the same resolved GitHub token without writing the token to disk or argv.
    ``host`` scopes the helper to one trusted Git host (default
    ``github.com``); bound App flows pass the deployment endpoint host.
    """

    env = {str(key): str(value) for key, value in (base_env or {}).items()}
    normalized_token = str(token or "").strip()
    if not normalized_token:
        return env

    normalized_host = str(host or "").strip().lower() or "github.com"
    env["GITHUB_TOKEN"] = normalized_token
    env["GIT_TERMINAL_PROMPT"] = str(
        env.get("GIT_TERMINAL_PROMPT") or terminal_prompt
    )
    env["GIT_CONFIG_COUNT"] = "2"
    env["GIT_CONFIG_KEY_0"] = f"credential.https://{normalized_host}.helper"
    env["GIT_CONFIG_VALUE_0"] = ""
    env["GIT_CONFIG_KEY_1"] = f"credential.https://{normalized_host}.helper"
    env["GIT_CONFIG_VALUE_1"] = _GITHUB_TOKEN_GIT_CREDENTIAL_HELPER
    return env


# Transport routing and trust a deployment legitimately supplies to Git. No
# credential, credential helper, header, askpass, or config override survives.
_ISOLATED_GIT_ENV_PASSTHROUGH = (
    "PATH",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TZ",
    "TMPDIR",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "NO_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "no_proxy",
    "all_proxy",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "GIT_SSL_CAINFO",
    "GIT_SSL_CAPATH",
    "CURL_CA_BUNDLE",
)
_ISOLATED_GIT_CREDENTIAL_ENV = "MOONMIND_GIT_CREDENTIAL"
_ISOLATED_GIT_CREDENTIAL_HELPER = (
    '!f() { test "$1" = get || exit 0; '
    f'echo username=x-access-token; echo password="${_ISOLATED_GIT_CREDENTIAL_ENV}"; '
    "}; f"
)


def build_isolated_git_environment(
    token: str | None,
    *,
    base_env: Mapping[str, str] | None = None,
    host: str = "github.com",
) -> dict[str, str]:
    """Return a Git process environment that admits only ``token``.

    Unlike :func:`build_github_token_git_environment`, which layers a helper
    over an inherited environment, this starts from transport routing only:
    ambient ``GITHUB_TOKEN``/``GH_TOKEN``, ``GIT_ASKPASS``, injected config
    (``GIT_CONFIG_PARAMETERS``/``GIT_CONFIG_COUNT``), global/system config
    helpers and ``http.extraHeader``, and ``~/.netrc`` cannot reach Git. Without
    a token the process is anonymous. The caller's own ``HOME`` is untouched;
    only this Git process reads an empty one. Known Git LFS filters are added
    explicitly so isolation still materializes LFS files without trusting
    arbitrary ambient filter commands.
    """

    source = base_env or {}
    env = {
        key: str(source[key]) for key in _ISOLATED_GIT_ENV_PASSTHROUGH if key in source
    }
    env.update(
        {
            "HOME": "/nonexistent",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_TERMINAL_PROMPT": "0",
        }
    )
    entries = [
        ("credential.helper", ""),
        ("http.extraHeader", ""),
        # System/global isolation also removes the normal Git LFS install.
        # Restore only the fixed trusted driver, never inherited filter config.
        ("filter.lfs.clean", "git-lfs clean -- %f"),
        ("filter.lfs.smudge", "git-lfs smudge -- %f"),
        ("filter.lfs.process", "git-lfs filter-process"),
        ("filter.lfs.required", "true"),
    ]
    normalized_token = str(token or "").strip()
    if normalized_token:
        normalized_host = str(host or "").strip().lower() or "github.com"
        env[_ISOLATED_GIT_CREDENTIAL_ENV] = normalized_token
        entries.append(
            (
                f"credential.https://{normalized_host}.helper",
                _ISOLATED_GIT_CREDENTIAL_HELPER,
            )
        )
    env["GIT_CONFIG_COUNT"] = str(len(entries))
    for index, (key, value) in enumerate(entries):
        env[f"GIT_CONFIG_KEY_{index}"] = key
        env[f"GIT_CONFIG_VALUE_{index}"] = value
    return env


__all__ = [
    "build_github_token_git_environment",
    "build_isolated_git_environment",
]
