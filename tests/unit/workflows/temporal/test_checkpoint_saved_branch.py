"""Canonical sandbox saves keep their branch without reinterpreting old saves."""

import json
import subprocess

import pytest

from moonmind.schemas.checkpoint_restore_models import CheckpointRestoreError
from moonmind.workflows.skills.artifact_store import InMemoryArtifactStore
from moonmind.workflows.temporal import activity_runtime
from moonmind.workflows.temporal.runtime.checkpoint_restore import (
    ManagedCheckpointRestoreService,
)
from tests.artifact_access import access_for_test
from tests.unit.workflows.temporal.test_checkpoint_restore import _repo, _request, _sha


def git(path, *args):
    return (
        subprocess.check_output(
            ["git", "-C", str(path), *args], stderr=subprocess.DEVNULL
        )
        .decode()
        .strip()
    )


async def capture_workspace(tmp_path, *, detached=False):
    source, head = _repo(tmp_path / "temporal_sandbox" / "source")
    git(source, "checkout", "-b", "saved/accepted-work")
    if detached:
        git(source, "checkout", "--detach", head)
    store = InMemoryArtifactStore()
    capture = await activity_runtime.TemporalSandboxActivities(
        workspace_root=tmp_path, artifact_store=store
    ).workspace_capture_checkpoint(
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
    return source, head, store, capture


def restore_request(store, capture, head, manifest):
    body = json.dumps(manifest).encode()
    capture["workspace"]["manifestRef"] = store.put_bytes(
        body, content_type="application/json"
    ).artifact_ref
    capture["workspace"]["manifestDigest"] = _sha(body)
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
    return _request(checkpoint_ref=checkpoint_ref, capture=capture, base=head)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "shape", ["attached", "detached", "retained-sandbox", "retained-managed"]
)
async def test_checkpoint_saved_branch_and_retained_detachment(tmp_path, shape):
    _source, head, store, capture = await capture_workspace(
        tmp_path, detached=shape == "detached"
    )
    manifest = json.loads(store.get_bytes(capture["workspace"]["manifestRef"]))
    if shape in {"attached", "detached"}:
        assert manifest["git"]["branch"] == (
            "" if shape == "detached" else "saved/accepted-work"
        )
    if shape == "retained-sandbox":
        manifest["git"].pop("branch", None)
    elif shape == "retained-managed":
        manifest.pop("kind")
        manifest["contentType"] = (
            "application/vnd.moonmind.managed-workspace-checkpoint-manifest+json;version=1"
        )
        manifest["git"].update(baseCommit=head, branch="saved/accepted-work")
        for entry in manifest["entries"]:
            entry["sha256"] = entry.pop("digest")
        manifest["archive"] = {
            "ref": manifest["archiveRef"],
            "sha256": manifest["archiveDigest"],
        }
    request = restore_request(store, capture, head, manifest)
    service = ManagedCheckpointRestoreService(
        authority_root=tmp_path / "authority", artifact_store=store
    )
    first = await service.restore(request, access=access_for_test("workflow:source"))
    restored = service.root / "new-run" / "repo"
    assert git(restored, "rev-parse", "HEAD") == head
    assert git(restored, "branch", "--show-current") == (
        "saved/accepted-work" if shape == "attached" else ""
    )
    assert (
        await service.restore(request, access=access_for_test("workflow:source"))
        == first
    )
    if shape in {"attached", "detached"}:
        git(restored, "checkout", "-b", "different-branch")
        with pytest.raises(
            CheckpointRestoreError, match="CHECKPOINT_ENTRY_DIGEST_MISMATCH"
        ):
            await service.restore(request, access=access_for_test("workflow:source"))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "branch",
    ["--orphan", "refs/heads/../escape", 42],
    ids=["option-like", "invalid-ref", "wrong-type"],
)
async def test_invalid_saved_branch_fails_before_destination_replacement(
    tmp_path, branch
):
    _source, head, store, capture = await capture_workspace(tmp_path)
    manifest = json.loads(store.get_bytes(capture["workspace"]["manifestRef"]))
    manifest["git"]["branch"] = branch
    request = restore_request(store, capture, head, manifest)
    service = ManagedCheckpointRestoreService(
        authority_root=tmp_path / "authority", artifact_store=store
    )
    with pytest.raises(CheckpointRestoreError, match="CHECKPOINT_MANIFEST_CORRUPTED"):
        await service.restore(request, access=access_for_test("workflow:source"))
    assert not (service.root / "new-run" / "repo").exists()


@pytest.mark.asyncio
async def test_capture_rejects_branch_switch_at_same_head(tmp_path, monkeypatch):
    real_command = activity_runtime._run_command
    reads = 0

    async def switch_after_read(command, **kwargs):
        nonlocal reads
        result = await real_command(command, **kwargs)
        if command[-2:] == ["branch", "--show-current"]:
            reads += 1
            if reads == 1:
                source = tmp_path / "temporal_sandbox" / "source"
                git(source, "checkout", "-b", "concurrent-branch")
        return result

    monkeypatch.setattr(activity_runtime, "_run_command", switch_after_read)
    with pytest.raises(
        activity_runtime.TemporalActivityRuntimeError, match="workspace changed"
    ):
        await capture_workspace(tmp_path)
