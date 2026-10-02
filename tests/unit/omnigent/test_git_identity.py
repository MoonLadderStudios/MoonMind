"""The commit identity MoonMind provisions for agent-owned publication."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from moonmind.config.settings import settings
from moonmind.omnigent.git_identity import (
    DEFAULT_GIT_USER_EMAIL,
    DEFAULT_GIT_USER_NAME,
    ensure_workspace_git_identity,
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


def _identity_owner() -> tuple[int, int]:
    import os

    return os.getuid(), os.getgid()


def test_ensure_workspace_git_identity_adds_user_section_preserving_rest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(settings.workflow, "git_user_name", "Deployment Operator")
    monkeypatch.setattr(settings.workflow, "git_user_email", "operator@example.test")

    workspace = tmp_path / "repo"
    (workspace / ".git").mkdir(parents=True)
    (workspace / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (workspace / ".git" / "config").write_text(
        '[core]\n\trepositoryformatversion = 0\n', encoding="utf-8"
    )
    (workspace / "KEEP").write_text("x", encoding="utf-8")

    uid, gid = _identity_owner()
    assert ensure_workspace_git_identity(workspace, runtime_uid=uid, runtime_gid=gid)

    assert (workspace / "KEEP").read_text() == "x"
    config = (workspace / ".git" / "config").read_text(encoding="utf-8")
    assert "repositoryformatversion = 0" in config
    assert '"Deployment Operator"' in config
    assert '"operator@example.test"' in config


def test_ensure_workspace_git_identity_replaces_stale_imported_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """An imported config cannot leave stale attribution behind."""

    monkeypatch.setattr(settings.workflow, "git_user_name", "Deployment Operator")
    monkeypatch.setattr(settings.workflow, "git_user_email", "operator@example.test")

    workspace = tmp_path / "repo"
    (workspace / ".git").mkdir(parents=True)
    (workspace / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (workspace / ".git" / "config").write_text(
        '[core]\n\trepositoryformatversion = 0\n'
        '[user]\n\tname = Stale Import\n\temail = stale@example.test\n'
        '[remote "origin"]\n\turl = https://github.com/org/repo.git\n',
        encoding="utf-8",
    )

    uid, gid = _identity_owner()
    assert ensure_workspace_git_identity(workspace, runtime_uid=uid, runtime_gid=gid)

    config = (workspace / ".git" / "config").read_text(encoding="utf-8")
    assert "Stale Import" not in config
    assert "stale@example.test" not in config
    assert "https://github.com/org/repo.git" in config
    assert '"Deployment Operator"' in config


def test_ensure_workspace_git_identity_skips_non_git_directories(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(settings.workflow, "git_user_name", "Deployment Operator")
    monkeypatch.setattr(settings.workflow, "git_user_email", "operator@example.test")

    workspace = tmp_path / "plain"
    workspace.mkdir()

    uid, gid = _identity_owner()
    assert (
        ensure_workspace_git_identity(workspace, runtime_uid=uid, runtime_gid=gid)
        is False
    )


def test_ensure_workspace_git_identity_skips_git_dir_without_head(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """A bare ``.git`` path (for example from an input projection) is not a repo."""

    monkeypatch.setattr(settings.workflow, "git_user_name", "Deployment Operator")
    monkeypatch.setattr(settings.workflow, "git_user_email", "operator@example.test")

    workspace = tmp_path / "repo"
    (workspace / ".git" / "info").mkdir(parents=True)

    uid, gid = _identity_owner()
    assert (
        ensure_workspace_git_identity(workspace, runtime_uid=uid, runtime_gid=gid)
        is False
    )
    assert not (workspace / ".git" / "config").exists()


@pytest.mark.parametrize("kind", ["config", "git_parent", "workspace_parent"])
def test_identity_never_follows_workspace_symlinks(tmp_path, kind):
    outside = tmp_path / "outside"
    (outside / ".git").mkdir(parents=True)
    (outside / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    victim = outside / ".git" / "config"
    victim.write_text("[core]\n\tbare = false\n")
    workspace = tmp_path / "workspace"
    if kind == "workspace_parent":
        workspace.symlink_to(outside, target_is_directory=True)
    else:
        workspace.mkdir()
        if kind == "git_parent":
            (workspace / ".git").symlink_to(outside / ".git", target_is_directory=True)
        else:
            (workspace / ".git").mkdir()
            (workspace / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
            (workspace / ".git" / "config").symlink_to(victim)
    before = victim.read_bytes()
    assert not ensure_workspace_git_identity(
        workspace, runtime_uid=None, runtime_gid=None
    )
    assert victim.read_bytes() == before


def test_identity_ignores_malformed_git_pointer(tmp_path):
    (tmp_path / ".git").write_bytes(b"gitdir: \xff")
    assert not ensure_workspace_git_identity(
        tmp_path, runtime_uid=None, runtime_gid=None
    )


def test_identity_git_pointer_does_not_canonicalize_away_root_swap(
    tmp_path, monkeypatch
):
    from moonmind.omnigent import git_identity

    workspace = tmp_path / "workspace"
    internal = workspace / "git-internal"
    internal.mkdir(parents=True)
    (workspace / ".git").write_text("gitdir: git-internal\n")
    (internal / "HEAD").write_text("ref: refs/heads/main\n")
    outside = tmp_path / "outside"
    (outside / "git-internal").mkdir(parents=True)
    victim = outside / "git-internal" / "config"
    victim.write_text("[core]\n\tbare = false\n")
    (victim.parent / "HEAD").write_text("ref: refs/heads/main\n")
    real_read = git_identity.read_regular_file

    def swap_after_pointer(path, *, limit):
        data = real_read(path, limit=limit)
        if path == workspace / ".git":
            workspace.rename(tmp_path / "retained")
            workspace.symlink_to(outside, target_is_directory=True)
        return data

    monkeypatch.setattr(git_identity, "read_regular_file", swap_after_pointer)
    before = victim.read_bytes()
    assert not ensure_workspace_git_identity(
        workspace, runtime_uid=None, runtime_gid=None
    )
    assert victim.read_bytes() == before


@pytest.mark.parametrize(
    "pointer", ["internal/git", "internal/../internal/git", "absolute"]
)
def test_identity_preserves_contained_git_pointer_support(tmp_path, pointer):
    git_dir = tmp_path / "internal" / "git"
    git_dir.mkdir(parents=True)
    (git_dir / "HEAD").write_text("ref: refs/heads/main\n")
    target = str(git_dir) if pointer == "absolute" else pointer
    (tmp_path / ".git").write_text(f"gitdir: {target}\n")
    assert ensure_workspace_git_identity(tmp_path, runtime_uid=None, runtime_gid=None)
    assert "[user]" in (git_dir / "config").read_text()
