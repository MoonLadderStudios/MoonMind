from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from moonmind.schemas.managed_session_models import CodexManagedSessionRecord
from moonmind.workflows.temporal.runtime.log_streamer import RuntimeLogStreamer
from moonmind.workflows.temporal.runtime.managed_session_store import (
    ManagedSessionStore,
)
from moonmind.workflows.temporal.runtime.managed_session_supervisor import (
    ManagedSessionSupervisor,
)


class _LocalArtifactStorage:
    def __init__(self, root: Path) -> None:
        self._root = root

    def write_artifact(
        self, *, job_id: str, artifact_name: str, data: bytes
    ) -> tuple[Path, str]:
        target_dir = self._root / job_id
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / artifact_name
        target.write_bytes(data)
        return target, f"{job_id}/{artifact_name}"

    def resolve_storage_path(self, ref: str) -> Path:
        return self._root / ref


def _record(tmp_path: Path) -> CodexManagedSessionRecord:
    workspace = tmp_path / "repo"
    session_workspace = tmp_path / "session"
    spool = tmp_path / "artifacts"
    workspace.mkdir()
    session_workspace.mkdir()
    spool.mkdir()
    return CodexManagedSessionRecord(
        sessionId="sess-1",
        sessionEpoch=1,
        taskRunId="task-1",
        containerId="ctr-1",
        threadId="thread-1",
        runtimeId="codex_cli",
        imageRef="ghcr.io/moonladderstudios/moonmind:latest",
        controlUrl="docker-exec://mm-codex-session-sess-1",
        status="ready",
        workspacePath=str(workspace),
        sessionWorkspacePath=str(session_workspace),
        artifactSpoolPath=str(spool),
        startedAt=datetime(2026, 4, 6, 12, 0, tzinfo=UTC),
    )


@pytest.mark.asyncio
async def test_session_supervisor_publishes_artifacts_and_offsets(tmp_path: Path) -> None:
    store = ManagedSessionStore(tmp_path / "store")
    artifact_storage = _LocalArtifactStorage(tmp_path / "published")
    supervisor = ManagedSessionSupervisor(
        store=store,
        log_streamer=RuntimeLogStreamer(artifact_storage),
        artifact_storage=artifact_storage,
        poll_interval_seconds=0.01,
    )
    record = _record(tmp_path)
    store.save(record)
    spool = Path(record.artifact_spool_path)

    await supervisor.start(record)
    (spool / "stdout.log").write_text("session started\nassistant: OK\n", encoding="utf-8")
    (spool / "stderr.log").write_text("warning: none\n", encoding="utf-8")
    await asyncio.sleep(0.05)
    watched = store.load("sess-1")

    finalized = await supervisor.finalize("sess-1", status="terminated")

    assert watched is not None
    assert watched.last_log_offset == len("session started\nassistant: OK\nwarning: none\n")
    assert watched.last_log_at is not None
    assert finalized.status == "terminated"
    assert finalized.stdout_artifact_ref == "sess-1/stdout.log"
    assert finalized.stderr_artifact_ref == "sess-1/stderr.log"
    assert finalized.diagnostics_ref == "sess-1/diagnostics.json"
    assert finalized.last_log_offset == len("session started\nassistant: OK\nwarning: none\n")
    assert finalized.last_log_at is not None
    assert artifact_storage.resolve_storage_path("sess-1/stdout.log").read_text(encoding="utf-8") == "session started\nassistant: OK\n"
    assert artifact_storage.resolve_storage_path("sess-1/stderr.log").read_text(encoding="utf-8") == "warning: none\n"


