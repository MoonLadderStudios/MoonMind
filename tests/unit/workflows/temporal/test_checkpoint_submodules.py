"""Checkpoint empty submodules without dropping populated nested worktrees."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from moonmind.workflows.skills.artifact_store import InMemoryArtifactStore
from moonmind.workflows.temporal.activity_runtime import TemporalSandboxActivities
from moonmind.workflows.temporal.runtime.checkpoint_restore import (
    ManagedCheckpointRestoreService,
)
from tests.unit.workflows.temporal.test_checkpoint_restore import _repo, _request


def git(workspace: Path, *arguments: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(workspace), *arguments], text=True
    ).strip()


def repository_with_submodule(tmp_path: Path) -> tuple[Path, Path]:
    module_origin, _ = _repo(tmp_path / "module-origin")
    git(module_origin, "commit", "--allow-empty", "-m", "Submodule base")
    module_head = git(module_origin, "rev-parse", "HEAD")
    remote, _ = _repo(tmp_path / "remote")
    (remote / ".gitmodules").write_text(
        '[submodule "module"]\n'
        "\tpath = module\n"
        "\turl = https://example.invalid/module.git\n"
    )
    git(remote, "add", ".gitmodules")
    git(remote, "update-index", "--add", "--cacheinfo", f"160000,{module_head},module")
    git(remote, "commit", "-m", "Add submodule")
    source = tmp_path / "temporal_sandbox" / "source"
    source.parent.mkdir()
    git(tmp_path, "clone", str(remote), str(source))
    return remote, source


async def capture_checkpoint(
    source: Path,
    store: InMemoryArtifactStore,
    activities: TemporalSandboxActivities | None = None,
) -> dict:
    activities = activities or TemporalSandboxActivities(
        workspace_root=source.parent.parent, artifact_store=store
    )
    return await activities.workspace_capture_checkpoint(
        {
            "identity": {
                "workflowId": "source",
                "runId": "source-run",
                "logicalStepId": "implement",
                "executionOrdinal": 1,
            },
            "boundary": "before_execution",
            "kind": "worktree_archive",
            "workspacePath": str(source),
            "artifactNamespace": "checkpoint",
            "idempotencyKey": "capture",
            "includeUntracked": True,
        }
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("staged_gitlink", [False, True])
async def test_uninitialized_submodule_restores_after_source_loss(
    tmp_path: Path, staged_gitlink: bool
) -> None:
    remote, source = repository_with_submodule(tmp_path)
    if staged_gitlink:
        module_origin = tmp_path / "module-origin"
        git(module_origin, "commit", "--allow-empty", "-m", "Advance submodule")
        new_module_head = git(module_origin, "rev-parse", "HEAD")
        git(source, "update-index", "--cacheinfo", f"160000,{new_module_head},module")
        assert git(source, "diff", "--cached", "--name-only") == "module"
    base = git(source, "rev-parse", "HEAD")
    index = git(source, "ls-files", "--stage")
    status = git(source, "status", "--porcelain=v1")
    store = InMemoryArtifactStore()

    capture = await capture_checkpoint(source, store)

    assert capture["status"] == "captured"
    manifest = json.loads(store.get_bytes(capture["workspace"]["manifestRef"]))
    assert {entry["path"] for entry in manifest["entries"]} == {
        ".gitmodules",
        "tracked.txt",
    }
    checkpoint_ref = store.put_bytes(
        json.dumps(
            {
                "contentType": "application/vnd.moonmind.step-execution-checkpoint+json;version=1",
                "source": {
                    "workflowId": "source",
                    "runId": "source-run",
                    "logicalStepId": "implement",
                    "executionOrdinal": 1,
                },
                "boundary": "before_execution",
                "workspace": capture["workspace"],
            }
        ).encode(),
        content_type="application/vnd.moonmind.step-execution-checkpoint+json;version=1",
    ).artifact_ref
    request = _request(checkpoint_ref=checkpoint_ref, capture=capture, base=base)
    authority = tmp_path / "authority"
    service = ManagedCheckpointRestoreService(
        authority_root=authority, artifact_store=store, repository_source_root=remote
    )
    shutil.rmtree(source)

    result = await service.restore(request)

    restored = authority / "new-run" / "repo"
    assert git(restored, "ls-files", "--stage") == index
    assert git(restored, "status", "--porcelain=v1") == status
    assert not any((restored / "module").iterdir())
    assert (restored / ".gitmodules").read_bytes() == (
        remote / ".gitmodules"
    ).read_bytes()
    assert await service.restore(request) == result


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "materialization", ["populated", "initialized", "symlink", "missing", "file"]
)
async def test_submodule_capture_refuses_unpreserved_state(
    tmp_path: Path, materialization: str
) -> None:
    _, source = repository_with_submodule(tmp_path)
    module = source / "module"
    if materialization == "missing":
        module.rmdir()
    elif materialization == "file":
        module.rmdir()
        module.write_text("replaced submodule worktree\n")
    elif materialization == "symlink":
        module.rmdir()
        module.symlink_to(tmp_path / "module-origin", target_is_directory=True)
    elif materialization == "initialized":
        module.rmdir()
        git(source, "clone", str(tmp_path / "module-origin"), "module")
    else:
        (module / "valuable.txt").write_text("unfinished submodule work\n")

    with pytest.raises(ValueError, match="submodule"):
        await capture_checkpoint(source, InMemoryArtifactStore())

    if materialization == "populated":
        assert (module / "valuable.txt").read_text() == "unfinished submodule work\n"


@pytest.mark.asyncio
async def test_submodule_population_during_capture_is_rejected(tmp_path, monkeypatch):
    _, source = repository_with_submodule(tmp_path)
    activities = TemporalSandboxActivities(
        workspace_root=tmp_path, artifact_store=InMemoryArtifactStore()
    )
    original = activities._build_worktree_archive

    def populate_after_archive(*args, **kwargs):
        archive = original(*args, **kwargs)
        (source / "module" / "valuable.txt").write_text("concurrent submodule work\n")
        return archive

    monkeypatch.setattr(activities, "_build_worktree_archive", populate_after_archive)
    result = await capture_checkpoint(source, InMemoryArtifactStore(), activities)

    assert result["status"] == "invalid"
    assert "submodule" in result["summary"]
    assert (
        source / "module" / "valuable.txt"
    ).read_text() == "concurrent submodule work\n"
