"""The commit identity MoonMind provisions for agent-owned publication."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from moonmind.config.settings import settings
from moonmind.omnigent.git_identity import (
    DEFAULT_GIT_USER_EMAIL,
    DEFAULT_GIT_USER_NAME,
    resolve_git_identity,
)


def test_resolve_git_identity_prefers_configured_workflow_identity(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(settings.workflow, "git_user_name", "Deployment Operator")
    monkeypatch.setattr(settings.workflow, "git_user_email", "operator@example.test")

    assert resolve_git_identity() == (
        "Deployment Operator",
        "operator@example.test",
    )


@pytest.mark.parametrize("blank", [None, "", "   "])
def test_resolve_git_identity_falls_back_to_documented_default(
    monkeypatch: pytest.MonkeyPatch, blank: str | None
):
    """An undeclared identity still yields a usable default, not a refusal."""

    monkeypatch.setattr(settings.workflow, "git_user_name", blank)
    monkeypatch.setattr(settings.workflow, "git_user_email", blank)

    assert resolve_git_identity() == (DEFAULT_GIT_USER_NAME, DEFAULT_GIT_USER_EMAIL)


def _git(repo: Path, *args: str, env: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        env=env,
    )


def test_repo_local_resolved_identity_commits_without_global_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """The resolved identity is sufficient for a commit on a bare host.

    The generic Omnigent host image ships a credential helper and no
    ``[user]`` section, so a repository-local identity is the only thing
    standing between a commit-capable skill and a blocked run.
    """

    monkeypatch.setattr(settings.workflow, "git_user_name", None)
    monkeypatch.setattr(settings.workflow, "git_user_email", None)
    name, email = resolve_git_identity()

    repo = tmp_path / "repo"
    repo.mkdir()
    # No global or system identity is reachable from this environment.
    env = {
        "PATH": "/usr/bin:/bin:/usr/local/bin",
        "HOME": str(tmp_path / "empty-home"),
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_SYSTEM": "/dev/null",
        "GIT_TERMINAL_PROMPT": "0",
    }
    (tmp_path / "empty-home").mkdir()
    assert _git(repo, "init", "-q", env=env).returncode == 0

    (repo / "file.txt").write_text("content\n", encoding="utf-8")
    assert _git(repo, "add", "file.txt", env=env).returncode == 0

    uncommittable = _git(repo, "commit", "-m", "no identity", env=env)
    assert uncommittable.returncode != 0, "git must refuse an unidentified commit"

    assert _git(repo, "config", "--local", "user.name", name, env=env).returncode == 0
    assert _git(repo, "config", "--local", "user.email", email, env=env).returncode == 0

    committed = _git(repo, "commit", "-m", "identified", env=env)
    assert committed.returncode == 0, committed.stderr

    author = _git(repo, "log", "-1", "--format=%an <%ae>", env=env)
    assert author.stdout.strip() == f"{name} <{email}>"
