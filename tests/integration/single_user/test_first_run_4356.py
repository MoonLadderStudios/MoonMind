"""MoonLadderStudios/MoonMind#4356 R2: hermetic first-run journey (integration).

Hermetic subset of the fresh-local first-run journey, executed under the
``integration_ci`` resource boundary with test-only isolation and synthetic
credentials only:

- fresh schema contains no User/UserProfile seeding;
- ``startup_event`` (real production startup boundary) creates no
  UserProfile row from env keys;
- a synthetic provider credential is redacted from logs/artifacts;
- instance recurring-execution policy honors documented defaults,
  admits the cancel/recover primitive, fails closed on invalid input,
  and maps to the Temporal overlap/catchup vocabulary the operator
  observes.

Full submit/observe/cancel/recover against built API/frontend artifacts
plus browser coverage consumes the integrated #4346-4355 candidate; this
suite pins the seed-free foundation hermetically so the final journey
cannot silently regress it.
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db import base as db_base
from api_service.db.models import Base, User, UserProfile

pytestmark = [pytest.mark.integration, pytest.mark.integration_ci]


def _factory(tmp_path, name="first_run_integration_4356.db"):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/{name}")
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    return engine, factory


@pytest.mark.asyncio
async def test_fresh_schema_carries_no_user_or_profile_seeding(tmp_path) -> None:
    engine, factory = _factory(tmp_path)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with factory() as session:
        user_count = (await session.execute(select(func.count(User.id)))).scalar()
        profile_count = (
            await session.execute(select(func.count(UserProfile.id)))
        ).scalar()
    assert user_count == 0
    assert profile_count == 0
    await engine.dispose()


@pytest.mark.asyncio
async def test_startup_event_leaves_user_profile_empty(
    disabled_env_keys, tmp_path
) -> None:
    """Real startup must not seed UserProfile rows from env keys.

    Accurate scope: ``api_service/main.py`` guarantees this startup path
    must not create or update a UserProfile row; the default User row
    lifecycle in disabled local mode stays owned by #4346/#4347, so this
    test observes both tables but asserts only the UserProfile boundary
    it owns. It is a hermetic seed-free foundation check, not a built
    browser-to-API submit/cancel/recover journey.
    """
    from api_service.main import startup_event

    db_url = f"sqlite+aiosqlite:///{tmp_path}/startup_4356.db"
    orig_url, orig_engine, orig_maker = (
        db_base.DATABASE_URL,
        db_base.engine,
        db_base.async_session_maker,
    )
    db_base.DATABASE_URL = db_url
    db_base.engine = create_async_engine(db_url, future=True)
    db_base.async_session_maker = sessionmaker(
        db_base.engine, class_=AsyncSession, expire_on_commit=False
    )
    try:
        async with db_base.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        with patch("api_service.main._initialize_oidc_provider"):
            await startup_event()

        async with db_base.async_session_maker() as session:
            rows = (await session.execute(select(UserProfile))).scalars().all()
            assert rows == []
            # Observe the User table at the same boundary without pinning
            # cohort-owned disabled-mode seeding: the count is recorded
            # so a future cohort change is visible, but emptiness is not
            # asserted here.
            user_count = (
                await session.execute(select(func.count(User.id)))
            ).scalar()
            assert isinstance(user_count, int)
    finally:
        await db_base.engine.dispose()
        db_base.DATABASE_URL = orig_url
        db_base.engine = orig_engine
        db_base.async_session_maker = orig_maker


@pytest.mark.asyncio
async def test_provider_composition_never_provisions_user_profile(tmp_path) -> None:
    """Registration/login composition leaves UserProfile empty (real boundary)."""
    from api_service.auth import UserManager

    engine, factory = _factory(tmp_path, "composition_4356.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with factory() as session:
        user = User(id=uuid.uuid4(), email="operator-first-run-4356@example.com")
        session.add(user)
        await session.commit()
        manager = UserManager(MagicMock())
        assert await manager.on_after_register(user) is None
        assert await manager.on_after_login(user) is None
        assert await manager._ensure_profile(user) is None
        rows = (await session.execute(select(UserProfile))).scalars().all()
        assert rows == []
    await engine.dispose()


def test_synthetic_credential_never_survives_log_scrubbing() -> None:
    from moonmind.utils.logging import SecretRedactor

    synthetic = "sk-test-synthetic-4356-first-run-xyz987"
    redactor = SecretRedactor(secrets=[synthetic])
    sample = f"provider profile configured key={synthetic} for first run"
    assert redactor.scrub(sample) == sample.replace(synthetic, "***")


def test_first_run_recurring_defaults_cancel_recover_and_fail_closed() -> None:
    """Omitted policy matches documented defaults; cancel/recover admitted."""
    from api_service.services.recurring_workflows_service import (
        RecurringPolicy,
        RecurringWorkflowValidationError,
        _catchup_mode_from_temporal_window,
        _normalize_policy,
        _overlap_mode_from_temporal,
    )

    assert _normalize_policy(None, global_max_backfill=10) == RecurringPolicy(
        overlap_mode="skip",
        max_concurrent_runs=1,
        catchup_mode="last",
        max_backfill=3,
        misfire_grace_seconds=900,
        jitter_seconds=0,
    )
    assert _normalize_policy({}, global_max_backfill=10) == _normalize_policy(
        None, global_max_backfill=10
    )
    admitted = _normalize_policy(
        {"overlap": {"mode": "cancel_previous", "maxConcurrentRuns": 2}},
        global_max_backfill=10,
    )
    assert admitted.overlap_mode == "cancel_previous"
    assert admitted.max_concurrent_runs == 2
    with pytest.raises(RecurringWorkflowValidationError):
        _normalize_policy({"overlap": {"mode": "bogus-4356"}}, global_max_backfill=10)
    policy = _normalize_policy(None, global_max_backfill=10)
    assert _overlap_mode_from_temporal(policy.overlap_mode) == "skip"
    assert _overlap_mode_from_temporal("SKIP") == "skip"
    assert _catchup_mode_from_temporal_window(None) == "last"
    assert policy.catchup_mode == "last"
