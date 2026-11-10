"""Immutable current inputs are distinct from saved repository work."""

import copy
import hashlib
import os

import pytest

from moonmind.omnigent.workspace_artifacts import (
    WorkspaceArtifactProjectionError,
    WorkspaceArtifactProjector,
)
from tests.unit.omnigent.test_workspace_restore_convergence import FakeArtifactService


def options():
    return dict(
        workflow_id="workflow-1", runtime_uid=os.getuid(), runtime_gid=os.getgid()
    )


def _mutation_state(path):
    # Verification may update atime; only writes change this state.
    info = path.stat()
    return (
        info.st_ino,
        info.st_mode,
        info.st_uid,
        info.st_gid,
        info.st_nlink,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


@pytest.mark.asyncio
async def test_projection_pins_all_effective_inputs_and_rejects_drift(tmp_path):
    service = FakeArtifactService(
        {"restore": b"saved input", "brief": b"brief", "gate": b"gate"}
    )
    owner = WorkspaceArtifactProjector(service)
    kwargs = dict(
        restore_refs=("artifact://restore",),
        attachment_refs=("artifact://brief", "artifact://gate"),
        named_refs={"gateResultPath": "artifact://gate"},
        **options(),
    )
    projection = await owner.prepare_input_projection(**kwargs)
    assert len(projection["entries"]) == 3
    assert (
        await owner.prepare_input_projection(**kwargs, retained=projection)
        == projection
    )
    for changed in (
        dict(kwargs, attachment_refs=("artifact://gate",)),
        dict(kwargs, named_refs={"remainingWorkPath": "artifact://gate"}),
    ):
        with pytest.raises(WorkspaceArtifactProjectionError, match="changed"):
            await owner.prepare_input_projection(**changed, retained=projection)
    workspace = tmp_path / "repo"
    workspace.mkdir()
    await owner.project_bound_inputs(workspace, projection=projection, **options())
    for entry in projection["entries"]:
        path = workspace / entry["path"]
        assert path.is_file()
        assert (
            "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest() == entry["digest"]
        )
        assert path.stat().st_mode & 0o777 == 0o400
    assert not list(tmp_path.glob(".moonmind-import-*"))
    assert not list(workspace.rglob(".input-*"))


@pytest.mark.asyncio
async def test_projection_revalidates_metadata_before_mutation(tmp_path):
    service = FakeArtifactService({"brief": b"brief"})
    owner = WorkspaceArtifactProjector(service)
    projection = await owner.prepare_input_projection(
        attachment_refs=("artifact://brief",), **options()
    )
    workspace = tmp_path / "repo"
    workspace.mkdir()
    service.payloads["brief"] = b"changed"
    with pytest.raises(WorkspaceArtifactProjectionError, match="changed"):
        await owner.project_bound_inputs(workspace, projection=projection, **options())
    assert list(workspace.iterdir()) == []


@pytest.mark.asyncio
async def test_bound_projection_does_not_reclassify_unrelated_baseline_work(tmp_path):
    owner = WorkspaceArtifactProjector(FakeArtifactService({"brief": b"brief"}))
    projection = await owner.prepare_input_projection(
        attachment_refs=("artifact://brief",), **options()
    )
    workspace = tmp_path / "repo"
    unknown = workspace / ".moonmind/attachments/unknown"
    unknown.parent.mkdir(parents=True)
    unknown.write_text("saved work")
    # Full baseline membership belongs to canonical restore, not this
    # exact-target projector. The projector never deletes unrelated work.
    await owner.project_bound_inputs(workspace, projection=projection, **options())
    assert unknown.read_text() == "saved work"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change", ["refs", "named", "mandatory", "group-order", "empty-to-input"]
)
async def test_bound_input_contract_rejects_each_same_attempt_change(change):
    owner = WorkspaceArtifactProjector(FakeArtifactService({"a": b"a", "b": b"b"}))
    kwargs = dict(
        attachment_refs=("artifact://a", "artifact://b"),
        evidence_bundles=[["artifact://a"], ["artifact://b"]],
        mandatory_refs=[],
        **options(),
    )
    if change == "empty-to-input":
        kwargs.update(attachment_refs=(), evidence_bundles=None)
    retained = await owner.prepare_input_projection(**kwargs)
    changed = copy.deepcopy(kwargs)
    if change == "refs":
        changed["attachment_refs"] = ("artifact://a",)
    elif change == "named":
        changed["named_refs"] = {"gateResultPath": "artifact://a"}
    elif change == "mandatory":
        changed["mandatory_refs"] = ["artifact://a"]
    elif change == "group-order":
        changed["evidence_bundles"].reverse()
    else:
        changed["attachment_refs"] = ("artifact://a",)
    with pytest.raises(WorkspaceArtifactProjectionError, match="changed"):
        await owner.prepare_input_projection(**changed, retained=retained)


