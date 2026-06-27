from __future__ import annotations

from datetime import UTC, datetime, timedelta
import os
from pathlib import Path

import pytest

from moonmind.schemas.agent_runtime_models import ManagedRunRecord
from moonmind.schemas.managed_session_models import CodexManagedSessionRecord
from moonmind.workflows.temporal.runtime.managed_runtime_cleanup import (
    cleanup_managed_runtime_files,
)
from moonmind.workflows.temporal.runtime.managed_session_store import ManagedSessionStore
from moonmind.workflows.temporal.runtime.store import ManagedRunStore


def _env(root: Path, *, enabled: str = "1", dry_run: str = "1") -> dict[str, str]:
    return {
        "MOONMIND_AGENT_RUNTIME_STORE": str(root),
        "MOONMIND_AGENT_RUNTIME_ARTIFACTS": str(root / "artifacts"),
        "MOONMIND_MANAGED_RUNTIME_JANITOR_ENABLED": enabled,
        "MOONMIND_MANAGED_RUNTIME_JANITOR_DRY_RUN": dry_run,
        "MOONMIND_MANAGED_RUNTIME_WORKSPACE_RETENTION_DAYS": "30",
        "MOONMIND_MANAGED_RUNTIME_ARTIFACT_RETENTION_DAYS": "30",
        "MOONMIND_MANAGED_RUNTIME_JANITOR_GRACE_SECONDS": "0",
    }


def _run_record(
    *,
    run_id: str,
    workspace_path: Path,
    status: str = "completed",
    started_at: datetime,
    finished_at: datetime | None = None,
    stdout_ref: str | None = None,
) -> ManagedRunRecord:
    return ManagedRunRecord(
        runId=run_id,
        workflowId=f"workflow-{run_id}",
        agentId="codex",
        runtimeId="codex",
        status=status,
        startedAt=started_at,
        finishedAt=finished_at or started_at,
        workspacePath=str(workspace_path),
        stdoutArtifactRef=stdout_ref,
    )


def _session_record(
    *,
    session_id: str,
    agent_run_id: str,
    workspace_path: Path,
    status: str = "terminated",
    started_at: datetime,
) -> CodexManagedSessionRecord:
    return CodexManagedSessionRecord(
        sessionId=session_id,
        sessionEpoch=1,
        agentRunId=agent_run_id,
        containerId=f"container-{session_id}",
        threadId=f"thread-{session_id}",
        runtimeId="codex",
        imageRef="codex:latest",
        controlUrl="http://control",
        status=status,
        workspacePath=str(workspace_path),
        sessionWorkspacePath=str(workspace_path / "session"),
        artifactSpoolPath=str(workspace_path / "artifacts"),
        startedAt=started_at,
        updatedAt=started_at,
    )


def _age_path(path: Path, when: datetime) -> None:
    timestamp = when.timestamp()
    if path.is_dir():
        for child in path.rglob("*"):
            os.utime(child, (timestamp, timestamp), follow_symlinks=False)
    os.utime(path, (timestamp, timestamp), follow_symlinks=False)


def test_cleanup_dry_run_reports_candidates_without_deleting(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 6, 27, tzinfo=UTC)
    old = now - timedelta(days=45)
    workspace = tmp_path / "workspaces" / "mm-workflow"
    workspace.mkdir(parents=True)
    (workspace / "repo.txt").write_text("delete candidate", encoding="utf-8")
    artifact_dir = tmp_path / "artifacts" / "job-1"
    artifact_dir.mkdir(parents=True)
    (artifact_dir / "stdout.log").write_text("logs", encoding="utf-8")
    run_store = ManagedRunStore(tmp_path / "managed_runs")
    session_store = ManagedSessionStore(tmp_path / "managed_sessions")
    run_store.save(
        _run_record(
            run_id="run-1",
            workspace_path=workspace / "repo",
            started_at=old,
            stdout_ref="job-1/stdout.log",
        )
    )
    _age_path(workspace, old)
    _age_path(artifact_dir, old)
    _age_path(tmp_path / "managed_runs" / "run-1.json", old)

    result = cleanup_managed_runtime_files(
        run_store=run_store,
        session_store=session_store,
        env=_env(tmp_path),
        now=now,
    )

    assert result.disabled is False
    assert result.dry_run is True
    assert result.eligible_roots == 2
    assert result.deleted_roots == 0
    assert result.deleted_artifact_dirs == 0
    assert result.estimated_deleted_bytes > 0
    assert workspace.exists()
    assert artifact_dir.exists()
    assert {
        (sample.resource_class, sample.classification)
        for sample in result.candidate_samples
    } >= {("workspace_root", "eligible"), ("artifact_dir", "eligible")}
    assert result.metrics["resource.workspace_root.eligible"] == 1
    assert result.metrics["resource.artifact_dir.eligible"] == 1


