from __future__ import annotations

import asyncio
import hashlib
import io
import json
import subprocess
import tarfile
from pathlib import Path

import pytest
from pydantic import ValidationError

from moonmind.schemas.checkpoint_restore_models import (
    CheckpointRestoreError,
    ManagedWorkspaceRestoreRequest,
)
from moonmind.workflows.skills.artifact_store import InMemoryArtifactStore
from moonmind.workflows.temporal.activity_runtime import TemporalSandboxActivities
from moonmind.workflows.temporal.runtime.checkpoint_restore import (
    ManagedCheckpointRestoreService,
)


def _sha(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _repo(path: Path) -> tuple[Path, str]:
    path.mkdir(parents=True)
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"], cwd=path, check=True
    )
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True)
    (path / "tracked.txt").write_text("base\n")
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run(
        ["git", "commit", "-m", "base"], cwd=path, check=True, capture_output=True
    )
    commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=path, text=True
    ).strip()
    return path, commit


def _request(
    *, checkpoint_ref: str, capture: dict, base: str, key: str = "restore-key"
) -> dict:
    workspace = capture["workspace"]
    return {
        "schemaVersion": "v1",
        "recoveryIdentity": {
            "workflowId": "recovery",
            "runId": "recovery-run",
            "logicalStepId": "implement",
            "executionOrdinal": 2,
        },
        "source": {
            "workflowId": "source",
            "runId": "source-run",
            "logicalStepId": "implement",
            "executionOrdinal": 1,
            "checkpointRef": checkpoint_ref,
            "checkpointBoundary": "before_execution",
            "sourceWorkspaceLocator": {
                "kind": "managed_runtime",
                "runtimeId": "codex_cli",
                "agentRunId": "old-run",
                "relativePath": "repo",
            },
        },
        "checkpoint": {
            "kind": "worktree_archive",
            "baseCommit": base,
            "archiveRef": workspace["archiveRef"],
            "archiveDigest": workspace["archiveDigest"],
            "manifestRef": workspace["manifestRef"],
            "manifestDigest": workspace["manifestDigest"],
        },
        "destination": {
            "runtimeId": "codex_cli",
            "agentRunId": "new-run",
            "repository": "MoonLadderStudios/MoonMind",
            "relativePath": "repo",
        },
        "workspacePolicy": "restore_pre_execution",
        "resumePhase": "rerun_failed_step",
        "capabilitySetVersion": "runtime-execution-capabilities-v1",
        "capabilityDigest": "sha256:capability",
        "idempotencyKey": key,
    }


