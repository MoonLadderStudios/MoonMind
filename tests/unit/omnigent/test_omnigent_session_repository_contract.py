"""Shared port contract suite for the ``SessionRepository`` port.

Both the in-memory adapter and the production SQLAlchemy adapter must produce
identical revision and fencing outcomes (MoonLadderStudios/MoonMind#3711). The
same tests run against each adapter via the ``repo`` fixture.
"""

from __future__ import annotations

from dataclasses import replace
from typing import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from moonmind.omnigent.adapters.persistence.memory import InMemorySessionRepository
from moonmind.omnigent.adapters.persistence.sqlalchemy_sessions import (
    OmnigentSessionBase,
    SqlAlchemySessionRepository,
)
from moonmind.omnigent.ports.sessions import SessionRepository, SessionRevisionConflict


@pytest_asyncio.fixture(params=["memory", "sqlalchemy"])
async def repo(request) -> AsyncIterator[SessionRepository]:
    if request.param == "memory":
        yield InMemorySessionRepository()
        return
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(OmnigentSessionBase.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with maker() as session:
        yield SqlAlchemySessionRepository(session=session)
    await engine.dispose()


@pytest.mark.asyncio
async def test_create_starts_at_revision_one(repo: SessionRepository) -> None:
    record = await repo.create("s1", status="declared")
    assert record.revision == 1
    assert record.status == "declared"
    fetched = await repo.get("s1")
    assert fetched is not None
    assert fetched.revision == 1


@pytest.mark.asyncio
async def test_get_missing_returns_none(repo: SessionRepository) -> None:
    assert await repo.get("missing") is None


@pytest.mark.asyncio
async def test_duplicate_create_conflicts(repo: SessionRepository) -> None:
    await repo.create("s1", status="declared")
    with pytest.raises(SessionRevisionConflict):
        await repo.create("s1", status="creating")


@pytest.mark.asyncio
async def test_save_increments_revision(repo: SessionRepository) -> None:
    record = await repo.create("s1", status="declared")
    updated = await repo.save(replace(record, status="active"), expected_revision=1)
    assert updated.revision == 2
    assert updated.status == "active"


@pytest.mark.asyncio
async def test_stale_revision_is_fenced(repo: SessionRepository) -> None:
    record = await repo.create("s1", status="declared")
    # First writer wins.
    await repo.save(replace(record, status="active"), expected_revision=1)
    # Second writer holding the stale revision is rejected.
    with pytest.raises(SessionRevisionConflict) as excinfo:
        await repo.save(replace(record, status="completed"), expected_revision=1)
    assert excinfo.value.expected == 1
    assert excinfo.value.actual == 2


@pytest.mark.asyncio
async def test_save_missing_record_conflicts(repo: SessionRepository) -> None:
    from moonmind.omnigent.ports.sessions import SessionRecord

    ghost = SessionRecord(bridge_session_id="ghost", status="active", revision=1)
    with pytest.raises(SessionRevisionConflict):
        await repo.save(ghost, expected_revision=1)


@pytest.mark.asyncio
async def test_metadata_and_provider_id_round_trip(repo: SessionRepository) -> None:
    record = await repo.create(
        "s1",
        status="declared",
        omnigent_session_id="prov-1",
        metadata={"k": "v"},
    )
    fetched = await repo.get("s1")
    assert fetched is not None
    assert fetched.omnigent_session_id == "prov-1"
    assert dict(fetched.metadata) == {"k": "v"}
    assert record.omnigent_session_id == "prov-1"
