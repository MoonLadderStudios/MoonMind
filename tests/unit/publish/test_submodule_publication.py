"""Publication must retain a candidate until its dependency commits are fetchable."""

from __future__ import annotations

import asyncio
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from moonmind.publish.service import PublishService
from moonmind.publish.submodules import SubmodulePublicationError
from moonmind.workflows.temporal.activity_runtime import (
    TemporalAgentRuntimeActivities,
    classify_git_push_failure,
)


def _git(
    root: Path, *args: str, check: bool = True
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        check=check,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_AUTHOR_NAME": "Fixture",
            "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
            "GIT_COMMITTER_NAME": "Fixture",
            "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
        },
    )


def _commit(root: Path, name: str, content: str) -> str:
    (root / name).write_text(content)
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "Save fixture work")
    return _git(root, "rev-parse", "HEAD").stdout.strip()


@pytest.fixture
def dependency_candidate(tmp_path: Path):
    dependency_remote = tmp_path / "dependency.git"
    parent_remote = tmp_path / "parent.git"
    for remote in (dependency_remote, parent_remote):
        _git(tmp_path, "init", "--bare", "--initial-branch=main", str(remote))
    dependency_writer = tmp_path / "dependency-writer"
    _git(tmp_path, "clone", str(dependency_remote), str(dependency_writer))
    dependency_base = _commit(dependency_writer, "SKILL.md", "Published Skill source\n")
    _git(dependency_writer, "push", "origin", "main")
    parent = tmp_path / "parent"
    _git(tmp_path, "clone", str(parent_remote), str(parent))
    _git(
        parent,
        "-c",
        "protocol.file.allow=always",
        "submodule",
        "add",
        str(dependency_remote),
        "dependency",
    )
    base = _commit(parent, "README.md", "Fixture consumer\n")
    _git(parent, "push", "origin", "main")
    _git(parent, "checkout", "-b", "feature/rescue")
    dependency_head = _commit(
        parent / "dependency", "SKILL.md", "Requested Skill fix\n"
    )
    _git(parent, "add", "dependency")
    _git(parent, "commit", "-qm", "Consume requested Skill fix")
    head = _git(parent, "rev-parse", "HEAD").stdout.strip()
    return SimpleNamespace(
        parent=parent,
        remote=parent_remote,
        base=base,
        head=head,
        dependency_remote=dependency_remote,
        dependency_base=dependency_base,
        dependency_head=dependency_head,
    )


