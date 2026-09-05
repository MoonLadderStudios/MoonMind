"""Shared-scope unit ownership established from real durable rows.

Source: MoonLadderStudios/MoonMind#3882 (AC1, AC3, AC6, AC7).

The findings this covers are all about *whose* units a lease spends, and that
question is only answerable from the durable ledger:

* a lease is bound to the allowance and generation that admitted it, so a
  manager restart must restore that binding rather than re-deriving membership
  from whichever scope the profile names now;
* a profile repointed at a different allowance while work is in flight must not
  transfer its active units; and
* a rate-limit report arriving after that repoint must be validated against the
  restored authority, not accepted because the caller quoted a scope ref.

Everything below goes through the real ``provider_profile.sync_slot_leases``
Activity and a real PostgreSQL cluster.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import (
    ManagedAgentProviderProfile,
    ProviderCapacityScope,
    ProviderProfileSlotLease,
)
from moonmind.workflows.temporal.artifacts import TemporalArtifactActivities
from moonmind.workflows.temporal.workflows import provider_profile_manager
from moonmind.workflows.temporal.workflows.provider_profile_manager import (
    CapacityScopeState,
    MoonMindProviderProfileManagerWorkflow,
    ProfileSlotState,
)

pytestmark = [pytest.mark.integration, pytest.mark.integration_ci]

RUNTIME_ID = "opencode"
SHARED_SCOPE = "opencode-zen-account"
OTHER_SCOPE = "opencode-zen-overflow"
PROFILE_A = "opencode-zen-a"
PROFILE_B = "opencode-zen-b"

_TABLES = [
    ManagedAgentProviderProfile.__table__,
    ProviderCapacityScope.__table__,
    ProviderProfileSlotLease.__table__,
]


def _create_tables(sync_conn) -> None:
    for table in _TABLES:
        table.create(sync_conn, checkfirst=True)


def _drop_tables(sync_conn) -> None:
    for table in reversed(_TABLES):
        table.drop(sync_conn, checkfirst=True)


@pytest_asyncio.fixture()
async def scope_session_maker(control_plane_postgres_url, monkeypatch):
    """Bind the production session factory to an ephemeral PostgreSQL cluster."""

    import api_service.db.base as db_base

    engine = create_async_engine(control_plane_postgres_url)
    async with engine.begin() as conn:
        await conn.run_sync(_create_tables)
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(db_base, "async_session_maker", maker)
    try:
        yield maker
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(_drop_tables)
        await engine.dispose()


def _lease_activity():
    return TemporalArtifactActivities(service=None).provider_profile_sync_slot_leases


def _grant_payload(
    *,
    lease_id: str,
    profile_id: str,
    capacity_scope_ref: str,
    scope_generation: int,
    fencing_generation: int,
) -> dict[str, Any]:
    return {
        "lease_id": lease_id,
        "workflow_id": lease_id,
        "profile_id": profile_id,
        "owner_id": lease_id,
        "owner_kind": "workflow",
        "purpose": "execution_omnigent",
        "fencing_generation": fencing_generation,
        "scope_generation": scope_generation,
        "capacity_scope_ref": capacity_scope_ref,
        "lease_state": "held",
        "expiresAt": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        "ownerIsWorkflow": True,
    }


def _patch_workflow_module(monkeypatch: pytest.MonkeyPatch) -> None:
    activity = _lease_activity()

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
        "now",
        lambda: datetime.now(timezone.utc),
    )
    monkeypatch.setattr(
        provider_profile_manager.workflow,
        "info",
        lambda: SimpleNamespace(
            workflow_id="provider-profile-manager:opencode",
            run_id="run-1",
            task_queue="agent-runtime",
            continued_run_id=None,
        ),
    )
    monkeypatch.setattr(
        provider_profile_manager.workflow,
        "logger",
        logging.getLogger("test.provider_scope_accounting"),
    )


class _RestartedManager(MoonMindProviderProfileManagerWorkflow):
    """A manager restored from the durable ledger, with no external signals."""

    def __init__(self) -> None:
        super().__init__()
        self.reconnected: list[tuple[str, str]] = []

    async def _signal_slot_assigned(
        self,
        requester_workflow_id: str,
        profile_id: str,
        *,
        fencing_generation: int | None = None,
    ) -> None:
        self.reconnected.append((requester_workflow_id, profile_id))


async def _restart_manager(
    monkeypatch: pytest.MonkeyPatch,
    *,
    profile_scopes: dict[str, str],
    scopes: dict[str, CapacityScopeState],
) -> _RestartedManager:
    _patch_workflow_module(monkeypatch)
    manager = _RestartedManager()
    manager._runtime_id = RUNTIME_ID
    manager._purpose_aware_leases = True
    manager._purpose_aware_capacity_ledger = True
    manager._durable_maintenance_queue = True
    manager._scope_accounting = True
    for profile_id, scope_ref in profile_scopes.items():
        manager._profiles[profile_id] = ProfileSlotState(
            profile_id=profile_id,
            max_parallel_runs=8,
            cooldown_after_429_seconds=60,
            rate_limit_policy="cooldown",
            enabled=True,
            capacity_scope_ref=scope_ref,
            effective_limit=8,
            purpose_aware_capacity=True,
        )
    manager._scopes.update(scopes)
    assert await manager._load_leases_from_db() is True
    return manager


@pytest.mark.asyncio
async def test_restored_leases_are_counted_against_the_scope_that_admitted_them(
    scope_session_maker, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC1/AC6: two profiles under one shared allowance, established from rows.

    Both profiles are configured for 8, so a manager that summed per-profile
    ceilings would believe it had 16 units. The shared row says 10, and the
    restored leases have to be attributed to it.
    """

    activity = _lease_activity()
    for index in range(6):
        profile_id = PROFILE_A if index % 2 == 0 else PROFILE_B
        granted = await activity(
            runtime_id=RUNTIME_ID,
            leases=[
                _grant_payload(
                    lease_id=f"agent-run-{index}",
                    profile_id=profile_id,
                    capacity_scope_ref=SHARED_SCOPE,
                    scope_generation=1,
                    fencing_generation=index + 1,
                )
            ],
            action="grant",
        )
        assert granted == {"granted": True, "duplicate": False}

    manager = await _restart_manager(
        monkeypatch,
        profile_scopes={PROFILE_A: SHARED_SCOPE, PROFILE_B: SHARED_SCOPE},
        scopes={
            SHARED_SCOPE: CapacityScopeState(
                scope_ref=SHARED_SCOPE,
                runtime_id=RUNTIME_ID,
                configured_limit=10,
                effective_limit=10,
            )
        },
    )

    assert manager._scope_active_units(SHARED_SCOPE) == 6
    for profile_id in (PROFILE_A, PROFILE_B):
        profile = manager._profiles[profile_id]
        assert profile.execution_lease_count == 3
        # Every restored lease names the allowance the durable row recorded.
        for lease_id in profile.current_leases:
            assert manager._lease_scope_ref(profile, lease_id) == SHARED_SCOPE
            assert profile.lease_metadata[lease_id]["scopeGeneration"] == 1
    assert manager._profile_admitted_by_capacity(manager._profiles[PROFILE_A]) is True


