from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import pytest

from moonmind.schemas.agent_runtime_models import ManagedRunRecord
from moonmind.schemas.managed_session_models import CodexManagedSessionRecord
from moonmind.workflows.temporal.runtime.managed_session_store import (
    ManagedSessionStore,
)
from moonmind.workflows.temporal.runtime.paths import managed_runtime_artifact_root
from moonmind.workflows.temporal.runtime.store import ManagedRunStore
from moonmind.workflows.temporal.runtime.workspace_janitor import (
    DockerRuntimeState,
    ManagedRuntimeJanitorConfig,
    ManagedRuntimeWorkspaceJanitor,
)


NOW = datetime(2026, 6, 27, 12, 0, tzinfo=UTC)
OLD = NOW - timedelta(days=250)


def _config(tmp_path, **updates) -> ManagedRuntimeJanitorConfig:
    values = {
        "enabled": True,
        "dry_run": False,
        "runtime_root": tmp_path,
        "artifact_root": tmp_path / "artifacts",
        "workspace_retention": timedelta(days=30),
        "artifact_retention": timedelta(days=90),
        "record_retention": None,
        "grace": timedelta(hours=1),
        "max_delete_paths": 25,
        "max_delete_bytes": None,
        "lock_path": tmp_path / ".janitor.lock",
    }
    values.update(updates)
    return ManagedRuntimeJanitorConfig(**values)


def _run_record(
    run_id: str,
    *,
    status: str = "completed",
    workspace_path: str,
    finished_at: datetime = OLD,
    stdout_ref: str | None = None,
) -> ManagedRunRecord:
    return ManagedRunRecord(
        runId=run_id,
        workflowId=f"mm:{run_id}",
        agentId="agent-1",
        runtimeId="codex-cli",
        status=status,
        startedAt=finished_at - timedelta(minutes=5),
        finishedAt=finished_at,
        workspacePath=workspace_path,
        stdoutArtifactRef=stdout_ref,
    )


def _session_record(
    session_id: str,
    *,
    status: str = "terminated",
    workspace_path: str,
    artifact_spool_path: str,
    updated_at: datetime = OLD,
    stdout_ref: str | None = None,
) -> CodexManagedSessionRecord:
    return CodexManagedSessionRecord(
        sessionId=session_id,
        sessionEpoch=1,
        agentRunId=f"run-{session_id}",
        containerId=f"ctr-{session_id}",
        threadId=f"thread-{session_id}",
        runtimeId="codex_cli",
        imageRef="ghcr.io/moonladderstudios/moonmind:latest",
        controlUrl=f"docker-exec://{session_id}",
        status=status,
        workspacePath=workspace_path,
        sessionWorkspacePath=workspace_path,
        artifactSpoolPath=artifact_spool_path,
        stdoutArtifactRef=stdout_ref,
        startedAt=updated_at - timedelta(minutes=5),
        updatedAt=updated_at,
    )


def _janitor(tmp_path, run_store, session_store, **config_updates):
    return ManagedRuntimeWorkspaceJanitor(
        config=_config(tmp_path, **config_updates),
        run_store=run_store,
        session_store=session_store,
        docker_state_provider=lambda: DockerRuntimeState(available=True),
        now=lambda: NOW,
    )


def _age_path(path) -> None:
    timestamp = OLD.timestamp()
    os.utime(path, (timestamp, timestamp))


def test_blank_artifact_env_normalizes_to_agent_jobs_artifacts(monkeypatch) -> None:
    monkeypatch.setenv("MOONMIND_AGENT_RUNTIME_ARTIFACTS", " ")

    assert str(managed_runtime_artifact_root()) == "/work/agent_jobs/artifacts"


