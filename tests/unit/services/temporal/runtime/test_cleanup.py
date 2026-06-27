from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from moonmind.schemas.agent_runtime_models import ManagedRunRecord
from moonmind.schemas.managed_session_models import CodexManagedSessionRecord
from moonmind.workflows.temporal.runtime.cleanup import (
    DockerReferenceState,
    ManagedRuntimeCleanupConfig,
    ManagedRuntimeWorkspaceJanitor,
)
from moonmind.workflows.temporal.runtime.managed_session_store import (
    ManagedSessionStore,
)
from moonmind.workflows.temporal.runtime.store import ManagedRunStore

NOW = datetime(2026, 6, 27, 12, 0, tzinfo=UTC)
OLD = NOW - timedelta(days=45)
RECENT = NOW - timedelta(days=1)


def _config(root: Path, *, dry_run: bool = True) -> ManagedRuntimeCleanupConfig:
    return ManagedRuntimeCleanupConfig(
        enabled=True,
        dry_run=dry_run,
        workspace_retention=timedelta(days=30),
        artifact_retention=timedelta(days=30),
        record_retention=timedelta(days=30),
        grace=timedelta(hours=1),
        max_delete_paths=25,
        max_delete_bytes=None,
        lock_path=root / ".janitor.lock",
        runtime_store_root=root,
        artifact_root=root / "artifacts",
    )


def _run(
    run_id: str,
    status: str,
    *,
    root: Path,
    workspace_path: str | None = None,
    active_turn_id: str | None = None,
    finished_at: datetime | None = OLD,
    artifact_ref: str | None = None,
) -> ManagedRunRecord:
    return ManagedRunRecord(
        runId=run_id,
        workflowId=f"mm:{run_id}",
        agentId="agent-1",
        runtimeId="codex-cli",
        status=status,
        startedAt=OLD - timedelta(hours=1),
        finishedAt=finished_at,
        workspacePath=workspace_path or str(root / run_id / "repo"),
        activeTurnId=active_turn_id,
        stdoutArtifactRef=artifact_ref,
    )


def _session(
    session_id: str,
    status: str,
    *,
    root: Path,
    agent_run_id: str,
    active_turn_id: str | None = None,
    updated_at: datetime | None = OLD,
) -> CodexManagedSessionRecord:
    return CodexManagedSessionRecord(
        sessionId=session_id,
        sessionEpoch=1,
        agentRunId=agent_run_id,
        containerId=f"ctr-{session_id}",
        threadId=f"thread-{session_id}",
        runtimeId="codex_cli",
        imageRef="ghcr.io/moonladderstudios/moonmind:latest",
        controlUrl=f"docker-exec://{session_id}",
        status=status,
        workspacePath=str(root / agent_run_id / "repo"),
        sessionWorkspacePath=str(root / agent_run_id / "session"),
        artifactSpoolPath=str(root / agent_run_id / "artifacts"),
        activeTurnId=active_turn_id,
        startedAt=OLD - timedelta(hours=1),
        updatedAt=updated_at,
    )


def _stores(root: Path) -> tuple[ManagedRunStore, ManagedSessionStore]:
    return ManagedRunStore(root / "managed_runs"), ManagedSessionStore(
        root / "managed_sessions"
    )


