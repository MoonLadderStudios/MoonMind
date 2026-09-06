"""Database-backed evidence for the incremental lease contract.

Source: MoonLadderStudios/MoonMind#3883 (remaining implementation 1-7;
AC1, AC2, AC3, AC4, AC5, AC6, AC7, AC8).

The lease ledger is the authority for whether a Provider Profile slot is
spent, so its guarantees are proven against a real PostgreSQL cluster running
the real Alembic migration and the real
``provider_profile.sync_slot_leases`` Activity — not against mocked Activity
returns. Covered here:

* the ``369_provider_lease_incr_contract`` migration over pre-contract rows,
  including lease IDs that would collide when backfilled from ``workflow_id``;
* grant-or-get under real concurrency and a real unique constraint;
* conflicting profile / plan / purpose / generation reuse;
* release, duplicate release, stale fence and cleanup-request transitions;
* a mixed-generation cutover in which a retained snapshot writer must not
  delete a newer writer's rows;
* restart recovery of every mode, owner and generation without resurrecting
  released state.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import ProviderProfileSlotLease
from moonmind.provider_profiles.lease_client import (
    CredentialLeaseMode,
    DurableLeaseState,
    LeaseTransitionOutcome,
)
from moonmind.workflows.temporal.artifacts import TemporalArtifactActivities
from moonmind.workflows.temporal.workflows import provider_profile_manager
from moonmind.workflows.temporal.workflows.provider_profile_manager import (
    MoonMindProviderProfileManagerWorkflow,
    ProfileSlotState,
)

pytestmark = [pytest.mark.integration, pytest.mark.integration_ci]

RUNTIME_ID = "opencode"
PROFILE_REF = "opencode-zen-free"
OTHER_PROFILE_REF = "opencode-zen-paid"
SCOPE_REF = "provider-scope:opencode-zen"
PLAN_REF = "omnigent-execution-plan:sha256:" + "a" * 64
OTHER_PLAN_REF = "omnigent-execution-plan:sha256:" + "b" * 64

# The exact pre-369 shape of the lease table: the initial columns plus the
# owner/purpose/identity columns added by 337_mm1207_omnigent_oauth_hosts.
_PRE_CONTRACT_DDL = """
CREATE TABLE provider_profile_slot_leases (
    id SERIAL PRIMARY KEY,
    runtime_id VARCHAR(64) NOT NULL,
    workflow_id VARCHAR(255) NOT NULL,
    profile_id VARCHAR(128) NOT NULL,
    granted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    lease_id VARCHAR(255),
    owner_id VARCHAR(255),
    purpose VARCHAR(64) NOT NULL DEFAULT 'execution_direct',
    owner_is_workflow BOOLEAN NOT NULL DEFAULT true,
    step_execution_id VARCHAR(255),
    oauth_session_id VARCHAR(128),
    idempotency_key VARCHAR(255),
    expires_at TIMESTAMPTZ,
    CONSTRAINT uq_provider_slot_lease_runtime_workflow
        UNIQUE (runtime_id, workflow_id)
);
CREATE INDEX ix_provider_slot_leases_runtime
    ON provider_profile_slot_leases (runtime_id);
CREATE INDEX ix_provider_slot_leases_workflow
    ON provider_profile_slot_leases (workflow_id);
