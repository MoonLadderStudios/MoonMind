from __future__ import annotations

import os
import shutil
from datetime import UTC, datetime, timedelta
from functools import wraps

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


@pytest.mark.parametrize("shared_workspace", [False, True], ids=["run", "shared"])
def test_workspace_cleanup_recovers_transient_quarantine_delete_failure(
    tmp_path, monkeypatch, shared_workspace
) -> None:
    run_store = ManagedRunStore(tmp_path / "managed_runs")
    session_store = ManagedSessionStore(tmp_path / "managed_sessions")
    parent = tmp_path / "workspaces" if shared_workspace else tmp_path
    workspace = parent / "run-1"
    workspace.mkdir(parents=True)
    (workspace / "removed.txt").write_text("discarded", encoding="utf-8")
    (workspace / "remaining.txt").write_text("remaining", encoding="utf-8")
    _age_path(workspace)
    run_store.save(_run_record("run-1", workspace_path=str(workspace / "repo")))
    real_rmtree = shutil.rmtree
    failed = False

    @wraps(real_rmtree)
    def fail_after_partial_delete(path, *, dir_fd):
        nonlocal failed
        if not failed:
            failed = True
            (parent / path / "removed.txt").unlink()
            # A new run may recreate the canonical path after quarantine.
            workspace.mkdir()
            (workspace / "active.txt").write_text("active work", encoding="utf-8")
            run_store.save(
                _run_record(
                    "run-1", status="running", workspace_path=str(workspace / "repo")
                )
            )
            raise PermissionError("transient recursive deletion failure")
        return real_rmtree(path, dir_fd=dir_fd)

    monkeypatch.setattr(shutil, "rmtree", fail_after_partial_delete)

    result = _janitor(tmp_path, run_store, session_store).run()

    assert not list(parent.glob(".gc-*run-1"))
    assert (workspace / "active.txt").read_text(encoding="utf-8") == "active work"
    assert run_store.load("run-1").status == "running"
    assert result.deleted_roots == 0
    assert any(
        "transient recursive deletion failure" in error for error in result.errors
    )


def test_workspace_cleanup_preserves_failed_retry_quarantine_and_original_error(
    tmp_path, monkeypatch, caplog
) -> None:
    run_store = ManagedRunStore(tmp_path / "managed_runs")
    session_store = ManagedSessionStore(tmp_path / "managed_sessions")
    workspace = tmp_path / "run-1"
    workspace.mkdir()
    (workspace / "remaining.txt").write_text("remaining", encoding="utf-8")
    _age_path(workspace)
    run_store.save(_run_record("run-1", workspace_path=str(workspace / "repo")))
    real_rmtree = shutil.rmtree
    failed = False

    @wraps(real_rmtree)
    def fail_both_deletes(path, *, dir_fd):
        nonlocal failed
        if not failed:
            failed = True
            raise PermissionError("original recursive deletion failure")
        raise PermissionError("retry cleanup denied")

    monkeypatch.setattr(shutil, "rmtree", fail_both_deletes)

    result = _janitor(tmp_path, run_store, session_store).run()

    quarantines = list(tmp_path.glob(".gc-*run-1"))
    assert len(quarantines) == 1
    assert (quarantines[0] / "remaining.txt").read_text(encoding="utf-8") == "remaining"
    assert result.deleted_roots == 0
    assert any(
        "original recursive deletion failure" in error for error in result.errors
    )
    assert quarantines[0].name in caplog.text
    assert "retry cleanup denied" in caplog.text


def test_quarantine_cleanup_retry_keeps_parent_descriptor_after_symlink_swap(
    tmp_path, monkeypatch
) -> None:
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    workspace = runtime / "run-1"
    workspace.mkdir()
    (workspace / "remaining.txt").write_text("remaining", encoding="utf-8")
    _age_path(workspace)
    run_store = ManagedRunStore(runtime / "managed_runs")
    session_store = ManagedSessionStore(runtime / "managed_sessions")
    run_store.save(_run_record("run-1", workspace_path=str(workspace / "repo")))
    outside = tmp_path / "outside"
    outside.mkdir()
    retained_runtime = tmp_path / "retained-runtime"
    real_rmtree = shutil.rmtree
    failed = False
    marker = None

    @wraps(real_rmtree)
    def swap_after_failure(path, *, dir_fd):
        nonlocal failed, marker
        if not failed:
            failed = True
            runtime.rename(retained_runtime)
            runtime.symlink_to(outside, target_is_directory=True)
            victim = outside / path
            victim.mkdir()
            marker = victim / "payload.txt"
            marker.write_text("keep outside", encoding="utf-8")
            raise PermissionError("transient recursive deletion failure")
        return real_rmtree(path, dir_fd=dir_fd)

    monkeypatch.setattr(shutil, "rmtree", swap_after_failure)

    result = _janitor(runtime, run_store, session_store).run()

    assert not list(retained_runtime.glob(".gc-*run-1"))
    assert marker.read_text(encoding="utf-8") == "keep outside"
    assert result.deleted_roots == 0
    assert any(
        "transient recursive deletion failure" in error for error in result.errors
    )