def test_cleanup_preserves_per_candidate_skip_reasons(tmp_path: Path) -> None:
    now = datetime(2026, 6, 27, tzinfo=UTC)
    old = now - timedelta(days=45)
    recent = now - timedelta(days=2)
    active_workspace = tmp_path / "workspaces" / "active"
    recent_workspace = tmp_path / "workspaces" / "recent"
    active_workspace.mkdir(parents=True)
    recent_workspace.mkdir(parents=True)
    run_store = ManagedRunStore(tmp_path / "managed_runs")
    session_store = ManagedSessionStore(tmp_path / "managed_sessions")
    run_store.save(
        _run_record(
            run_id="active-run",
            workspace_path=active_workspace / "repo",
            status="running",
            started_at=old,
        )
    )
    run_store.save(
        _run_record(
            run_id="recent-run",
            workspace_path=recent_workspace / "repo",
            started_at=recent,
        )
    )
    _age_path(active_workspace, old)
    _age_path(recent_workspace, recent)

    result = cleanup_managed_runtime_files(
        run_store=run_store,
        session_store=session_store,
        env=_env(tmp_path),
        now=now,
    )

    samples = {sample.safe_path: sample for sample in result.candidate_samples}
    assert result.skipped_active == 1
    assert result.skipped_recent >= 1
    assert samples["store:workspaces/active"].classification == "protected_active"
    assert "activeTurnId" in samples["store:workspaces/active"].reason
    assert samples["store:workspaces/recent"].classification == "protected_recent"
    assert "retention" in samples["store:workspaces/recent"].reason


def test_cleanup_record_errors_are_visible_with_safe_path(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 6, 27, tzinfo=UTC)
    old = now - timedelta(days=45)
    workspace = tmp_path / "workspaces" / "eligible"
    workspace.mkdir(parents=True)
    (workspace / "data.txt").write_text("payload", encoding="utf-8")
    run_store = ManagedRunStore(tmp_path / "managed_runs")
    session_store = ManagedSessionStore(tmp_path / "managed_sessions")
    run_store.save(
        _run_record(
            run_id="run-1",
            workspace_path=workspace / "repo",
            started_at=old,
        )
    )
    _age_path(workspace, old)
    _age_path(tmp_path / "managed_runs" / "run-1.json", old)
    bad_record = tmp_path / "managed_sessions" / "bad.json"
    bad_record.parent.mkdir(parents=True, exist_ok=True)
    bad_record.write_text("{not json", encoding="utf-8")

    result = cleanup_managed_runtime_files(
        run_store=run_store,
        session_store=session_store,
        env=_env(tmp_path, dry_run="0"),
        now=now,
    )

    assert workspace.exists()
    assert result.delete_budget_exhausted == 0
    assert result.errors
    assert result.errors[0].startswith("store:managed_sessions")


def test_cleanup_delete_budget_exhaustion_is_visible(tmp_path: Path) -> None:
    now = datetime(2026, 6, 27, tzinfo=UTC)
    old = now - timedelta(days=45)
    workspace = tmp_path / "workspaces" / "eligible"
    workspace.mkdir(parents=True)
    (workspace / "data.txt").write_text("payload", encoding="utf-8")
    run_store = ManagedRunStore(tmp_path / "managed_runs")
    session_store = ManagedSessionStore(tmp_path / "managed_sessions")
    run_store.save(
        _run_record(
            run_id="run-1",
            workspace_path=workspace / "repo",
            started_at=old,
        )
    )
    _age_path(workspace, old)
    _age_path(tmp_path / "managed_runs" / "run-1.json", old)
    env = _env(tmp_path, dry_run="0")
    env["MOONMIND_MANAGED_RUNTIME_JANITOR_MAX_DELETE_PATHS"] = "0"

    result = cleanup_managed_runtime_files(
        run_store=run_store,
        session_store=session_store,
        env=env,
        now=now,
    )

    assert workspace.exists()
    assert result.eligible_roots == 1
    assert result.delete_budget_exhausted == 1
    assert result.metrics["resource.workspace_root.budget_exhausted"] == 1


def test_cleanup_deletes_when_enabled_and_dry_run_disabled(tmp_path: Path) -> None:
    now = datetime(2026, 6, 27, tzinfo=UTC)
    old = now - timedelta(days=45)
    workspace = tmp_path / "workspaces" / "eligible"
    workspace.mkdir(parents=True)
    (workspace / "data.txt").write_text("payload", encoding="utf-8")
    run_store = ManagedRunStore(tmp_path / "managed_runs")
    session_store = ManagedSessionStore(tmp_path / "managed_sessions")
    run_store.save(
        _run_record(
            run_id="run-1",
            workspace_path=workspace / "repo",
            started_at=old,
        )
    )
    session_store.save(
        _session_record(
            session_id="sess-1",
            agent_run_id="run-1",
            workspace_path=workspace,
            started_at=old,
        )
    )
    _age_path(workspace, old)
    _age_path(tmp_path / "managed_runs" / "run-1.json", old)
    _age_path(tmp_path / "managed_sessions" / "sess-1.json", old)
    env = _env(tmp_path, dry_run="0")
    env["MOONMIND_MANAGED_RUNTIME_RECORD_RETENTION_DAYS"] = "30"

    result = cleanup_managed_runtime_files(
        run_store=run_store,
        session_store=session_store,
        env=env,
        now=now,
    )

    assert result.deleted_roots == 1
    assert result.deleted_record_files == 2
    assert not workspace.exists()
    assert run_store.load("run-1") is None
    assert session_store.load("sess-1") is None
