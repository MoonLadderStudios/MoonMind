"""The admitted turn vocabulary must survive the durable SQL boundary."""

import pytest
from sqlalchemy.exc import IntegrityError

from moonmind.omnigent.control_plane.turn_sources import TurnSource
from tests.unit.omnigent.test_control_plane_aggregates import (  # noqa: F401
    session_factory,
    store,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("source", list(TurnSource))
async def test_admitted_turn_source_is_persisted(store, source):
    async with store.transaction() as repos:
        await repos.sessions.create(
            session_id="session", moonmind_workflow_id="workflow", provider="omnigent"
        )
        turn = await repos.turn_attempts.create(
            turn_attempt_id="turn",
            session_id="session",
            idempotency_key="instruction",
            lineage_kind=source,
        )
        assert turn.lineage_kind == source.value
    async with store.transaction() as repos:
        assert (await repos.turn_attempts.get("turn")).lineage_kind == source.value


@pytest.mark.asyncio
async def test_non_unique_integrity_error_is_not_reported_as_duplicate_turn(store):
    async with store.transaction() as repos:
        with pytest.raises(IntegrityError, match="NOT NULL"):
            await repos.turn_attempts.create(
                turn_attempt_id="turn", session_id=None, idempotency_key="new-key"
            )
        # The savepoint still protects the enclosing transaction.
        assert await repos.turn_attempts.get_by_idempotency_key("new-key") is None