@pytest.mark.asyncio
async def test_optional_selection_is_frozen_after_admission(monkeypatch):
    from moonmind.omnigent import workspace_artifacts as module

    monkeypatch.setattr(module, "MAX_TOTAL_BYTES", 2)
    owner = WorkspaceArtifactProjector(FakeArtifactService({"a": b"aa", "b": b"bb"}))
    kwargs = dict(
        attachment_refs=("artifact://a", "artifact://b"),
        evidence_bundles=[["artifact://a"], ["artifact://b"]],
        mandatory_refs=[],
        **options(),
    )
    retained = await owner.prepare_input_projection(**kwargs)
    assert retained["selectedAttachments"] == ["artifact://b"]
    monkeypatch.setattr(module, "MAX_TOTAL_BYTES", 4)
    assert (await owner.prepare_input_projection(**kwargs))["selectedAttachments"] == [
        "artifact://b",
        "artifact://a",
    ]
    assert await owner.prepare_input_projection(**kwargs, retained=retained) == retained


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault",
    [
        "revoked",
        "incomplete",
        "quarantined",
        "restricted",
        "missing-digest",
        "wrong-bytes",
    ],
)
async def test_artifact_failure_preserves_candidate_and_staging(
    tmp_path, monkeypatch, fault
):
    service = FakeArtifactService({"brief": b"brief"})
    owner = WorkspaceArtifactProjector(service)
    projection = await owner.prepare_input_projection(
        attachment_refs=("artifact://brief",), **options()
    )
    workspace = tmp_path / "repo"
    workspace.mkdir()
    saved = workspace / "saved.txt"
    saved.write_bytes(b"unpublished work")
    if fault == "revoked":
        service.link_workflow_id = "sibling"
    elif fault == "incomplete":
        service.status = "PENDING"
    elif fault == "restricted":
        service.redaction_level = "RESTRICTED"
    elif fault in ("quarantined", "missing-digest"):
        original = service.get_metadata

        async def changed(**kwargs):
            result = await original(**kwargs)
            if fault == "quarantined":
                result[0].quarantined = True
            else:
                result[0].sha256 = None
            return result

        monkeypatch.setattr(service, "get_metadata", changed)
    else:

        async def wrong(**kwargs):
            return None, iter((b"wrong",))

        monkeypatch.setattr(service, "read_chunks", wrong)
    with pytest.raises(WorkspaceArtifactProjectionError):
        await owner.project_bound_inputs(workspace, projection=projection, **options())
    assert saved.read_bytes() == b"unpublished work"
    assert list(workspace.iterdir()) == [saved]
    assert not list(tmp_path.glob(".moonmind-import-*"))


@pytest.mark.asyncio
async def test_cancellation_during_download_removes_only_owned_staging(
    tmp_path, monkeypatch
):
    import asyncio

    owner = WorkspaceArtifactProjector(FakeArtifactService({"brief": b"brief"}))
    projection = await owner.prepare_input_projection(
        attachment_refs=("artifact://brief",), **options()
    )
    workspace = tmp_path / "repo"
    workspace.mkdir()
    sibling = tmp_path / "sibling"
    sibling.mkdir()
    (sibling / ".moonmind-import-retained").mkdir()
    entered = asyncio.Event()

    async def interrupted(*args, **kwargs):
        kwargs["target"].write_bytes(b"partial")
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(owner, "_write_payload", interrupted)
    task = asyncio.create_task(
        owner.project_bound_inputs(workspace, projection=projection, **options())
    )
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not list(tmp_path.glob(".moonmind-import-*"))
    assert (sibling / ".moonmind-import-retained").is_dir()
    assert list(workspace.iterdir()) == []


@pytest.mark.asyncio
async def test_changed_input_during_download_is_not_overwritten(tmp_path, monkeypatch):
    owner = WorkspaceArtifactProjector(FakeArtifactService({"brief": b"brief"}))
    projection = await owner.prepare_input_projection(
        attachment_refs=("artifact://brief",), **options()
    )
    workspace = tmp_path / "repo"
    workspace.mkdir()
    await owner.project_bound_inputs(workspace, projection=projection, **options())
    target = workspace / projection["entries"][0]["path"]
    write = owner._write_payload

    async def concurrent_change(*args, **kwargs):
        # An input may be replaced by a writer outside the controller fence.
        target.unlink()
        target.write_bytes(b"later saved work")
        return await write(*args, **kwargs)

    monkeypatch.setattr(owner, "_write_payload", concurrent_change)
    with pytest.raises(WorkspaceArtifactProjectionError):
        await owner.project_bound_inputs(workspace, projection=projection, **options())
    assert target.read_bytes() == b"later saved work"


