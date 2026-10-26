"""Preserve submodule work and Git state across loss of the original host."""

import json
import shutil
import subprocess
import tarfile
from io import BytesIO
from pathlib import Path

import pytest

from moonmind.workflows.skills.artifact_store import InMemoryArtifactStore
from moonmind.workflows.temporal.activity_runtime import TemporalSandboxActivities
from moonmind.workflows.temporal.runtime.checkpoint_restore import (
    ManagedCheckpointRestoreService,
)
from moonmind.schemas.checkpoint_restore_models import CheckpointRestoreError
from tests.unit.workflows.temporal.test_checkpoint_restore import _repo, _request


def git(workspace: Path, *arguments: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(workspace), *arguments], text=True
    ).strip()


def repository_with_submodule(
    tmp_path: Path, module_path: str = "module"
) -> tuple[Path, Path]:
    module_origin, _ = _repo(tmp_path / "module-origin")
    git(module_origin, "commit", "--allow-empty", "-m", "Submodule base")
    module_head = git(module_origin, "rev-parse", "HEAD")
    remote, _ = _repo(tmp_path / "remote")
    (remote / ".gitmodules").write_text(
        '[submodule "module"]\n'
        f"\tpath = {module_path}\n"
        "\turl = https://example.invalid/module.git\n"
    )
    git(remote, "add", ".gitmodules")
    git(
        remote,
        "update-index",
        "--add",
        "--cacheinfo",
        f"160000,{module_head},{module_path}",
    )
    git(remote, "commit", "-m", "Add submodule")
    source = tmp_path / "temporal_sandbox" / "source"
    source.parent.mkdir()
    git(tmp_path, "clone", str(remote), str(source))
    return remote, source


async def capture_checkpoint(
    source: Path,
    store: InMemoryArtifactStore,
    activities: TemporalSandboxActivities | None = None,
    *,
    include_untracked: bool = True,
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
            "includeUntracked": include_untracked,
        }
    )


def initialize_submodule(source: Path, origin: Path) -> Path:
    git(source, "config", "submodule.module.url", str(origin))
    git(source, "-c", "protocol.file.allow=always", "submodule", "update", "--init")
    return source / "module"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change", ["clean", "dirty", "unpublished", "nested", "staged_gitlink"]
)
async def test_initialized_submodules_restore_without_their_source(tmp_path, change):
    remote, source = repository_with_submodule(tmp_path)
    module = initialize_submodule(source, tmp_path / "module-origin")
    git(module, "config", "credential.helper", "fixture-only-do-not-restore")
    package = module / "src" / "credentials"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("# credential provider implementation\n")
    git(module, "add", "src")
    git(
        module,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-m",
        "Credential provider source",
    )
    git(module, "update-ref", "refs/remotes/origin/snapshot", "HEAD")
    if change in {"unpublished", "staged_gitlink"}:
        (module / "committed.txt").write_text("unpublished submodule commit\n")
        git(module, "add", "committed.txt")
        git(
            module,
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-m",
            "Unpublished work",
        )
        if change == "staged_gitlink":
            git(source, "add", "module")
    if change == "nested":
        git(
            module,
            "-c",
            "protocol.file.allow=always",
            "submodule",
            "add",
            str(tmp_path / "module-origin"),
            "nested",
        )
        git(
            module,
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-am",
            "Nested dependency",
        )
    repositories = [source, module]
    if change == "nested":
        repositories.append(module / "nested")
    if change != "clean":
        for repo in repositories[1:]:
            (repo / "tracked.txt").write_text("staged submodule bytes\n")
            git(repo, "add", "tracked.txt")
            (repo / "tracked.txt").write_text("unstaged submodule bytes\n")
            (repo / "new.bin").write_bytes(b"\x00\xffunfinished")
    states = {
        repo.relative_to(source).as_posix(): (
            git(repo, "rev-parse", "HEAD"),
            git(repo, "ls-files", "--stage"),
            git(repo, "status", "--porcelain=v1", "--untracked-files=all"),
        )
        for repo in repositories
    }
    base = states["."][0]
    store = InMemoryArtifactStore()
    capture = await capture_checkpoint(source, store)
    assert capture["status"] == "captured", capture
    with tarfile.open(
        fileobj=BytesIO(store.get_bytes(capture["workspace"]["archiveRef"]))
    ) as archive:
        assert all(
            ".git" not in Path(name).parts and not name.endswith("/.env")
            for name in archive.getnames()
        )
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
    shutil.rmtree(tmp_path / "module-origin")
    result = await service.restore(request)
    restored = authority / "new-run" / "repo"
    for name, expected in states.items():
        repo = restored / name
        assert (
            git(repo, "rev-parse", "HEAD"),
            git(repo, "ls-files", "--stage"),
            git(repo, "status", "--porcelain=v1", "--untracked-files=all"),
        ) == expected

        if name != "." and change != "clean":
            assert git(repo, "show", ":tracked.txt") == "staged submodule bytes"
            assert (repo / "tracked.txt").read_text() == "unstaged submodule bytes\n"
            assert (repo / "new.bin").read_bytes() == b"\x00\xffunfinished"
    assert (
        "fixture-only-do-not-restore"
        not in (restored / "module" / ".git" / "config").read_text()
    )
    if change == "unpublished":
        assert git(restored / "module", "log", "-2", "--format=%s").splitlines() == [
            "Unpublished work",
            "Credential provider source",
        ]
    assert await service.restore(request) == result
    shutil.rmtree(restored)
    repeated = await service.restore(request)
    assert repeated["baseCommit"] == base
    for name, expected in states.items():
        repo = restored / name
        assert (
            git(repo, "rev-parse", "HEAD"),
            git(repo, "ls-files", "--stage"),
            git(repo, "status", "--porcelain=v1", "--untracked-files=all"),
        ) == expected

    git(
        restored / "module",
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "--allow-empty",
        "-m",
        "Subsequently changed candidate",
    )
    with pytest.raises(CheckpointRestoreError):
        await service.restore(request)


