"""Bound input additions never relax the retained checkpoint's identity."""

import hashlib
import json
import os
import shutil
import subprocess

import pytest

from moonmind.schemas.checkpoint_restore_models import CheckpointRestoreError
from moonmind.workflows.skills.artifact_store import InMemoryArtifactStore
from moonmind.workflows.temporal.activity_runtime import TemporalSandboxActivities
from moonmind.workflows.temporal.runtime.checkpoint_restore import (
    ManagedCheckpointRestoreService,
)
from moonmind.workflows.temporal.runtime.workspace_locators import (
    SandboxWorkspaceRecord,
    SandboxWorkspaceRecordStore,
)
from tests.artifact_access import access_for_test
from tests.unit.workflows.temporal.test_checkpoint_restore import _repo, _request, _sha


def _git(workspace, *args):
    return (
        subprocess.check_output(["git", "-C", str(workspace), *args]).decode().strip()
    )


def _projection():
    return {
        "version": "input-projection-v1",
        "entries": [
            {
                "path": f".moonmind/{kind}/"
                + hashlib.sha256(ref.encode()).hexdigest()[:24],
                "ref": ref,
                "digest": _sha(payload),
                "bytes": len(payload),
                "mode": 0o400,
                "uid": os.getuid(),
                "gid": os.getgid(),
            }
            for kind, ref, payload in (
                ("restore", "artifact://restore", b"restore input"),
                ("attachments", "artifact://attachment", b"attachment input"),
            )
        ],
    }


def _write_projection(workspace, projection, count=2):
    for item, payload in zip(
        projection["entries"][:count], (b"restore input", b"attachment input")
    ):
        path = workspace / item["path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            continue
        path.write_bytes(payload)
        path.chmod(item["mode"])


async def _candidate(
    tmp_path, *, ignored=False, collision=None, cold=False, saved_projection=None
):
    source, _ = _repo(tmp_path / "temporal_sandbox" / "source")
    _git(source, "checkout", "-b", "saved-work")
    (source / "deleted.txt").write_text("deleted baseline")
    if ignored:
        (source / ".gitignore").write_text(".moonmind/\n")
    projection = _projection()
    if saved_projection == "tracked":
        _write_projection(source, projection, count=1)
    if collision and collision != "index_only":
        name = projection["entries"][0]["path"]
        if collision == "ancestor":
            name = ".moonmind"
        elif collision == "directory":
            name += "/nested.txt"
        target = source / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if collision == "symlink":
            target.symlink_to("../../tracked.txt")
        else:
            target.write_text("saved collision")
    _git(source, "add", "-f", ".")
    _git(source, "commit", "-qm", "baseline")
    base = _git(source, "rev-parse", "HEAD")
    if saved_projection == "untracked":
        _write_projection(source, projection, count=1)
    if collision == "index_only":
        _write_projection(source, projection, count=1)
        target = source / projection["entries"][0]["path"]
        _git(source, "add", str(target))
        target.unlink()
    (source / "deleted.txt").unlink()
    (source / "tracked.txt").write_text("staged work\n")
    _git(source, "add", "tracked.txt")
    (source / "tracked.txt").write_text("unstaged work\n")
    if collision in {"deleted", "staged_deleted"}:
        target.unlink()
        if collision == "staged_deleted":
            _git(source, "add", "-u")
    store = InMemoryArtifactStore()
    capture = await TemporalSandboxActivities(
        workspace_root=tmp_path, artifact_store=store
    ).workspace_capture_checkpoint(
        {
            "identity": {
                "workflowId": "source",
                "runId": "source-run",
                "logicalStepId": "implement",
                "executionOrdinal": 1,
            },
            "boundary": "after_execution",
            "kind": "worktree_archive",
            "workspacePath": str(source),
            "artifactNamespace": "checkpoint",
            "idempotencyKey": "capture",
            "includeUntracked": True,
        }
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
                "boundary": "after_execution",
                "workspace": capture["workspace"],
            }
        ).encode(),
        content_type="application/vnd.moonmind.step-execution-checkpoint+json;version=1",
    ).artifact_ref
    request = _request(checkpoint_ref=checkpoint_ref, capture=capture, base=base)
    authority = tmp_path / "authority"
    workspace_id = hashlib.sha256(b"recovery:implement-2").hexdigest()[:24]
    SandboxWorkspaceRecordStore(authority).ensure(
        SandboxWorkspaceRecord(workspace_id, "recovery", "implement-2", "repo")
    )
    request["destination"] = {
        "kind": "sandbox",
        "workspaceId": workspace_id,
        "stepExecutionId": "implement-2",
    }
    request["source"].update(
        checkpointBoundary="after_execution",
        sourceWorkspaceLocator={
            "kind": "sandbox",
            "workspaceId": "source",
            "relativePath": "repo",
        },
    )
    request["workspacePolicy"] = "restore_step_input"
    workspace = authority / "temporal_sandbox" / workspace_id / "repo"
    service = ManagedCheckpointRestoreService(
        authority_root=authority, artifact_store=store, repository_source_root=source
    )
    result = (
        None
        if cold
        else await service.restore(request, access=access_for_test("workflow:source"))
    )
    return service, request, workspace, projection, result


