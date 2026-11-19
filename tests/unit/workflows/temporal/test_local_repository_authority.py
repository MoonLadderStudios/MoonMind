"""Bounded committed-source reads do not inherit Git execution authority."""

import subprocess

import pytest

from moonmind.workflows.temporal.runtime.local_repository_authority import (
    _capture_source_bytes,
)


def git(path, *args):
    return subprocess.check_output(["git", "-C", str(path), *args], text=True).strip()


@pytest.fixture
def source(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    git(root, "init", "-b", "chosen")
    git(root, "config", "user.name", "Test operator")
    git(root, "config", "user.email", "operator@example.invalid")
    (root / "tracked.txt").write_text("committed\n")
    git(root, "add", "tracked.txt")
    git(root, "commit", "-m", "Initial source")
    return root


def test_source_export_ignores_dirty_files_hooks_and_ambient_git(
    source, tmp_path, monkeypatch
):
    sentinel = tmp_path / "executed"
    (source / ".git/hooks/post-checkout").write_text(f"#!/bin/sh\ntouch {sentinel}\n")
    (source / ".git/hooks/post-checkout").chmod(0o700)
    hostile = tmp_path / "hostile-config"
    hostile.write_text(
        f'[core]\n hooksPath = {source / ".git/hooks"}\n[filter "poison"]\n smudge = touch {sentinel}\n'
    )
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(hostile))
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.hooksPath")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", str(source / ".git/hooks"))
    (source / "tracked.txt").write_text("dirty\n")
    (source / "untracked.txt").write_text("untracked\n")
    head, branch, refs, bundle, history, archive, entries = _capture_source_bytes(
        source, {"branch": "chosen"}
    )
    assert head == git(source, "rev-parse", "HEAD")
    assert branch == "chosen" and bundle and archive
    assert history["requiresSourceObjects"] is False
    assert {entry["path"] for entry in entries} == {"tracked.txt"}
    assert refs["refs/heads/chosen"]["commit"] == head
    assert not sentinel.exists()
    assert (source / "tracked.txt").read_text() == "dirty\n"


@pytest.mark.parametrize(
    "poison",
    [
        "filter",
        "uploadpack",
        "include",
        "alternates",
        "git-symlink",
        "object-symlink",
        "replace",
        "packed-replace",
        "submodule",
        "excluded",
        "lfs",
        "nested-filter",
        "lfsconfig",
        "tag",
    ],
)
def test_source_export_refuses_external_or_unsupported_authority(
    source, tmp_path, poison
):
    sentinel = tmp_path / "executed"
    if poison in {"filter", "uploadpack", "include"}:
        key = {
            "filter": "filter.poison.smudge",
            "uploadpack": "uploadpack.packObjectsHook",
            "include": "include.path",
        }[poison]
        git(source, "config", key, f"touch {sentinel}")
    elif poison == "alternates":
        (source / ".git/objects/info/alternates").write_text(
            str(tmp_path / "foreign-objects")
        )
    elif poison == "git-symlink":
        (source / ".git").rename(tmp_path / "foreign-git")
        (source / ".git").symlink_to(tmp_path / "foreign-git", target_is_directory=True)
    elif poison == "object-symlink":
        (source / ".git/objects/foreign").symlink_to(tmp_path)
    elif poison == "packed-replace":
        first = git(source, "rev-parse", "HEAD")
        (source / "tracked.txt").write_text("second\n")
        git(source, "commit", "-am", "Second")
        git(source, "replace", "HEAD", first)
        git(source, "pack-refs", "--all", "--prune")
        replacement_dir = source / ".git/refs/replace"
        if replacement_dir.exists():
            replacement_dir.rmdir()
    elif poison == "replace":
        (source / ".git/refs/replace").mkdir()
    elif poison == "submodule":
        git(
            source,
            "update-index",
            "--add",
            "--cacheinfo",
            "160000," + git(source, "rev-parse", "HEAD") + ",nested",
        )
        git(source, "commit", "-m", "Unsupported gitlink")
    elif poison == "nested-filter":
        (source / "nested").mkdir()
        (source / "nested/.gitattributes").write_text("*.txt filter=custom\n")
        (source / "nested/file.txt").write_text("input\n")
        git(source, "add", "nested")
        git(source, "commit", "-m", "Nested external filter")
    elif poison == "lfsconfig":
        (source / ".lfsconfig").write_text("[lfs]\nurl = https://example.invalid/lfs\n")
        git(source, "add", ".lfsconfig")
        git(source, "commit", "-m", "LFS remote authority")
    elif poison == "tag":
        git(source, "tag", "-a", "v1", "-m", "Annotated source tag")
    elif poison == "lfs":
        (source / ".gitattributes").write_text("tracked.txt filter=lfs\n")
        git(source, "add", ".gitattributes")
        git(source, "commit", "-m", "External filter required")
    else:
        (source / ".env").write_text("not a credential\n")
        git(source, "add", ".env")
        git(source, "commit", "-m", "Excluded tracked file")
    with pytest.raises(ValueError):
        _capture_source_bytes(source, {"branch": "chosen"})
    assert not sentinel.exists()


