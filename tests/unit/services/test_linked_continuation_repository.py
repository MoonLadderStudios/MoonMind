"""Unit tests for the linked-continuation reservation repository (#3641).

Covers the idempotent source-immutable admission that backs the terminal
**Continue in a new workflow** action: one client key binds to one destination,
duplicate/edited keys are reconciled or rejected, and only finalized
relationships are listed bidirectionally.
"""

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api_service.db.models import WorkflowLinkedContinuationRecord
from api_service.services.linked_continuation import (
    RELATIONSHIP_TYPE_LINKED_CONTINUATION,
    LinkedContinuationConflict,
    SqlLinkedContinuationRepository,
    compute_request_digest,
)


async def _database():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync_connection: WorkflowLinkedContinuationRecord.__table__.create(
                sync_connection
            )
        )
    return engine, async_sessionmaker(engine, expire_on_commit=False)


def _reserve_kwargs(**overrides):
    base = dict(
        source_workflow_id="mm:source",
        source_run_id="run-1",
        idempotency_key="key-1",
        request_digest="digest-1",
        pinned_source_refs={
            "relationshipType": RELATIONSHIP_TYPE_LINKED_CONTINUATION,
            "sourceWorkflowId": "mm:source",
            "sourceRunId": "run-1",
            "sourceFinalSnapshotRef": "art_final",
        },
        source_logical_step_id=None,
        source_step_execution_id="step-1",
        created_by="user-7",
        bounded_purpose="follow up on the PR",
    )
    base.update(overrides)
    return base


@pytest.mark.asyncio
async def test_reserve_pins_one_destination_and_is_idempotent() -> None:
    engine, sessions = await _database()
    async with sessions() as session:
        repo = SqlLinkedContinuationRepository(session)
        first = await repo.reserve_or_get(**_reserve_kwargs())
        await session.commit()

    assert first.already_finalized is False
    assert first.destination_workflow_id.startswith("mm:")

    # Same key, same digest → same destination, still not finalized (create not
    # yet driven). No second destination is minted.
    async with sessions() as session:
        repo = SqlLinkedContinuationRepository(session)
        second = await repo.reserve_or_get(**_reserve_kwargs())
        await session.commit()

    assert second.destination_workflow_id == first.destination_workflow_id
    assert second.already_finalized is False
    await engine.dispose()


@pytest.mark.asyncio
async def test_finalize_marks_reservation_and_reports_already_finalized() -> None:
    engine, sessions = await _database()
    async with sessions() as session:
        repo = SqlLinkedContinuationRepository(session)
        reservation = await repo.reserve_or_get(**_reserve_kwargs())
        await repo.finalize(reservation.record, destination_run_id="dest-run")
        await session.commit()

    async with sessions() as session:
        repo = SqlLinkedContinuationRepository(session)
        again = await repo.reserve_or_get(**_reserve_kwargs())
        await session.commit()

    assert again.already_finalized is True
    assert again.destination_workflow_id == reservation.destination_workflow_id
    assert again.record.destination_run_id == "dest-run"
    assert again.record.reserved_at is not None
    await engine.dispose()


@pytest.mark.asyncio
async def test_reserve_rejects_reused_key_with_different_request() -> None:
    engine, sessions = await _database()
    async with sessions() as session:
        repo = SqlLinkedContinuationRepository(session)
        await repo.reserve_or_get(**_reserve_kwargs())
        await session.commit()

    async with sessions() as session:
        repo = SqlLinkedContinuationRepository(session)
        with pytest.raises(LinkedContinuationConflict):
            await repo.reserve_or_get(
                **_reserve_kwargs(request_digest="digest-CHANGED")
            )
    await engine.dispose()


@pytest.mark.asyncio
async def test_list_and_lookup_only_return_finalized_relationships() -> None:
    engine, sessions = await _database()
    async with sessions() as session:
        repo = SqlLinkedContinuationRepository(session)
        pending = await repo.reserve_or_get(**_reserve_kwargs())
        await session.commit()

    # Pending (unfinalized) reservation is not yet a durable relationship.
    async with sessions() as session:
        repo = SqlLinkedContinuationRepository(session)
        assert await repo.list_for_source("mm:source") == []
        assert (
            await repo.get_for_destination(pending.destination_workflow_id) is None
        )

    async with sessions() as session:
        repo = SqlLinkedContinuationRepository(session)
        reservation = await repo.reserve_or_get(**_reserve_kwargs())
        await repo.finalize(reservation.record, destination_run_id="dest-run")
        await session.commit()

    async with sessions() as session:
        repo = SqlLinkedContinuationRepository(session)
        outbound = await repo.list_for_source("mm:source")
        assert [r.destination_workflow_id for r in outbound] == [
            reservation.destination_workflow_id
        ]
        inbound = await repo.get_for_destination(
            reservation.destination_workflow_id
        )
        assert inbound is not None
        assert inbound.source_workflow_id == "mm:source"
        assert inbound.relationship_type == RELATIONSHIP_TYPE_LINKED_CONTINUATION
    await engine.dispose()


def test_request_digest_is_order_independent() -> None:
    left = compute_request_digest(
        {"a": 1, "b": [1, 2], "c": {"x": 1, "y": 2}}
    )
    right = compute_request_digest(
        {"c": {"y": 2, "x": 1}, "b": [1, 2], "a": 1}
    )
    assert left == right

    changed = compute_request_digest({"a": 1, "b": [2, 1], "c": {"x": 1, "y": 2}})
    assert changed != left
