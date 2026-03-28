"""Unit tests for _auto_seed_provider_profiles startup function."""

import asyncio

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db import base as db_base
from api_service.db.models import Base, ManagedAgentProviderProfile


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
async def test_auto_seed_creates_default_profiles(_module_db, monkeypatch):
    """When the table is empty, auto-seeding should create 3 default profiles."""
    from api_service.main import _auto_seed_provider_profiles

    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)

    seeded = await _auto_seed_provider_profiles()
    assert set(seeded) == {"gemini_cli", "codex_cli", "claude_code"}

    # Verify they exist in the DB with correct profile_id values.
    async with db_base.async_session_maker() as session:
        result = await session.execute(select(ManagedAgentProviderProfile))
        profiles = result.scalars().all()

    assert len(profiles) == 3
    profile_ids = {p.profile_id for p in profiles}
    assert profile_ids == {"gemini_default", "codex_default", "claude_anthropic"}



@pytest.mark.asyncio
async def test_auto_seed_is_idempotent(_module_db, monkeypatch):
    """Calling auto-seed twice should not duplicate profiles."""
    from api_service.main import _auto_seed_provider_profiles

    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)

    first = await _auto_seed_provider_profiles()
    assert len(first) == 3

    second = await _auto_seed_provider_profiles()
    assert second == []

    # Still only 3 in DB.
    async with db_base.async_session_maker() as session:
        result = await session.execute(select(ManagedAgentProviderProfile))
        profiles = result.scalars().all()
    assert len(profiles) == 3


@pytest.mark.asyncio
async def test_auto_seed_skipped_when_env_set(_module_db, monkeypatch):
    """Seeding should be skipped when MOONMIND_SKIP_PROVIDER_PROFILE_SEED is set."""
    from api_service.main import _auto_seed_provider_profiles

    monkeypatch.setenv("MOONMIND_SKIP_PROVIDER_PROFILE_SEED", "true")
    seeded = await _auto_seed_provider_profiles()
    assert seeded == []

    async with db_base.async_session_maker() as session:
        result = await session.execute(select(ManagedAgentProviderProfile))
        profiles = result.scalars().all()
    assert len(profiles) == 0


@pytest.mark.asyncio
async def test_auto_seed_includes_minimax_when_env_set(_module_db, monkeypatch):
    """When MINIMAX_API_KEY is set, a 4th 'claude_minimax' profile should be seeded."""
    from api_service.main import _auto_seed_provider_profiles

    monkeypatch.setenv("MINIMAX_API_KEY", "test-minimax-key")

    seeded = await _auto_seed_provider_profiles()
    assert "claude_code" in seeded  # seeded twice (default + minimax)

    async with db_base.async_session_maker() as session:
        result = await session.execute(select(ManagedAgentProviderProfile))
        profiles = result.scalars().all()

    assert len(profiles) == 4
    profile_ids = {p.profile_id for p in profiles}
    assert "claude_anthropic" in profile_ids
    assert "claude_minimax" in profile_ids


    # Verify MiniMax profile details.
    mm_profile = next(p for p in profiles if p.profile_id == "claude_minimax")
    assert mm_profile.runtime_id == "claude_code"
    assert mm_profile.secret_refs is not None
    assert mm_profile.secret_refs.get("ANTHROPIC_AUTH_TOKEN") == "env://MINIMAX_API_KEY"
    assert mm_profile.env_template is not None
    assert mm_profile.env_template["ANTHROPIC_BASE_URL"] == "https://api.minimax.io/anthropic"
    assert mm_profile.env_template["ANTHROPIC_MODEL"] == "MiniMax-M2.7"
    assert mm_profile.env_template["API_TIMEOUT_MS"] == "3000000"
    assert mm_profile.volume_ref is None
    assert mm_profile.volume_mount_path is None


@pytest.mark.asyncio
async def test_auto_seed_adds_minimax_after_initial_seed(_module_db, monkeypatch):
    """MINIMAX_API_KEY added after initial seed → claude_minimax is inserted on next call."""
    from api_service.main import _auto_seed_provider_profiles

    # First seed without MiniMax key.
    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    first = await _auto_seed_provider_profiles()
    assert len(first) == 3

    # Now the key becomes available.
    monkeypatch.setenv("MINIMAX_API_KEY", "test-minimax-key")
    second = await _auto_seed_provider_profiles()
    assert "claude_code" in second  # minimax profile was added

    async with db_base.async_session_maker() as session:
        result = await session.execute(select(ManagedAgentProviderProfile))
        profiles = result.scalars().all()

    assert len(profiles) == 4
    profile_ids = {p.profile_id for p in profiles}
    assert "claude_anthropic" in profile_ids
    assert "claude_minimax" in profile_ids



@pytest.mark.asyncio
async def test_auto_seed_excludes_minimax_when_env_unset(_module_db, monkeypatch):
    """When MINIMAX_API_KEY is absent, only the 3 default profiles are seeded."""
    from api_service.main import _auto_seed_provider_profiles

    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)

    seeded = await _auto_seed_provider_profiles()
    assert set(seeded) == {"gemini_cli", "codex_cli", "claude_code"}

    async with db_base.async_session_maker() as session:
        result = await session.execute(select(ManagedAgentProviderProfile))
        profiles = result.scalars().all()

    profile_ids = {p.profile_id for p in profiles}
    assert "claude_minimax" not in profile_ids
    assert "claude_anthropic" in profile_ids
    assert len(profiles) == 3