@pytest.mark.asyncio
async def test_projection_preserves_verified_existing_inode(tmp_path):
    owner = WorkspaceArtifactProjector(FakeArtifactService({"brief": b"brief"}))
    projection = await owner.prepare_input_projection(
        attachment_refs=("artifact://brief",), **options()
    )
    workspace = tmp_path / "repo"
    workspace.mkdir()
    await owner.project_bound_inputs(workspace, projection=projection, **options())
    target = workspace / projection["entries"][0]["path"]
    inode = target.stat().st_ino
    await owner.project_bound_inputs(workspace, projection=projection, **options())
    assert target.stat().st_ino == inode


@pytest.mark.asyncio
async def test_input_arriving_at_promotion_is_not_replaced(tmp_path, monkeypatch):
    owner = WorkspaceArtifactProjector(FakeArtifactService({"brief": b"brief"}))
    projection = await owner.prepare_input_projection(
        attachment_refs=("artifact://brief",), **options()
    )
    workspace = tmp_path / "repo"
    workspace.mkdir()
    target = workspace / projection["entries"][0]["path"]
    link = os.link

    def concurrent_arrival(*args, **kwargs):
        target.write_bytes(b"new saved work")
        return link(*args, **kwargs)

    monkeypatch.setattr(os, "link", concurrent_arrival)
    with pytest.raises(WorkspaceArtifactProjectionError):
        await owner.project_bound_inputs(workspace, projection=projection, **options())
    assert target.read_bytes() == b"new saved work"
    assert not list(tmp_path.glob(".moonmind-import-*"))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "proposal", ["too-many-required", "too-many-optional", "invalid-group"]
)
async def test_invalid_optional_proposal_is_bounded_before_metadata(
    monkeypatch, proposal
):
    from unittest.mock import AsyncMock

    owner = WorkspaceArtifactProjector(FakeArtifactService({}))
    read = AsyncMock(
        side_effect=AssertionError("metadata read before proposal validation")
    )
    monkeypatch.setattr(owner, "_validate_metadata", read)
    refs = [f"artifact://input-{index}" for index in range(129)]
    bundles = [] if proposal == "too-many-required" else [[ref] for ref in refs]
    if proposal == "invalid-group":
        bundles = [refs[:34]]
    with pytest.raises(WorkspaceArtifactProjectionError):
        await owner.prepare_input_projection(
            attachment_refs=refs,
            evidence_bundles=bundles,
            mandatory_refs=[],
            **options(),
        )
    read.assert_not_awaited()


@pytest.mark.asyncio
async def test_input_deleted_during_download_is_not_recreated(tmp_path, monkeypatch):
    owner = WorkspaceArtifactProjector(FakeArtifactService({"brief": b"brief"}))
    projection = await owner.prepare_input_projection(
        attachment_refs=("artifact://brief",), **options()
    )
    workspace = tmp_path / "repo"
    workspace.mkdir()
    await owner.project_bound_inputs(workspace, projection=projection, **options())
    target = workspace / projection["entries"][0]["path"]
    write = owner._write_payload

    async def concurrent_delete(*args, **kwargs):
        target.unlink()
        return await write(*args, **kwargs)

    monkeypatch.setattr(owner, "_write_payload", concurrent_delete)
    with pytest.raises(WorkspaceArtifactProjectionError):
        await owner.project_bound_inputs(workspace, projection=projection, **options())
    assert not target.exists()


@pytest.mark.asyncio
async def test_bound_attachment_ignore_preserves_baseline_status(tmp_path):
    import subprocess

    owner = WorkspaceArtifactProjector(
        FakeArtifactService({"saved": b"saved", "new": b"new"})
    )
    projection = await owner.prepare_input_projection(
        attachment_refs=("artifact://saved", "artifact://new"), **options()
    )
    workspace = tmp_path / "repo"
    workspace.mkdir()
    subprocess.run(["git", "init", "-q", str(workspace)], check=True)
    saved, new = projection["entries"]
    target = workspace / saved["path"]
    target.parent.mkdir(parents=True)
    target.write_bytes(b"saved")
    target.chmod(0o400)
    inode = target.stat().st_ino
    await owner.project_bound_inputs(
        workspace, projection=projection, baseline_paths={saved["path"]}, **options()
    )
    status = subprocess.check_output(
        [
            "git",
            "-C",
            str(workspace),
            "status",
            "--porcelain=v1",
            "-z",
            "--untracked-files=all",
        ]
    )
    assert status == b"?? " + saved["path"].encode() + b"\0"
    assert target.stat().st_ino == inode
    assert (workspace / new["path"]).read_bytes() == b"new"
    assert (
        (workspace / ".git/info/exclude").read_text().endswith("/" + new["path"] + "\n")
    )