"""


def _load_migration_module():
    """Load the real ``369_provider_lease_incr_contract`` revision module.

    Alembic revision files are not importable as a package (the directory has
    no ``__init__`` and the module name starts with a digit), so the real file
    is loaded by path. This runs the shipped migration, not a copy of it.
    """

    import importlib.util
    from pathlib import Path

    path = (
        Path(__file__).resolve().parents[3]
        / "api_service"
        / "migrations"
        / "versions"
        / "369_provider_lease_incr_contract.py"
    )
    spec = importlib.util.spec_from_file_location(
        "moonmind_test_migration_369", path
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _create_lease_table(sync_conn) -> None:
    ProviderProfileSlotLease.__table__.create(sync_conn, checkfirst=True)


def _drop_lease_table(sync_conn) -> None:
    ProviderProfileSlotLease.__table__.drop(sync_conn, checkfirst=True)


@pytest_asyncio.fixture()
async def lease_session_maker(control_plane_postgres_url, monkeypatch):
    """Bind the production session factory to an ephemeral PostgreSQL cluster.

    The Activity resolves ``api_service.db.base.async_session_maker`` at call
    time, so rebinding it exercises the real Activity against a real ledger.
    """

    import api_service.db.base as db_base

    engine = create_async_engine(control_plane_postgres_url)
    async with engine.begin() as conn:
        await conn.run_sync(_create_lease_table)
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(db_base, "async_session_maker", maker)
    try:
        yield maker
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(_drop_lease_table)
        await engine.dispose()


def _activity() -> Any:
    return TemporalArtifactActivities(service=None).provider_profile_sync_slot_leases


async def _run(action: str, leases: list[dict[str, Any]] | None = None, **kwargs: Any):
    return await _activity()(
        runtime_id=RUNTIME_ID, leases=leases, action=action, **kwargs
    )


def _grant(
    lease_id: str,
    *,
    fence: int,
    profile_id: str = PROFILE_REF,
    purpose: str = "execution_omnigent",
    compatibility_class: str = CredentialLeaseMode.SHARED_EXECUTION.value,
    scope_generation: int = 1,
    credential_generation: int | None = 7,
    plan_ref: str = PLAN_REF,
    identity: str = "evidence-1",
    scope_ref: str = SCOPE_REF,
) -> dict[str, Any]:
    return {
        "lease_id": lease_id,
        "workflow_id": lease_id,
        "profile_id": profile_id,
        "owner_id": lease_id,
        "owner_kind": "workflow",
        "purpose": purpose,
        "compatibility_class": compatibility_class,
        "fencing_generation": fence,
        "scope_generation": scope_generation,
        "capacity_scope_ref": scope_ref,
        "credential_generation": credential_generation,
        "executionPlanRef": plan_ref,
        "lease_state": DurableLeaseState.HELD.value,
        "ownerIsWorkflow": True,
        "safe_metadata": {"evidenceIdentity": identity},
    }


async def _rows(maker) -> list[ProviderProfileSlotLease]:
    async with maker() as session:
        result = await session.execute(
            select(ProviderProfileSlotLease).order_by(ProviderProfileSlotLease.id)
        )
        return list(result.scalars().all())


# ---------------------------------------------------------------------------
# Acceptance 1: the real migration over real pre-contract rows
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_migration_versions_existing_rows_and_breaks_id_collisions(
    control_plane_postgres_url,
) -> None:
    """Existing rows survive; colliding backfilled lease IDs stay distinct.

    Pre-contract rows are unique per ``(runtime_id, workflow_id)``, so two
    runtime families can hold the same workflow ID. Backfilling ``lease_id``
    from ``workflow_id`` and then adding a global unique constraint has to
    resolve that without failing the migration and without merging two leases
    into one.
    """

    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    migration = _load_migration_module()

    engine = create_async_engine(control_plane_postgres_url)
    try:
        async with engine.begin() as conn:
            for statement in _PRE_CONTRACT_DDL.strip().split(";"):
                if statement.strip():
                    await conn.execute(text(statement))
            await conn.execute(
                text(
                    "INSERT INTO provider_profile_slot_leases "
                    "(runtime_id, workflow_id, profile_id, purpose, "
                    " owner_id, owner_is_workflow) VALUES "
                    "('opencode', 'agent-run-1', 'p1', 'execution_omnigent', "
                    " 'agent-run-1', true), "
                    "('codex_cli', 'agent-run-1', 'p2', 'credential_validation', "
                    " 'agent-run-1', false), "
                    "('opencode', 'agent-run-2', 'p1', 'execution_direct', "
                    " NULL, true)"
                )
            )

        def _upgrade(sync_conn) -> None:
            context = MigrationContext.configure(sync_conn)
            with Operations.context(context):
                migration.upgrade()

        async with engine.begin() as conn:
            await conn.run_sync(_upgrade)

        async with engine.connect() as conn:
            rows = (
                await conn.execute(
                    text(
                        "SELECT runtime_id, workflow_id, lease_id, owner_kind, "
                        "compatibility_class, scope_generation, "
                        "fencing_generation, lease_state "
                        "FROM provider_profile_slot_leases ORDER BY id"
                    )
                )
            ).all()

        assert len(rows) == 3, "existing rows must survive the migration"
        # The oldest row keeps the exact identity its holder already quotes.
        assert rows[0].lease_id == "agent-run-1"
        # The colliding row is disambiguated instead of merged or dropped.
        assert rows[1].lease_id.startswith("agent-run-1#")
        assert rows[1].lease_id != rows[0].lease_id
        assert rows[2].lease_id == "agent-run-2"
        assert len({row.lease_id for row in rows}) == 3

        # Historical values are versioned, not fabricated.
        assert rows[0].owner_kind == "workflow"
        assert rows[1].owner_kind == "activity"
        assert rows[0].compatibility_class == "execution_omnigent"
        assert all(row.scope_generation == 1 for row in rows)
        assert all(row.fencing_generation == 1 for row in rows)
        assert all(row.lease_state == DurableLeaseState.HELD.value for row in rows)

        async with engine.connect() as conn:
            constraint = (
                await conn.execute(
                    text(
                        "SELECT conname FROM pg_constraint "
                        "WHERE conname = 'uq_provider_slot_lease_lease_id'"
                    )
                )
            ).scalar()
        assert constraint == "uq_provider_slot_lease_lease_id"
    finally:
        async with engine.begin() as conn:
            await conn.execute(
                text("DROP TABLE IF EXISTS provider_profile_slot_leases CASCADE")
            )
        await engine.dispose()


# ---------------------------------------------------------------------------
# Acceptance 2: grant-or-get under real concurrency and real constraints
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concurrent_identical_grants_commit_at_most_one_lease(
    lease_session_maker,
) -> None:
    payload = _grant("agent-run-1", fence=1)
    results = await asyncio.gather(
        *(_run("grant", [dict(payload)]) for _ in range(6)),
        return_exceptions=True,
    )

    rows = await _rows(lease_session_maker)
    assert len(rows) == 1, "concurrent grants created more than one authority"
    fresh = [
        result
        for result in results
        if isinstance(result, dict) and result.get("duplicate") is False
    ]
    assert len(fresh) <= 1
    assert rows[0].fencing_generation == 1
    assert rows[0].lease_state == DurableLeaseState.HELD.value


@pytest.mark.asyncio
async def test_a_matching_retry_returns_the_committed_row(
    lease_session_maker,
) -> None:
    payload = _grant("agent-run-1", fence=3)
    assert await _run("grant", [dict(payload)]) == {
        "granted": True,
        "duplicate": False,
    }
    assert await _run("grant", [dict(payload)]) == {
        "granted": True,
        "duplicate": True,
    }
    assert len(await _rows(lease_session_maker)) == 1


@pytest.mark.parametrize(
    "override",
    [
        {"profile_id": OTHER_PROFILE_REF},
        {"purpose": "credential_validation"},
        {"compatibility_class": CredentialLeaseMode.EXCLUSIVE_MAINTENANCE.value},
        {"plan_ref": OTHER_PLAN_REF},
        {"credential_generation": 8},
        {"scope_generation": 4},
        {"identity": "evidence-2"},
    ],
    ids=[
        "profile",
        "purpose",
        "compatibility",
        "plan",
        "credential-generation",
        "scope-generation",
        "evidence",
    ],
)
@pytest.mark.asyncio
async def test_conflicting_reuse_of_a_live_identity_creates_no_second_unit(
    lease_session_maker, override: dict[str, Any]
) -> None:
    """A different plan, purpose or generation is not the same grant."""

    await _run("grant", [_grant("agent-run-1", fence=3)])
    result = await _run("grant", [_grant("agent-run-1", fence=4, **override)])

    assert result == {"error": "lease identity conflict"}
    rows = await _rows(lease_session_maker)
    assert len(rows) == 1
    assert rows[0].fencing_generation == 3
    assert rows[0].profile_id == PROFILE_REF


@pytest.mark.asyncio
async def test_an_older_generation_never_downgrades_a_committed_fence(
    lease_session_maker,
) -> None:
    await _run("grant", [_grant("agent-run-1", fence=9)])
    assert await _run("grant", [_grant("agent-run-1", fence=4)]) == {
        "error": "lease fencing regression"
    }
    rows = await _rows(lease_session_maker)
    assert rows[0].fencing_generation == 9


@pytest.mark.asyncio
async def test_a_tombstoned_identity_can_be_granted_again(
    lease_session_maker,
) -> None:
    await _run("grant", [_grant("agent-run-1", fence=3)])
    await _run(
        "release_one", [{"lease_id": "agent-run-1", "fencing_generation": 3}]
    )
    result = await _run(
        "grant", [_grant("agent-run-1", fence=4, purpose="credential_validation")]
    )

    assert result["granted"] is True
    rows = await _rows(lease_session_maker)
    assert len(rows) == 1
    assert rows[0].lease_state == DurableLeaseState.HELD.value
    assert rows[0].fencing_generation == 4


@pytest.mark.asyncio
async def test_an_ordinary_grant_and_release_touch_a_single_row(
    lease_session_maker,
) -> None:
    """Durable cost is independent of how many leases the runtime holds."""

    for index in range(6):
        await _run("grant", [_grant(f"agent-run-{index}", fence=index + 1)])
    before = {row.lease_id: row.lease_state for row in await _rows(lease_session_maker)}

    await _run("grant", [_grant("agent-run-9", fence=20)])
    await _run(
        "release_one", [{"lease_id": "agent-run-3", "fencing_generation": 4}]
    )

    after = {row.lease_id: row.lease_state for row in await _rows(lease_session_maker)}
    changed = {
        lease_id
        for lease_id in set(before) | set(after)
        if before.get(lease_id) != after.get(lease_id)
    }
    assert changed == {"agent-run-9", "agent-run-3"}


# ---------------------------------------------------------------------------
# Acceptance 3 / 4: releases, fences and the ambiguous-outcome reconciliation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_release_outcomes_are_explicit_and_idempotent(
    lease_session_maker,
) -> None:
    await _run("grant", [_grant("agent-run-1", fence=5)])

    first = await _run(
        "release_one", [{"lease_id": "agent-run-1", "fencing_generation": 5}]
    )
    second = await _run(
        "release_one", [{"lease_id": "agent-run-1", "fencing_generation": 5}]
    )
    missing = await _run(
        "release_one", [{"lease_id": "agent-run-nope", "fencing_generation": 1}]
    )

    assert first["outcome"] == LeaseTransitionOutcome.RELEASED.value
    assert second["outcome"] == LeaseTransitionOutcome.ALREADY_RELEASED.value
    assert missing["outcome"] == LeaseTransitionOutcome.ALREADY_RELEASED.value
    rows = await _rows(lease_session_maker)
    assert rows[0].lease_state == DurableLeaseState.RELEASED.value
    assert rows[0].released_at is not None
    # The tombstone keeps the generation as the runtime's high-water evidence.
    assert rows[0].fencing_generation == 5


@pytest.mark.asyncio
async def test_a_stale_release_cannot_free_the_replacement_holder(
    lease_session_maker,
) -> None:
    await _run("grant", [_grant("agent-run-1", fence=5)])
    await _run("release_one", [{"lease_id": "agent-run-1", "fencing_generation": 5}])
    await _run("grant", [_grant("agent-run-1", fence=6)])

    stale = await _run(
        "release_one", [{"lease_id": "agent-run-1", "fencing_generation": 5}]
    )

    assert stale["outcome"] == LeaseTransitionOutcome.STALE.value
    rows = await _rows(lease_session_maker)
    assert rows[0].lease_state == DurableLeaseState.HELD.value
    assert rows[0].fencing_generation == 6


@pytest.mark.asyncio
async def test_a_release_naming_another_profile_is_a_conflict(
    lease_session_maker,
) -> None:
    await _run("grant", [_grant("agent-run-1", fence=5)])

    result = await _run(
        "release_one",
        [
            {
                "lease_id": "agent-run-1",
                "profile_id": OTHER_PROFILE_REF,
                "fencing_generation": 5,
            }
        ],
    )

    assert result["outcome"] == LeaseTransitionOutcome.CONFLICT.value
    assert result["released"] is False
    rows = await _rows(lease_session_maker)
    assert rows[0].lease_state == DurableLeaseState.HELD.value


@pytest.mark.asyncio
async def test_describe_reconciles_a_lost_commit_acknowledgment(
    lease_session_maker,
) -> None:
    """The write committed; only its acknowledgment was lost."""

    await _run("grant", [_grant("agent-run-1", fence=5)])

    described = await _run("describe", [{"lease_id": "agent-run-1"}])
    missing = await _run("describe", [{"lease_id": "agent-run-absent"}])

    assert described["found"] is True
    assert described["lease"]["fencing_generation"] == 5
    assert described["lease"]["lease_state"] == DurableLeaseState.HELD.value
    assert described["lease"]["profile_id"] == PROFILE_REF
    assert missing == {"found": False, "lease_id": "agent-run-absent"}


@pytest.mark.asyncio
async def test_cleanup_is_requested_without_freeing_the_slot(
    lease_session_maker,
) -> None:
    """MoonLadderStudios/MoonMind#1089: expiry asks for cleanup, it does not free."""

    await _run("grant", [_grant("agent-run-1", fence=5)])

    result = await _run(
        "request_cleanup",
        [
            {
                "lease_id": "agent-run-1",
                "profile_id": PROFILE_REF,
                "fencing_generation": 5,
                "reason": "lease_expired",
            }
        ],
    )
    stale = await _run(
        "request_cleanup",
        [{"lease_id": "agent-run-1", "fencing_generation": 4}],
    )

    assert result["outcome"] == LeaseTransitionOutcome.CLEANUP_REQUESTED.value
    assert stale["outcome"] == LeaseTransitionOutcome.STALE.value
    rows = await _rows(lease_session_maker)
    assert rows[0].lease_state == DurableLeaseState.CLEANUP_REQUESTED.value
    assert rows[0].safe_metadata_json["cleanupReason"] == "lease_expired"

    # A cleanup-requested lease still spends its slot on recovery.
    loaded = await _run("load")
    assert [lease["leaseId"] for lease in loaded["leases"]] == ["agent-run-1"]
    assert loaded["leases"][0]["leaseState"] == (
        DurableLeaseState.CLEANUP_REQUESTED.value
    )