@pytest.mark.asyncio
async def test_submodule_exclusions_apply_at_each_repository_root(tmp_path):
    _, source = repository_with_submodule(tmp_path)
    module = initialize_submodule(source, tmp_path / "module-origin")
    (module / ".env").write_text("PRIVATE_VALUE=local-only\n")
    (module / "credentials").mkdir()
    (module / "credentials" / "session.json").write_text("private state\n")
    (module / ".agents").mkdir()
    (module / ".agents" / "skills").symlink_to(
        tmp_path / "module-origin", target_is_directory=True
    )
    store = InMemoryArtifactStore()

    capture = await capture_checkpoint(source, store)

    assert capture["status"] == "captured", capture
    manifest = json.loads(store.get_bytes(capture["workspace"]["manifestRef"]))
    assert {entry["path"] for entry in manifest["entries"]} == {
        ".gitmodules",
        "tracked.txt",
        "module/tracked.txt",
    }
    assert {
        "module/.env",
        "module/credentials/session.json",
        "module/.agents/skills",
    } <= set(manifest["excludedPaths"])
    assert "gitStatusDigest" not in manifest
    assert "statusDigest" not in manifest["git"]["submodules"][0]["git"]


@pytest.mark.asyncio
async def test_dirty_submodule_mutation_after_archive_does_not_commit_checkpoint(
    tmp_path, monkeypatch
):
    from moonmind.workflows.temporal.activity_runtime import (
        TemporalActivityRuntimeError,
    )

    _, source = repository_with_submodule(tmp_path)
    module = initialize_submodule(source, tmp_path / "module-origin")
    (module / "tracked.txt").write_text("first unfinished content\n")
    store = InMemoryArtifactStore()
    activities = TemporalSandboxActivities(
        workspace_root=tmp_path, artifact_store=store
    )
    original = activities._build_worktree_archive

    def mutate_after_archive(*args, **kwargs):
        archive = original(*args, **kwargs)
        (module / "tracked.txt").write_text("later unfinished content\n")
        return archive

    monkeypatch.setattr(activities, "_build_worktree_archive", mutate_after_archive)
    with pytest.raises(TemporalActivityRuntimeError, match="submodule changed"):
        await capture_checkpoint(source, store, activities)
    assert (module / "tracked.txt").read_text() == "later unfinished content\n"


def test_submodule_git_objects_are_scanned_before_packing(tmp_path):
    from moonmind.workflows.temporal.runtime.checkpoint_history import (
        capture_git_history,
    )

    _, source = repository_with_submodule(tmp_path)
    module = initialize_submodule(source, tmp_path / "module-origin")
    (module / "binary.bin").write_bytes(b"\x00api_key=fixture-private-value\n")
    git(module, "add", "binary.bin")
    git(
        module,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-m",
        "Unsafe saved commit",
    )
    (module / "binary.bin").write_bytes(b"\x00safe worktree\n")
    with pytest.raises(
        ValueError, match="submodule history failed export secret scanning"
    ):
        capture_git_history(
            module, git(module, "rev-parse", "HEAD"), self_contained=True
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
async def test_newly_staged_gitlink_is_not_a_restorable_empty_directory(tmp_path):
    _, source = repository_with_submodule(tmp_path)
    module_head = git(source, "rev-parse", ":module")
    added = source / "added-module"
    added.mkdir()
    git(
        source,
        "update-index",
        "--add",
        "--cacheinfo",
        f"160000,{module_head},added-module",
    )
    assert git(source, "status", "--porcelain=v1") == "A  added-module"

    with pytest.raises(ValueError, match="submodule"):
        await capture_checkpoint(source, InMemoryArtifactStore())

    assert added.is_dir() and not any(added.iterdir())


@pytest.mark.asyncio
@pytest.mark.parametrize("include_untracked", [False, True])
async def test_submodule_parent_symlink_cannot_hide_worktree_deletion(
    tmp_path, include_untracked
):
    _, source = repository_with_submodule(tmp_path, "deps/module")
    shutil.rmtree(source / "deps")
    target = source / "empty-target"
    (target / "module").mkdir(parents=True)
    parent = source / "deps"
    parent.symlink_to(target, target_is_directory=True)
    if include_untracked:
        # Even a parent omitted by Git's ignore rules must be checked.
        (source / ".git" / "info" / "exclude").write_text("deps\n")
    assert "D deps/module" in git(source, "status", "--porcelain=v1")

    with pytest.raises(ValueError, match="submodule"):
        await capture_checkpoint(
            source, InMemoryArtifactStore(), include_untracked=include_untracked
        )

    assert parent.is_symlink() and not any((target / "module").iterdir())


@pytest.mark.asyncio
@pytest.mark.parametrize("materialization", ["populated", "symlink", "missing", "file"])
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
