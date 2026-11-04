"""Shared workspace preparation preserves the authored local branch and retry edits."""

import os
import subprocess
from pathlib import Path

import pytest

from moonmind.omnigent.host_services import workspace as module
from tests.unit.omnigent.test_workspace_materializer import (
    _CONTROLLED_GID,
    _CONTROLLED_UID,
    _request,
    _SelectedRepositoryCredentials,
    _workspace_id,
)


def _repository(tmp_path):
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    (repo / "README.md").write_text("admitted input\n")
    subprocess.run(["git", "-C", str(repo), "add", "README.md"], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=Fixture", "-c", "user.email=fixture@example.test", "commit", "-qm", "Baseline"], check=True)
    return repo


def _prepare(repo, target, *, initialize):
    argv = module.build_daemon_workspace_branch_argv(
        volume="agent_workspaces", target_in_volume="owned/repo", target_branch=target,
        runtime_uid=_CONTROLLED_UID, runtime_gid=_CONTROLLED_GID,
        image="fixture-unused", allow_initialization=initialize,
    )
    assert argv[argv.index("--network") + 1] == "none"
    assert argv[argv.index("--user") + 1] == f"{_CONTROLLED_UID}:{_CONTROLLED_GID}"
    positional = argv[argv.index("--") + 1:]
    return subprocess.run(
        ["/bin/sh", "-ceu", argv[argv.index("-ceu") + 1], "--", str(repo), *positional[1:]],
        env={"PATH": os.environ["PATH"]}, text=True, capture_output=True,
    )


@pytest.mark.parametrize("target", ["main", "feature/selected", "README.md", "feature/$(id)", "développement"])
def test_new_workspace_selects_or_creates_the_authored_branch(tmp_path, target):
    repo = _repository(tmp_path)
    original = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"])
    result = _prepare(repo, target, initialize=True)
    assert result.returncode == 0, result.stderr
    current_branch = subprocess.check_output(
        ["git", "-C", str(repo), "symbolic-ref", "--short", "HEAD"], text=True
    ).strip()
    prepared_head = subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", "HEAD"]
    )
    assert current_branch == target
    assert prepared_head == original
    assert (repo / "README.md").read_text() == "admitted input\n"


def test_redelivery_preserves_a_later_agent_branch_and_uncommitted_work(tmp_path):
    repo = _repository(tmp_path)
    initial = _prepare(repo, "feature/selected", initialize=True)
    assert initial.returncode == 0, initial.stderr
    subprocess.run(["git", "-C", str(repo), "switch", "-qc", "agent/candidate"], check=True)
    (repo / "README.md").write_text("preserve candidate edits\n")
    result = _prepare(repo, "feature/selected", initialize=False)
    assert result.returncode == 0, result.stderr
    current_branch = subprocess.check_output(
        ["git", "-C", str(repo), "symbolic-ref", "--short", "HEAD"], text=True
    ).strip()
    assert current_branch == "agent/candidate"
    assert (repo / "README.md").read_text() == "preserve candidate edits\n"


@pytest.mark.parametrize("target", ["", ".", "/repo", "../repo", "owned/../repo", "owned,readonly/repo"])
def test_branch_preparation_rejects_a_mount_outside_its_workspace(target):
    with pytest.raises(module.HarnessPlatformError):
        module.build_daemon_workspace_branch_argv(
            volume="fixture-workspaces", target_in_volume=target,
            target_branch="feature/selected", runtime_uid=_CONTROLLED_UID,
            runtime_gid=_CONTROLLED_GID, image="fixture-unused", allow_initialization=True,
        )


@pytest.mark.parametrize("target", ["-B", "invalid..branch", "other-work"])
def test_unproven_existing_workspace_is_preserved_instead_of_switching(tmp_path, target):
    repo = _repository(tmp_path)
    (repo / "README.md").write_text("preserve uncertain edits\n")
    result = _prepare(repo, target, initialize=False)
    assert result.returncode != 0
    current_branch = subprocess.check_output(
        ["git", "-C", str(repo), "symbolic-ref", "--short", "HEAD"], text=True
    ).strip()
    assert current_branch == "main"
    assert (repo / "README.md").read_text() == "preserve uncertain edits\n"


@pytest.mark.asyncio
async def test_materializer_passes_target_and_distinguishes_fresh_from_retained(tmp_path):
    calls = []

    async def token():
        return "synthetic-selected"

    async def runner(argv, input_bytes=None):
        if input_bytes is not None:
            target = tmp_path / Path(argv[-3].removeprefix("/work/"))
            target.mkdir(parents=True)
            (target / "README.md").write_text("cloned input")
        elif "-ceu" in argv:
            calls.append(argv[argv.index("--") + 1:])
        return 0, "", ""

    owner = module.OmnigentWorkspaceMaterializer(
        command_runner=runner, workspace_root=tmp_path,
        repository_credential_service=_SelectedRepositoryCredentials(token),
    )
    request = _request({
        "workspaceLocator": {"kind": "sandbox", "workspaceId": _workspace_id(), "relativePath": "repo"},
        "repository": "owner/repo", "branch": "main", "targetBranch": "feature/selected",
    })
    for _ in range(2):
        await owner.materialize(request, plan=object(), runtime_uid=_CONTROLLED_UID, runtime_gid=_CONTROLLED_GID)
    assert [args[1:] for args in calls] == [["feature/selected", "initialize"], ["feature/selected", "retain"]]