def test_source_export_rejects_pin_different_from_branch_tip(source):
    with pytest.raises(ValueError, match="pin"):
        _capture_source_bytes(source, {"branch": "chosen", "commitSha": "0" * 40})


@pytest.mark.asyncio
async def test_active_source_claim_refuses_before_git_or_artifacts(
    monkeypatch, tmp_path
):
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from moonmind.workflows.temporal.runtime import local_repository_authority as owner

    @asynccontextmanager
    async def locked(_):
        yield

    monkeypatch.setattr(
        owner,
        "SandboxWorkspaceRecordStore",
        lambda _: SimpleNamespace(
            materialization_locked=locked,
            active_claim_grantees=lambda _: ("live-reader",),
        ),
    )
    reader = AsyncMock()
    monkeypatch.setattr(owner, "_capture_source_bytes", reader)
    artifacts, revalidate = AsyncMock(), AsyncMock()
    with pytest.raises(ValueError, match="active workspace user"):
        await owner.capture_initial_source(
            request=None,
            intent={"sourcePath": str(tmp_path)},
            authority={"workspaceId": "owned", "sourcePath": str(tmp_path)},
            workspace_root=tmp_path,
            artifact_service=artifacts,
            access=None,
            revalidate_authority=revalidate,
        )
    reader.assert_not_called()
    revalidate.assert_not_called()
    assert not artifacts.mock_calls


@pytest.mark.asyncio
async def test_cancelled_source_read_holds_claim_until_bounded_reader_stops(
    monkeypatch, tmp_path
):
    import asyncio
    import threading
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from moonmind.workflows.temporal.runtime import local_repository_authority as owner

    entered, finish = threading.Event(), threading.Event()
    released = False

    @asynccontextmanager
    async def locked(_):
        nonlocal released
        try:
            yield
        finally:
            released = True

    monkeypatch.setattr(
        owner,
        "SandboxWorkspaceRecordStore",
        lambda _: SimpleNamespace(
            materialization_locked=locked,
            active_claim_grantees=lambda _: (),
            load=lambda _: SimpleNamespace(
                workflow_id="workflow", step_execution_id="source-step"
            ),
        ),
    )

    def reader(*_args):
        entered.set()
        assert finish.wait(5)
        raise ValueError("bounded read stopped")

    monkeypatch.setattr(owner, "_capture_source_bytes", reader)
    artifacts = AsyncMock()
    task = asyncio.create_task(
        owner.capture_initial_source(
            request=None,
            intent={"sourcePath": str(tmp_path), "workflowId": "workflow"},
            authority={
                "workspaceId": "owned",
                "sourcePath": str(tmp_path),
                "workflowId": "workflow",
                "stepExecutionId": "source-step",
            },
            workspace_root=tmp_path,
            artifact_service=artifacts,
            access=None,
            revalidate_authority=AsyncMock(),
        )
    )
    assert await asyncio.to_thread(entered.wait, 5)
    task.cancel()
    await asyncio.sleep(0)
    assert not released
    finish.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert released
    assert not artifacts.mock_calls


def test_source_export_keeps_builtin_nonfilter_attributes(source):
    (source / ".gitattributes").write_text("tracked.txt text eol=lf\n")
    git(source, "add", ".gitattributes")
    git(source, "commit", "-m", "Built-in text attributes")
    *_, entries = _capture_source_bytes(source, {"branch": "chosen"})
    assert {entry["path"] for entry in entries} == {"tracked.txt", ".gitattributes"}


def test_source_config_parse_failure_does_not_echo_private_values(source):
    marker = "private-config-value-do-not-echo"
    (source / ".git/config").write_text(f"malformed {marker}\n")
    with pytest.raises(ValueError) as failure:
        _capture_source_bytes(source, {"branch": "chosen"})
    assert marker not in str(failure.value)


@pytest.mark.parametrize("source_head", ["chosen", "other"])
@pytest.mark.parametrize("transport", ["path", "file-uri"])
def test_export_matches_native_selected_branch_refs(
    source, tmp_path, source_head, transport
):
    from moonmind.workflows.temporal.runtime.local_repository_authority import (
        _WORKER_GIT_EXECUTABLE,
    )

    # Same-commit branches must not confuse advertised HEAD with selected refs.
    git(source, "branch", "other")
    git(source, "symbolic-ref", "HEAD", "refs/heads/" + source_head)
    native = tmp_path / "native"
    repository = source.as_uri() if transport == "file-uri" else str(source)
    subprocess.run(
        [
            _WORKER_GIT_EXECUTABLE,
            "clone",
            "--branch",
            "chosen",
            "--single-branch",
            repository,
            str(native),
        ],
        check=True,
        capture_output=True,
    )
    _, _, refs, *_ = _capture_source_bytes(
        source, {"branch": "chosen", "repository": repository}
    )
    expected = set(git(native, "for-each-ref", "--format=%(refname)").splitlines())
    assert set(refs) == expected
    assert "refs/remotes/origin/other" not in refs
