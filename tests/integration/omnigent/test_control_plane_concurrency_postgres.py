"""Real PostgreSQL concurrency proofs for the Omnigent control plane.

MoonLadderStudios/MoonMind#3704. These run concurrent transactions against the
compose PostgreSQL backend to prove the compare-and-swap and fencing invariants
that in-process SQLite serialization cannot demonstrate.
"""

from __future__ import annotations

import asyncio

import pytest
import pytest_asyncio
from sqlalchemy import delete, select

from api_service.db.base import async_session_maker
from api_service.db.models import (
    OmnigentBridgeSession,
    OmnigentCommand,
    OmnigentFencingGeneration,
)
from moonmind.omnigent.concurrency import (
    Aggregate,
    ConflictOutcome,
    OmnigentControlPlaneRepository,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]

SESSION_ID = "cp-fencing-postgres"


async def _seed_session() -> None:
    async with async_session_maker() as session, session.begin():
        session.add(
            OmnigentBridgeSession(
                bridge_session_id=SESSION_ID,
                provider="openai",
                compatibility_profile="codex-native",
                moonmind_workflow_id="wf-cp",
                moonmind_agent_run_id="run-cp",
                idempotency_key=f"idem-{SESSION_ID}",
                omnigent_endpoint_ref="default",
                host_type="external",
                status="running",
            )
        )


async def _cleanup() -> None:
    async with async_session_maker() as session, session.begin():
        # Cascades remove turn attempts, commands, and cleanup authority.
        await session.execute(
            delete(OmnigentBridgeSession).where(
                OmnigentBridgeSession.bridge_session_id == SESSION_ID
            )
        )
        await session.execute(
            delete(OmnigentFencingGeneration).where(
                OmnigentFencingGeneration.scope_key.like(f"{SESSION_ID}:%")
            )
        )


@pytest_asyncio.fixture
async def repo():
    await _cleanup()
    await _seed_session()
    try:
        yield OmnigentControlPlaneRepository(async_session_maker)
    finally:
        await _cleanup()


async def test_two_writers_cannot_both_advance_one_revision(repo) -> None:
    start = asyncio.Event()

    async def writer(status: str):
        await start.wait()
        return await repo.compare_and_swap_session(
            SESSION_ID,
            expected_revision=1,
            expected_supervisor_generation=1,
            values={"status": status},
        )

    pending = [
        asyncio.create_task(writer("harvesting")),
        asyncio.create_task(writer("draining")),
    ]
    await asyncio.sleep(0)
    start.set()
    results = await asyncio.gather(*pending)

    applied = [r for r in results if r.outcome is ConflictOutcome.APPLIED]
    conflicts = [r for r in results if r.outcome is ConflictOutcome.REVISION_CONFLICT]
    assert len(applied) == 1
    assert len(conflicts) == 1

    snapshot = await repo.load_for_update(Aggregate.SESSION, SESSION_ID)
    assert snapshot["revision"] == 2


async def test_stale_writer_cannot_overwrite_newer_terminal_state(repo) -> None:
    terminal = await repo.compare_and_swap_session(
        SESSION_ID,
        expected_revision=1,
        expected_supervisor_generation=1,
        values={"status": "terminal"},
    )
    assert terminal.outcome is ConflictOutcome.APPLIED

    stale = await repo.compare_and_swap_session(
        SESSION_ID,
        expected_revision=1,
        expected_supervisor_generation=1,
        values={"status": "running"},
        immutable_states=frozenset({"terminal"}),
    )
    assert stale.outcome is ConflictOutcome.IMMUTABLE_AUTHORITY_CONFLICT

    snapshot = await repo.load_for_update(Aggregate.SESSION, SESSION_ID)
    assert snapshot["status"] == "terminal"


async def test_one_command_claimed_once_under_concurrent_retries(repo) -> None:
    start = asyncio.Event()

    async def claimant():
        await start.wait()
        return await repo.claim_command(
            command_id="cp-command",
            bridge_session_id=SESSION_ID,
            command_type="post_first_message",
            payload_digest="digest-a",
            idempotency_key="cp-command-idem",
            expected_session_revision=1,
            owner_class="session_supervisor",
            owner="supervisor",
        )

    pending = [asyncio.create_task(claimant()) for _ in range(6)]
    await asyncio.sleep(0)
    start.set()
    results = await asyncio.gather(*pending)

    applied = [r for r in results if r.outcome is ConflictOutcome.APPLIED]
    already = [r for r in results if r.outcome is ConflictOutcome.ALREADY_APPLIED]
    assert len(applied) == 1
    assert len(already) == len(results) - 1

    async with async_session_maker() as session:
        rows = (
            await session.scalars(
                select(OmnigentCommand).where(
                    OmnigentCommand.idempotency_key == "cp-command-idem"
                )
            )
        ).all()
    assert len(rows) == 1


async def test_two_janitors_cannot_both_claim_cleanup(repo) -> None:
    start = asyncio.Event()

    async def janitor(owner: str):
        await start.wait()
        return await repo.claim_cleanup(
            cleanup_id=f"cp-cleanup-{owner}",
            bridge_session_id=SESSION_ID,
            owner=owner,
            owner_generation=1,
        )

    pending = [
        asyncio.create_task(janitor("janitor-a")),
        asyncio.create_task(janitor("janitor-b")),
    ]
    await asyncio.sleep(0)
    start.set()
    results = await asyncio.gather(*pending)

    applied = [r for r in results if r.outcome is ConflictOutcome.APPLIED]
    fenced = [r for r in results if r.outcome is ConflictOutcome.FENCING_CONFLICT]
    assert len(applied) == 1
    assert len(fenced) == 1


async def test_former_janitor_cannot_release_replacement_lease(repo) -> None:
    await repo.claim_cleanup(
        cleanup_id="cp-cleanup",
        bridge_session_id=SESSION_ID,
        owner="janitor-1",
        owner_generation=1,
    )
    replacement = await repo.claim_cleanup(
        cleanup_id="cp-cleanup",
        bridge_session_id=SESSION_ID,
        owner="janitor-2",
        owner_generation=2,
    )
    assert replacement.outcome is ConflictOutcome.APPLIED

    snapshot = await repo.load_for_update(Aggregate.CLEANUP, "cp-cleanup")
    former = await repo.complete_cleanup(
        "cp-cleanup",
        owner="janitor-1",
        owner_generation=1,
        expected_revision=snapshot["revision"],
    )
    assert former.outcome is ConflictOutcome.NOT_OWNER


async def test_concurrent_fencing_acquisitions_are_monotonic(repo) -> None:
    scope = f"{SESSION_ID}:session_supervisor"
    start = asyncio.Event()

    async def acquire():
        await start.wait()
        return await repo.acquire_fencing_generation(
            scope, scope_kind="session_supervisor"
        )

    pending = [asyncio.create_task(acquire()) for _ in range(5)]
    await asyncio.sleep(0)
    start.set()
    generations = await asyncio.gather(*pending)

    assert sorted(generations) == [1, 2, 3, 4, 5]
    assert await repo.current_fencing_generation(scope) == 5