@pytest.mark.asyncio
async def test_publish_snapshot_keeps_watch_task_running(tmp_path: Path) -> None:
    store = ManagedSessionStore(tmp_path / "store")
    artifact_storage = _LocalArtifactStorage(tmp_path / "published")
    supervisor = ManagedSessionSupervisor(
        store=store,
        log_streamer=RuntimeLogStreamer(artifact_storage),
        artifact_storage=artifact_storage,
        poll_interval_seconds=0.01,
    )
    record = _record(tmp_path)
    store.save(record)
    spool = Path(record.artifact_spool_path)

    await supervisor.start(record)
    (spool / "stdout.log").write_text("first\n", encoding="utf-8")
    await asyncio.sleep(0.05)

    snapshot = await supervisor.publish_snapshot("sess-1")
    assert snapshot.stdout_artifact_ref == "sess-1/stdout.log"

    (spool / "stdout.log").write_text("first\nsecond\n", encoding="utf-8")
    await asyncio.sleep(0.05)
    watched = store.load("sess-1")

    assert watched is not None
    assert watched.last_log_offset == len("first\nsecond\n")

    await supervisor.finalize("sess-1", status="terminated")


@pytest.mark.asyncio
async def test_publish_reset_artifacts_writes_epoch_specific_control_and_boundary_refs(
    tmp_path: Path,
) -> None:
    store = ManagedSessionStore(tmp_path / "store")
    artifact_storage = _LocalArtifactStorage(tmp_path / "published")
    supervisor = ManagedSessionSupervisor(
        store=store,
        log_streamer=RuntimeLogStreamer(artifact_storage),
        artifact_storage=artifact_storage,
        poll_interval_seconds=0.01,
    )
    previous = _record(tmp_path)
    store.save(previous)
    current = previous.model_copy(
        update={
            "session_epoch": 2,
            "thread_id": "thread-2",
            "updated_at": datetime(2026, 4, 7, 8, 0, tzinfo=UTC),
        }
    )

    published = await supervisor.publish_reset_artifacts(
        previous_record=previous,
        record=current,
        action="clear_session",
        reason="reset stale context",
    )

    assert published.latest_control_event_ref == "sess-1/session.control_event.epoch-2.json"
    assert published.latest_checkpoint_ref == "sess-1/session.reset_boundary.epoch-2.json"

    control_payload = json.loads(
        artifact_storage.resolve_storage_path(
            "sess-1/session.control_event.epoch-2.json"
        ).read_text(encoding="utf-8")
    )
    boundary_payload = json.loads(
        artifact_storage.resolve_storage_path(
            "sess-1/session.reset_boundary.epoch-2.json"
        ).read_text(encoding="utf-8")
    )

    assert control_payload["linkType"] == "session.control_event"
    assert control_payload["action"] == "clear_session"
    assert control_payload["previousSessionEpoch"] == 1
    assert control_payload["newSessionEpoch"] == 2
    assert control_payload["previousThreadId"] == "thread-1"
    assert control_payload["newThreadId"] == "thread-2"
    assert control_payload["reason"] == "reset stale context"

    assert boundary_payload["linkType"] == "session.reset_boundary"
    assert boundary_payload["boundaryKind"] == "clear_session"
    assert boundary_payload["previousSessionEpoch"] == 1
    assert boundary_payload["sessionEpoch"] == 2
    assert boundary_payload["previousThreadId"] == "thread-1"
    assert boundary_payload["threadId"] == "thread-2"

    next_record = published.model_copy(
        update={
            "session_epoch": 3,
            "thread_id": "thread-3",
            "updated_at": datetime(2026, 4, 7, 8, 5, tzinfo=UTC),
        }
    )
    republished = await supervisor.publish_reset_artifacts(
        previous_record=published,
        record=next_record,
        action="clear_session",
        reason=None,
    )

    assert republished.latest_control_event_ref == "sess-1/session.control_event.epoch-3.json"
    assert republished.latest_checkpoint_ref == "sess-1/session.reset_boundary.epoch-3.json"
    assert artifact_storage.resolve_storage_path(
        "sess-1/session.control_event.epoch-2.json"
    ).exists()
    assert artifact_storage.resolve_storage_path(
        "sess-1/session.reset_boundary.epoch-2.json"
    ).exists()
