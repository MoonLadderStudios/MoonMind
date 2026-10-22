"""Shared host-side Git authentication helpers for managed runtimes."""

from __future__ import annotations

import subprocess
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import unquote, urlsplit

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
    """Layer an in-memory GitHub HTTPS credential helper over ``base_env``.

    The helper reaches Git through per-process config variables, never disk or
    argv, and ``host`` scopes it to one trusted Git host. It removes nothing
    from ``base_env``, so it suits only the saved-work publisher, whose base is
    already contained. Host-side clone, fetch, push and ``gh`` consumers use
    :func:`build_remote_git_environment` so ambient credentials cannot win.
    """

    env = {str(key): str(value) for key, value in (base_env or {}).items()}
    normalized_token = str(token or "").strip()
    if not normalized_token:
        return env

    normalized_host = str(host or "").strip().lower() or "github.com"
    env["GITHUB_TOKEN"] = normalized_token
    env["GIT_TERMINAL_PROMPT"] = str(env.get("GIT_TERMINAL_PROMPT") or terminal_prompt)
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
    "GIT_AUTHOR_NAME",
    "GIT_AUTHOR_EMAIL",
    "GIT_COMMITTER_NAME",
    "GIT_COMMITTER_EMAIL",
)
# Some runners overlay the returned environment on the worker's own. These
# values neutralize the inherited Git/gh credential, header, prompt, host and
# login-cache input that the overlay would otherwise leave in place; Git and gh
# treat an empty value as unset. ``GIT_CONFIG_PARAMETERS`` must be emptied
# because Git reads it after ``GIT_CONFIG_COUNT``, so it would win the reset.
_ISOLATED_GIT_ENV_NEUTRALIZED = {
    "GIT_CONFIG_PARAMETERS": "",
    "GIT_ASKPASS": "",
    "SSH_ASKPASS": "",
    "GITHUB_TOKEN": "",
    "GH_TOKEN": "",
    "GITHUB_ENTERPRISE_TOKEN": "",
    "GH_ENTERPRISE_TOKEN": "",
    "GH_HOST": "",
    "GH_REPO": "",
    "GH_CONFIG_DIR": "/nonexistent",
    "XDG_CONFIG_HOME": "/nonexistent",
}
_ISOLATED_GIT_CREDENTIAL_ENV = "MOONMIND_GIT_CREDENTIAL"
_ISOLATED_GIT_CREDENTIAL_HELPER = (
    '!f() { test "$1" = get || exit 0; '
    "protocol=; host=; path=; while IFS= read -r field; do "
    'case "$field" in protocol=*) protocol=${field#*=};; '
    "host=*) host=${field#*=};; path=*) path=${field#*=};; esac; done; "
    'host=$(printf %s "$host" | tr "[:upper:]" "[:lower:]"); path=${path%/}; '
    'test "$protocol" = https && test "$host" = "$MOONMIND_GIT_HOST" || exit 0; '
    'test "$path" = "$MOONMIND_GIT_LFS_PATH" || '
    'test "${path%.git}" = "$MOONMIND_GIT_REPOSITORY_PATH" || exit 0; '
    f'printf "username=x-access-token\\npassword=%s\\n" "${_ISOLATED_GIT_CREDENTIAL_ENV}"; '
    "}; f"
)


