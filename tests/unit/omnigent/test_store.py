"""Unit tests for the durable Omnigent run store (STORY-002).

MM-1155 (source: MM-1140): the bridge proxy persists the provider session id
before any first-message prepare/post and emits ``session.created`` into the
durable bridge event journal carried on the STORY-002 run row.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api_service.db.models import OmnigentExternalRun
from moonmind.omnigent.store import (
    BRIDGE_EVENT_JOURNAL_KEY,
    SESSION_CREATED_EVENT_TYPE,
    OmnigentRunStore,
)
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest

pytestmark = [pytest.mark.asyncio]


def _request(idempotency_key: str = "idem-1") -> AgentExecutionRequest:
    return AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        correlationId="corr-1",
        idempotencyKey=idempotency_key,
    )


@pytest_asyncio.fixture
async def store() -> OmnigentRunStore:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(OmnigentExternalRun.__table__.create)
    session_maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        yield OmnigentRunStore(session_maker)
    finally:
        await engine.dispose()


async def _seed(store: OmnigentRunStore, key: str = "idem-1") -> None:
    await store.get_or_create(
        request=_request(key),
        endpoint_ref="default",
        agent_id="agent-1",
        agent_name="codex",
        target_metadata={"hostType": "managed", "workspace": "https://x/y#main"},
    )


async def test_record_session_created_appends_event(store: OmnigentRunStore) -> None:
    await _seed(store)
    await store.attach_session("idem-1", "sess-1")

    row = await store.record_session_created(
        "idem-1", session_id="sess-1", agent_id="agent-1", endpoint_ref="default"
    )

    journal = row.target_metadata[BRIDGE_EVENT_JOURNAL_KEY]
    assert len(journal) == 1
    event = journal[0]
    assert event["type"] == SESSION_CREATED_EVENT_TYPE
    assert event["omnigentSessionId"] == "sess-1"
    assert event["omnigentAgentId"] == "agent-1"
    assert event["endpointRef"] == "default"
    assert event["sequence"] == 1
    assert event["timestamp"]
    # Existing target metadata is preserved alongside the journal.
    assert row.target_metadata["hostType"] == "managed"


async def test_record_session_created_is_idempotent(store: OmnigentRunStore) -> None:
    await _seed(store)
    await store.attach_session("idem-1", "sess-1")

    await store.record_session_created("idem-1", session_id="sess-1")
    row = await store.record_session_created("idem-1", session_id="sess-1")

    journal = row.target_metadata[BRIDGE_EVENT_JOURNAL_KEY]
    assert len(journal) == 1


async def test_record_session_created_persists_across_reads(
    store: OmnigentRunStore,
) -> None:
    await _seed(store)
    await store.attach_session("idem-1", "sess-1")
    await store.record_session_created("idem-1", session_id="sess-1")

    # A subsequent get_or_create (retry) must not clobber the journal.
    row = await store.get_or_create(
        request=_request("idem-1"),
        endpoint_ref="default",
        agent_id="agent-1",
        agent_name="codex",
        target_metadata={"hostType": "managed", "workspace": "https://x/y#main"},
    )
    assert BRIDGE_EVENT_JOURNAL_KEY in row.target_metadata
    assert len(row.target_metadata[BRIDGE_EVENT_JOURNAL_KEY]) == 1