# ---------------------------------------------------------------------------
# Acceptance 6: mixed-generation cutover and shared scopes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_retained_snapshot_writer_cannot_delete_newer_authority(
    lease_session_maker,
) -> None:
    """A stale writer's snapshot is fenced by durable generations, not a patch."""

    await _run("grant", [_grant("agent-run-old", fence=2)])
    await _run("grant", [_grant("agent-run-new", fence=40)])

    result = await _run(
        "save",
        [
            {
                "workflow_id": "agent-run-old",
                "profile_id": PROFILE_REF,
                "leaseId": "agent-run-old",
                "ownerId": "agent-run-old",
                "fencingGeneration": 2,
            }
        ],
        writer_generation=2,
    )

    assert result == {"saved": 1, "preserved": 1}
    rows = {row.lease_id: row for row in await _rows(lease_session_maker)}
    assert set(rows) == {"agent-run-old", "agent-run-new"}
    assert rows["agent-run-new"].fencing_generation == 40
    assert rows["agent-run-new"].lease_state == DurableLeaseState.HELD.value


@pytest.mark.asyncio
async def test_a_pre_contract_snapshot_without_a_generation_still_replaces(
    lease_session_maker,
) -> None:
    """Replay safety: the recorded legacy command keeps its exact semantics."""

    await _run("grant", [_grant("agent-run-old", fence=2)])

    result = await _run(
        "save",
        [
            {
                "workflow_id": "agent-run-old",
                "profile_id": PROFILE_REF,
                "leaseId": "agent-run-old",
                "fencingGeneration": 2,
            }
        ],
    )

    assert result == {"saved": 1}
    assert len(await _rows(lease_session_maker)) == 1