async def _command(command, *, cwd, check=True, env=None, **_kwargs):
    proc = await asyncio.create_subprocess_exec(
        *command,
        cwd=cwd,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if check and proc.returncode:
        raise subprocess.CalledProcessError(proc.returncode, command, stdout, stderr)
    return SimpleNamespace(
        stdout=stdout.decode(), stderr=stderr.decode(), returncode=proc.returncode
    )


async def _publish(candidate, route: str):
    if route == "managed":
        activities = TemporalAgentRuntimeActivities(
            run_store=SimpleNamespace(
                load=lambda _run_id: SimpleNamespace(
                    workspace_path=str(candidate.parent)
                )
            )
        )
        return await activities._push_workspace_branch(
            "fixture-run",
            target_branch="main",
            head_branch="feature/rescue",
            github_token="fixture-token",
        )
    service = PublishService()
    if route == "immediate":
        return await service.publish(
            job_id=uuid4(),
            instruction="Update repository Skill source",
            publish_mode="branch",
            publish_base_branch="main",
            runtime_mode="codex",
            repo_dir=candidate.parent,
            run_command=_command,
            publication_branch_name="feature/rescue",
            publish_existing_commits=True,
            verify_remote=True,
            github_token="fixture-token",
        )
    return await service.push_candidate(
        repo_dir=candidate.parent,
        repository="fixture/consumer",
        head_branch="feature/rescue",
        candidate_sha=candidate.head,
        base_sha=candidate.base,
        expected_remote_sha=None,
        remote_url=str(candidate.remote),
        github_token="fixture-token",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["managed", "immediate", "saved"])
@pytest.mark.parametrize("fork_only", [False, True])
@pytest.mark.parametrize("publication_owner", ["workspace", "separate"])
async def test_publication_preserves_unpublished_dependency_then_resumes(
    dependency_candidate,
    route: str,
    fork_only: bool,
    publication_owner: str,
):
    candidate = dependency_candidate
    if fork_only:
        fork = candidate.parent.parent / "dependency-fork.git"
        _git(candidate.parent.parent, "init", "--bare", str(fork))
        _git(candidate.parent / "dependency", "remote", "add", "fork", str(fork))
        _git(candidate.parent / "dependency", "push", "fork", "HEAD:main")
    try:
        first = await _publish(candidate, route)
    except (subprocess.CalledProcessError, SubmodulePublicationError) as exc:
        error = (
            exc.stderr.decode()
            if isinstance(exc, subprocess.CalledProcessError)
            else str(exc)
        )
    else:
        if route == "managed":
            assert (
                first["push_status"] == "failed"
            ), "parent was pushed with an unpublished dependency"
            error = first["push_error"]
        else:
            assert (
                first.status == "unavailable"
            ), "parent was pushed with an unpublished dependency"
            error = first.summary
    assert "dependency" in error
    if route == "managed":
        failure = classify_git_push_failure(stderr=error, branch="feature/rescue")
        assert failure["diagnostic_kind"] == "publish_dependency_unavailable"
        assert failure["retryable"] is False
    if route == "saved":
        assert first.reason_code == "dependency_commit_unavailable"
        assert first.retryable is False
    assert not _git(
        candidate.remote, "for-each-ref", "refs/heads/feature/rescue"
    ).stdout
    assert _git(candidate.parent, "rev-parse", "HEAD").stdout.strip() == candidate.head
    assert (
        _git(candidate.parent / "dependency", "rev-parse", "HEAD").stdout.strip()
        == candidate.dependency_head
    )
    assert (
        _git(candidate.dependency_remote, "rev-parse", "main").stdout.strip()
        == candidate.dependency_base
    )

    # The dependency owner publishes; the consumer does not acquire that authority.
    owner = candidate.parent / "dependency"
    if publication_owner == "separate":
        owner = candidate.parent.parent / "dependency-owner"
        _git(
            candidate.parent.parent,
            "clone",
            str(candidate.parent / "dependency"),
            str(owner),
        )
        _git(owner, "remote", "set-url", "origin", str(candidate.dependency_remote))
    _git(owner, "push", "origin", "HEAD:main")
    second = await _publish(candidate, route)
    if route == "managed":
        assert second["push_status"] == "pushed", second
        assert second["remote_verified"]
    elif route == "immediate":
        assert second.status == "published" and second.remote_verified
    else:
        assert second.status == "pushed" and second.remote_verified
    assert (
        _git(candidate.remote, "rev-parse", "refs/heads/feature/rescue").stdout.strip()
        == candidate.head
    )
    fresh = candidate.parent.parent / "fresh-consumer"
    _git(
        candidate.parent.parent,
        "clone",
        "--branch",
        "feature/rescue",
        str(candidate.remote),
        str(fresh),
    )
    _git(
        fresh,
        "-c",
        "protocol.file.allow=always",
        "submodule",
        "update",
        "--init",
        "--checkout",
    )
    assert (fresh / "dependency" / "SKILL.md").read_text() == "Requested Skill fix\n"


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["managed", "immediate", "saved"])
@pytest.mark.parametrize("local_state", ["uninitialized", "overridden-origin"])
async def test_publication_checks_committed_relative_dependency_url(
    dependency_candidate, route: str, local_state: str
):
    candidate = dependency_candidate
    # Git resolves this against the parent repository's origin, as a fresh clone does.
    _git(
        candidate.parent,
        "config",
        "-f",
        ".gitmodules",
        "submodule.dependency.url",
        "../dependency.git",
    )
    _git(candidate.parent, "add", ".gitmodules")
    _git(candidate.parent, "commit", "--amend", "--no-edit")
    candidate.head = _git(candidate.parent, "rev-parse", "HEAD").stdout.strip()
    fork = candidate.parent.parent / "dependency-fork.git"
    _git(candidate.parent.parent, "init", "--bare", str(fork))
    _git(candidate.parent / "dependency", "remote", "add", "fork", str(fork))
    _git(candidate.parent / "dependency", "push", "fork", "HEAD:main")
    if local_state == "uninitialized":
        _git(candidate.parent, "submodule", "deinit", "--force", "dependency")
    else:
        _git(candidate.parent / "dependency", "remote", "set-url", "origin", str(fork))

    try:
        blocked = await _publish(candidate, route)
    except (subprocess.CalledProcessError, SubmodulePublicationError):
        pass
    else:
        status = blocked["push_status"] if route == "managed" else blocked.status
        assert status in {
            "failed",
            "unavailable",
        }, "declared remote cannot supply the dependency"
    assert not _git(
        candidate.remote,
        "show-ref",
        "--verify",
        "refs/heads/feature/rescue",
        check=False,
    ).stdout
    assert _git(candidate.parent, "rev-parse", "HEAD").stdout.strip() == candidate.head

    # Publish through the dependency owner without changing the consumer candidate.
    _git(
        candidate.parent,
        "--git-dir=.git/modules/dependency",
        "push",
        str(candidate.dependency_remote),
        "HEAD:main",
    )
    resumed = await _publish(candidate, route)
    status = resumed["push_status"] if route == "managed" else resumed.status
    assert status in {"pushed", "published"}, resumed
    fresh = candidate.parent.parent / "fresh-consumer"
    _git(
        candidate.parent.parent,
        "clone",
        "--branch",
        "feature/rescue",
        str(candidate.remote),
        str(fresh),
    )
    _git(
        fresh,
        "-c",
        "protocol.file.allow=always",
        "submodule",
        "update",
        "--init",
        "--checkout",
    )
    assert (fresh / "dependency" / "SKILL.md").read_text() == "Requested Skill fix\n"