@pytest.fixture
def directory_ownership(monkeypatch):
    """Model a privileged worker's directory handoff on non-root test hosts."""
    import stat

    original_fstat = os.fstat
    owners = {}
    handoffs = []
    watched = set()
    identities = {}

    def observed_stat(fd):
        current = original_fstat(fd)
        for path in watched:
            if path.exists() and not path.is_symlink():
                info = path.stat()
                identities[(info.st_dev, info.st_ino)] = path
        path = identities.get((current.st_dev, current.st_ino))
        if path is not None:
            assert stat.S_ISDIR(current.st_mode)
            values = list(current)
            values[4:6] = owners.get(path, (os.getuid() + 1, os.getgid() + 1))
            return os.stat_result(values)
        return current

    def handoff(fd, uid, gid):
        current = original_fstat(fd)
        path = identities[(current.st_dev, current.st_ino)]
        handoffs.append(path)
        owners[path] = (uid, gid)

    monkeypatch.setattr(os, "fstat", observed_stat)
    monkeypatch.setattr(os, "fchown", handoff)
    return watched, owners, handoffs, handoff


@pytest.mark.asyncio
async def test_projection_hands_off_both_private_parents_before_promotion(
    tmp_path, monkeypatch, directory_ownership
):
    import stat

    owner = WorkspaceArtifactProjector(
        FakeArtifactService({"saved": b"saved", "brief": b"brief"})
    )
    projection = await owner.prepare_input_projection(
        restore_refs=("artifact://saved",),
        attachment_refs=("artifact://brief",),
        **options(),
    )
    workspace = tmp_path / "repo"
    workspace.mkdir()
    watched, owners, handoffs, _ = directory_ownership
    root = workspace / ".moonmind"
    watched.update((root, root / "restore", root / "attachments"))
    link = os.link

    def promote(source, destination, **kwargs):
        parent_info = os.fstat(kwargs["dst_dir_fd"])
        parent = next(
            path
            for path in watched
            if path.exists() and path.stat().st_ino == parent_info.st_ino
        )
        assert owners.get(root) == (os.getuid(), os.getgid())
        assert owners.get(parent) == (os.getuid(), os.getgid())
        assert stat.S_IMODE(parent_info.st_mode) == 0o700
        return link(source, destination, **kwargs)

    monkeypatch.setattr(os, "link", promote)
    previous_umask = os.umask(0o077)
    try:
        await owner.project_bound_inputs(workspace, projection=projection, **options())
    finally:
        os.umask(previous_umask)
    assert set(handoffs) == watched
    assert len(handoffs) == 3
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o700 for path in watched)


@pytest.mark.asyncio
@pytest.mark.parametrize("failed_parent", [".moonmind", ".moonmind/attachments"])
async def test_directory_handoff_failure_prevents_promotion_and_allows_retry(
    tmp_path, monkeypatch, directory_ownership, failed_parent
):
    owner = WorkspaceArtifactProjector(FakeArtifactService({"brief": b"brief"}))
    projection = await owner.prepare_input_projection(
        attachment_refs=("artifact://brief",), **options()
    )
    workspace = tmp_path / "repo"
    workspace.mkdir()
    saved = workspace / "saved.txt"
    saved.write_bytes(b"saved work")
    target = workspace / projection["entries"][0]["path"]
    watched, _, _, handoff = directory_ownership
    watched.update((target.parent, target.parent.parent))
    original_fstat = os.fstat

    def fail_handoff(fd, uid, gid):
        if original_fstat(fd).st_ino == (workspace / failed_parent).stat().st_ino:
            raise PermissionError("directory ownership transfer denied")
        handoff(fd, uid, gid)

    monkeypatch.setattr(os, "fchown", fail_handoff)
    with pytest.raises(WorkspaceArtifactProjectionError) as caught:
        await owner.project_bound_inputs(workspace, projection=projection, **options())
    assert caught.value.code == "WORKSPACE_AUTHORITY_MISMATCH"
    assert isinstance(caught.value.__cause__, PermissionError)
    assert not target.exists()
    assert saved.read_bytes() == b"saved work"
    assert not list(tmp_path.glob(".moonmind-import-*"))
    monkeypatch.setattr(os, "fchown", handoff)
    await owner.project_bound_inputs(workspace, projection=projection, **options())
    assert target.read_bytes() == b"brief"
    assert saved.read_bytes() == b"saved work"