@pytest.mark.asyncio
@pytest.mark.parametrize("ignored", [False, True])
async def test_bound_projection_redelivery_preserves_receipt_and_partial_additions(
    tmp_path, ignored
):
    service, request, workspace, projection, result = await _candidate(
        tmp_path, ignored=ignored
    )
    receipt = next((service.root / "managed_restores").glob("*.json"))
    original_receipt = receipt.read_bytes()
    await service.validate_input_projection(
        request,
        access=access_for_test("workflow:source"),
        admitted_projection=projection,
    )
    for count in (0, 1, 2):
        _write_projection(workspace, projection, count)
        assert (
            await service.restore(
                request,
                access=access_for_test("workflow:source"),
                admitted_projection=projection,
            )
            == result
        )
        assert receipt.read_bytes() == original_receipt
    with pytest.raises(CheckpointRestoreError):
        await service.restore(request, access=access_for_test("workflow:source"))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault",
    [
        "bytes",
        "size",
        "mode",
        "uid",
        "gid",
        "leaf_symlink",
        "parent_symlink",
        "unknown",
        "temporary",
        "baseline",
        "index",
        "staged_input",
        "head",
        "branch",
        "special_mode",
        "fifo",
    ],
)
async def test_bound_projection_rejects_changed_or_unknown_candidate(tmp_path, fault):
    service, request, workspace, projection, _ = await _candidate(
        tmp_path, ignored=True
    )
    _write_projection(workspace, projection)
    target = workspace / projection["entries"][0]["path"]
    if fault == "bytes":
        target.chmod(0o600)
        target.write_bytes(b"changed input")
        target.chmod(0o400)
    elif fault in {"size", "uid", "gid"}:
        projection["entries"][0]["bytes" if fault == "size" else fault] += 1
    elif fault == "mode":
        target.chmod(0o600)
    elif fault == "leaf_symlink":
        target.unlink()
        target.symlink_to(workspace / "tracked.txt")
    elif fault == "parent_symlink":
        target.parent.rename(workspace / "elsewhere")
        target.parent.symlink_to(workspace / "elsewhere", target_is_directory=True)
    elif fault in {"unknown", "temporary"}:
        (
            target.parent / ("unexpected" if fault == "unknown" else ".input-crashed")
        ).write_bytes(b"unknown")
    elif fault == "baseline":
        (workspace / "tracked.txt").write_text("changed baseline")
    elif fault == "index":
        _git(workspace, "add", "tracked.txt")
    elif fault == "staged_input":
        _git(workspace, "add", "-f", str(target))
    elif fault == "special_mode":
        target.chmod(0o4400)
    elif fault == "fifo":
        target.unlink()
        os.mkfifo(target)
    elif fault == "head":
        _git(
            workspace,
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "--allow-empty",
            "-qm",
            "changed head",
        )
    elif fault == "branch":
        _git(workspace, "branch", "-m", "unadmitted-branch")
    with pytest.raises(CheckpointRestoreError):
        await service.restore(
            request,
            access=access_for_test("workflow:source"),
            admitted_projection=projection,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "collision",
    [
        "leaf",
        "ancestor",
        "directory",
        "deleted",
        "staged_deleted",
        "symlink",
        "index_only",
    ],
)
async def test_projection_collisions_rejected_before_input_writes(tmp_path, collision):
    service, request, workspace, projection, _ = await _candidate(
        tmp_path, collision=collision
    )
    before = _git(workspace, "status", "--porcelain=v1")
    with pytest.raises(CheckpointRestoreError):
        await service.validate_input_projection(
            request,
            access=access_for_test("workflow:source"),
            admitted_projection=projection,
        )
    assert _git(workspace, "status", "--porcelain=v1") == before


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault", ["version", "duplicate", "path", "ref", "digest", "mode", "negative_bytes"]
)
async def test_projection_descriptor_is_narrow_and_fail_closed(tmp_path, fault):
    service, request, _, projection, _ = await _candidate(tmp_path)
    if fault == "version":
        projection["version"] = "other"
    elif fault == "duplicate":
        projection["entries"].append(dict(projection["entries"][0]))
    elif fault == "negative_bytes":
        projection["entries"][0]["bytes"] = -1
    else:
        projection["entries"][0][fault] = {
            "path": ".moonmind/restore/.input-unbound",
            "ref": "artifact://different",
            "digest": "sha256:bad",
            "mode": 0o600,
        }[fault]
    with pytest.raises(CheckpointRestoreError):
        await service.restore(
            request,
            access=access_for_test("workflow:source"),
            admitted_projection=projection,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["managed", "pre_execution", "original_owner"])
