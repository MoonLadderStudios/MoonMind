"""Unit tests for _auto_seed_auth_profiles startup function."""

import asyncio

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db import base as db_base
from api_service.db.models import Base, ManagedAgentAuthProfile


@pytest.fixture()
def _module_db(tmp_path):
    """Create a single in-memory SQLite engine and schema for the test."""

    db_url = f"sqlite+aiosqlite:///{tmp_path}/seed_test.db"

    async def _setup():
        engine = create_async_engine(db_url, future=True)
        session_maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        return engine, session_maker

    async def _teardown(engine):
        await engine.dispose()

    engine, session_maker = asyncio.run(_setup())

    _orig = (db_base.DATABASE_URL, db_base.engine, db_base.async_session_maker)
    db_base.DATABASE_URL = db_url
    db_base.engine = engine
    db_base.async_session_maker = session_maker
    yield
    db_base.DATABASE_URL, db_base.engine, db_base.async_session_maker = _orig
    asyncio.run(_teardown(engine))


@pytest.mark.asyncio
async def test_auto_seed_creates_default_profiles(_module_db):
    """When the table is empty, auto-seeding should create 3 default profiles."""
    from api_service.main import _auto_seed_auth_profiles

    seeded = await _auto_seed_auth_profiles()
    assert set(seeded) == {"gemini_cli", "codex_cli", "claude_code"}

    # Verify they exist in the DB.
    async with db_base.async_session_maker() as session:
        result = await session.execute(select(ManagedAgentAuthProfile))
        profiles = result.scalars().all()

    assert len(profiles) == 3
    runtime_ids = {p.runtime_id for p in profiles}
    assert runtime_ids == {"gemini_cli", "codex_cli", "claude_code"}


@pytest.mark.asyncio
async def test_auto_seed_is_idempotent(_module_db):
    """Calling auto-seed twice should not duplicate profiles."""
    from api_service.main import _auto_seed_auth_profiles

    first = await _auto_seed_auth_profiles()
    assert len(first) == 3

    second = await _auto_seed_auth_profiles()
    assert second == []

    # Still only 3 in DB.
    async with db_base.async_session_maker() as session:
        result = await session.execute(select(ManagedAgentAuthProfile))
        profiles = result.scalars().all()
    assert len(profiles) == 3


@pytest.mark.asyncio
async def test_auto_seed_skipped_when_env_set(_module_db, monkeypatch):
    """Seeding should be skipped when MOONMIND_SKIP_AUTH_PROFILE_SEED is set."""
    from api_service.main import _auto_seed_auth_profiles

    monkeypatch.setenv("MOONMIND_SKIP_AUTH_PROFILE_SEED", "true")
    seeded = await _auto_seed_auth_profiles()
    assert seeded == []

    async with db_base.async_session_maker() as session:
        result = await session.execute(select(ManagedAgentAuthProfile))
        profiles = result.scalars().all()
    assert len(profiles) == 0