def test_quarantine_cleanup_retry_does_not_follow_replaced_quarantine(
    tmp_path, monkeypatch, caplog
) -> None:
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    workspace = runtime / "run-1"
    workspace.mkdir()
    (workspace / "remaining.txt").write_text("remaining", encoding="utf-8")
    _age_path(workspace)
    run_store = ManagedRunStore(runtime / "managed_runs")
    session_store = ManagedSessionStore(runtime / "managed_sessions")
    run_store.save(_run_record("run-1", workspace_path=str(workspace / "repo")))
    outside = tmp_path / "outside"
    outside.mkdir()
    marker = outside / "payload.txt"
    marker.write_text("keep outside", encoding="utf-8")
    real_rmtree = shutil.rmtree
    failed = False

    @wraps(real_rmtree)
    def replace_quarantine_after_failure(path, *, dir_fd):
        nonlocal failed
        if not failed:
            failed = True
            os.rename(path, ".preserved-original", src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
            os.symlink(outside, path, dir_fd=dir_fd)
            raise PermissionError("original recursive deletion failure")
        return real_rmtree(path, dir_fd=dir_fd)

    monkeypatch.setattr(shutil, "rmtree", replace_quarantine_after_failure)

    result = _janitor(runtime, run_store, session_store).run()

    assert marker.read_text(encoding="utf-8") == "keep outside"
    assert (runtime / ".preserved-original" / "remaining.txt").read_text(
        encoding="utf-8"
    ) == "remaining"
    quarantines = list(runtime.glob(".gc-*run-1"))
    assert len(quarantines) == 1
    assert quarantines[0].is_symlink()
    assert quarantines[0].name in caplog.text
    assert any(
        "original recursive deletion failure" in error for error in result.errors
    )
    assert result.deleted_roots == 0


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
    assert result["dryRun"] is False


def test_janitor_never_enumerates_symlinked_artifact_root(tmp_path):
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    outside = tmp_path / "outside"
    stale = outside / "old-artifact"
    stale.mkdir(parents=True)
    marker = stale / "payload.txt"
    marker.write_text("keep outside")
    _age_path(marker)
    _age_path(stale)
    (runtime / "artifacts").symlink_to(outside, target_is_directory=True)
    result = _janitor(runtime, ManagedRunStore(runtime / "managed_runs"), ManagedSessionStore(runtime / "managed_sessions")).run()
    assert marker.read_text() == "keep outside"
    assert result.deleted_artifact_dirs == 0


def test_quarantine_rejects_parent_swapped_to_symlink(tmp_path, monkeypatch):
    runtime = tmp_path / "runtime"
    parent = runtime / "artifacts"
    candidate = parent / "old-artifact"
    candidate.mkdir(parents=True)
    (candidate / "payload.txt").write_text("original")
    outside = tmp_path / "outside"
    victim = outside / "old-artifact"
    victim.mkdir(parents=True)
    marker = victim / "payload.txt"
    marker.write_text("keep outside")
    janitor = _janitor(runtime, ManagedRunStore(runtime / "managed_runs"), ManagedSessionStore(runtime / "managed_sessions"))
    estimate = janitor._estimate_bytes
    swapped = False
    def swap_after_estimate(path):
        nonlocal swapped
        size = estimate(path)
        if not swapped:
            swapped = True
            parent.rename(runtime / "original-artifacts")
            parent.symlink_to(outside, target_is_directory=True)
        return size
    monkeypatch.setattr(janitor, "_estimate_bytes", swap_after_estimate)
    try:
        janitor._quarantine_delete(candidate)
    except OSError:
        # Refusing the swapped parent is valid; outside data must survive.
        pass
    assert marker.read_text() == "keep outside"