@pytest.mark.asyncio
async def test_projection_preserves_baseline_modes_and_verify_only_does_not_write(
    tmp_path, monkeypatch
):
    owner = WorkspaceArtifactProjector(
        FakeArtifactService({"saved": b"saved", "new": b"new"})
    )
    projection = await owner.prepare_input_projection(
        attachment_refs=("artifact://saved", "artifact://new"), **options()
    )
    workspace = tmp_path / "repo"
    saved = workspace / projection["entries"][0]["path"]
    saved.parent.mkdir(parents=True)
    saved.parent.parent.chmod(0o750)
    saved.parent.chmod(0o710)
    saved.write_bytes(b"saved")
    saved.chmod(0o400)
    before = saved.stat()
    original_chmod = os.chmod

    def no_directory_chmod(path, mode, **kwargs):
        assert not os.path.isdir(path), "saved directory modes must not change"
        return original_chmod(path, mode, **kwargs)

    def no_fchown(*args):
        pytest.fail("already runtime-owned directories must not be reassigned")

    monkeypatch.setattr(os, "chmod", no_directory_chmod)
    monkeypatch.setattr(os, "fchmod", no_fchown)
    monkeypatch.setattr(os, "fchown", no_fchown)
    await owner.project_bound_inputs(
        workspace,
        projection=projection,
        baseline_paths={projection["entries"][0]["path"]},
        **options(),
    )
    after = saved.stat()
    assert (after.st_ino, after.st_mode, after.st_mtime_ns, after.st_ctime_ns) == (
        before.st_ino,
        before.st_mode,
        before.st_mtime_ns,
        before.st_ctime_ns,
    )
    assert saved.parent.stat().st_mode & 0o7777 == 0o710
    assert saved.parent.parent.stat().st_mode & 0o7777 == 0o750
    snapshot = {path: _mutation_state(path) for path in workspace.rglob("*")}
    await owner.project_bound_inputs(
        workspace, projection=projection, verify_only=True, **options()
    )
    assert {path: _mutation_state(path) for path in workspace.rglob("*")} == snapshot
    assert saved.read_bytes() == b"saved"


@pytest.mark.asyncio
@pytest.mark.parametrize("swapped_parent", [".moonmind", ".moonmind/attachments"])
async def test_directory_handoff_cannot_follow_parent_symlink_swap(
    tmp_path, monkeypatch, directory_ownership, swapped_parent
):
    owner = WorkspaceArtifactProjector(FakeArtifactService({"brief": b"brief"}))
    projection = await owner.prepare_input_projection(
        attachment_refs=("artifact://brief",), **options()
    )
    workspace = tmp_path / "repo"
    workspace.mkdir()
    target = workspace / projection["entries"][0]["path"]
    watched, _, _, _ = directory_ownership
    watched.update((target.parent, target.parent.parent))
    outside = tmp_path / "outside"
    (outside / "attachments").mkdir(parents=True)
    sentinel = outside / "untouched"
    sentinel.write_bytes(b"outside work")
    snapshot = {path: _mutation_state(path) for path in (outside, *outside.rglob("*"))}
    selected = workspace / swapped_parent
    moved = tmp_path / "detached"
    changed_inodes = []
    original_fstat = os.fstat

    def swap_then_handoff(fd, uid, gid):
        # Both descriptors have already been pinned. A path-based chown here
        # would act on the newly installed symlink's external destination.
        if not selected.is_symlink():
            selected.rename(moved)
            selected.symlink_to(outside, target_is_directory=True)
        changed_inodes.append(original_fstat(fd).st_ino)

    monkeypatch.setattr(os, "fchown", swap_then_handoff)
    with pytest.raises(WorkspaceArtifactProjectionError):
        await owner.project_bound_inputs(workspace, projection=projection, **options())
    assert len(changed_inodes) == 2
    assert not set(changed_inodes).intersection(state[0] for state in snapshot.values())
    assert {
        path: _mutation_state(path) for path in (outside, *outside.rglob("*"))
    } == snapshot
    assert sentinel.read_bytes() == b"outside work"
    assert not list(tmp_path.glob(".moonmind-import-*"))