@pytest.mark.asyncio
async def test_repointing_a_busy_profile_does_not_transfer_its_restored_units(
    scope_session_maker, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC6: a scope change while work is active preserves unit ownership."""

    activity = _lease_activity()
    for index in range(4):
        await activity(
            runtime_id=RUNTIME_ID,
            leases=[
                _grant_payload(
                    lease_id=f"agent-run-{index}",
                    profile_id=PROFILE_A,
                    capacity_scope_ref=SHARED_SCOPE,
                    scope_generation=1,
                    fencing_generation=index + 1,
                )
            ],
            action="grant",
        )

    # The operator repointed the profile between the grants and this restart.
    manager = await _restart_manager(
        monkeypatch,
        profile_scopes={PROFILE_A: OTHER_SCOPE},
        scopes={
            SHARED_SCOPE: CapacityScopeState(
                scope_ref=SHARED_SCOPE,
                runtime_id=RUNTIME_ID,
                configured_limit=10,
                effective_limit=10,
            ),
            OTHER_SCOPE: CapacityScopeState(
                scope_ref=OTHER_SCOPE,
                runtime_id=RUNTIME_ID,
                configured_limit=2,
                effective_limit=2,
            ),
        },
    )

    assert manager._scope_active_units(SHARED_SCOPE) == 4
    # The new allowance is empty: nothing moved onto it by re-reading metadata.
    assert manager._scope_active_units(OTHER_SCOPE) == 0
    assert manager._profile_admitted_by_capacity(manager._profiles[PROFILE_A]) is True


@pytest.mark.asyncio
async def test_a_report_for_a_repointed_lease_is_validated_against_its_own_scope(
    scope_session_maker, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC3: report ownership is established from the restored lease, not the caller."""

    await _lease_activity()(
        runtime_id=RUNTIME_ID,
        leases=[
            _grant_payload(
                lease_id="agent-run-0",
                profile_id=PROFILE_A,
                capacity_scope_ref=SHARED_SCOPE,
                scope_generation=1,
                fencing_generation=1,
            )
        ],
        action="grant",
    )
    manager = await _restart_manager(
        monkeypatch,
        profile_scopes={PROFILE_A: OTHER_SCOPE},
        scopes={
            SHARED_SCOPE: CapacityScopeState(
                scope_ref=SHARED_SCOPE,
                runtime_id=RUNTIME_ID,
                configured_limit=10,
                effective_limit=10,
            ),
            OTHER_SCOPE: CapacityScopeState(
                scope_ref=OTHER_SCOPE,
                runtime_id=RUNTIME_ID,
                configured_limit=4,
                effective_limit=4,
            ),
        },
    )

    # A report naming the profile's *new* scope is refused: the attempt that
    # saw the 429 was admitted under the old one.
    manager.report_cooldown(
        {
            "profile_id": PROFILE_A,
            "lease_id": "agent-run-0",
            "failure_class": "rate_limit",
            "retry_after_seconds": 120,
            "report_id": "report-wrong-scope",
            "capacity_scope_ref": OTHER_SCOPE,
        }
    )
    assert manager._scopes[OTHER_SCOPE].effective_limit == 4
    assert manager._scopes[SHARED_SCOPE].effective_limit == 10

    # The same report against the allowance that actually admitted it applies,
    # and applies exactly once.
    for _ in range(2):
        manager.report_cooldown(
            {
                "profile_id": PROFILE_A,
                "lease_id": "agent-run-0",
                "failure_class": "rate_limit",
                "retry_after_seconds": 120,
                "report_id": "report-right-scope",
                "capacity_scope_ref": SHARED_SCOPE,
            }
        )
    assert manager._scopes[SHARED_SCOPE].effective_limit == 5
    assert manager._scopes[OTHER_SCOPE].effective_limit == 4


@pytest.mark.asyncio
async def test_adapted_scope_state_is_persisted_and_restored(
    scope_session_maker, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC4/AC7: a reduction survives the manager that made it.

    Held only in workflow history, a reduced allowance and its cooldown are
    lost the moment the manager is reset or replaced — and the replacement
    resumes granting at the full configured ceiling into a provider that is
    still rate-limiting.
    """

    async with scope_session_maker() as session:
        session.add(
            ProviderCapacityScope(
                scope_ref=SHARED_SCOPE,
                runtime_id=RUNTIME_ID,
                provider_class="opencode",
                configured_limit=10,
                effective_limit=10,
            )
        )
        await session.commit()

    activities = TemporalArtifactActivities(service=None)
    cooldown_until = datetime.now(timezone.utc) + timedelta(seconds=300)
    result = await activities.provider_profile_sync_capacity_scope(
        scope_ref=SHARED_SCOPE,
        generation=1,
        effective_limit=5,
        cooldown_until=cooldown_until.isoformat(),
        backpressure_state="cooldown",
        last_decrease_at=datetime.now(timezone.utc).isoformat(),
    )
    assert result["persisted"] is True
    assert result["effective_limit"] == 5

    # A fresh manager with no history restores the reduction from the row.
    _patch_workflow_module(monkeypatch)
    manager = _RestartedManager()
    manager._runtime_id = RUNTIME_ID
    manager._purpose_aware_capacity_ledger = True
    manager._scope_accounting = True
    manager._profiles[PROFILE_A] = ProfileSlotState(
        profile_id=PROFILE_A,
        max_parallel_runs=8,
        cooldown_after_429_seconds=60,
        rate_limit_policy="cooldown",
        enabled=True,
        capacity_scope_ref=SHARED_SCOPE,
        effective_limit=8,
        purpose_aware_capacity=True,
    )
    listed = await activities.provider_profile_list(runtime_id=RUNTIME_ID)
    manager._apply_scope_sync(listed["scopes"])

    scope = manager._scopes[SHARED_SCOPE]
    assert scope.configured_limit == 10
    assert scope.effective_limit == 5
    assert scope.backpressure_state == "cooldown"
    assert scope.cooldown_until is not None
    assert manager._profile_scope_available(manager._profiles[PROFILE_A]) is False


@pytest.mark.asyncio
async def test_a_stale_manager_cannot_overwrite_a_replacement_generation(
    scope_session_maker,
) -> None:
    """AC3: the persistence write is fenced on the scope generation."""

    async with scope_session_maker() as session:
        session.add(
            ProviderCapacityScope(
                scope_ref=SHARED_SCOPE,
                runtime_id=RUNTIME_ID,
                provider_class="opencode",
                generation=4,
                configured_limit=10,
                effective_limit=10,
            )
        )
        await session.commit()

    activities = TemporalArtifactActivities(service=None)
    stale = await activities.provider_profile_sync_capacity_scope(
        scope_ref=SHARED_SCOPE, generation=3, effective_limit=1
    )
    assert stale == {"persisted": False, "reason": "stale_generation"}

    async with scope_session_maker() as session:
        row = await session.get(ProviderCapacityScope, SHARED_SCOPE)
        assert row is not None
        assert row.effective_limit == 10

    # A scope this manager does not own is never created by the write path.
    missing = await activities.provider_profile_sync_capacity_scope(
        scope_ref="never-provisioned", generation=1, effective_limit=2
    )
    assert missing == {"persisted": False, "reason": "scope_not_found"}


@pytest.mark.asyncio
async def test_a_disabled_scope_is_not_re_enabled_by_a_persisted_write(
    scope_session_maker,
) -> None:
    """AC4: disabled is operator policy; a workflow never clears it."""

    async with scope_session_maker() as session:
        session.add(
            ProviderCapacityScope(
                scope_ref=SHARED_SCOPE,
                runtime_id=RUNTIME_ID,
                provider_class="opencode",
                configured_limit=10,
                effective_limit=2,
                backpressure_state="disabled",
            )
        )
        await session.commit()

    result = await TemporalArtifactActivities(
        service=None
    ).provider_profile_sync_capacity_scope(
        scope_ref=SHARED_SCOPE,
        generation=1,
        effective_limit=10,
        backpressure_state="healthy",
    )
    assert result["backpressure_state"] == "disabled"

    async with scope_session_maker() as session:
        row = await session.get(ProviderCapacityScope, SHARED_SCOPE)
        assert row is not None
        assert row.backpressure_state == "disabled"


@pytest.mark.asyncio
async def test_the_authoritative_profile_list_returns_scope_rows(
    scope_session_maker, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC7: the manager's scope authority comes from real rows, not a fallback."""

    async with scope_session_maker() as session:
        session.add(
            ProviderCapacityScope(
                scope_ref=SHARED_SCOPE,
                runtime_id=RUNTIME_ID,
                provider_class="opencode",
                generation=3,
                configured_limit=10,
                effective_limit=6,
                backpressure_state="reduced",
            )
        )
        for profile_id in (PROFILE_A, PROFILE_B):
            session.add(
                ManagedAgentProviderProfile(
                    profile_id=profile_id,
                    runtime_id=RUNTIME_ID,
                    provider_id="opencode",
                    capacity_scope_ref=SHARED_SCOPE,
                    max_parallel_runs=8,
                    enabled=True,
                )
            )
        await session.commit()

    # A profile holding a durable lease is returned regardless of launch
    # readiness: the manager has to keep accounting for capacity it granted.
    for index, profile_id in enumerate((PROFILE_A, PROFILE_B)):
        await _lease_activity()(
            runtime_id=RUNTIME_ID,
            leases=[
                _grant_payload(
                    lease_id=f"agent-run-{index}",
                    profile_id=profile_id,
                    capacity_scope_ref=SHARED_SCOPE,
                    scope_generation=3,
                    fencing_generation=index + 1,
                )
            ],
            action="grant",
        )

    result = await TemporalArtifactActivities(service=None).provider_profile_list(
        runtime_id=RUNTIME_ID
    )
    scopes = {entry["scope_ref"]: entry for entry in result["scopes"]}
    assert scopes[SHARED_SCOPE]["configured_limit"] == 10
    assert scopes[SHARED_SCOPE]["effective_limit"] == 6
    assert scopes[SHARED_SCOPE]["generation"] == 3
    assert scopes[SHARED_SCOPE]["backpressure_state"] == "reduced"

    _patch_workflow_module(monkeypatch)
    manager = _RestartedManager()
    manager._runtime_id = RUNTIME_ID
    manager._purpose_aware_capacity_ledger = True
    manager._scope_accounting = True
    manager._apply_profile_sync(result["profiles"], authoritative=True)
    manager._apply_scope_sync(result["scopes"])

    scope = manager._scopes[SHARED_SCOPE]
    assert (scope.configured_limit, scope.effective_limit) == (10, 6)
    assert scope.backpressure_state == "reduced"
    assert manager._scopes_requiring_reconciliation == set()
    # Both profiles are configured for 8 but share one allowance of 6.
    for profile_id in (PROFILE_A, PROFILE_B):
        assert manager._profiles[profile_id].max_parallel_runs == 8
    projection = manager.get_state()["capacity_scopes"][SHARED_SCOPE]
    assert projection["effective_limit"] == 6
    assert projection["member_profile_ids"] == sorted([PROFILE_A, PROFILE_B])