@pytest.mark.asyncio
async def test_two_profiles_sharing_one_scope_keep_separate_accounting(
    lease_session_maker,
) -> None:
    await _run("grant", [_grant("agent-run-1", fence=1)])
    await _run(
        "grant", [_grant("agent-run-2", fence=2, profile_id=OTHER_PROFILE_REF)]
    )
    await _run(
        "grant",
        [_grant("agent-run-3", fence=3, scope_ref="provider-scope:other")],
    )

    shared = await _run("list_active", [{"capacity_scope_ref": SCOPE_REF}])
    assert sorted(lease["lease_id"] for lease in shared["leases"]) == [
        "agent-run-1",
        "agent-run-2",
    ]
    assert {lease["profile_id"] for lease in shared["leases"]} == {
        PROFILE_REF,
        OTHER_PROFILE_REF,
    }

    # Releasing one profile's lease leaves the other's scope accounting intact.
    await _run("release_one", [{"lease_id": "agent-run-1", "fencing_generation": 1}])
    still_shared = await _run("list_active", [{"capacity_scope_ref": SCOPE_REF}])
    live = [
        lease
        for lease in still_shared["leases"]
        if lease["lease_state"] == DurableLeaseState.HELD.value
    ]
    assert [lease["lease_id"] for lease in live] == ["agent-run-2"]


