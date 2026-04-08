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
    assert finalized.latest_summary_ref == "sess-1/session.summary.json"
    assert finalized.latest_checkpoint_ref == "sess-1/session.step_checkpoint.json"
    assert finalized.last_log_offset == len("session started\nassistant: OK\nwarning: none\n")
    assert finalized.last_log_at is not None
    assert artifact_storage.resolve_storage_path("sess-1/stdout.log").read_text(encoding="utf-8") == "session started\nassistant: OK\n"
    assert artifact_storage.resolve_storage_path("sess-1/stderr.log").read_text(encoding="utf-8") == "warning: none\n"
    assert artifact_storage.resolve_storage_path("sess-1/session.summary.json").exists()
    assert artifact_storage.resolve_storage_path("sess-1/session.step_checkpoint.json").exists()


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
    assert snapshot.latest_summary_ref == "sess-1/session.summary.json"
    assert snapshot.latest_checkpoint_ref == "sess-1/session.step_checkpoint.json"

    (spool / "stdout.log").write_text("first\nsecond\n", encoding="utf-8")
    await asyncio.sleep(0.05)
    watched = store.load("sess-1")

    assert watched is not None
    assert watched.last_log_offset == len("first\nsecond\n")

    await supervisor.finalize("sess-1", status="terminated")


@pytest.mark.asyncio
async def test_publish_snapshot_persists_run_keyed_session_events(tmp_path: Path) -> None:
    store = ManagedSessionStore(tmp_path / "store")
    artifact_storage = _LocalArtifactStorage(tmp_path / "published")
    log_streamer = RuntimeLogStreamer(artifact_storage)
    supervisor = ManagedSessionSupervisor(
        store=store,
        log_streamer=log_streamer,
        artifact_storage=artifact_storage,
        poll_interval_seconds=0.01,
    )
    record = _record(tmp_path)
    store.save(record)

    supervisor.emit_session_event(
        record=record,
        text="Session cleared.",
        kind="session_cleared",
        metadata={"reason": "operator_reset"},
    )

    snapshot = await supervisor.publish_snapshot("sess-1")
    diagnostics_payload = json.loads(
        artifact_storage.resolve_storage_path(snapshot.diagnostics_ref).read_text(
            encoding="utf-8"
        )
    )

    assert diagnostics_payload["observability_events"][0]["stream"] == "session"
    assert diagnostics_payload["observability_events"][0]["kind"] == "session_cleared"
    assert diagnostics_payload["observability_events"][0]["metadata"]["reason"] == "operator_reset"
    assert snapshot.observability_events_ref == "sess-1/observability.events.jsonl"
    journal = artifact_storage.resolve_storage_path(snapshot.observability_events_ref)
    assert journal.exists()
    journal_text = journal.read_text(encoding="utf-8")
    assert '"stream":"session"' in journal_text
    assert '"kind":"summary_published"' in journal_text
    assert '"kind":"checkpoint_published"' in journal_text


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
    assert published.latest_reset_boundary_ref == "sess-1/session.reset_boundary.epoch-2.json"

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
    assert republished.latest_reset_boundary_ref == "sess-1/session.reset_boundary.epoch-3.json"
    assert artifact_storage.resolve_storage_path(
        "sess-1/session.control_event.epoch-2.json"
    ).exists()
    assert artifact_storage.resolve_storage_path(
        "sess-1/session.reset_boundary.epoch-2.json"
    ).exists()


@pytest.mark.asyncio
async def test_publish_reset_artifacts_preserves_newer_store_fields(tmp_path: Path) -> None:
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
    await store.update(
        "sess-1",
        session_epoch=2,
        thread_id="thread-2",
        last_log_offset=77,
        last_log_at=datetime(2026, 4, 7, 8, 1, tzinfo=UTC),
    )
    stale_current = previous.model_copy(
        update={
            "session_epoch": 2,
            "thread_id": "thread-2",
            "last_log_offset": None,
            "last_log_at": None,
        }
    )

    published = await supervisor.publish_reset_artifacts(
        previous_record=previous,
        record=stale_current,
        action="clear_session",
        reason=None,
    )

    assert published.last_log_offset == 77
    assert published.last_log_at == datetime(2026, 4, 7, 8, 1, tzinfo=UTC)
    assert published.latest_control_event_ref == "sess-1/session.control_event.epoch-2.json"
    assert published.latest_reset_boundary_ref == "sess-1/session.reset_boundary.epoch-2.json"


@pytest.mark.asyncio
async def test_publish_reset_artifacts_tolerates_event_publication_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
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

    def _raise(*args, **kwargs) -> None:
        raise RuntimeError("publisher unavailable")

    monkeypatch.setattr(supervisor._log_streamer, "emit_observability_event", _raise)

    published = await supervisor.publish_reset_artifacts(
        previous_record=previous,
        record=current,
        action="clear_session",
        reason="reset stale context",
    )

    assert published.latest_control_event_ref == "sess-1/session.control_event.epoch-2.json"
    assert published.latest_reset_boundary_ref == "sess-1/session.reset_boundary.epoch-2.json"
