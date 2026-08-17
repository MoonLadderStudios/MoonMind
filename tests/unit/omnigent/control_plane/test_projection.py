"""Projection tests for the Omnigent control-plane canonical aggregate.

Issue MoonLadderStudios/MoonMind#3703 (Projection tests). Workflow Detail,
diagnostic reads, and chat-binding resolution use the canonical aggregate and
preserve historical access after provider and host resources are removed.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import Base
from moonmind.omnigent.control_plane import (
    ChatBindingAliasRepository,
    SessionRepository,
    TurnAttemptRepository,
    WorkflowDetailProjectionRepository,
    create_canonical_session,
)


@pytest_asyncio.fixture()
async def session_factory(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/proj.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield maker
    await engine.dispose()


@pytest.mark.asyncio
async def test_projection_uses_canonical_aggregate(session_factory) -> None:
    async with session_factory() as db:
        record, attempt = await create_canonical_session(
            db,
            moonmind_workflow_id="wf-proj",
            provider="omnigent",
            compatibility_profile="omnigent.server.v1",
            idempotency_key="idem-proj",
        )
        # Attach the provider session and bind host resources.
        sessions = SessionRepository(db)
        turns = TurnAttemptRepository(db)
        await sessions.attach_provider_session(
            record.session_id, provider_session_id="prov-secret"
        )
        await turns.update_state(attempt.turn_attempt_id, state="running")
        await sessions.update_states(
            record.session_id,
            observed_state="running",
            active_turn_attempt_id=attempt.turn_attempt_id,
        )
        await db.commit()

    async with session_factory() as db:
        projection = WorkflowDetailProjectionRepository(db)
        by_id = await projection.by_session_id(record.session_id)
        by_binding = await projection.by_chat_binding(record.chat_binding_id)

    assert by_id is not None
    assert by_id.moonmind_workflow_id == "wf-proj"
    assert by_id.active_turn_state == "running"
    assert by_id.turn_attempt_count == 1
    # Projection is browser-safe: no provider session id is exposed.
    assert "prov-secret" not in repr(by_id)
    assert by_binding is not None
    assert by_binding.session_id == record.session_id


@pytest.mark.asyncio
async def test_projection_preserves_historical_access_after_cleanup(
    session_factory,
) -> None:
    async with session_factory() as db:
        record, _ = await create_canonical_session(
            db,
            moonmind_workflow_id="wf-hist",
            provider="omnigent",
            compatibility_profile="omnigent.server.v1",
            idempotency_key="idem-hist",
        )
        sessions = SessionRepository(db)
        await sessions.mark_terminal(record.session_id, terminal_state="completed")
        # Provider and host resources removed; historical read projection kept.
        await sessions.mark_cleanup_state(record.session_id, cleanup_state="completed")
        await sessions.mark_historical_read(
            record.session_id, historical_read_state="historical"
        )
        await db.commit()

    async with session_factory() as db:
        projection = WorkflowDetailProjectionRepository(db)
        binding = ChatBindingAliasRepository(db)
        by_binding = await projection.by_chat_binding(record.chat_binding_id)
        resolution = await binding.resolve(record.chat_binding_id)

    # Chat-binding resolution and Workflow Detail still resolve after cleanup.
    assert by_binding is not None
    assert by_binding.terminal_state == "completed"
    assert by_binding.cleanup_state == "completed"
    assert by_binding.historical_read_state == "historical"
    assert resolution.canonical_session_id == record.session_id