# ---------------------------------------------------------------------------
# Item 6 / acceptance 5: recovery reads and the manager restart
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_load_reports_unknown_state_and_identity_conflicts(
    lease_session_maker,
) -> None:
    await _run("grant", [_grant("agent-run-1", fence=1)])
    await _run("grant", [_grant("agent-run-2", fence=2)])
    # Two live leases claiming one deterministic owner: the second grant reused
    # the owner ID while the first row was never released. Nothing in the
    # schema forbids it, so recovery has to detect it rather than merge them.
    await _run(
        "grant",
        [{**_grant("agent-run-3", fence=3), "owner_id": "agent-run-2"}],
    )
    async with lease_session_maker() as session:
        # A state no contract version defines is reconciliation work, not free
        # capacity and not a row to drop.
        await session.execute(
            text(
                "UPDATE provider_profile_slot_leases SET lease_state = "
                "'quarantined' WHERE lease_id = 'agent-run-1'"
            )
        )
        await session.commit()

    loaded = await _run("load")

    assert [entry["lease_id"] for entry in loaded["unreconciled"]] == ["agent-run-1"]
    assert loaded["unreconciled"][0]["lease_state"] == "quarantined"
    assert len(loaded["conflicts"]) == 1
    assert loaded["conflicts"][0]["field"] == "owner_id"
    assert loaded["conflicts"][0]["value"] == "agent-run-2"
    assert {
        lease["lease_id"] for lease in loaded["conflicts"][0]["leases"]
    } == {"agent-run-2", "agent-run-3"}
    # The unknown-state row is not returned as usable capacity either.
    assert sorted(lease["leaseId"] for lease in loaded["leases"]) == [
        "agent-run-2",
        "agent-run-3",
    ]