def test_workspace_delete_uses_quarantine_protocol(tmp_path) -> None:
    run_store = ManagedRunStore(tmp_path / "managed_runs")
    session_store = ManagedSessionStore(tmp_path / "managed_sessions")
    workspace = tmp_path / "run-1"
    workspace.mkdir()
    (workspace / "payload.txt").write_text("payload", encoding="utf-8")
    _age_path(workspace / "payload.txt")
    _age_path(workspace)
    run_store.save(_run_record("run-1", workspace_path=str(workspace / "repo")))

    result = _janitor(tmp_path, run_store, session_store).run()

    assert result.deleted_roots == 1
    assert result.estimated_deleted_bytes == len("payload")
    assert not workspace.exists()
    assert not list(tmp_path.glob(".gc-*run-1"))


def test_second_pass_recheck_prevents_delete_when_owner_becomes_active(
    tmp_path,
) -> None:
    run_store = ManagedRunStore(tmp_path / "managed_runs")
    session_store = ManagedSessionStore(tmp_path / "managed_sessions")
    workspace = tmp_path / "run-1"
    workspace.mkdir()
    _age_path(workspace)
    run_store.save(_run_record("run-1", workspace_path=str(workspace / "repo")))
    calls = 0

    def docker_state() -> DockerRuntimeState:
        nonlocal calls
        calls += 1
        if calls == 2:
            run_store.save(
                _run_record(
                    "run-1",
                    status="running",
                    workspace_path=str(workspace / "repo"),
                )
            )
        return DockerRuntimeState(available=True)

    janitor = ManagedRuntimeWorkspaceJanitor(
        config=_config(tmp_path),
        run_store=run_store,
        session_store=session_store,
        docker_state_provider=docker_state,
        now=lambda: NOW,
    )

    result = janitor.run()

    assert result.deleted_roots == 0
    assert result.skipped_active == 1
    assert workspace.exists()


def test_delete_budgets_limit_paths(tmp_path) -> None:
    run_store = ManagedRunStore(tmp_path / "managed_runs")
    session_store = ManagedSessionStore(tmp_path / "managed_sessions")
    for run_id in ("run-1", "run-2"):
        workspace = tmp_path / run_id
        workspace.mkdir()
        (workspace / "payload.txt").write_text("payload", encoding="utf-8")
        _age_path(workspace / "payload.txt")
        _age_path(workspace)
        run_store.save(_run_record(run_id, workspace_path=str(workspace / "repo")))

    result = _janitor(tmp_path, run_store, session_store, max_delete_paths=1).run()

    assert result.eligible_roots == 2
    assert result.deleted_roots == 1
    assert result.skipped_total == 1
    remaining = sum(
        1 for path in (tmp_path / "run-1", tmp_path / "run-2") if path.exists()
    )
    assert remaining == 1


def test_dry_run_reports_eligible_workspace_bytes(tmp_path) -> None:
    run_store = ManagedRunStore(tmp_path / "managed_runs")
    session_store = ManagedSessionStore(tmp_path / "managed_sessions")
    workspace = tmp_path / "run-1"
    workspace.mkdir()
    (workspace / "payload.txt").write_text("payload", encoding="utf-8")
    _age_path(workspace / "payload.txt")
    _age_path(workspace)
    run_store.save(_run_record("run-1", workspace_path=str(workspace / "repo")))

    result = _janitor(tmp_path, run_store, session_store, dry_run=True).run()

    assert result.eligible_roots == 1
    assert result.deleted_roots == 0
    assert result.estimated_deleted_bytes == len("payload")
    assert result.to_payload()["estimatedDeletedBytes"] == len("payload")
    assert workspace.exists()


def test_artifact_directory_is_skipped_while_referenced_by_retained_record(
    tmp_path,
) -> None:
    run_store = ManagedRunStore(tmp_path / "managed_runs")
    session_store = ManagedSessionStore(tmp_path / "managed_sessions")
    artifact_dir = tmp_path / "artifacts" / "run-1"
    artifact_dir.mkdir(parents=True)
    (artifact_dir / "stdout.log").write_text("log", encoding="utf-8")
    _age_path(artifact_dir / "stdout.log")
    _age_path(artifact_dir)
    run_store.save(
        _run_record(
            "run-1",
            workspace_path=str(tmp_path / "run-1" / "repo"),
            stdout_ref="run-1/stdout.log",
        )
    )

    result = _janitor(tmp_path, run_store, session_store).run()

    assert result.deleted_artifact_dirs == 0
    assert result.skipped_recent == 1
    assert artifact_dir.exists()


