"""MoonLadderStudios/MoonMind#4356: seed-free first-run boundaries (unit).

Narrow production boundaries of a fresh local instance: an empty schema plus
ordinary API startup seeds no User or UserProfile rows, claiming the default
administrator requires explicit operator authorization, and omitted recurring
policy inputs behave like their documented defaults.

The integrated fresh and eligible-upgrade journeys (submit, observe, cancel,
artifacts, recurring dispatch, and the compiled dashboard) run against the
default Compose install in ``tools/first_run_journey_3938.sh``.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import Base, User, UserProfile


def _factory(tmp_path, name="first_run_4356.db"):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/{name}")
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    return engine, factory


@pytest.mark.asyncio
async def test_fresh_schema_and_startup_seed_no_person(tmp_path) -> None:
    """A fresh schema plus ordinary API startup creates no User/UserProfile."""
    from api_service.db import base as db_base
    from api_service.main import startup_event

    engine, factory = _factory(tmp_path)
    original = (db_base.DATABASE_URL, db_base.engine, db_base.async_session_maker)
    db_base.DATABASE_URL = str(engine.url)
    db_base.engine = engine
    db_base.async_session_maker = factory
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        with patch("api_service.main._initialize_oidc_provider"):
            await startup_event()
        async with factory() as session:
            users = (await session.execute(select(func.count(User.id)))).scalar()
            profiles = (
                await session.execute(select(func.count(UserProfile.id)))
            ).scalar()
    finally:
        await engine.dispose()
        db_base.DATABASE_URL, db_base.engine, db_base.async_session_maker = original
    assert users == 0
    assert profiles == 0


@pytest.mark.asyncio
async def test_default_admin_claim_refuses_without_operator_authorization(
    tmp_path,
) -> None:
    """Whoever reaches setup first cannot claim the default administrator."""
    from api_service.auth import (
        DefaultAdminClaimAuthorizationError,
        UserManager,
        claim_default_admin,
    )

    engine, factory = _factory(tmp_path)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with factory() as session:
        with pytest.raises(DefaultAdminClaimAuthorizationError):
            await claim_default_admin(
                session, UserManager(MagicMock()), operator_authorized=False
            )


def test_recurring_policy_omitted_inputs_match_documented_defaults() -> None:
    """First-run recurring execution needs no explicit policy to be safe.

    Omitted inputs must behave like their documented default equivalents
    (AGENTS.md principle 7): skip/1/last/3/900s/0s. Exercises the real
    production normalization boundary, not a copy of its defaults.
    """
    from api_service.services.recurring_workflows_service import (
        RecurringPolicy,
        _normalize_policy,
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


def test_recurring_policy_invalid_modes_fail_closed() -> None:
    """An invalid recurring policy is refused, never partially scheduled."""
    from api_service.services.recurring_workflows_service import (
        RecurringWorkflowValidationError,
        _normalize_policy,
    )

    with pytest.raises(RecurringWorkflowValidationError):
        _normalize_policy({"overlap": {"mode": "bogus-4356"}}, global_max_backfill=10)
    with pytest.raises(RecurringWorkflowValidationError):
        _normalize_policy({"catchup": {"mode": "bogus-4356"}}, global_max_backfill=10)
    with pytest.raises(RecurringWorkflowValidationError):
        _normalize_policy(
            {"misfireGraceSeconds": "not-a-number"}, global_max_backfill=10
        )


def test_recurring_policy_backfill_clamped_to_global_max() -> None:
    """A first-run backfill request cannot exceed the deployment bound."""
    from api_service.services.recurring_workflows_service import _normalize_policy

    policy = _normalize_policy({"catchup": {"maxBackfill": 99}}, global_max_backfill=4)
    assert policy.max_backfill == 4


def test_recurring_policy_cancel_recover_primitive_accepted() -> None:
    """Cancel-previous overlap is an admitted recover primitive, not a refusal.

    A first-run operator that cancels in-flight work and reschedules with
    ``cancel_previous`` must normalize cleanly through the real production
    boundary; an unknown mode must still fail closed.
    """
    from api_service.services.recurring_workflows_service import (
        RecurringWorkflowValidationError,
        _normalize_policy,
    )

    admitted = _normalize_policy(
        {"overlap": {"mode": "cancel_previous", "maxConcurrentRuns": 2}},
        global_max_backfill=10,
    )
    assert admitted.overlap_mode == "cancel_previous"
    assert admitted.max_concurrent_runs == 2
    with pytest.raises(RecurringWorkflowValidationError):
        _normalize_policy(
            {"overlap": {"mode": "cancel-everything-4356"}},
            global_max_backfill=10,
        )


def test_recurring_policy_negative_bounds_clamped_not_scheduled() -> None:
    """Negative misfire/jitter bounds clamp to zero instead of scheduling."""
    from api_service.services.recurring_workflows_service import _normalize_policy

    policy = _normalize_policy(
        {"misfireGraceSeconds": -30, "jitterSeconds": -5},
        global_max_backfill=10,
    )
    assert policy.misfire_grace_seconds == 0
    assert policy.jitter_seconds == 0


def test_recurring_policy_defaults_survive_temporal_round_trip() -> None:
    """Documented defaults map to the Temporal overlap/catchup vocabulary.

    The first-run omitted-input policy (skip/last) must translate to the
    Temporal ``SKIP`` overlap and a last-only catchup window so the
    scheduled work the operator observes matches the documented defaults.
    """
    from api_service.services.recurring_workflows_service import (
        _catchup_mode_from_temporal_window,
        _normalize_policy,
        _overlap_mode_from_temporal,
    )

    policy = _normalize_policy(None, global_max_backfill=10)
    assert policy.overlap_mode == "skip"
    assert _overlap_mode_from_temporal(policy.overlap_mode) == "skip"
    assert _overlap_mode_from_temporal("SKIP") == "skip"
    assert _catchup_mode_from_temporal_window(None) == "last"
    assert policy.catchup_mode == "last"