@pytest.mark.asyncio
async def test_load_never_resurrects_a_released_row(lease_session_maker) -> None:
    await _run("grant", [_grant("agent-run-1", fence=11)])
    await _run("release_one", [{"lease_id": "agent-run-1", "fencing_generation": 11}])

    loaded = await _run("load")

    assert loaded["leases"] == []
    # The high-water mark outlives the tombstone, so a fresh manager never
    # reissues a generation a delayed release could still match.
    assert loaded["max_fencing_generation"] == 11


class _RestartedManager(MoonMindProviderProfileManagerWorkflow):
    """A manager restored from the durable ledger, with no external signals."""

    def __init__(self) -> None:
        super().__init__()
        self.reconnected: list[tuple[str, str, int | None]] = []

    async def _signal_slot_assigned(
        self,
        requester_workflow_id: str,
        profile_id: str,
        *,
        fencing_generation: int | None = None,
    ) -> None:
        self.reconnected.append(
            (requester_workflow_id, profile_id, fencing_generation)
        )


async def _restore_manager(monkeypatch: pytest.MonkeyPatch) -> _RestartedManager:
    activity = _activity()

    async def _execute_activity(name: str, payload: Any, **_kwargs: Any) -> Any:
        assert name == "provider_profile.sync_slot_leases"
        return await activity(**payload)

    monkeypatch.setattr(
        provider_profile_manager.workflow, "execute_activity", _execute_activity
    )
    monkeypatch.setattr(
        provider_profile_manager.workflow, "patched", lambda _patch_id: True
    )
    monkeypatch.setattr(
        provider_profile_manager.workflow,
        "info",
        lambda: SimpleNamespace(
            workflow_id="provider-profile-manager-opencode",
            run_id="run-1",
            task_queue="agent-runtime",
        ),
    )
    monkeypatch.setattr(
        provider_profile_manager.workflow,
        "logger",
        logging.getLogger("test.provider_profile_manager"),
    )

    manager = _RestartedManager()
    manager._runtime_id = RUNTIME_ID
    manager._durable_maintenance_queue = True
    manager._lease_transition_contract = True
    manager._purpose_aware_capacity_ledger = True
    for profile_ref in (PROFILE_REF, OTHER_PROFILE_REF):
        manager._profiles[profile_ref] = ProfileSlotState(
            profile_id=profile_ref,
            max_parallel_runs=8,
            cooldown_after_429_seconds=60,
            rate_limit_policy="cooldown",
            enabled=True,
            purpose_aware_capacity=True,
            capacity_scope_ref=SCOPE_REF,
        )
    return manager


