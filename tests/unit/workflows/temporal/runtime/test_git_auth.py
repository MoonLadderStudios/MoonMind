"""Admitted GitHub authority at the shared worker-side Git/gh boundary.

MoonLadderStudios/MoonMind#4011: ``build_github_token_git_environment`` is the
one environment builder for worker-side clone, fetch, checkpoint restore, and
publication commands. The admitted credential B must win over every ambient
selector A the real clients would otherwise prefer or hand B to
(https://cli.github.com/manual/gh_help_environment,
https://git-scm.com/docs/gitcredentials). Synthetic secrets only; the remote is
a loopback TLS fixture.
"""

from __future__ import annotations

import base64
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from moonmind.workflows.temporal.runtime.git_auth import (
    build_github_token_git_environment,
)

_REAL_GIT = shutil.which("git")
_REAL_GH = shutil.which("gh")
_ADMITTED_B = "ghs_admittedWorkerCredentialB00000000000000"
_AMBIENT_A = "ghp_ambientWorkerCredentialA000000000000000"


def _recording_helper(tmp_path: Path, name: str) -> Path:
    """An ambient helper that answers with A and records every action it sees."""

    log = tmp_path / f"{name}.log"
    helper = tmp_path / f"{name}.sh"
    helper.write_text(
        "#!/bin/sh\n"
        f'{{ echo "action=$1"; cat; }} >> "{log}"\n'
        'if [ "$1" = get ]; then\n'
        f"  echo username=ambient; echo password={_AMBIENT_A}\n"
        "fi\n",
        encoding="utf-8",
    )
    helper.chmod(0o700)
    return helper


def _ambient_worker_env(tmp_path: Path, *, host: str) -> dict[str, str]:
    """A worker environment in which every inherited selector chooses A."""

    home = tmp_path / "home"
    home.mkdir()
    parameters_helper = _recording_helper(tmp_path, "parameters-helper")
    global_helper = _recording_helper(tmp_path, "global-helper")
    askpass = tmp_path / "askpass.sh"
    askpass.write_text(f"#!/bin/sh\necho {_AMBIENT_A}\n", encoding="utf-8")
    askpass.chmod(0o700)
    ambient_header = base64.b64encode(f"ambient:{_AMBIENT_A}".encode()).decode()
    (home / ".gitconfig").write_text(
        f"[credential]\n\thelper = !{global_helper}\n"
        f"[http]\n\textraHeader = Authorization: Basic {ambient_header}\n"
        f'[http "https://{host}/"]\n'
        f"\textraHeader = Authorization: Basic {ambient_header}\n"
        f"[core]\n\taskPass = {askpass}\n",
        encoding="utf-8",
    )
    return {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(home),
        "GIT_CONFIG_NOSYSTEM": "1",
        # ``git -c`` from a parent process: applied after every config file.
        "GIT_CONFIG_PARAMETERS": f"'credential.helper'='!{parameters_helper}'",
        "GIT_ASKPASS": str(askpass),
        "SSH_ASKPASS": str(askpass),
        "GH_TOKEN": _AMBIENT_A,
        "GH_ENTERPRISE_TOKEN": _AMBIENT_A,
        "GITHUB_ENTERPRISE_TOKEN": _AMBIENT_A,
        "GH_HOST": "ambient.invalid",
        "GH_CONFIG_DIR": str(tmp_path / "gh-config"),
    }


def _git(args: list[str], env: dict[str, str], *, stdin: str = "", cwd: Path):
    return subprocess.run(
        [_REAL_GIT or "git", *args],
        input=stdin,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
        cwd=cwd,
    )


def test_builder_leaves_the_environment_unchanged_without_a_token() -> None:
    base = {"GH_TOKEN": _AMBIENT_A, "GIT_ASKPASS": "/bin/true"}

    assert build_github_token_git_environment("", base_env=base) == base


@pytest.mark.skipif(_REAL_GIT is None, reason="requires the real git client")
def test_admitted_credential_is_the_only_one_git_fills_or_shares(
    tmp_path: Path,
) -> None:
    env = build_github_token_git_environment(
        _ADMITTED_B, base_env=_ambient_worker_env(tmp_path, host="github.com")
    )
    request = "protocol=https\nhost=github.com\npath=owner/repo.git\n\n"

    filled = _git(["credential", "fill"], env, stdin=request, cwd=tmp_path)
    assert filled.returncode == 0, filled.stderr
    assert f"password={_ADMITTED_B}\n" in filled.stdout
    assert _AMBIENT_A not in filled.stdout

    approved = _git(
        ["credential", "approve"],
        env,
        stdin=(
            "protocol=https\nhost=github.com\npath=owner/repo.git\n"
            f"username=x-access-token\npassword={_ADMITTED_B}\n\n"
        ),
        cwd=tmp_path,
    )
    assert approved.returncode == 0, approved.stderr
    # Neither an inherited ``git -c`` helper nor a login-cache helper is asked
    # for A or handed B to store.
    for name in ("parameters-helper", "global-helper"):
        assert not (tmp_path / f"{name}.log").exists()
    assert "GIT_CONFIG_PARAMETERS" not in env
    assert "GIT_ASKPASS" not in env and "SSH_ASKPASS" not in env


@pytest.mark.skipif(_REAL_GH is None, reason="requires the real gh client")
def test_admitted_credential_outranks_ambient_github_cli_selectors(
    tmp_path: Path,
) -> None:
    env = build_github_token_git_environment(
        _ADMITTED_B, base_env=_ambient_worker_env(tmp_path, host="github.com")
    )

    token = subprocess.run(
        [_REAL_GH or "gh", "auth", "token"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        cwd=tmp_path,
    )

    assert token.returncode == 0, token.stderr
    assert token.stdout.strip() == _ADMITTED_B
    for name in ("GH_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN"):
        assert name not in env
    assert "GH_HOST" not in env


def test_admitted_host_selector_is_preserved() -> None:
    env = build_github_token_git_environment(
        _ADMITTED_B,
        base_env={"GH_HOST": "ghe.example.test"},
        host="ghe.example.test",
    )

    assert env["GH_HOST"] == "ghe.example.test"


@pytest.mark.skipif(_REAL_GIT is None, reason="requires the real git client")
def test_remote_receives_only_the_admitted_credential(tmp_path: Path) -> None:
    from tests.support.credential_recording_remote import (
        credential_recording_remote,
    )

    with credential_recording_remote(tmp_path) as remote:
        env = build_github_token_git_environment(
            _ADMITTED_B,
            base_env=_ambient_worker_env(tmp_path, host=remote.host),
            host=remote.host,
        )
        env["GIT_SSL_CAINFO"] = str(remote.ca_file)
        _git(["ls-remote", f"{remote.url}/owner/repo.git"], env, cwd=tmp_path)
        received = list(remote.authorization_headers)

    admitted = (
        "Basic " + base64.b64encode(f"x-access-token:{_ADMITTED_B}".encode()).decode()
    )
    assert admitted in received
    decoded = [
        base64.b64decode(value.split(" ", 1)[1]).decode()
        for value in received
        if value.startswith("Basic ")
    ]
    assert not any(_AMBIENT_A in value for value in decoded)
