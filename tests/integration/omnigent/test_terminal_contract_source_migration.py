"""Upgrade a populated database and admit a real canonical continuation."""

import importlib

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.exc import IntegrityError

from moonmind.omnigent.control_plane.turn_commands import (
    CanonicalSessionBootstrap,
    CanonicalTurnCommandService,
)
from moonmind.omnigent.control_plane.turn_sources import TurnSource

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


def apply(connection, operation):
    with Operations.context(MigrationContext.configure(connection)):
        operation()


async def test_populated_upgrade_preserves_turns_and_admits_contract_continuation(
    pg_store,
):
    migration = importlib.import_module(
        "api_service.migrations.versions.383_terminal_contract_source"
    )
    service = CanonicalTurnCommandService(pg_store)
    initial = await service.claim(
        workflow_id="workflow",
        provider_session_ref="",
        chat_binding_id=None,
        command_type="execute",
        turn_source=TurnSource.INITIAL,
        idempotency_key="initial",
        payload_digest="plan",
        step_execution_id="step",
        bootstrap=CanonicalSessionBootstrap(
            provider="omnigent",
            step_execution_id="step",
            agent_run_id="agent",
            source_idempotency_key="initial",
        ),
    )
    engine = pg_store._session_factory.kw["bind"]
    async with engine.begin() as conn:
        await conn.run_sync(apply, migration.downgrade)
        before = (
            (await conn.execute(sa.text("SELECT * FROM omnigent_turn_attempts")))
            .mappings()
            .all()
        )
        await conn.run_sync(apply, migration.upgrade)
        after = (
            (await conn.execute(sa.text("SELECT * FROM omnigent_turn_attempts")))
            .mappings()
            .all()
        )
        assert before == after
    followup = {
        "workflow_id": "workflow",
        "provider_session_ref": "",
        "chat_binding_id": None,
        "session_id": initial.session_id,
        "command_type": "terminal_contract_continuation",
        "turn_source": TurnSource.TERMINAL_CONTRACT_CONTINUATION,
        "idempotency_key": "initial:terminal-contract:1",
        "payload_digest": "instruction",
        "step_execution_id": "step",
    }
    accepted = await service.claim(**followup)
    assert accepted.owns_delivery
    redelivered = await service.claim(**followup)
    assert redelivered.turn_attempt_id == accepted.turn_attempt_id
    async with pg_store.transaction() as repos:
        turn = await repos.turn_attempts.get(accepted.turn_attempt_id)
        assert turn.lineage_kind == TurnSource.TERMINAL_CONTRACT_CONTINUATION.value
        assert turn.session_id == initial.session_id
    async with engine.begin() as conn:
        with pytest.raises(RuntimeError, match="retained turns"):
            await conn.run_sync(apply, migration.downgrade)
        assert (
            await conn.execute(sa.text("SELECT count(*) FROM omnigent_turn_attempts"))
        ).scalar() == 2
    # Invalid sources retain the real database error and never become allowed.
    async with engine.begin() as conn:
        with pytest.raises(
            IntegrityError, match="ck_omnigent_turn_attempts_lineage_kind"
        ):
            await conn.execute(
                sa.text("UPDATE omnigent_turn_attempts SET lineage_kind = 'invented'")
            )
