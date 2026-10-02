"""Real regressions for narrowly scoped Git publication guards."""

import asyncio
import os
import subprocess
from types import SimpleNamespace

import pytest

from moonmind.agents.codex_worker.worker import CodexWorker
from moonmind.config.settings import settings
from moonmind.publish.service import PublishService
from moonmind.workflows.temporal.activity_runtime import TemporalAgentRuntimeActivities


def git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True
    ).stdout


@pytest.fixture
def repository(tmp_path):
    repo = tmp_path / "run" / "repo"
    repo.mkdir(parents=True)
    git(repo, "init", "--initial-branch=main")
    git(repo, "config", "user.name", "Test")
    git(repo, "config", "user.email", "test@example.invalid")
    (repo / "data.txt").write_text("before\n")
    (repo / ".gitattributes").write_text("*.txt diff=hide\n")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "initial")
    git(repo, "branch", "baseline")
    (repo / "data.txt").write_text("api_key = synthetic-fixture-secret\n")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "candidate")
    return repo


async def scan(owner, repo, base):
    if owner == "publisher":
        return await PublishService()._scan_git_push_before_publish(
            repo_dir=repo, branch_name="main", base_ref=base, env=dict(os.environ)
        )
    if owner == "legacy":
        return await CodexWorker.__new__(CodexWorker)._scan_publish_git_push(
            repo_dir=repo, branch_name="main", base_ref=base, env=dict(os.environ)
        )
    result = await TemporalAgentRuntimeActivities()._scan_workspace_push_range(
        workspace=str(repo),
        run_id="test",
        base_ref=base,
        branch="main",
        remote_sha=None,
        env=dict(os.environ),
    )
    if result:
        raise RuntimeError(result["push_error"])


@pytest.mark.asyncio
@pytest.mark.parametrize("owner", ["publisher", "legacy", "managed"])
async def test_publication_scan_cannot_hide_content_with_textconv(
    repository, tmp_path, monkeypatch, owner
):
    monkeypatch.setattr(settings.security, "high_security_mode", True)
    marker = tmp_path / "converted"
    script = tmp_path / "textconv"
    script.write_text(f"#!/bin/sh\necho converted > {marker}\necho innocuous\n")
    script.chmod(0o755)
    git(repository, "config", "diff.hide.textconv", str(script))
    with pytest.raises(RuntimeError, match="blocked"):
        await scan(owner, repository, "baseline")
    assert not marker.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("owner", ["publisher", "legacy", "managed"])
async def test_publication_fetch_treats_option_like_ref_as_data(
    repository, tmp_path, monkeypatch, owner
):
    monkeypatch.setattr(settings.security, "high_security_mode", True)
    origin = tmp_path / "origin.git"
    git(tmp_path, "init", "--bare", str(origin))
    git(repository, "remote", "add", "origin", str(origin))
    marker = tmp_path / "upload-pack-ran"
    script = tmp_path / "upload-pack"
    script.write_text(f"#!/bin/sh\necho invoked > {marker}\nexit 1\n")
    script.chmod(0o755)
    with pytest.raises(RuntimeError, match="blocked"):
        await scan(owner, repository, f"origin/--upload-pack={script}")
    assert not marker.exists()


@pytest.mark.parametrize("operation", ["normalize", "recover"])
@pytest.mark.parametrize("component", ["file", "parent"])
def test_alternates_never_follow_links(
    repository, tmp_path, caplog, operation, component
):
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "alternates"
    secret.write_text("private-fixture-content\n")
    info = repository / ".git" / "objects" / "info"
    if component == "file":
        (info / "alternates").symlink_to(secret)
    else:
        info.rmdir()
        info.symlink_to(outside, target_is_directory=True)
    orphan = repository.parent / "git-objects" / "ab"
    orphan.mkdir(parents=True)
    (orphan / ("c" * 38)).write_bytes(b"test-object")
    action = (
        TemporalAgentRuntimeActivities._normalize_workspace_git_alternates
        if operation == "normalize"
        else TemporalAgentRuntimeActivities._recover_orphan_workspace_object_stores
    )
    action(str(repository))
    assert secret.read_text() == "private-fixture-content\n"
    assert "private-fixture-content" not in caplog.text


def test_workspace_auth_does_not_write_or_execute_workload_support(
    tmp_path, monkeypatch
):
    workspace = tmp_path / "run" / "repo"
    workspace.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (workspace.parent / ".moonmind").symlink_to(outside, target_is_directory=True)
    monkeypatch.setenv("PATH", "/usr/bin")
    env = TemporalAgentRuntimeActivities._workspace_command_env(
        str(workspace), github_token="fixture-token"
    )
    assert not list(outside.iterdir())
    assert env["PATH"] == "/usr/bin"
    assert env["GIT_CONFIG_KEY_1"] == "credential.https://github.com.helper"
    assert env["GITHUB_TOKEN"] == "fixture-token"


@pytest.mark.asyncio
async def test_default_verification_does_not_receive_publish_credentials(
    tmp_path, monkeypatch
):
    worker = CodexWorker.__new__(CodexWorker)
    monkeypatch.setattr(worker, "_collect_verification_evidence", lambda **kw: ((), ()))
    monkeypatch.setattr(worker, "_append_stage_log", lambda *args: None)
    repo = tmp_path / "repo"
    (repo / "tools").mkdir(parents=True)
    script = repo / "tools/test_unit.sh"
    script.write_text(
        '#!/bin/sh\n[ -z "${GITHUB_TOKEN}${GH_TOKEN}${PRIVATE_WORKER_SECRET}" ]\n'
    )
    script.chmod(0o755)
    monkeypatch.setenv("GITHUB_TOKEN", "ambient-fixture-token")
    monkeypatch.setenv("PRIVATE_WORKER_SECRET", "fixture-secret")

    async def run(command, **kwargs):
        await asyncio.to_thread(
            subprocess.run, command, cwd=kwargs["cwd"], env=kwargs["env"], check=True
        )

    monkeypatch.setattr(worker, "_run_stage_command", run)
    prepared = SimpleNamespace(
        repo_dir=repo,
        publish_log_path=tmp_path / "log",
        repo_command_env={**os.environ, "GH_TOKEN": "repo-fixture-token"},
        publish_command_env={**os.environ, "GITHUB_TOKEN": "publish-fixture-token"},
    )
    await worker._run_default_publish_verification_if_needed(
        prepared=prepared, publish={}, status_output=" M file.py\n"
    )


def test_in_memory_auth_preserves_github_host_scope(tmp_path):
    env = TemporalAgentRuntimeActivities._workspace_command_env(
        str(tmp_path / "repo"), github_token="fixture-token"
    )
    for host in ("github.com", "unrelated.example"):
        result = subprocess.run(
            ["git", "credential", "fill"],
            cwd=tmp_path,
            input=f"protocol=https\nhost={host}\n\n",
            env=env,
            check=False,
            capture_output=True,
            text=True,
        )
        assert ("password=fixture-token" in result.stdout) is (host == "github.com")