@pytest.mark.asyncio
async def test_a_restart_restores_every_mode_owner_and_generation(
    lease_session_maker, monkeypatch: pytest.MonkeyPatch
) -> None:
    expiry = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    await _run(
        "grant",
        [{**_grant("agent-run-1", fence=11, scope_generation=3), "expiresAt": expiry}],
    )
    await _run(
        "grant",
        [
            _grant(
                "owner-maintenance",
                fence=12,
                profile_id=OTHER_PROFILE_REF,
                purpose="credential_validation",
                compatibility_class=(
                    CredentialLeaseMode.SINGLE_FLIGHT_VALIDATION.value
                ),
                scope_generation=3,
                identity="evidence-maint",
            )
        ],
    )
    await _run("grant", [_grant("agent-run-gone", fence=13)])
    await _run(
        "release_one", [{"lease_id": "agent-run-gone", "fencing_generation": 13}]
    )

    manager = await _restore_manager(monkeypatch)
    assert await manager._load_leases_from_db() is True

    held = manager._profiles[PROFILE_REF]
    metadata = held.lease_metadata["agent-run-1"]
    assert held.current_leases == ["agent-run-1"]
    assert metadata["compatibilityClass"] == CredentialLeaseMode.SHARED_EXECUTION.value
    assert metadata["scopeGeneration"] == 3
    assert metadata["capacityScopeRef"] == SCOPE_REF
    assert metadata["credentialGeneration"] == 7
    assert metadata["executionPlanRef"] == PLAN_REF
    assert metadata["evidenceIdentity"] == "evidence-1"
    assert metadata["fencingGeneration"] == 11
    assert metadata["ownerKind"] == "workflow"

    maintenance = manager._profiles[OTHER_PROFILE_REF]
    assert maintenance.current_leases == ["owner-maintenance"]
    assert maintenance.lease_metadata["owner-maintenance"]["compatibilityClass"] == (
        CredentialLeaseMode.SINGLE_FLIGHT_VALIDATION.value
    )

    # The released lease is not resurrected, and the sequence resumes above
    # every generation the runtime ever issued.
    assert "agent-run-gone" not in held.current_leases
    assert manager._lease_grant_sequence == 13
    # Only the execution owner is reconnected; maintenance owners are not.
    assert manager.reconnected == [("agent-run-1", PROFILE_REF, 11)]


@pytest.mark.asyncio
async def test_an_unreconciled_row_blocks_a_restarting_manager(
    lease_session_maker, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _run("grant", [_grant("agent-run-1", fence=1)])
    async with lease_session_maker() as session:
        await session.execute(
            text(
                "UPDATE provider_profile_slot_leases SET lease_state = "
                "'quarantined' WHERE lease_id = 'agent-run-1'"
            )
        )
        await session.commit()

    manager = await _restore_manager(monkeypatch)

    assert await manager._load_leases_from_db() is False
    assert manager._lease_index_conflicts[0]["kind"] == "unreconciled_state"
    assert manager._profiles[PROFILE_REF].current_leases == []


# ---------------------------------------------------------------------------
# Acceptance 8: no credential material reaches a lease row
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_credential_value_is_persisted_on_a_lease_row(
    lease_session_maker,
) -> None:
    await _run("grant", [_grant("agent-run-1", fence=1)])

    rows = await _rows(lease_session_maker)
    stored = " ".join(
        str(getattr(rows[0], column.name)) for column in rows[0].__table__.columns
    ).lower()
    for secret_marker in ("sk-", "ghp_", "bearer ", "password", "refresh_token"):
        assert secret_marker not in stored
    assert set(rows[0].safe_metadata_json or {}) <= {
        "evidenceIdentity",
        "cleanupReason",
        "releaseReason",
        "cleanupRequested",
    }