def _touch_old(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    old_epoch = OLD.timestamp()
    os.utime(path, (old_epoch, old_epoch))


def _janitor(
    root: Path,
    run_store: ManagedRunStore,
    session_store: ManagedSessionStore,
    *,
    dry_run: bool = True,
    docker_state: DockerReferenceState | None = None,
) -> ManagedRuntimeWorkspaceJanitor:
    return ManagedRuntimeWorkspaceJanitor(
        run_store=run_store,
        session_store=session_store,
        config=_config(root, dry_run=dry_run),
        docker_reference_provider=(
            None if docker_state is None else lambda: docker_state
        ),
        now=lambda: NOW,
    )


def test_mm_949_groups_shared_ownership_root_and_protects_active_owner(
    tmp_path: Path,
) -> None:
    root = tmp_path / "agent_jobs"
    shared = root / "workspaces" / "mm:workflow-123"
    _touch_old(shared)
    run_store, session_store = _stores(root)
    run_store.save(
        _run(
            "run-old",
            "completed",
            root=root,
            workspace_path=str(shared / "repo"),
        )
    )
    run_store.save(
        _run(
            "run-active",
            "running",
            root=root,
            workspace_path=str(shared / "repo"),
        )
    )

    result = _janitor(root, run_store, session_store).run()

    assert result.scanned_workspace_roots == 1
    assert result.decisions[0].ownership_root == str(shared)
    assert result.decisions[0].classification == "protected_shared"
    assert result.decisions[0].reason == "active owner or active turn"


def test_mm_949_terminal_old_per_run_workspace_is_eligible_in_dry_run(
    tmp_path: Path,
) -> None:
    root = tmp_path / "agent_jobs"
    run_root = root / "run-1"
    _touch_old(run_root)
    run_store, session_store = _stores(root)
    run_store.save(_run("run-1", "completed", root=root))
    session_store.save(_session("sess-1", "terminated", root=root, agent_run_id="run-1"))

    result = _janitor(root, run_store, session_store).run()

    workspace_decisions = [d for d in result.decisions if d.kind == "workspace"]
    assert len(workspace_decisions) == 1
    assert workspace_decisions[0].classification == "eligible"
    assert workspace_decisions[0].reason == "dry-run would delete"
    assert run_root.exists()

def test_mm_949_filesystem_workspace_without_records_is_ambiguous(
    tmp_path: Path,
) -> None:
    root = tmp_path / "agent_jobs"
    unowned = root / "workspaces" / "mm:unowned"
    _touch_old(unowned)
    run_store, session_store = _stores(root)

    result = _janitor(root, run_store, session_store).run()

    workspace_decision = next(d for d in result.decisions if d.kind == "workspace")
    assert workspace_decision.path == str(unowned)
    assert workspace_decision.classification == "skipped_ambiguous_owner"
    assert workspace_decision.reason == "no durable owner records reference candidate"


def test_mm_949_recent_filesystem_timestamp_prevents_deletion(tmp_path: Path) -> None:
    root = tmp_path / "agent_jobs"
    run_root = root / "run-1"
    _touch_old(run_root)
    os.utime(run_root, (RECENT.timestamp(), RECENT.timestamp()))
    run_store, session_store = _stores(root)
    run_store.save(_run("run-1", "completed", root=root, finished_at=OLD))

    result = _janitor(root, run_store, session_store).run()

    workspace_decision = next(d for d in result.decisions if d.kind == "workspace")
    assert workspace_decision.classification == "protected_recent"
    assert workspace_decision.reason == "retention window has not elapsed"
    assert workspace_decision.newest_activity_at == RECENT


def test_mm_949_symlink_and_ambiguous_owner_are_specific_skips(tmp_path: Path) -> None:
    root = tmp_path / "agent_jobs"
    target = tmp_path / "target"
    target.mkdir()
    symlink_root = root / "run-symlink"
    symlink_root.parent.mkdir(parents=True, exist_ok=True)
    symlink_root.symlink_to(target, target_is_directory=True)
    run_store, session_store = _stores(root)
    run_store.save(
        _run("run-symlink", "completed", root=root, workspace_path=str(symlink_root / "repo"))
    )
    run_store.save(
        _run(
            "run-ambiguous",
            "completed",
            root=root,
            workspace_path=str(root / "managed_runs" / "run-ambiguous.json"),
        )
    )

    result = _janitor(root, run_store, session_store).run()
    workspace_classifications = {
        Path(d.path).name: d.classification
        for d in result.decisions
        if d.kind == "workspace"
    }

    assert workspace_classifications["run-symlink"] == "skipped_unsafe_path"
    assert workspace_classifications["run-ambiguous.json"] == "skipped_ambiguous_owner"


def test_mm_949_live_docker_reference_prevents_deletion(tmp_path: Path) -> None:
    root = tmp_path / "agent_jobs"
    run_root = root / "run-1"
    _touch_old(run_root)
    run_store, session_store = _stores(root)
    run_store.save(_run("run-1", "completed", root=root))

    result = _janitor(
        root,
        run_store,
        session_store,
        docker_state=DockerReferenceState(active_mount_paths=frozenset({str(run_root)})),
    ).run()

    workspace_decision = next(d for d in result.decisions if d.kind == "workspace")
    assert workspace_decision.classification == "protected_active"
    assert workspace_decision.reason == "live Docker reference"


def test_mm_949_enabled_delete_uses_rescan_and_deletes_records_after_retention(
    tmp_path: Path,
) -> None:
    root = tmp_path / "agent_jobs"
    run_root = root / "run-1"
    _touch_old(run_root)
    run_store, session_store = _stores(root)
    run_store.save(_run("run-1", "completed", root=root))
    session_store.save(_session("sess-1", "terminated", root=root, agent_run_id="run-1"))
    os.utime(root / "managed_runs" / "run-1.json", (OLD.timestamp(), OLD.timestamp()))
    os.utime(
        root / "managed_sessions" / "sess-1.json",
        (OLD.timestamp(), OLD.timestamp()),
    )

    result = _janitor(root, run_store, session_store, dry_run=False).run()

    classifications = {(d.kind, Path(d.path).name): d.classification for d in result.decisions}
    assert classifications[("workspace", "run-1")] == "deleted"
    assert classifications[("run_record", "run-1.json")] == "deleted"
    assert classifications[("session_record", "sess-1.json")] == "deleted"
    assert not run_root.exists()
    assert run_store.load("run-1") is None
    assert session_store.load("sess-1") is None


def test_mm_949_config_from_env_normalizes_artifact_root_and_caps() -> None:
    root = Path("/tmp/agent_jobs")
    config = ManagedRuntimeCleanupConfig.from_env(
        {
            "MOONMIND_AGENT_RUNTIME_STORE": str(root),
            "MOONMIND_AGENT_RUNTIME_ARTIFACTS": str(root),
            "MOONMIND_MANAGED_RUNTIME_JANITOR_ENABLED": "1",
            "MOONMIND_MANAGED_RUNTIME_JANITOR_DRY_RUN": "0",
            "MOONMIND_MANAGED_RUNTIME_WORKSPACE_RETENTION_DAYS": "7",
            "MOONMIND_MANAGED_RUNTIME_ARTIFACT_RETENTION_DAYS": "11",
            "MOONMIND_MANAGED_RUNTIME_RECORD_RETENTION_DAYS": "13",
            "MOONMIND_MANAGED_RUNTIME_JANITOR_GRACE_SECONDS": "17",
            "MOONMIND_MANAGED_RUNTIME_JANITOR_MAX_DELETE_PATHS": "3",
            "MOONMIND_MANAGED_RUNTIME_JANITOR_MAX_DELETE_BYTES": "19",
            "MOONMIND_MANAGED_RUNTIME_JANITOR_LOCK_PATH": str(root / "lock"),
        }
    )

    assert config.enabled is True
    assert config.dry_run is False
    assert config.artifact_root == root / "artifacts"
    assert config.workspace_retention == timedelta(days=7)
    assert config.artifact_retention == timedelta(days=11)
    assert config.record_retention == timedelta(days=13)
    assert config.grace == timedelta(seconds=17)
    assert config.max_delete_paths == 3
    assert config.max_delete_bytes == 19
    assert config.lock_path == root / "lock"