@pytest.mark.asyncio
async def test_cold_restore_survives_source_deletion_and_is_idempotent(
    tmp_path: Path,
) -> None:
    store = InMemoryArtifactStore()
    source, base = _repo(tmp_path / "temporal_sandbox" / "source")
    (source / "tracked.txt").write_text("changed\n")
    (source / "binary.bin").write_bytes(b"\x00\xff\x10")
    (source / "run.sh").write_text("#!/bin/sh\n")
    (source / "run.sh").chmod(0o755)
    (source / "safe-link").symlink_to("tracked.txt")
    sandbox = TemporalSandboxActivities(workspace_root=tmp_path, artifact_store=store)
    capture = await sandbox.workspace_capture_checkpoint(
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
            "baseCommit": base,
        }
    )
    checkpoint = {
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
    checkpoint_ref = store.put_bytes(
        json.dumps(checkpoint).encode(),
        content_type="application/vnd.moonmind.step-execution-checkpoint+json;version=1",
    ).artifact_ref
    request = _request(checkpoint_ref=checkpoint_ref, capture=capture, base=base)
    source.rename(tmp_path / "source-destroyed")
    service = ManagedCheckpointRestoreService(
        authority_root=tmp_path / "authority",
        artifact_store=store,
        repository_source_root=tmp_path / "source-destroyed",
    )

    first, second = await asyncio.gather(
        service.restore(request), service.restore(request)
    )

    restored = tmp_path / "authority" / "new-run" / "repo"
    assert first == second
    assert first["destinationWorkspaceLocator"]["agentRunId"] == "new-run"
    assert (restored / "tracked.txt").read_text() == "changed\n"
    assert (restored / "binary.bin").read_bytes() == b"\x00\xff\x10"
    assert (restored / "run.sh").stat().st_mode & 0o111
    assert (restored / "safe-link").readlink() == Path("tracked.txt")
    service.assert_ready_for_launch(
        agent_run_id="new-run",
        checkpoint_ref=checkpoint_ref,
        capability_digest="sha256:capability",
    )


def test_restore_contract_rejects_reused_identity_and_arbitrary_destination_path() -> (
    None
):
    base = {
        "schemaVersion": "v1",
        "recoveryIdentity": {
            "workflowId": "same",
            "runId": "same",
            "logicalStepId": "x",
            "executionOrdinal": 1,
        },
        "source": {
            "workflowId": "same",
            "runId": "same",
            "logicalStepId": "x",
            "executionOrdinal": 1,
            "checkpointRef": "a",
            "checkpointBoundary": "before_execution",
        },
        "checkpoint": {
            "kind": "worktree_archive",
            "baseCommit": "a",
            "archiveRef": "a",
            "archiveDigest": "sha256:" + "0" * 64,
            "manifestRef": "m",
            "manifestDigest": "sha256:" + "0" * 64,
        },
        "destination": {
            "runtimeId": "codex_cli",
            "agentRunId": "new",
            "repository": "owner/repo",
            "relativePath": "/tmp",
        },
        "workspacePolicy": "restore_pre_execution",
        "resumePhase": "rerun_failed_step",
        "capabilitySetVersion": "v1",
        "capabilityDigest": "d",
        "idempotencyKey": "k",
    }
    with pytest.raises(ValidationError):
        ManagedWorkspaceRestoreRequest.model_validate(base)


def test_secure_extractor_rejects_traversal_special_files_and_unsafe_symlink(
    tmp_path: Path,
) -> None:
    service = ManagedCheckpointRestoreService(
        authority_root=tmp_path, artifact_store=InMemoryArtifactStore()
    )
    cases = [
        ("../escape", tarfile.REGTYPE, ""),
        ("fifo", tarfile.FIFOTYPE, ""),
        ("link", tarfile.SYMTYPE, "../../escape"),
    ]
    for name, kind, link in cases:
        output = io.BytesIO()
        with tarfile.open(fileobj=output, mode="w:gz") as archive:
            info = tarfile.TarInfo(name)
            info.type = kind
            info.linkname = link
            archive.addfile(info, io.BytesIO(b"") if kind == tarfile.REGTYPE else None)
        manifest = {
            "entries": [
                {
                    "path": name,
                    "type": "symlink" if kind == tarfile.SYMTYPE else "file",
                    "target": link,
                    "digest": _sha(b""),
                    "mode": "0644",
                }
            ]
        }
        with pytest.raises(CheckpointRestoreError):
            service._extract(
                output.getvalue(), tmp_path / f"stage-{len(name)}", manifest
            )


def test_secure_extractor_enforces_limits_and_stable_failure_envelope(
    tmp_path: Path,
) -> None:
    service = ManagedCheckpointRestoreService(
        authority_root=tmp_path, artifact_store=InMemoryArtifactStore()
    )
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        info = tarfile.TarInfo("large.bin")
        info.size = 4
        info.mode = 0o644
        archive.addfile(info, io.BytesIO(b"data"))
    manifest = {
        "entries": [{
            "path": "large.bin", "type": "file", "digest": _sha(b"data"),
            "mode": "0644", "bytes": 4,
        }]
    }
    with pytest.raises(CheckpointRestoreError) as raised:
        service._extract(
            output.getvalue(), tmp_path / "stage-limit", manifest,
            max_entries=1, max_bytes=3,
        )
    assert raised.value.code == "CHECKPOINT_RESTORE_LIMIT_EXCEEDED"
    assert raised.value.failure_envelope["failureClass"] == "recovery_restoration"
    assert raised.value.failure_envelope["retryRecommendation"] == "do_not_retry"


@pytest.mark.asyncio
async def test_restore_rejects_incompatible_artifact_content_type(tmp_path: Path) -> None:
    store = InMemoryArtifactStore()
    checkpoint_ref = store.put_bytes(b"{}", content_type="application/json").artifact_ref
    service = ManagedCheckpointRestoreService(
        authority_root=tmp_path / "authority", artifact_store=store
    )
    capture = {"workspace": {
        "archiveRef": "artifact://archive", "archiveDigest": "sha256:" + "0" * 64,
        "manifestRef": "artifact://manifest", "manifestDigest": "sha256:" + "0" * 64,
    }}
    with pytest.raises(CheckpointRestoreError) as raised:
        await service.restore(_request(checkpoint_ref=checkpoint_ref, capture=capture, base="a"))
    assert raised.value.code == "CHECKPOINT_ARTIFACT_CONTENT_TYPE_MISMATCH"


@pytest.mark.asyncio
async def test_restore_rejects_digest_mismatch_and_idempotency_drift(
    tmp_path: Path,
) -> None:
    store = InMemoryArtifactStore()
    service = ManagedCheckpointRestoreService(
        authority_root=tmp_path / "authority", artifact_store=store
    )
    archive = store.put_bytes(
        b"bad", content_type="application/vnd.moonmind.worktree-archive"
    ).artifact_ref
    manifest = store.put_bytes(b"{}", content_type="application/json").artifact_ref
    capture = {
        "workspace": {
            "archiveRef": archive,
            "archiveDigest": _sha(b"expected"),
            "manifestRef": manifest,
            "manifestDigest": _sha(b"{}"),
        }
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
                "workspace": {
                    **capture["workspace"],
                    "kind": "worktree_archive",
                    "baseCommit": "deadbeef",
                },
            }
        ).encode(),
        content_type="application/vnd.moonmind.step-execution-checkpoint+json;version=1",
    ).artifact_ref
    request = _request(checkpoint_ref=checkpoint_ref, capture=capture, base="deadbeef")
    with pytest.raises(CheckpointRestoreError, match="CHECKPOINT_ARCHIVE_CORRUPTED"):
        await service.restore(request)
    drift = dict(request)
    drift["capabilityDigest"] = "changed"
    with pytest.raises(
        CheckpointRestoreError, match="CHECKPOINT_RESTORE_IDEMPOTENCY_CONFLICT"
    ):
        await service.restore(drift)


