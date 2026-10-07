"""Retained sandbox workspace reclamation by the managed-runtime janitor.

Omnigent and sandbox activities materialize repositories under
``temporal_sandbox/<workspace_id>`` with owner records beside them. The
janitor previously saw only the whole ``temporal_sandbox`` store as one
ownerless directory and never reclaimed any workspace in it, so checkouts of
long-finished workflows filled the shared Docker disk.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from temporalio.client import WorkflowExecutionStatus
from temporalio.service import RPCError, RPCStatusCode

from moonmind.omnigent.workspace_sources import ExistingWorkspaceGrant
from moonmind.schemas.agent_runtime_models import ManagedRunRecord
from moonmind.workflows.temporal.runtime.cleanup import (
    ClosedWorkflowLookupError,
    DockerReferenceState,
    ManagedRuntimeCleanupConfig,
    ManagedRuntimeWorkspaceJanitor,
    resolve_closed_workflows,
)
from moonmind.workflows.temporal.runtime.managed_session_store import (
    ManagedSessionStore,
)
from moonmind.workflows.temporal.runtime.store import ManagedRunStore
from moonmind.workflows.temporal.runtime.workspace_locators import (
    SandboxWorkspaceRecord,
    SandboxWorkspaceRecordStore,
)

NOW = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)
OLD = NOW - timedelta(days=40)
WORKSPACE_ID = "0123456789abcdef01234567"
OWNER = "mm:owner-workflow"


def _config(root: Path, *, dry_run: bool = False) -> ManagedRuntimeCleanupConfig:
    return ManagedRuntimeCleanupConfig(
        enabled=True,
        dry_run=dry_run,
        workspace_retention=timedelta(days=7),
        artifact_retention=timedelta(days=7),
        record_retention=None,
        grace=timedelta(hours=1),
        max_delete_paths=25,
        lock_path=root / ".janitor.lock",
        runtime_store_root=root,
        artifact_root=root / "artifacts",
    )


def _age(path: Path, when: datetime = OLD) -> None:
    epoch = when.timestamp()
    for child in [path, *path.rglob("*")] if path.is_dir() else [path]:
        os.utime(child, (epoch, epoch))


def _sandbox_workspace(
    root: Path, workspace_id: str = WORKSPACE_ID, *, owner: str | None = OWNER
) -> Path:
    workspace = root / "temporal_sandbox" / workspace_id
    (workspace / "repo").mkdir(parents=True)
    (workspace / "repo" / "README.md").write_text("checkout", encoding="utf-8")
    store = SandboxWorkspaceRecordStore(root)
    if owner is not None:
        store.ensure(
            SandboxWorkspaceRecord(
                workspace_id=workspace_id,
                workflow_id=owner,
                step_execution_id=f"{owner}:step:1",
                relative_path="repo",
            )
        )
        store.mark_ready(workspace_id, {"targetOwnerWorkflowId": owner})
        _age(store.store_root)
    _age(workspace)
    return workspace


def _grant(
    root: Path,
    *,
    grantee: str,
    expires_at: datetime,
    workspace_id: str = WORKSPACE_ID,
) -> None:
    claims = SandboxWorkspaceRecordStore(root).store_root / f"{workspace_id}.grants"
    claims.mkdir(parents=True, exist_ok=True)
    (claims / "grant-1.json").write_text(
        json.dumps(
            {
                "grantId": "grant-1",
                "mode": "exclusive",
                "granteeWorkflowId": grantee,
                "granteeIdentityVerified": True,
                "expiresAt": expires_at.isoformat(),
            }
        ),
        encoding="utf-8",
    )
    _age(claims)


class _Closures:
    """Closed-workflow lookups answered from a mutable table."""

    def __init__(self, closed: Mapping[str, datetime | None]) -> None:
        self.closed = dict(closed)
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, workflow_ids: Sequence[str]) -> Mapping[str, datetime | None]:
        self.calls.append(tuple(workflow_ids))
        return {
            workflow_id: self.closed[workflow_id]
            for workflow_id in workflow_ids
            if workflow_id in self.closed
        }


def _janitor(
    root: Path,
    closures,
    *,
    dry_run: bool = False,
    docker_state: DockerReferenceState | None = None,
) -> ManagedRuntimeWorkspaceJanitor:
    return ManagedRuntimeWorkspaceJanitor(
        run_store=ManagedRunStore(root / "managed_runs"),
        session_store=ManagedSessionStore(root / "managed_sessions"),
        config=_config(root, dry_run=dry_run),
        docker_reference_provider=lambda: docker_state or DockerReferenceState(),
        closed_workflow_provider=closures,
        now=lambda: NOW,
    )


def _decision(result, path: Path):
    matches = [d for d in result.decisions if d.path == str(path)]
    assert len(matches) == 1, [d.path for d in result.decisions]
    return matches[0]


def test_closed_owner_workspace_past_retention_is_deleted_with_its_records(
    tmp_path: Path,
) -> None:
    root = tmp_path / "agent_jobs"
    workspace = _sandbox_workspace(root)
    store = SandboxWorkspaceRecordStore(root)

    result = _janitor(root, _Closures({OWNER: NOW - timedelta(days=10)})).run()

    assert _decision(result, workspace).classification == "deleted"
    assert not workspace.exists()
    assert store.load(WORKSPACE_ID) is None
    assert not store.is_materialized(WORKSPACE_ID)
    assert store.read_readiness(WORKSPACE_ID) is None
    assert (root / "temporal_sandbox").is_dir()


def test_owner_whose_history_expired_counts_as_closed(tmp_path: Path) -> None:
    root = tmp_path / "agent_jobs"
    workspace = _sandbox_workspace(root)

    result = _janitor(root, _Closures({OWNER: None})).run()

    assert _decision(result, workspace).classification == "deleted"


@pytest.mark.parametrize("closures", [_Closures({}), None])
def test_open_or_unknown_owner_keeps_the_workspace(tmp_path: Path, closures) -> None:
    root = tmp_path / "agent_jobs"
    workspace = _sandbox_workspace(root)

    result = _janitor(root, closures).run()

    assert _decision(result, workspace).classification == "protected_active"
    assert workspace.exists()


def test_closure_lookup_failure_keeps_workspaces_and_is_reported(
    tmp_path: Path,
) -> None:
    root = tmp_path / "agent_jobs"
    workspace = _sandbox_workspace(root)

    def failing(_workflow_ids: Sequence[str]) -> Mapping[str, datetime | None]:
        raise RuntimeError("temporal unavailable")

    result = _janitor(root, failing).run()

    assert _decision(result, workspace).classification == "protected_active"
    assert any("temporal unavailable" in error for error in result.errors)
    assert workspace.exists()


def test_retention_runs_from_owner_close_not_workspace_creation(
    tmp_path: Path,
) -> None:
    root = tmp_path / "agent_jobs"
    workspace = _sandbox_workspace(root)

    result = _janitor(root, _Closures({OWNER: NOW - timedelta(days=2)})).run()

    assert _decision(result, workspace).classification == "protected_recent"
    assert workspace.exists()


def test_unexpired_grant_to_an_open_workflow_protects_the_workspace(
    tmp_path: Path,
) -> None:
    root = tmp_path / "agent_jobs"
    workspace = _sandbox_workspace(root)
    _grant(root, grantee="mm:grantee", expires_at=NOW + timedelta(days=1))
    closures = _Closures({OWNER: NOW - timedelta(days=10)})

    protected = _janitor(root, closures).run()
    assert _decision(protected, workspace).classification == "protected_shared"

    closures.closed["mm:grantee"] = NOW - timedelta(days=9)
    released = _janitor(root, closures).run()
    assert _decision(released, workspace).classification == "deleted"
    assert not (
        SandboxWorkspaceRecordStore(root).store_root / f"{WORKSPACE_ID}.grants"
    ).exists()


def test_expired_grant_does_not_protect_the_workspace(tmp_path: Path) -> None:
    root = tmp_path / "agent_jobs"
    workspace = _sandbox_workspace(root)
    _grant(root, grantee="mm:grantee", expires_at=NOW - timedelta(days=1))

    result = _janitor(root, _Closures({OWNER: NOW - timedelta(days=10)})).run()

    assert _decision(result, workspace).classification == "deleted"


def test_container_mounting_the_workspace_subpath_protects_it(
    tmp_path: Path,
) -> None:
    """Host containers mount the workspace volume by subpath, not worker path."""

    root = tmp_path / "agent_jobs"
    workspace = _sandbox_workspace(root)
    docker_state = DockerReferenceState(
        active_mount_paths=frozenset(
            {
                "/var/lib/docker/volumes/agent_workspaces/_data/temporal_sandbox/"
                f"{WORKSPACE_ID}/repo"
            }
        )
    )

    result = _janitor(
        root, _Closures({OWNER: NOW - timedelta(days=10)}), docker_state=docker_state
    ).run()

    assert _decision(result, workspace).classification == "protected_active"
    assert workspace.exists()


def test_workspace_without_owner_record_stays_ambiguous_and_store_is_not_a_candidate(
    tmp_path: Path,
) -> None:
    root = tmp_path / "agent_jobs"
    workspace = _sandbox_workspace(root, owner=None)
    SandboxWorkspaceRecordStore(root).store_root.mkdir(parents=True)

    result = _janitor(root, _Closures({})).run()

    assert _decision(result, workspace).classification == "skipped_ambiguous_owner"
    assert str(root / "temporal_sandbox") not in {d.path for d in result.decisions}
    assert not any(
        Path(d.path).name.startswith(".") for d in result.decisions
    ), "the owner record store is never a workspace candidate"


def test_unreadable_owner_record_keeps_the_workspace(tmp_path: Path) -> None:
    root = tmp_path / "agent_jobs"
    workspace = _sandbox_workspace(root)
    record = SandboxWorkspaceRecordStore(root).store_root / f"{WORKSPACE_ID}.json"
    record.write_text("{not json", encoding="utf-8")

    result = _janitor(root, _Closures({OWNER: None})).run()

    assert _decision(result, workspace).classification == "protected_unreadable_owner"
    assert workspace.exists()


def test_dry_run_reports_without_deleting(tmp_path: Path) -> None:
    root = tmp_path / "agent_jobs"
    workspace = _sandbox_workspace(root)

    result = _janitor(
        root, _Closures({OWNER: NOW - timedelta(days=10)}), dry_run=True
    ).run()

    assert _decision(result, workspace).classification == "eligible"
    assert workspace.exists()
    assert SandboxWorkspaceRecordStore(root).load(WORKSPACE_ID) is not None


def test_final_rescan_rechecks_owner_closure_before_delete(tmp_path: Path) -> None:
    root = tmp_path / "agent_jobs"
    workspace = _sandbox_workspace(root)
    closures = _Closures({OWNER: NOW - timedelta(days=10)})
    original = closures.__call__

    def reopened_on_rescan(workflow_ids: Sequence[str]):
        answer = original(workflow_ids)
        closures.closed.pop(OWNER, None)
        return answer

    result = _janitor(root, reopened_on_rescan).run()

    assert _decision(result, workspace).classification == "protected_active"
    assert workspace.exists()


def test_run_record_inside_the_sandbox_owns_only_its_workspace(
    tmp_path: Path,
) -> None:
    root = tmp_path / "agent_jobs"
    finished = _sandbox_workspace(root, owner=None)
    neighbour = _sandbox_workspace(root, "fedcba9876543210fedcba98", owner=OWNER)
    run_store = ManagedRunStore(root / "managed_runs")
    run_store.save(
        ManagedRunRecord(
            runId="run-1",
            workflowId="mm:run-1",
            agentId="agent-1",
            runtimeId="codex-cli",
            status="completed",
            startedAt=OLD - timedelta(hours=1),
            finishedAt=OLD,
            workspacePath=str(finished / "repo"),
        )
    )

    result = _janitor(root, _Closures({})).run()

    assert _decision(result, finished).classification == "deleted"
    assert _decision(result, neighbour).classification == "protected_active"
    assert neighbour.exists()


@pytest.mark.asyncio
async def test_resolve_closed_workflows_maps_temporal_states() -> None:
    closed_at = NOW - timedelta(days=3)
    descriptions = {
        "mm:running": SimpleNamespace(
            status=WorkflowExecutionStatus.RUNNING, close_time=None
        ),
        "mm:completed": SimpleNamespace(
            status=WorkflowExecutionStatus.COMPLETED, close_time=closed_at
        ),
        "mm:failed": SimpleNamespace(
            status=WorkflowExecutionStatus.FAILED, close_time=closed_at
        ),
    }

    async def describe(workflow_id: str):
        if workflow_id == "mm:purged":
            raise RPCError("not found", RPCStatusCode.NOT_FOUND, b"")
        if workflow_id == "mm:unreachable":
            raise RPCError("unavailable", RPCStatusCode.UNAVAILABLE, b"")
        return descriptions[workflow_id]

    with pytest.raises(ClosedWorkflowLookupError, match="mm:unreachable") as raised:
        await resolve_closed_workflows(
            [
                "mm:running",
                "mm:completed",
                "mm:failed",
                "mm:purged",
                "mm:unreachable",
                "",
            ],
            describe=describe,
        )

    # Workflows Temporal answered for keep their verdict; the unreachable one
    # stays protected and the failure is surfaced instead of looking healthy.
    assert raised.value.closed == {
        "mm:completed": closed_at,
        "mm:failed": closed_at,
        "mm:purged": None,
    }
    assert raised.value.failed == ("mm:unreachable",)


def test_per_workflow_lookup_failure_protects_and_is_reported(
    tmp_path: Path,
) -> None:
    root = tmp_path / "agent_jobs"
    unreachable = _sandbox_workspace(root)
    finished = _sandbox_workspace(root, "fedcba9876543210fedcba98", owner="mm:done")

    async def describe(workflow_id: str):
        if workflow_id == OWNER:
            raise RPCError("unavailable", RPCStatusCode.UNAVAILABLE, b"")
        return SimpleNamespace(status=WorkflowExecutionStatus.COMPLETED, close_time=OLD)

    def provider(workflow_ids: Sequence[str]) -> Mapping[str, datetime | None]:
        return asyncio.run(resolve_closed_workflows(workflow_ids, describe=describe))

    result = _janitor(root, provider).run()

    assert _decision(result, unreachable).classification == "protected_active"
    assert unreachable.exists()
    assert _decision(result, finished).classification == "deleted"
    assert any(OWNER in error for error in result.errors), result.errors


def test_claim_acquired_after_the_final_rescan_keeps_the_workspace(
    tmp_path: Path,
) -> None:
    """A reader granted the workspace mid-delete must not lose it."""

    root = tmp_path / "agent_jobs"
    workspace = _sandbox_workspace(root)
    store = SandboxWorkspaceRecordStore(root)
    janitor = _janitor(root, _Closures({OWNER: NOW - timedelta(days=10)}))
    rescan = janitor._rescan_blocks_delete

    def claim_after_rescan(candidate):
        blocked = rescan(candidate)
        store.claim_existing_workspace(
            WORKSPACE_ID,
            ExistingWorkspaceGrant(
                workspace_id=WORKSPACE_ID,
                owner_workflow_id=OWNER,
                owner_step_execution_id=f"{OWNER}:step:1",
                generation=1,
                mode="read_only",
                expires_at=datetime.now(tz=UTC) + timedelta(hours=1),
                grant_digest="hmac-sha256:reader",
            ),
            grantee_workflow_id="mm:reader",
        )
        return blocked

    janitor._rescan_blocks_delete = claim_after_rescan

    result = janitor.run()

    assert _decision(result, workspace).classification == "protected_shared"
    assert workspace.exists()
    assert store.load(WORKSPACE_ID) is not None
    assert store.active_claim_grantees(WORKSPACE_ID) == ("mm:reader",)


def test_interrupted_deletion_quarantine_is_resumed_on_the_next_pass(
    tmp_path: Path,
) -> None:
    root = tmp_path / "agent_jobs"
    sandbox_quarantine = root / "temporal_sandbox" / f".gc-{'a' * 32}-{WORKSPACE_ID}"
    workspace_quarantine = root / "workspaces" / f".gc-{'b' * 32}-run-1"
    for quarantine in (sandbox_quarantine, workspace_quarantine):
        (quarantine / "repo").mkdir(parents=True)
        (quarantine / "repo" / "partial.bin").write_bytes(b"left behind")
    unrelated = root / "temporal_sandbox" / ".gc-not-a-quarantine"
    unrelated.mkdir()

    dry = _janitor(root, _Closures({}), dry_run=True).run()
    assert sandbox_quarantine.exists(), dry.errors

    result = _janitor(root, _Closures({})).run()

    assert not sandbox_quarantine.exists()
    assert not workspace_quarantine.exists()
    assert unrelated.exists()
    assert result.errors == ()


def test_legacy_claim_with_unverified_grantee_stays_protected(tmp_path: Path) -> None:
    root = tmp_path / "agent_jobs"
    workspace = _sandbox_workspace(root)
    _grant(root, grantee=OWNER, expires_at=NOW + timedelta(days=1))
    claim = next(
        (SandboxWorkspaceRecordStore(root).store_root / f"{WORKSPACE_ID}.grants").glob(
            "*.json"
        )
    )
    payload = json.loads(claim.read_text())
    payload.pop("granteeIdentityVerified", None)
    claim.write_text(json.dumps(payload))
    _age(claim.parent)
    result = _janitor(root, _Closures({OWNER: OLD})).run()
    assert _decision(result, workspace).classification == "protected_shared"
    assert workspace.exists()


def test_claims_mutex_excludes_another_thread(tmp_path: Path) -> None:
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    store = SandboxWorkspaceRecordStore(tmp_path)
    attempted, entered = Event(), Event()

    def enter():
        attempted.set()
        with store.claims_locked(WORKSPACE_ID):
            entered.set()

    with ThreadPoolExecutor(max_workers=1) as pool:
        with store.claims_locked(WORKSPACE_ID):
            future = pool.submit(enter)
            assert attempted.wait(1)
            assert not entered.wait(0.05), "another thread stole a live claim lock"
        future.result(timeout=2)
    assert entered.is_set()


def test_claim_after_workspace_deletion_is_rejected(tmp_path: Path) -> None:
    store = SandboxWorkspaceRecordStore(tmp_path)
    with pytest.raises(ValueError, match="unavailable"):
        store.claim_existing_workspace(
            WORKSPACE_ID,
            SimpleNamespace(
                grant_id="late",
                mode="read_only",
                grantee_workflow_id="mm:reader",
            ),
        )


@pytest.mark.parametrize("parent", ["", "workspaces", "temporal_sandbox", "artifacts"])
def test_quarantine_deletion_obeys_zero_path_budget(
    tmp_path: Path, parent: str
) -> None:
    from dataclasses import replace

    root = tmp_path / "agent_jobs"
    quarantine = root / parent / (".gc-" + "a" * 32 + "-workspace")
    quarantine.mkdir(parents=True)
    (quarantine / "saved.txt").write_text("retained")
    janitor = _janitor(root, _Closures({}))
    janitor._config = replace(janitor._config, max_delete_paths=0)
    result = janitor.run()
    assert quarantine.exists()
    assert _decision(result, quarantine).classification == "budget_exhausted"


def test_quarantine_deletion_obeys_byte_budget(tmp_path: Path) -> None:
    from dataclasses import replace

    root = tmp_path / "agent_jobs"
    quarantine = root / "temporal_sandbox" / (".gc-" + "a" * 32 + "-workspace")
    quarantine.mkdir(parents=True)
    (quarantine / "saved.txt").write_text("retained")
    janitor = _janitor(root, _Closures({}))
    janitor._config = replace(janitor._config, max_delete_bytes=1)
    result = janitor.run()
    assert quarantine.exists()
    assert _decision(result, quarantine).classification == "budget_exhausted"


@pytest.mark.parametrize("failed_scan", [False, True])
def test_quarantine_keeps_data_while_docker_mount_is_live_or_unknown(
    tmp_path: Path, failed_scan: bool
) -> None:
    root = tmp_path / "agent_jobs"
    quarantine = root / "temporal_sandbox" / (".gc-" + "a" * 32 + "-" + WORKSPACE_ID)
    quarantine.mkdir(parents=True)
    (quarantine / "saved.txt").write_text("retained")
    result = _janitor(
        root,
        _Closures({}),
        docker_state=DockerReferenceState(
            failed=failed_scan,
            reason="docker offline" if failed_scan else None,
            active_mount_paths=frozenset(
                {"/daemon/temporal_sandbox/" + WORKSPACE_ID + "/repo"}
            ),
        ),
    ).run()
    assert quarantine.exists()
    assert _decision(result, quarantine).classification == "protected_active"


def test_temporal_error_during_final_rescan_is_reported(tmp_path: Path) -> None:
    root = tmp_path / "agent_jobs"
    workspace = _sandbox_workspace(root)
    calls = 0

    def closures(workflow_ids):
        nonlocal calls
        calls += 1
        if calls > 1:
            raise ClosedWorkflowLookupError({}, [OWNER])
        return {OWNER: OLD}

    result = _janitor(root, closures).run()
    assert _decision(result, workspace).classification == "protected_active"
    assert result.errors, "a final-rescan outage must not look like a healthy pass"
    assert workspace.exists()


def test_quarantine_deletion_consumes_shared_path_budget(tmp_path: Path) -> None:
    from dataclasses import replace

    root = tmp_path / "agent_jobs"
    quarantines = [
        root / "temporal_sandbox" / (".gc-" + digit * 32 + "-workspace")
        for digit in ("a", "b")
    ]
    for quarantine in quarantines:
        quarantine.mkdir(parents=True)
        (quarantine / "saved.txt").write_text("retained")
    janitor = _janitor(root, _Closures({}))
    janitor._config = replace(janitor._config, max_delete_paths=1)
    result = janitor.run()
    assert sum(path.exists() for path in quarantines) == 1
    assert sum(d.classification == "deleted" for d in result.decisions) == 1
    assert sum(d.classification == "budget_exhausted" for d in result.decisions) == 1
    assert result.estimated_deleted_bytes == len("retained")


def test_quarantine_lock_rejects_reader_waiting_at_rename(
    tmp_path: Path, monkeypatch
) -> None:
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    root = tmp_path / "agent_jobs"
    workspace = _sandbox_workspace(root)
    store = SandboxWorkspaceRecordStore(root)
    janitor = _janitor(root, _Closures({OWNER: OLD}))
    attempted, finished = Event(), Event()
    original_rename = Path.rename
    futures = []

    def claim():
        attempted.set()
        try:
            store.claim_existing_workspace(
                WORKSPACE_ID,
                SimpleNamespace(
                    grant_id="late",
                    mode="read_only",
                    grantee_workflow_id="mm:reader",
                ),
            )
        finally:
            finished.set()

    with ThreadPoolExecutor(max_workers=1) as pool:

        def rename(path, target):
            if path == workspace:
                futures.append(pool.submit(claim))
                assert attempted.wait(1)
                assert not finished.wait(
                    0.05
                ), "a reader entered before quarantine completed"
            return original_rename(path, target)

        monkeypatch.setattr(Path, "rename", rename)
        result = janitor.run()
        assert _decision(result, workspace).classification == "deleted"
        with pytest.raises(ValueError, match="unavailable"):
            futures[0].result(timeout=2)
    assert not workspace.exists()


def test_unsaved_workspace_outlives_ordinary_retention_until_its_bound(
    tmp_path: Path,
) -> None:
    """A failed required save keeps the only local copy, but not forever.

    The realizer's durable unsaved decision protects the workspace past the
    ordinary retention window. Repeated recording cannot extend the bound,
    and once the explicit #4017 limit passes ordinary cleanup applies and
    takes the marker with it.
    """

    from moonmind.schemas.saved_work_retention import (
        SAVED_WORK_UNSAVED_LOCAL_RETENTION,
    )

    root = tmp_path / "agent_jobs"
    workspace = _sandbox_workspace(root)
    store = SandboxWorkspaceRecordStore(root)
    closures = _Closures({OWNER: NOW - timedelta(days=10)})
    first = store.mark_unsaved(
        WORKSPACE_ID,
        reason_code="WORKSPACE_SAVE_UNAVAILABLE",
        recorded_at=NOW - timedelta(days=10),
    )
    repeated = store.mark_unsaved(
        WORKSPACE_ID, reason_code="WORKSPACE_SAVE_UNAVAILABLE", recorded_at=NOW
    )

    assert repeated == first
    assert first["availability"] == "locally_retained_but_unsaved"
    protected = _janitor(root, closures).run()
    decision = _decision(protected, workspace)
    assert decision.classification == "protected_unsaved"
    assert "locally_retained_but_unsaved" in decision.reason
    assert workspace.exists()

    store.clear_unsaved(WORKSPACE_ID)
    store.mark_unsaved(
        WORKSPACE_ID,
        reason_code="WORKSPACE_SAVE_UNAVAILABLE",
        recorded_at=NOW - SAVED_WORK_UNSAVED_LOCAL_RETENTION - timedelta(days=1),
    )
    _age(store.store_root)
    expired = _janitor(root, closures).run()

    assert _decision(expired, workspace).classification == "deleted"
    assert not workspace.exists()
    assert store.read_unsaved(WORKSPACE_ID) is None


def test_cleared_unsaved_decision_returns_workspace_to_ordinary_cleanup(
    tmp_path: Path,
) -> None:
    root = tmp_path / "agent_jobs"
    workspace = _sandbox_workspace(root)
    store = SandboxWorkspaceRecordStore(root)
    store.mark_unsaved(
        WORKSPACE_ID, reason_code="WORKSPACE_SAVE_UNAVAILABLE", recorded_at=NOW
    )
    store.clear_unsaved(WORKSPACE_ID)
    _age(store.store_root)

    result = _janitor(root, _Closures({OWNER: NOW - timedelta(days=10)})).run()

    assert _decision(result, workspace).classification == "deleted"