async def test_projection_authority_cannot_relax_other_restore_paths(tmp_path, kind):
    service, request, workspace, projection, _ = await _candidate(tmp_path, cold=True)
    if kind == "managed":
        request["destination"] = {
            "runtimeId": "codex_cli",
            "agentRunId": "new-run",
            "repository": "MoonLadderStudios/MoonMind",
        }
        request["workspacePolicy"] = "restore_publication_candidate"
    elif kind == "pre_execution":
        request["workspacePolicy"] = "restore_pre_execution"
    elif kind == "original_owner":
        request["workspacePolicy"] = "restore_publication_candidate"
        request["resumePhase"] = "resume_publication"
        request["recoveryIdentity"] = {
            key: request["source"][key]
            for key in ("workflowId", "runId", "logicalStepId", "executionOrdinal")
        }
        request["source"]["sourceWorkspaceLocator"]["workspaceId"] = request[
            "destination"
        ]["workspaceId"]
    with pytest.raises(
        CheckpointRestoreError, match="CHECKPOINT_DESTINATION_IDENTITY_MISMATCH"
    ):
        await service.restore(
            request,
            access=access_for_test("workflow:source"),
            admitted_projection=projection,
        )
    assert not workspace.exists()


@pytest.mark.asyncio
async def test_bound_projection_keeps_explicit_checkpoint_branch_admission(tmp_path):
    service, request, workspace, projection, result = await _candidate(tmp_path)
    _write_projection(workspace, projection)
    _git(workspace, "branch", "-m", "admitted-target")
    assert (
        await service.restore(
            request,
            access=access_for_test("workflow:source"),
            admitted_projection=projection,
            admitted_target_branch="admitted-target",
        )
        == result
    )