def _repo_with(path: Path, files: dict[str, str]) -> tuple[Path, str]:
    path.mkdir(parents=True)
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"], cwd=path, check=True
    )
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True)
    for name, contents in files.items():
        (path / name).write_text(contents)
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run(
        ["git", "commit", "-m", "base"], cwd=path, check=True, capture_output=True
    )
    commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=path, text=True
    ).strip()
    return path, commit


async def _capture_ref(
    tmp_path: Path, store: InMemoryArtifactStore, source: Path, base: str
) -> tuple[str, dict]:
    sandbox = TemporalSandboxActivities(workspace_root=tmp_path, artifact_store=store)
    capture = await sandbox.workspace_capture_checkpoint(
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
            "baseCommit": base,
        }
    )
    checkpoint = {
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
    checkpoint_ref = store.put_bytes(
        json.dumps(checkpoint).encode(),
        content_type="application/vnd.moonmind.step-execution-checkpoint+json;version=1",
    ).artifact_ref
    return checkpoint_ref, capture


@pytest.mark.asyncio
async def test_restore_replays_base_file_deletions(tmp_path: Path) -> None:
    """A file present at baseCommit but deleted in the worktree must not remain."""
    store = InMemoryArtifactStore()
    source, base = _repo_with(
        tmp_path / "temporal_sandbox" / "source",
        {"tracked.txt": "base\n", "removed.txt": "gone\n"},
    )
    (source / "tracked.txt").write_text("changed\n")
    (source / "removed.txt").unlink()
    checkpoint_ref, capture = await _capture_ref(tmp_path, store, source, base)
    request = _request(checkpoint_ref=checkpoint_ref, capture=capture, base=base)
    source.rename(tmp_path / "source-destroyed")
    service = ManagedCheckpointRestoreService(
        authority_root=tmp_path / "authority",
        artifact_store=store,
        repository_source_root=tmp_path / "source-destroyed",
    )

    await service.restore(request)

    restored = tmp_path / "authority" / "new-run" / "repo"
    assert (restored / "tracked.txt").read_text() == "changed\n"
    # The deleted tracked file from baseCommit is replayed away, not left behind.
    assert not (restored / "removed.txt").exists()


@pytest.mark.asyncio
async def test_restore_allowed_before_run_record_exists(tmp_path: Path) -> None:
    """Cold restore runs before agent_runtime.launch writes the run record."""
    store = InMemoryArtifactStore()
    source, base = _repo_with(
        tmp_path / "temporal_sandbox" / "source", {"tracked.txt": "base\n"}
    )
    (source / "tracked.txt").write_text("changed\n")
    checkpoint_ref, capture = await _capture_ref(tmp_path, store, source, base)
    request = _request(checkpoint_ref=checkpoint_ref, capture=capture, base=base)
    source.rename(tmp_path / "source-destroyed")

    class _RunStore:
        def load(self, agent_run_id: str):
            return None

    service = ManagedCheckpointRestoreService(
        authority_root=tmp_path / "authority",
        artifact_store=store,
        repository_source_root=tmp_path / "source-destroyed",
        run_store=_RunStore(),
    )

    result = await service.restore(request)
    assert result["destinationWorkspaceLocator"]["agentRunId"] == "new-run"


@pytest.mark.asyncio
async def test_restore_rejects_bound_run_record_mismatch(tmp_path: Path) -> None:
    """When a run record exists, its binding is still enforced."""
    from types import SimpleNamespace

    store = InMemoryArtifactStore()
    source, base = _repo_with(
        tmp_path / "temporal_sandbox" / "source", {"tracked.txt": "base\n"}
    )
    (source / "tracked.txt").write_text("changed\n")
    checkpoint_ref, capture = await _capture_ref(tmp_path, store, source, base)
    request = _request(checkpoint_ref=checkpoint_ref, capture=capture, base=base)
    source.rename(tmp_path / "source-destroyed")

    class _RunStore:
        def load(self, agent_run_id: str):
            return SimpleNamespace(
                runtime_id="other", workflow_id="recovery", workspace_path="/nope"
            )

    service = ManagedCheckpointRestoreService(
        authority_root=tmp_path / "authority",
        artifact_store=store,
        repository_source_root=tmp_path / "source-destroyed",
        run_store=_RunStore(),
    )

    with pytest.raises(
        CheckpointRestoreError, match="CHECKPOINT_DESTINATION_IDENTITY_MISMATCH"
    ):
        await service.restore(request)


@pytest.mark.asyncio
async def test_restore_rejects_non_object_checkpoint(tmp_path: Path) -> None:
    store = InMemoryArtifactStore()
    checkpoint_ref = store.put_bytes(
        json.dumps([1, 2, 3]).encode(),
        content_type="application/vnd.moonmind.step-execution-checkpoint+json;version=1",
    ).artifact_ref
    service = ManagedCheckpointRestoreService(
        authority_root=tmp_path / "authority", artifact_store=store
    )
    capture = {
        "workspace": {
            "archiveRef": "artifact://archive",
            "archiveDigest": "sha256:" + "0" * 64,
            "manifestRef": "artifact://manifest",
            "manifestDigest": "sha256:" + "0" * 64,
        }
    }
    with pytest.raises(CheckpointRestoreError, match="CHECKPOINT_MANIFEST_CORRUPTED"):
        await service.restore(
            _request(checkpoint_ref=checkpoint_ref, capture=capture, base="a")
        )


def test_extract_rejects_invalid_manifest_mode(tmp_path: Path) -> None:
    service = ManagedCheckpointRestoreService(
        authority_root=tmp_path, artifact_store=InMemoryArtifactStore()
    )
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        info = tarfile.TarInfo("f.txt")
        info.size = 1
        info.mode = 0o644
        archive.addfile(info, io.BytesIO(b"x"))
    manifest = {
        "entries": [
            {"path": "f.txt", "type": "file", "digest": _sha(b"x"), "mode": "not-octal"}
        ]
    }
    with pytest.raises(CheckpointRestoreError) as raised:
        service._extract(output.getvalue(), tmp_path / "stage-mode", manifest)
    assert raised.value.code == "CHECKPOINT_MANIFEST_CORRUPTED"


def test_extract_replaces_base_symlink_without_following_it(tmp_path: Path) -> None:
    service = ManagedCheckpointRestoreService(
        authority_root=tmp_path, artifact_store=InMemoryArtifactStore()
    )
    staging = tmp_path / "stage-symlink-conflict"
    staging.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("original\n")
    # Simulate the base checkout having materialized a symlink at this path that
    # points outside the workspace; the checkpoint turns it into a regular file.
    (staging / "f.txt").symlink_to(outside)
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        info = tarfile.TarInfo("f.txt")
        info.size = 4
        info.mode = 0o644
        archive.addfile(info, io.BytesIO(b"data"))
    manifest = {
        "entries": [
            {"path": "f.txt", "type": "file", "digest": _sha(b"data"), "mode": "0644"}
        ]
    }

    service._extract(output.getvalue(), staging, manifest)

    assert not (staging / "f.txt").is_symlink()
    assert (staging / "f.txt").read_bytes() == b"data"
    # Writing must not have followed the symlink and clobbered the outside target.
    assert outside.read_text() == "original\n"


def test_verify_materialized_keeps_directory_symlinks(tmp_path: Path) -> None:
    service = ManagedCheckpointRestoreService(
        authority_root=tmp_path, artifact_store=InMemoryArtifactStore()
    )
    staging = tmp_path / "stage-dirlink"
    staging.mkdir()
    (staging / "realdir").mkdir()
    (staging / "link").symlink_to("realdir")
    manifest = {"entries": [{"path": "link", "type": "symlink", "target": "realdir"}]}
    # A symlink pointing at a directory is a manifest leaf; it must not be
    # filtered out of the materialized path set.
    service._verify_materialized(staging, manifest)


def test_assert_ready_skips_corrupted_records(tmp_path: Path) -> None:
    service = ManagedCheckpointRestoreService(
        authority_root=tmp_path / "authority", artifact_store=InMemoryArtifactStore()
    )
    records = tmp_path / "authority" / "managed_restores"
    records.mkdir(parents=True)
    (records / "corrupt.json").write_text("{ not valid json")
    (records / "empty.json").write_text("")
    (records / "valid.json").write_text(
        json.dumps(
            {
                "status": "ready",
                "agentRunId": "run-1",
                "capabilityDigest": "cap",
                "result": {"checkpointRef": "ckpt"},
            }
        )
    )
    # Corrupted sibling records must not crash the launch gate.
    service.assert_ready_for_launch(
        agent_run_id="run-1", checkpoint_ref="ckpt", capability_digest="cap"
    )


@pytest.mark.asyncio
async def test_restore_handler_maps_checkpoint_error_to_typed_application_error() -> None:
    from temporalio import exceptions as temporal_exceptions

    from moonmind.workflows.temporal.activity_runtime import (
        TemporalAgentRuntimeActivities,
    )

    activities = object.__new__(TemporalAgentRuntimeActivities)

    class _FailingRestore:
        def __init__(self, code: str) -> None:
            self._code = code

        async def restore(self, request):
            raise CheckpointRestoreError(self._code, "boom")

    activities._checkpoint_restore = _FailingRestore("CHECKPOINT_ARCHIVE_CORRUPTED")
    with pytest.raises(temporal_exceptions.ApplicationError) as raised:
        await activities.agent_runtime_restore_workspace_checkpoint({})
    assert raised.value.type == "CHECKPOINT_ARCHIVE_CORRUPTED"
    assert raised.value.non_retryable is True

    # A retry-recommended failure code stays retryable.
    activities._checkpoint_restore = _FailingRestore("CHECKPOINT_ARTIFACT_MISSING")
    with pytest.raises(temporal_exceptions.ApplicationError) as retryable:
        await activities.agent_runtime_restore_workspace_checkpoint({})
    assert retryable.value.type == "CHECKPOINT_ARTIFACT_MISSING"
    assert retryable.value.non_retryable is False
