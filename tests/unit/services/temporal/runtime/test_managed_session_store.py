from __future__ import annotations

from datetime import UTC, datetime

import pytest

from moonmind.schemas.managed_session_models import CodexManagedSessionRecord
from moonmind.workflows.temporal.runtime.managed_session_store import (
    ManagedSessionStore,
)

def _record() -> CodexManagedSessionRecord:
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
        workspacePath="/work/agent_jobs/task-1/repo",
        sessionWorkspacePath="/work/agent_jobs/task-1/session",
        artifactSpoolPath="/work/agent_jobs/task-1/artifacts",
        startedAt=datetime(2026, 4, 6, 12, 0, tzinfo=UTC),
    )

def test_store_round_trips_phase6_session_record(tmp_path) -> None:
    store = ManagedSessionStore(tmp_path)
    record = _record()

    path = store.save(record)
    loaded = store.load(record.session_id)

    assert path.name == "sess-1.json"
    assert loaded == record
    assert loaded.task_run_id == "task-1"
    assert loaded.container_id == "ctr-1"

@pytest.mark.asyncio
async def test_store_update_status_persists_artifact_refs_and_offsets(tmp_path) -> None:
    store = ManagedSessionStore(tmp_path)
    record = _record()
    store.save(record)

    updated = await store.update(
        "sess-1",
        status="terminated",
        stdout_artifact_ref="sess-1/stdout.log",
        stderr_artifact_ref="sess-1/stderr.log",
        diagnostics_ref="sess-1/diagnostics.json",
        last_log_offset=17,
        last_log_at=datetime(2026, 4, 6, 12, 5, tzinfo=UTC),
    )

    assert updated.status == "terminated"
    assert updated.stdout_artifact_ref == "sess-1/stdout.log"
    assert updated.stderr_artifact_ref == "sess-1/stderr.log"
    assert updated.diagnostics_ref == "sess-1/diagnostics.json"
    assert updated.last_log_offset == 17
    assert updated.last_log_at == datetime(2026, 4, 6, 12, 5, tzinfo=UTC)

@pytest.mark.asyncio
async def test_store_update_revalidates_and_normalizes_mutations(tmp_path) -> None:
    store = ManagedSessionStore(tmp_path)
    store.save(_record())

    updated = await store.update(
        "sess-1",
        runtime_id="codex",
        error_message="  temporary failure  ",
        updated_at=datetime(2026, 4, 6, 12, 6),
    )

    assert updated.runtime_id == "codex_cli"
    assert updated.error_message == "temporary failure"
    assert updated.updated_at == datetime(2026, 4, 6, 12, 6, tzinfo=UTC)