def test_optional_record_delete_waits_for_workspace_and_artifact_cleanup(
    tmp_path,
) -> None:
    run_store = ManagedRunStore(tmp_path / "managed_runs")
    session_store = ManagedSessionStore(tmp_path / "managed_sessions")
    run_store.save(
        _run_record("run-1", workspace_path=str(tmp_path / "run-1" / "repo"))
    )
    session_store.save(
        _session_record(
            "sess-1",
            workspace_path=str(tmp_path / "sess-1" / "session"),
            artifact_spool_path=str(tmp_path / "artifacts" / "sess-1"),
        )
    )

    result = _janitor(
        tmp_path,
        run_store,
        session_store,
        record_retention=timedelta(days=180),
    ).run()

    assert result.deleted_record_files == 2
    assert run_store.load("run-1") is None
    assert session_store.load("sess-1") is None


def test_record_delete_waits_for_session_artifact_spool_cleanup(tmp_path) -> None:
    run_store = ManagedRunStore(tmp_path / "managed_runs")
    session_store = ManagedSessionStore(tmp_path / "managed_sessions")
    artifact_spool = tmp_path / "artifacts" / "sess-1"
    artifact_spool.mkdir(parents=True)
    os.utime(artifact_spool, (NOW.timestamp(), NOW.timestamp()))
    session_store.save(
        _session_record(
            "sess-1",
            workspace_path=str(tmp_path / "sess-1" / "session"),
            artifact_spool_path=str(artifact_spool),
        )
    )

    result = _janitor(
        tmp_path,
        run_store,
        session_store,
        record_retention=timedelta(days=180),
    ).run()

    assert result.deleted_record_files == 0
    assert session_store.load("sess-1") is not None


def test_record_delete_respects_path_budget(tmp_path) -> None:
    run_store = ManagedRunStore(tmp_path / "managed_runs")
    session_store = ManagedSessionStore(tmp_path / "managed_sessions")
    run_store.save(
        _run_record("run-1", workspace_path=str(tmp_path / "run-1" / "repo"))
    )
    session_store.save(
        _session_record(
            "sess-1",
            workspace_path=str(tmp_path / "sess-1" / "session"),
            artifact_spool_path=str(tmp_path / "artifacts" / "sess-1"),
        )
    )

    result = _janitor(
        tmp_path,
        run_store,
        session_store,
        record_retention=timedelta(days=180),
        max_delete_paths=1,
    ).run()

    assert result.deleted_record_files == 1
    assert result.skipped_total == 1
    remaining_records = [
        record
        for record in (run_store.load("run-1"), session_store.load("sess-1"))
        if record is not None
    ]
    assert len(remaining_records) == 1


@pytest.mark.asyncio
async def test_activity_returns_disabled_structured_result(
    tmp_path, monkeypatch
) -> None:
    from moonmind.workflows.temporal.activity_runtime import (
        TemporalAgentRuntimeActivities,
    )

    monkeypatch.setenv("MOONMIND_MANAGED_RUNTIME_JANITOR_ENABLED", "0")
    run_store = ManagedRunStore(tmp_path / "managed_runs")
    session_store = ManagedSessionStore(tmp_path / "managed_sessions")
    activities = TemporalAgentRuntimeActivities(
        run_store=run_store,
        session_store=session_store,
        client_adapter=object(),
    )

    result = await activities.agent_runtime_cleanup_managed_runtime_files({})

    assert result["disabled"] is True
    assert result["dryRun"] is True
