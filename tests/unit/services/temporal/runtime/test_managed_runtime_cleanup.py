from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

from moonmind.schemas.agent_runtime_models import ManagedRunRecord
from moonmind.schemas.managed_session_models import CodexManagedSessionRecord
from moonmind.workflows.temporal.runtime.managed_runtime_cleanup import (
    ManagedRuntimeCleanupConfig,
    ManagedRuntimeWorkspaceJanitor,
)
from moonmind.workflows.temporal.runtime.managed_session_store import ManagedSessionStore
from moonmind.workflows.temporal.runtime.store import ManagedRunStore


def _old_time() -> datetime:
    return datetime.now(tz=UTC) - timedelta(days=120)


def _run_record(run_id: str, workspace_path: str, status: str = "completed") -> ManagedRunRecord:
    return ManagedRunRecord(
        runId=run_id,
        workflowId="mm:wf-1",
        agentId="agent-1",
        runtimeId="codex-cli",
        status=status,
        startedAt=_old_time(),
        finishedAt=_old_time(),
        workspacePath=workspace_path,
    )


def _session_record(
    session_id: str,
    workspace_path: str,
    session_workspace_path: str,
    artifact_spool_path: str,
    status: str = "terminated",
) -> CodexManagedSessionRecord:
    return CodexManagedSessionRecord(
        sessionId=session_id,
        sessionEpoch=1,
        agentRunId="run-1",
        containerId=f"ctr-{session_id}",
        threadId=f"thread-{session_id}",
        runtimeId="codex_cli",
        imageRef="ghcr.io/moonladderstudios/moonmind:latest",
        controlUrl=f"docker-exec://{session_id}",
        status=status,
        workspacePath=workspace_path,
        sessionWorkspacePath=session_workspace_path,
        artifactSpoolPath=artifact_spool_path,
        startedAt=_old_time(),
        updatedAt=_old_time(),
    )


def _config(tmp_path, *, enabled: bool = True, dry_run: bool = True) -> ManagedRuntimeCleanupConfig:
    return ManagedRuntimeCleanupConfig(
        enabled=enabled,
        dry_run=dry_run,
        workspace_retention=timedelta(days=30),
        artifact_retention=timedelta(days=90),
        record_retention=None,
        grace=timedelta(seconds=0),
        max_delete_paths=25,
        store_root=tmp_path,
        artifact_root=tmp_path / "artifacts",
    )


def test_mm948_janitor_defaults_to_disabled(monkeypatch) -> None:
    monkeypatch.delenv("MOONMIND_MANAGED_RUNTIME_JANITOR_ENABLED", raising=False)
    monkeypatch.delenv("MOONMIND_MANAGED_RUNTIME_JANITOR_DRY_RUN", raising=False)

    config = ManagedRuntimeCleanupConfig.from_env()

    assert config.enabled is False
    assert config.dry_run is True


def test_mm948_dry_run_reports_old_terminal_workspace_without_deleting(tmp_path) -> None:
    workspace_root = tmp_path / "workspaces" / "mm:wf-1"
    repo = workspace_root / "repo"
    repo.mkdir(parents=True)
    (repo / "README.md").write_text("old checkout", encoding="utf-8")
    old_ts = (_old_time()).timestamp()
    os.utime(workspace_root, (old_ts, old_ts))

    run_store = ManagedRunStore(tmp_path / "managed_runs")
    run_store.save(_run_record("run-1", str(repo)))

    janitor = ManagedRuntimeWorkspaceJanitor(
        _config(tmp_path, enabled=True, dry_run=True),
        run_store=run_store,
        session_store=ManagedSessionStore(tmp_path / "managed_sessions"),
    )

    result = janitor.run()

    assert result.disabled is False
    assert result.dry_run is True
    assert result.scanned_run_records == 1
    assert result.scanned_workspace_roots == 1
    assert result.eligible_roots == 1
    assert result.deleted_roots == 0
    assert workspace_root.exists()


def test_mm948_delete_requires_enabled_and_non_dry_run_terminal_owners(tmp_path) -> None:
    run_root = tmp_path / "run-1"
    repo = run_root / "repo"
    repo.mkdir(parents=True)
    (repo / "README.md").write_text("old checkout", encoding="utf-8")
    old_ts = (_old_time()).timestamp()
    os.utime(run_root, (old_ts, old_ts))

    run_store = ManagedRunStore(tmp_path / "managed_runs")
    run_store.save(_run_record("run-1", str(repo)))

    janitor = ManagedRuntimeWorkspaceJanitor(
        _config(tmp_path, enabled=True, dry_run=False),
        run_store=run_store,
        session_store=ManagedSessionStore(tmp_path / "managed_sessions"),
    )

    result = janitor.run()

    assert result.eligible_roots == 1
    assert result.deleted_roots == 1
    assert not run_root.exists()


def test_mm948_active_owner_protects_workspace(tmp_path) -> None:
    run_root = tmp_path / "run-1"
    repo = run_root / "repo"
    repo.mkdir(parents=True)

    run_store = ManagedRunStore(tmp_path / "managed_runs")
    run_store.save(_run_record("run-1", str(repo), status="running"))

    janitor = ManagedRuntimeWorkspaceJanitor(
        _config(tmp_path, enabled=True, dry_run=False),
        run_store=run_store,
        session_store=ManagedSessionStore(tmp_path / "managed_sessions"),
    )

    result = janitor.run()

    assert result.skipped_active == 1
    assert result.deleted_roots == 0
    assert run_root.exists()


def test_mm948_artifact_scan_uses_normalized_artifacts_child_not_agent_jobs_glob(
    tmp_path,
) -> None:
    artifact_dir = tmp_path / "artifacts" / "artifact-job"
    artifact_dir.mkdir(parents=True)
    unsafe_sibling = tmp_path / "artifact-job"
    unsafe_sibling.mkdir()
    old_ts = (_old_time()).timestamp()
    os.utime(artifact_dir, (old_ts, old_ts))
    os.utime(unsafe_sibling, (old_ts, old_ts))

    janitor = ManagedRuntimeWorkspaceJanitor(
        _config(tmp_path, enabled=True, dry_run=True),
        run_store=ManagedRunStore(tmp_path / "managed_runs"),
        session_store=ManagedSessionStore(tmp_path / "managed_sessions"),
    )

    result = janitor.run()

    assert result.scanned_artifact_dirs == 1
    assert result.eligible_roots == 1
    assert result.scanned_workspace_roots == 1
    assert result.skipped_ambiguous_owner == 1
    assert artifact_dir.exists()
    assert unsafe_sibling.exists()