def test_status_normalization_only_removes_verified_untracked_records():
    path = ".moonmind/restore/" + "a" * 24
    record = b"?? " + path.encode()
    retained = (
        b"R  destination\0" + record + b"\0 M " + path.encode() + b"\0?? unknown\0"
    )
    assert (
        ManagedCheckpointRestoreService._checkpoint_status(
            retained + record + b"\0", frozenset({path})
        )
        == retained
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "loss", ["first_restore", "workspace", "receipt", "preparing_receipt"]
)
async def test_bound_projection_does_not_weaken_or_block_cold_recovery(tmp_path, loss):
    service, request, workspace, projection, result = await _candidate(
        tmp_path, cold=loss == "first_restore"
    )
    if loss == "workspace":
        shutil.rmtree(workspace)
    elif loss in {"receipt", "preparing_receipt"}:
        _write_projection(workspace, projection)
        (workspace / "unknown").write_text("unverified candidate")
        receipt = next((service.root / "managed_restores").glob("*.json"))
        if loss == "receipt":
            receipt.unlink()
        else:
            record = json.loads(receipt.read_bytes())
            record["status"] = "preparing_restore"
            receipt.write_text(json.dumps(record))
    restored = await service.restore(
        request,
        access=access_for_test("workflow:source"),
        admitted_projection=projection,
    )
    assert restored["baseCommit"] == request["checkpoint"]["baseCommit"]
    if result:
        assert restored["gitStatusDigest"] == result["gitStatusDigest"]
    assert (workspace / "tracked.txt").read_text() == "unstaged work\n"
    assert not (workspace / "unknown").exists()
    assert not any(
        (workspace / item["path"]).exists() for item in projection["entries"]
    )
    await service.validate_input_projection(
        request,
        access=access_for_test("workflow:source"),
        admitted_projection=projection,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("saved_projection", [None, "untracked"])
async def test_projection_verification_never_reads_through_a_swapped_parent(
    tmp_path, monkeypatch, saved_projection
):
    from moonmind.workflows.temporal.runtime import checkpoint_restore

    service, request, workspace, projection, _ = await _candidate(
        tmp_path, saved_projection=saved_projection
    )
    _write_projection(workspace, projection)
    target = workspace / projection["entries"][0]["path"]
    outside = tmp_path / "private"
    outside.mkdir()
    private_bytes = b"private bytes"
    assert len(private_bytes) == projection["entries"][0]["bytes"]
    private_file = outside / target.name
    private_file.write_bytes(private_bytes)
    private_file.chmod(0o400)
    original_validate = service._validate_input_projection_paths
    original_digest = checkpoint_restore._digest
    private_reads = []

    def swap_after_validation(*args):
        reused = original_validate(*args)
        target.parent.rename(tmp_path / "saved-projection")
        target.parent.symlink_to(outside, target_is_directory=True)
        return reused

    def observe_digest(payload):
        if payload == private_bytes:
            private_reads.append(payload)
        return original_digest(payload)

    monkeypatch.setattr(
        service, "_validate_input_projection_paths", swap_after_validation
    )
    monkeypatch.setattr(checkpoint_restore, "_digest", observe_digest)
    with pytest.raises(CheckpointRestoreError):
        await service.restore(
            request,
            access=access_for_test("workflow:source"),
            admitted_projection=projection,
        )
    assert not private_reads


@pytest.mark.asyncio
@pytest.mark.parametrize("saved_projection", [None, "untracked"])
async def test_projection_verification_does_not_reread_unpinned_paths(
    tmp_path, monkeypatch, saved_projection
):
    from pathlib import Path

    service, request, workspace, projection, result = await _candidate(
        tmp_path, saved_projection=saved_projection
    )
    _write_projection(workspace, projection)
    projected = {workspace / item["path"] for item in projection["entries"]}
    original_read = Path.read_bytes

    def reject_projected_path_read(path):
        assert path not in projected, "projected files require descriptor-safe reads"
        return original_read(path)

    monkeypatch.setattr(Path, "read_bytes", reject_projected_path_read)
    assert (
        await service.restore(
            request,
            access=access_for_test("workflow:source"),
            admitted_projection=projection,
        )
        == result
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("saved_projection", ["tracked", "untracked"])
@pytest.mark.parametrize("runtime_owner_differs", [False, True])
async def test_exact_saved_projection_stays_baseline_owned(
    tmp_path, saved_projection, runtime_owner_differs
):
    service, request, workspace, projection, result = await _candidate(
        tmp_path, saved_projection=saved_projection
    )
    saved = projection["entries"][0]
    target = workspace / saved["path"]
    inode = target.stat().st_ino
    raw_status = service._git_bytes(
        ["status", "--porcelain=v1", "-z", "--untracked-files=all"], workspace
    )
    assert _sha(raw_status) == result["gitStatusDigest"]
    if saved_projection == "untracked":
        assert b"?? " + saved["path"].encode() + b"\0" in raw_status
    if runtime_owner_differs:
        # Cold checkpoint files can still have the restorer's ownership before
        # the materializer's handoff. Baseline ownership was never checkpointed.
        saved["uid"] += 1
        saved["gid"] += 1
    receipt = next((service.root / "managed_restores").glob("*.json"))
    original_receipt = receipt.read_bytes()
    assert await service.validate_input_projection(
        request,
        access=access_for_test("workflow:source"),
        admitted_projection=projection,
    ) == frozenset({saved["path"]})
    _write_projection(workspace, projection)
    assert (
        await service.restore(
            request,
            access=access_for_test("workflow:source"),
            admitted_projection=projection,
        )
        == result
    )
    assert target.stat().st_ino == inode
    assert target.read_bytes() == b"restore input"
    assert receipt.read_bytes() == original_receipt


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault",
    [
        "descriptor_digest",
        "descriptor_size",
        "bytes",
        "mode",
        "missing",
        "symlink",
        "index",
        "status",
    ],
)
async def test_saved_projection_reuse_never_hides_baseline_drift(tmp_path, fault):
    service, request, workspace, projection, _ = await _candidate(
        tmp_path, saved_projection="untracked"
    )
    saved = projection["entries"][0]
    target = workspace / saved["path"]
    if fault == "descriptor_digest":
        saved["digest"] = _sha(b"changed input")
    elif fault == "descriptor_size":
        saved["bytes"] += 1
    elif fault == "bytes":
        target.chmod(0o600)
        target.write_bytes(b"changed input")
        target.chmod(0o400)
        saved["digest"] = _sha(b"changed input")
    elif fault == "mode":
        target.chmod(0o600)
    elif fault in {"missing", "symlink"}:
        target.unlink()
        if fault == "symlink":
            target.symlink_to(workspace / "tracked.txt")
    elif fault == "index":
        _git(workspace, "add", str(target))
    elif fault == "status":
        # Hiding a formerly untracked baseline file must still change the raw
        # checkpoint status, even though its current bytes are unchanged.
        (workspace / ".git/info/exclude").write_text("/.moonmind/restore/\n")
    with pytest.raises(CheckpointRestoreError):
        await service.restore(
            request,
            access=access_for_test("workflow:source"),
            admitted_projection=projection,
        )
    if fault not in {"index", "status"}:
        with pytest.raises(CheckpointRestoreError):
            await service.validate_input_projection(
                request,
                access=access_for_test("workflow:source"),
                admitted_projection=projection,
            )


@pytest.mark.asyncio
@pytest.mark.parametrize("size_key", ["bytes", "size", None])
async def test_saved_projection_reuse_verifies_manifest_and_actual_size(
    tmp_path, size_key
):
    service, request, workspace, projection, _ = await _candidate(
        tmp_path, saved_projection="untracked"
    )
    manifest = service._normalize_manifest(
        json.loads(
            service.artifact_store.get_bytes(request["checkpoint"]["manifestRef"])
        )
    )
    saved = next(
        entry
        for entry in manifest["entries"]
        if entry["path"] == projection["entries"][0]["path"]
    )
    saved.pop("bytes", None)
    if size_key:
        saved[size_key] = projection["entries"][0]["bytes"]
    entries = {entry["path"]: entry for entry in projection["entries"]}
    assert service._validate_input_projection_paths(
        workspace, manifest, entries
    ) == frozenset({saved["path"]})
    if size_key:
        saved[size_key] += 1
        with pytest.raises(CheckpointRestoreError):
            service._validate_input_projection_paths(workspace, manifest, entries)
    else:
        entries[saved["path"]]["bytes"] += 1
        with pytest.raises(CheckpointRestoreError):
            service._validate_input_projection_paths(workspace, manifest, entries)


@pytest.mark.asyncio
async def test_different_prior_input_path_remains_saved_baseline_work(tmp_path):
    service, request, workspace, projection, result = await _candidate(
        tmp_path, saved_projection="untracked"
    )
    previous_path = workspace / projection["entries"][0]["path"]
    previous_inode = previous_path.stat().st_ino
    current = projection["entries"][0]
    current["ref"] = "artifact://new-restore"
    current["path"] = (
        ".moonmind/restore/" + hashlib.sha256(current["ref"].encode()).hexdigest()[:24]
    )
    await service.validate_input_projection(
        request,
        access=access_for_test("workflow:source"),
        admitted_projection=projection,
    )
    _write_projection(workspace, projection)
    assert (
        await service.restore(
            request,
            access=access_for_test("workflow:source"),
            admitted_projection=projection,
        )
        == result
    )
    assert previous_path.stat().st_ino == previous_inode
    assert previous_path.read_bytes() == b"restore input"


@pytest.mark.asyncio
async def test_saved_projection_special_mode_is_not_exact_reuse(tmp_path):
    service, request, workspace, projection, _ = await _candidate(
        tmp_path, saved_projection="untracked"
    )
    manifest = service._normalize_manifest(
        json.loads(
            service.artifact_store.get_bytes(request["checkpoint"]["manifestRef"])
        )
    )
    saved = next(
        entry
        for entry in manifest["entries"]
        if entry["path"] == projection["entries"][0]["path"]
    )
    saved["mode"] = "4400"
    with pytest.raises(CheckpointRestoreError):
        service._validate_input_projection_paths(
            workspace,
            manifest,
            {entry["path"]: entry for entry in projection["entries"]},
        )
