"""Unit tests for Omnigent control-plane revisions and fencing enforcement.

Source: MoonLadderStudios/MoonMind#3704 ([Omnigent control plane 3/11]).

These exercise the single-writer logical behaviour of the fenced repository
operations, the stable conflict-outcome contract, delivery-ambiguity
reconciliation, durable cleanup authority, and bounded telemetry. The decisive
*concurrent* races (real row locks) are additionally proven on PostgreSQL in
``tests/integration/omnigent/test_control_plane_postgres.py``.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import Base, OmnigentCommand
from moonmind.omnigent.control_plane import (
    CleanupAuthorityRecord,
    ControlPlaneOutcome,
    FencingConflictError,
    FencingScope,
    NotCommandOwnerError,
    OmnigentControlPlaneStore,
    RevisionConflictError,
    telemetry,
)


@pytest_asyncio.fixture()
async def session_factory(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/fencing.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield maker
    await engine.dispose()


@pytest_asyncio.fixture()
async def store(session_factory):
    return OmnigentControlPlaneStore(session_factory)


@pytest.fixture(autouse=True)
def _reset_telemetry():
    telemetry.reset()
    yield
    telemetry.reset()


async def _new_session(store, session_id="s1"):
    async with store.transaction() as repos:
        return await repos.sessions.create(
            session_id=session_id,
            moonmind_workflow_id="wf-1",
            provider="codex",
            provider_session_ref=f"psess-{session_id}",
        )


# --- Mandatory fencing arguments --------------------------------------------


@pytest.mark.asyncio
async def test_lifecycle_methods_require_revision_and_fencing(store) -> None:
    await _new_session(store)
    # expected_revision and expected_fencing_generation are keyword-only with no
    # default: a lifecycle-changing write cannot omit the authority it observed.
    with pytest.raises(TypeError):
        async with store.transaction() as repos:
            await repos.sessions.update_lifecycle("s1", observed_state="running")


# --- Stable conflict outcomes ------------------------------------------------


@pytest.mark.asyncio
async def test_compare_and_swap_returns_revision_conflict(store) -> None:
    created = await _new_session(store)
    async with store.transaction() as repos:
        result = await repos.sessions.compare_and_swap_session(
            "s1",
            expected_revision=created.revision + 5,  # stale
            expected_fencing_generation=0,
            observed_state="running",
        )
    assert result.outcome is ControlPlaneOutcome.REVISION_CONFLICT
    assert result.conflicted is True
    # The current authority is returned so the reconciler converges without a
    # second read; the stale write did not land.
    assert result.record.observed_state is None
    assert telemetry.snapshot()[(telemetry.REVISION_CONFLICTS, "session_supervisor")] == 1


@pytest.mark.asyncio
async def test_update_lifecycle_raises_typed_conflicts(store) -> None:
    created = await _new_session(store)
    with pytest.raises(RevisionConflictError):
        async with store.transaction() as repos:
            await repos.sessions.update_lifecycle(
                "s1",
                expected_revision=created.revision + 1,
                expected_fencing_generation=0,
                observed_state="running",
            )
    with pytest.raises(FencingConflictError):
        async with store.transaction() as repos:
            await repos.sessions.update_lifecycle(
                "s1",
                expected_revision=created.revision,
                expected_fencing_generation=7,  # superseded
                observed_state="running",
            )


# --- Fencing generations are live authority ----------------------------------


@pytest.mark.asyncio
async def test_acquire_generation_supersedes_prior_owner(store) -> None:
    created = await _new_session(store)
    async with store.transaction() as repos:
        superseded = await repos.sessions.acquire_fencing_generation(
            "s1",
            FencingScope.SESSION_SUPERVISOR,
            expected_revision=created.revision,
        )
    assert superseded.fencing_generation == 1
    # The former owner (generation 0) is fenced out.
    async with store.transaction() as repos:
        stale = await repos.sessions.compare_and_swap_session(
            "s1",
            expected_revision=superseded.revision,
            expected_fencing_generation=0,
            observed_state="running",
        )
    assert stale.outcome is ControlPlaneOutcome.FENCING_CONFLICT
    assert telemetry.snapshot()[(telemetry.FENCING_CONFLICTS, "session_supervisor")] == 1


@pytest.mark.asyncio
async def test_acquire_lease_generation_fences_lease_owner_write(store) -> None:
    created = await _new_session(store)
    async with store.transaction() as repos:
        leased = await repos.sessions.acquire_fencing_generation(
            "s1", FencingScope.PROVIDER_PROFILE_LEASE, expected_revision=created.revision
        )
    assert leased.provider_profile_generation == 1
    # A profile-lease owner presenting the superseded lease generation is fenced.
    async with store.transaction() as repos:
        result = await repos.sessions.compare_and_swap_session(
            "s1",
            expected_revision=leased.revision,
            expected_fencing_generation=leased.fencing_generation,
            expected_provider_profile_generation=0,
            observed_state="running",
        )
    assert result.outcome is ControlPlaneOutcome.FENCING_CONFLICT
    assert (
        telemetry.snapshot()[(telemetry.FENCING_CONFLICTS, "provider_profile_lease")]
        == 1
    )


# --- Delayed events retained, not regressive ---------------------------------


@pytest.mark.asyncio
async def test_frontier_advances_then_stale_epoch_is_retained(store) -> None:
    created = await _new_session(store)
    async with store.transaction() as repos:
        advanced = await repos.sessions.advance_observation_frontier(
            "s1",
            expected_revision=created.revision,
            expected_fencing_generation=0,
            provider_event_cursor="cursor-2",
        )
    assert advanced.outcome is ControlPlaneOutcome.APPLIED
    async with store.transaction() as repos:
        current = await repos.sessions.get("s1")
        superseded = await repos.sessions.acquire_fencing_generation(
            "s1", FencingScope.SESSION_SUPERVISOR, expected_revision=current.revision
        )
    async with store.transaction() as repos:
        stale = await repos.sessions.advance_observation_frontier(
            "s1",
            expected_revision=superseded.revision,
            expected_fencing_generation=0,  # old epoch
            provider_event_cursor="cursor-1",
        )
    assert stale.outcome is ControlPlaneOutcome.FENCING_CONFLICT
    async with store.transaction() as repos:
        stored = await repos.sessions.get("s1")
    assert stored.provider_event_cursor == "cursor-2"
    assert telemetry.snapshot()[(telemetry.STALE_OBSERVATION_RETAINED, "session")] == 1


# --- Command claim / delivery-unknown reconciliation -------------------------


@pytest.mark.asyncio
async def test_command_claimed_once_and_delivery_confirmed(store) -> None:
    await _new_session(store)
    async with store.transaction() as repos:
        await repos.commands.record(
            command_id="c1",
            session_id="s1",
            command_type="ensure_host",
            idempotency_key="cmd-1",
            payload_digest="digest",
        )
        first = await repos.commands.claim_command("c1", owner_class="supervisor")
        second = await repos.commands.claim_command("c1", owner_class="supervisor")
        confirmed = await repos.commands.record_command_delivery(
            "c1", owner_class="supervisor", outcome=ControlPlaneOutcome.APPLIED
        )
    assert first.outcome is ControlPlaneOutcome.APPLIED
    assert second.outcome is ControlPlaneOutcome.ALREADY_APPLIED
    assert confirmed.outcome is ControlPlaneOutcome.APPLIED
    assert confirmed.record.status == "applied"
    assert confirmed.record.delivery_ambiguous is False
    # The duplicate claim is suppressed telemetry, not a re-execution.
    assert (
        telemetry.snapshot()[(telemetry.DUPLICATE_COMMAND_SUPPRESSED, "session")] == 1
    )


@pytest.mark.asyncio
async def test_delivery_non_owner_is_rejected(store) -> None:
    await _new_session(store)
    async with store.transaction() as repos:
        await repos.commands.record(
            command_id="c1",
            session_id="s1",
            command_type="ensure_host",
            idempotency_key="cmd-1",
            payload_digest="digest",
        )
        await repos.commands.claim_command("c1", owner_class="supervisor")
    with pytest.raises(NotCommandOwnerError):
        async with store.transaction() as repos:
            await repos.commands.record_command_delivery(
                "c1", owner_class="other", outcome=ControlPlaneOutcome.APPLIED
            )


# --- Cleanup authority -------------------------------------------------------


@pytest.mark.asyncio
async def test_cleanup_claim_and_complete_happy_path(store) -> None:
    await _new_session(store)
    async with store.transaction() as repos:
        claim = await repos.cleanup.claim_cleanup("s1", owner_class="janitor")
        assert claim.outcome is ControlPlaneOutcome.APPLIED
        # A second janitor cannot claim a live cleanup.
        conflict = await repos.cleanup.claim_cleanup("s1", owner_class="janitor-2")
        assert conflict.outcome is ControlPlaneOutcome.NOT_OWNER
        done = await repos.cleanup.complete_cleanup(
            "s1",
            generation=claim.record.generation,
            owner_class="janitor",
            session_repository=repos.sessions,
        )
    assert done.outcome is ControlPlaneOutcome.APPLIED
    assert done.record.state == "complete"
    assert telemetry.snapshot()[(telemetry.CLEANUP_CLAIM_CONFLICTS, "cleanup")] == 1


@pytest.mark.asyncio
async def test_cleanup_complete_fenced_against_renewed_lease(store) -> None:
    created = await _new_session(store)
    async with store.transaction() as repos:
        leased = await repos.sessions.acquire_fencing_generation(
            "s1", FencingScope.HOST_LEASE, expected_revision=created.revision
        )
        claim = await repos.cleanup.claim_cleanup(
            "s1",
            owner_class="janitor",
            fenced_host_generation=leased.host_lease_generation,
        )
    # Renew the host lease under a strictly newer generation.
    async with store.transaction() as repos:
        current = await repos.sessions.get("s1")
        await repos.sessions.acquire_fencing_generation(
            "s1", FencingScope.HOST_LEASE, expected_revision=current.revision
        )
    async with store.transaction() as repos:
        fenced = await repos.cleanup.complete_cleanup(
            "s1",
            generation=claim.record.generation,
            owner_class="janitor",
            session_repository=repos.sessions,
        )
    assert fenced.outcome is ControlPlaneOutcome.FENCING_CONFLICT


# --- Legacy compatibility / bounded migration policy -------------------------


@pytest.mark.asyncio
async def test_legacy_command_row_defaults_to_revision_one(store, session_factory) -> None:
    # A row written before #3704 (no explicit revision/owner_class) reads back
    # with the fail-closed defaults, not as universally-current authority.
    await _new_session(store)
    async with session_factory() as session:
        session.add(
            OmnigentCommand(
                command_id="legacy",
                session_id="s1",
                command_type="ensure_host",
                idempotency_key="legacy-key",
                payload_digest="digest",
            )
        )
        await session.commit()
    async with store.transaction() as repos:
        record = await repos.commands.get("legacy")
    assert record.revision == 1
    assert record.owner_class is None
    assert record.status == "pending"


@pytest.mark.asyncio
async def test_absent_cleanup_authority_is_unclaimed(store) -> None:
    # A session with no cleanup-authority row means *unclaimed* (fail-closed
    # default), never "already cleaned / universally current".
    await _new_session(store)
    async with store.transaction() as repos:
        assert await repos.cleanup.get("s1") is None
    # Completing a cleanup that was never claimed is refused.
    async with store.transaction() as repos:
        result = await repos.cleanup.complete_cleanup(
            "s1", generation=1, owner_class="janitor", session_repository=repos.sessions
        )
    assert result.outcome is ControlPlaneOutcome.NOT_OWNER
    assert isinstance(result.record, CleanupAuthorityRecord)
    assert result.record.state == "unclaimed"
