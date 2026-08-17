"""Unit coverage for the Omnigent control-plane concurrency repository.

MoonLadderStudios/MoonMind#3704. These exercise the typed compare-and-swap and
fencing semantics deterministically on SQLite; real concurrent-transaction proofs
run against PostgreSQL in
``tests/integration/omnigent/test_control_plane_concurrency_postgres.py``.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import Base, OmnigentBridgeSession, OmnigentTurnAttempt
from moonmind.omnigent.concurrency import (
    Aggregate,
    ConflictOutcome,
    OmnigentControlPlaneRepository,
    counter_snapshot,
    record_concurrency_event,
    reset_counters,
)

pytestmark = pytest.mark.asyncio

SESSION_ID = "bridge-1"


async def _make_factory(tmp_path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path}/control-plane.db"
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def _seed_session(session_factory, *, session_id: str = SESSION_ID, status="running"):
    async with session_factory() as session, session.begin():
        session.add(
            OmnigentBridgeSession(
                bridge_session_id=session_id,
                provider="openai",
                compatibility_profile="codex-native",
                moonmind_workflow_id="wf-1",
                moonmind_agent_run_id="run-1",
                idempotency_key=f"idem-{session_id}",
                omnigent_endpoint_ref="default",
                host_type="external",
                status=status,
            )
        )


async def _seed_turn(session_factory, *, turn_id="turn-1", session_id=SESSION_ID):
    async with session_factory() as session, session.begin():
        session.add(
            OmnigentTurnAttempt(
                turn_attempt_id=turn_id,
                bridge_session_id=session_id,
                attempt_index=1,
                session_revision_observed=1,
            )
        )


@pytest.fixture(autouse=True)
def _clear_counters():
    reset_counters()
    yield
    reset_counters()


@pytest_asyncio.fixture
async def repo(tmp_path):
    factory = await _make_factory(tmp_path)
    await _seed_session(factory)
    yield OmnigentControlPlaneRepository(factory)
    await factory.kw["bind"].dispose()


# -- session -----------------------------------------------------------------


async def test_session_cas_applies_and_advances_revision(repo):
    snapshot = await repo.load_for_update(Aggregate.SESSION, SESSION_ID)
    assert snapshot["revision"] == 1
    result = await repo.compare_and_swap_session(
        SESSION_ID,
        expected_revision=1,
        expected_supervisor_generation=1,
        values={"status": "harvesting"},
    )
    assert result.outcome is ConflictOutcome.APPLIED
    assert result.revision == 2
    after = await repo.load_for_update(Aggregate.SESSION, SESSION_ID)
    assert after["revision"] == 2 and after["status"] == "harvesting"


async def test_session_cas_stale_revision_conflicts(repo):
    await repo.compare_and_swap_session(
        SESSION_ID,
        expected_revision=1,
        expected_supervisor_generation=1,
        values={"status": "harvesting"},
    )
    stale = await repo.compare_and_swap_session(
        SESSION_ID,
        expected_revision=1,
        expected_supervisor_generation=1,
        values={"status": "running"},
    )
    assert stale.outcome is ConflictOutcome.REVISION_CONFLICT
    assert stale.observed["status"] == "harvesting"
    assert counter_snapshot()[("revision_conflict", "session")] == 1


async def test_session_cas_wrong_supervisor_generation_is_fencing_conflict(repo):
    result = await repo.compare_and_swap_session(
        SESSION_ID,
        expected_revision=1,
        expected_supervisor_generation=99,
        values={"status": "harvesting"},
    )
    assert result.outcome is ConflictOutcome.FENCING_CONFLICT
    assert counter_snapshot()[("fencing_conflict", "session")] == 1


async def test_stale_writer_cannot_overwrite_newer_terminal_state(repo):
    # A superseded supervisor advances the session to a terminal state.
    applied = await repo.compare_and_swap_session(
        SESSION_ID,
        expected_revision=1,
        expected_supervisor_generation=1,
        values={"status": "terminal"},
    )
    assert applied.outcome is ConflictOutcome.APPLIED
    stale = await repo.compare_and_swap_session(
        SESSION_ID,
        expected_revision=1,
        expected_supervisor_generation=1,
        values={"status": "running"},
        immutable_states=frozenset({"terminal"}),
    )
    assert stale.outcome is ConflictOutcome.IMMUTABLE_AUTHORITY_CONFLICT
    assert counter_snapshot()[("immutable_authority_conflict", "session")] == 1


# -- turn --------------------------------------------------------------------


async def test_turn_cas_applies_and_rejects_terminal_regression(repo, tmp_path):
    factory = repo._session_factory
    await _seed_turn(factory)
    applied = await repo.compare_and_swap_turn(
        "turn-1",
        expected_revision=1,
        expected_fencing_generation=1,
        values={"status": "submitted", "terminal": True},
    )
    assert applied.outcome is ConflictOutcome.APPLIED and applied.revision == 2

    regress = await repo.compare_and_swap_turn(
        "turn-1",
        expected_revision=2,
        expected_fencing_generation=1,
        values={"status": "running", "terminal": False},
    )
    assert regress.outcome is ConflictOutcome.IMMUTABLE_AUTHORITY_CONFLICT


async def test_turn_cas_fencing_conflict(repo):
    await _seed_turn(repo._session_factory, turn_id="turn-2")
    result = await repo.compare_and_swap_turn(
        "turn-2",
        expected_revision=1,
        expected_fencing_generation=5,
        values={"status": "submitted"},
    )
    assert result.outcome is ConflictOutcome.FENCING_CONFLICT


async def test_observation_frontier_advances_and_retains_stale(repo):
    await _seed_turn(repo._session_factory, turn_id="turn-3")
    advanced = await repo.advance_observation_frontier(
        "turn-3", observed_sequence=10, expected_revision=1
    )
    assert advanced.outcome is ConflictOutcome.APPLIED and advanced.revision == 2

    delayed = await repo.advance_observation_frontier(
        "turn-3", observed_sequence=7, expected_revision=2
    )
    assert delayed.outcome is ConflictOutcome.ALREADY_APPLIED
    assert delayed.observed["observation_frontier"] == 10
    assert counter_snapshot()[("stale_observation_retained", "observation")] == 1


# -- fencing -----------------------------------------------------------------


async def test_acquire_fencing_generation_is_monotonic(repo):
    scope = f"session_supervisor:{SESSION_ID}"
    first = await repo.acquire_fencing_generation(scope, scope_kind="session_supervisor")
    second = await repo.acquire_fencing_generation(scope, scope_kind="session_supervisor")
    third = await repo.acquire_fencing_generation(scope, scope_kind="session_supervisor")
    assert [first, second, third] == [1, 2, 3]
    assert await repo.current_fencing_generation(scope) == 3
    assert await repo.current_fencing_generation("never") == 0


# -- commands ----------------------------------------------------------------


async def _claim(repo, **overrides):
    payload = {
        "command_id": overrides.get("command_id", "cmd-1"),
        "bridge_session_id": SESSION_ID,
        "command_type": "post_first_message",
        "payload_digest": overrides.get("payload_digest", "digest-a"),
        "idempotency_key": overrides.get("idempotency_key", "cmd-idem-1"),
        "expected_session_revision": overrides.get("expected_session_revision", 1),
        "owner_class": "session_supervisor",
        "owner": overrides.get("owner", "owner-1"),
    }
    payload.update(
        {
            k: v
            for k, v in overrides.items()
            if k in {"expected_supervisor_generation", "fencing_generations"}
        }
    )
    return await repo.claim_command(**payload)


async def test_command_claim_is_once_and_duplicate_is_suppressed(repo):
    first = await _claim(repo)
    assert first.outcome is ConflictOutcome.APPLIED

    duplicate = await _claim(repo)
    assert duplicate.outcome is ConflictOutcome.ALREADY_APPLIED
    assert counter_snapshot()[("duplicate_command_suppressed", "command")] == 1


async def test_command_reused_idempotency_with_new_payload_is_immutable_conflict(repo):
    await _claim(repo)
    conflict = await _claim(repo, payload_digest="digest-b")
    assert conflict.outcome is ConflictOutcome.IMMUTABLE_AUTHORITY_CONFLICT


async def test_command_claim_rejects_stale_session_revision(repo):
    result = await _claim(repo, expected_session_revision=99)
    assert result.outcome is ConflictOutcome.REVISION_CONFLICT
    assert counter_snapshot()[("revision_conflict", "command")] == 1


async def test_command_claim_rejects_stale_supervisor_generation(repo):
    result = await _claim(repo, expected_supervisor_generation=99)
    assert result.outcome is ConflictOutcome.FENCING_CONFLICT


async def test_record_command_delivery_owner_and_unknown_flow(repo):
    await _claim(repo)
    not_owner = await repo.record_command_delivery(
        "cmd-1", owner="intruder", expected_revision=1, delivery_state="delivered"
    )
    assert not_owner.outcome is ConflictOutcome.NOT_OWNER
    assert counter_snapshot()[("not_owner", "command")] == 1

    unknown = await repo.record_command_delivery(
        "cmd-1",
        owner="owner-1",
        expected_revision=1,
        delivery_state="delivery_unknown",
        provider_receipt="receipt-1",
    )
    assert unknown.outcome is ConflictOutcome.DELIVERY_UNKNOWN
    assert unknown.revision == 2
    assert counter_snapshot()[("delivery_unknown_reconciled", "command")] == 1

    reconciled = await repo.record_command_delivery(
        "cmd-1", owner="owner-1", expected_revision=2, delivery_state="reconciled"
    )
    assert reconciled.outcome is ConflictOutcome.APPLIED


# -- cleanup -----------------------------------------------------------------


async def test_cleanup_claim_single_winner_and_replacement_generation(repo):
    first = await repo.claim_cleanup(
        cleanup_id="cleanup-1",
        bridge_session_id=SESSION_ID,
        owner="janitor-1",
        owner_generation=1,
    )
    assert first.outcome is ConflictOutcome.APPLIED

    # Re-claim by the same owner is idempotent.
    again = await repo.claim_cleanup(
        cleanup_id="cleanup-1",
        bridge_session_id=SESSION_ID,
        owner="janitor-1",
        owner_generation=1,
    )
    assert again.outcome is ConflictOutcome.ALREADY_APPLIED

    # A second janitor with an equal/older generation cannot steal the claim.
    loser = await repo.claim_cleanup(
        cleanup_id="cleanup-1b",
        bridge_session_id=SESSION_ID,
        owner="janitor-2",
        owner_generation=1,
    )
    assert loser.outcome is ConflictOutcome.FENCING_CONFLICT
    assert counter_snapshot()[("cleanup_claim_conflict", "cleanup")] == 1

    # A replacement janitor with a strictly newer generation fences the old owner.
    replacement = await repo.claim_cleanup(
        cleanup_id="cleanup-1c",
        bridge_session_id=SESSION_ID,
        owner="janitor-3",
        owner_generation=2,
    )
    assert replacement.outcome is ConflictOutcome.APPLIED


async def test_former_janitor_cannot_complete_replacement_cleanup(repo):
    await repo.claim_cleanup(
        cleanup_id="cleanup-2",
        bridge_session_id=SESSION_ID,
        owner="janitor-1",
        owner_generation=1,
    )
    # A replacement generation takes over cleanup authority.
    await repo.claim_cleanup(
        cleanup_id="cleanup-2",
        bridge_session_id=SESSION_ID,
        owner="janitor-2",
        owner_generation=2,
    )
    snapshot = await repo.load_for_update(Aggregate.CLEANUP, "cleanup-2")

    former = await repo.complete_cleanup(
        "cleanup-2", owner="janitor-1", owner_generation=1, expected_revision=snapshot["revision"]
    )
    assert former.outcome is ConflictOutcome.NOT_OWNER

    winner = await repo.complete_cleanup(
        "cleanup-2",
        owner="janitor-2",
        owner_generation=2,
        expected_revision=snapshot["revision"],
    )
    assert winner.outcome is ConflictOutcome.APPLIED

    idempotent = await repo.complete_cleanup(
        "cleanup-2", owner="janitor-2", owner_generation=2, expected_revision=99
    )
    assert idempotent.outcome is ConflictOutcome.ALREADY_APPLIED


# -- telemetry ---------------------------------------------------------------


async def test_record_concurrency_event_rejects_unknown_surface():
    from moonmind.omnigent.concurrency import ConcurrencyTelemetryEvent

    with pytest.raises(ValueError):
        record_concurrency_event(
            ConcurrencyTelemetryEvent.REVISION_CONFLICT, surface="workflow_id"
        )
