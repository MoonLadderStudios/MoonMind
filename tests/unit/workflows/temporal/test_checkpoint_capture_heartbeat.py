from __future__ import annotations

import asyncio
import subprocess
import tarfile
from io import BytesIO
from pathlib import Path

import pytest
from temporalio.testing import ActivityEnvironment

from moonmind.workflows.skills.artifact_store import InMemoryArtifactStore
from moonmind.workflows.temporal import activity_runtime


def _capture(tmp_path: Path):
    root = tmp_path / "workspaces"
    repo = root / "temporal_sandbox" / "repo"
    repo.mkdir(parents=True)
    for args in (
        ["init"],
        ["config", "user.name", "Checkpoint Test"],
        ["config", "user.email", "checkpoint@example.test"],
    ):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)
    (repo / "README.md").write_text("base\n")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True)
    subprocess.run(
        ["git", "commit", "-m", "base"], cwd=repo, check=True, capture_output=True
    )
    (repo / "README.md").write_text("saved candidate\n")
    store = InMemoryArtifactStore()
    activities = activity_runtime.TemporalSandboxActivities(
        workspace_root=root, artifact_store=store
    )
    request = {
        "identity": {
            "workflowId": "capture-workflow",
            "runId": "capture-run",
            "logicalStepId": "verify",
            "executionOrdinal": 1,
        },
        "boundary": "before_publication",
        "kind": "worktree_archive",
        "workspacePath": str(repo),
        "artifactNamespace": "checkpoint",
        "idempotencyKey": "capture-workflow:verify:before-publication",
    }
    return activities, request, store


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["archive", "upload"])
async def test_capture_heartbeats_while_checkpoint_work_is_pending(
    tmp_path: Path, monkeypatch, phase: str
) -> None:
    activities, request, store = _capture(tmp_path)
    heartbeat_seen = asyncio.Event()
    environment = ActivityEnvironment()
    environment.on_heartbeat = lambda *details: heartbeat_seen.set()
    monkeypatch.setattr(
        activity_runtime, "_SESSION_CONTROLLER_HEARTBEAT_INTERVAL_SECONDS", 0.01
    )
    name = "_capture_workspace_evidence" if phase == "archive" else "_put_checkpoint_bytes"
    original = getattr(activities, name)

    async def delayed(*args, **kwargs):
        heartbeat_seen.clear()
        await asyncio.wait_for(heartbeat_seen.wait(), timeout=1)
        return await original(*args, **kwargs)

    monkeypatch.setattr(activities, name, delayed)
    result = await environment.run(activities.workspace_capture_checkpoint, request)

    assert heartbeat_seen.is_set(), "pending checkpoint work must keep its Activity alive"
    assert result["status"] == "captured"
    with tarfile.open(
        fileobj=BytesIO(store.get_bytes(result["workspace"]["archiveRef"]))
    ) as archive:
        assert archive.extractfile("README.md").read() == b"saved candidate\n"


@pytest.mark.asyncio
async def test_capture_cancellation_stops_pending_checkpoint_work(
    tmp_path: Path, monkeypatch
) -> None:
    activities, request, store = _capture(tmp_path)
    heartbeat_seen = asyncio.Event()
    capture_started = asyncio.Event()
    stopped = asyncio.Event()
    environment = ActivityEnvironment()
    environment.on_heartbeat = lambda *details: heartbeat_seen.set()
    monkeypatch.setattr(
        activity_runtime, "_SESSION_CONTROLLER_HEARTBEAT_INTERVAL_SECONDS", 0.01
    )

    async def pending(*args, **kwargs):
        try:
            heartbeat_seen.clear()
            capture_started.set()
            await asyncio.Event().wait()
        finally:
            stopped.set()

    monkeypatch.setattr(activities, "_capture_workspace_evidence", pending)
    task = asyncio.create_task(
        environment.run(activities.workspace_capture_checkpoint, request)
    )
    try:
        await asyncio.wait_for(capture_started.wait(), timeout=1)
        await asyncio.wait_for(heartbeat_seen.wait(), timeout=1)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert stopped.is_set()
    assert not store._data