def build_isolated_git_environment(
    token: str | None,
    *,
    base_env: Mapping[str, str] | None = None,
    repository_url: str | None = None,
) -> dict[str, str]:
    """Return a Git process environment that admits only ``token``.

    This starts from transport routing and commit identity only: ambient
    ``GITHUB_TOKEN``/``GH_TOKEN``, ``GIT_ASKPASS``, injected config
    (``GIT_CONFIG_PARAMETERS``/``GIT_CONFIG_COUNT``), global/system config
    helpers and ``http.extraHeader``, ``~/.netrc`` and gh login state cannot
    reach Git or gh, even when a runner overlays the result on the worker
    environment. Without a token the process is anonymous. The caller's own
    ``HOME`` is untouched; only this Git process reads an empty one. Known Git
    LFS filters are added explicitly so isolation still materializes LFS files
    without trusting arbitrary ambient filter commands. Credentials are available only for the
    admitted HTTPS repository and its standard LFS endpoint. HTTP redirects are
    disabled because Git can forward already-acquired credentials without
    consulting the helper again. This is transport scoping, not confinement
    against arbitrary code that can read the process environment.
    """

    source = base_env or {}
    env = {
        key: str(source[key]) for key in _ISOLATED_GIT_ENV_PASSTHROUGH if key in source
    }
    env.update(_ISOLATED_GIT_ENV_NEUTRALIZED)
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
        ("credential.useHttpPath", "true"),
        ("http.followRedirects", "false"),
        # System/global isolation also removes the normal Git LFS install.
        # Restore only the fixed trusted driver, never inherited filter config.
        ("filter.lfs.clean", "git-lfs clean -- %f"),
        ("filter.lfs.smudge", "git-lfs smudge -- %f"),
        ("filter.lfs.process", "git-lfs filter-process"),
        ("filter.lfs.required", "true"),
    ]
    normalized_token = str(token or "").strip()
    if normalized_token:
        target = urlsplit(str(repository_url or ""))
        path = unquote(target.path).strip("/")
        if (
            target.scheme != "https"
            or not target.netloc
            or target.username is not None
            or target.password is not None
            or target.query
            or target.fragment
            or not path
            or any(char in str(repository_url) + path for char in "\r\n\x00")
        ):
            raise ValueError(
                "an admitted HTTPS repository is required for Git credentials"
            )
        normalized_host = target.netloc.lower()
        env["MOONMIND_GIT_HOST"] = normalized_host
        # ``owner/repo`` and ``owner/repo.git`` name the same repository.
        repository_path = path.removesuffix(".git")
        env["MOONMIND_GIT_REPOSITORY_PATH"] = repository_path
        env["MOONMIND_GIT_LFS_PATH"] = repository_path + ".git/info/lfs"
        env[_ISOLATED_GIT_CREDENTIAL_ENV] = normalized_token
        # Reset host-specific helpers a workspace's own config may declare.
        entries.append((f"credential.https://{normalized_host}.helper", ""))
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


def build_remote_git_environment(
    token: str | None,
    *,
    base_env: Mapping[str, str] | None,
    remote_url: str | None,
) -> dict[str, str]:
    """Return an isolated Git environment for commands against ``remote_url``.

    ``token`` is the operation's admitted credential and answers only for
    ``remote_url`` when it is an HTTPS repository. Any other remote (a local
    path, SSH, plain HTTP) gets no credential: the command stays isolated and
    anonymous rather than falling back to ambient authority.
    """

    url = str(remote_url or "").strip()
    try:
        return build_isolated_git_environment(
            token, base_env=base_env, repository_url=url
        )
    except ValueError:
        return build_isolated_git_environment(None, base_env=base_env)


def build_workspace_git_environment(
    token: str | None,
    *,
    base_env: Mapping[str, str] | None,
    workspace: str | Path,
) -> dict[str, str]:
    """Return an isolated Git and gh environment for an existing checkout.

    Git receives ``token`` only for the checkout's configured ``origin`` (read
    without ambient config); gh receives it as ``GH_TOKEN``. No token means no
    credential for either, never the worker's own GitHub login.
    """

    normalized_token = str(token or "").strip()
    origin = ""
    if normalized_token:
        resolved = str(Path(workspace).resolve())
        result = subprocess.run(
            [
                "git",
                "-c",
                f"safe.directory={resolved}",
                "-C",
                resolved,
                "config",
                "--get",
                "remote.origin.url",
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
            env=build_isolated_git_environment(None, base_env=base_env),
        )
        origin = result.stdout.strip() if result.returncode == 0 else ""
    env = build_remote_git_environment(
        normalized_token, base_env=base_env, remote_url=origin
    )
    if normalized_token:
        env["GH_TOKEN"] = normalized_token
    return env


__all__ = [
    "build_github_token_git_environment",
    "build_isolated_git_environment",
    "build_remote_git_environment",
    "build_workspace_git_environment",
]
