"""Issue #4346: startup guard preserves source and never seeds a bypass.

Release-level fixture: the supported upgrade/startup route consults the
guarded conversion entrypoint. Fresh databases initialize without an
account, refused sources keep serving unchanged, and no restart seeds a
default user or publishes a candidate on stale attribution.
"""

import uuid
from unittest.mock import patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db import base as db_base
from api_service.db.models import Base, ManagedSecret, User, UserProfile
from api_service.main import startup_event

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]


async def _seed_db(tmp_path, name="guard4346.db"):
    db_url = f"sqlite+aiosqlite:///{tmp_path}/{name}"
    db_base.DATABASE_URL = db_url
    db_base.engine = create_async_engine(db_url, future=True)
    db_base.async_session_maker = sessionmaker(
        db_base.engine, class_=AsyncSession, expire_on_commit=False
    )
    async with db_base.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


@pytest.mark.asyncio
async def test_startup_seeds_no_default_user_on_fresh_database(
    disabled_env_keys, tmp_path
):
    await _seed_db(tmp_path, "fresh.db")
    with (patch("api_service.main._initialize_oidc_provider"),):
        await startup_event()
    async with db_base.async_session_maker() as session:
        assert (await session.execute(select(User))).scalars().all() == []


@pytest.mark.asyncio
async def test_startup_preserves_refused_multi_person_source(
    disabled_env_keys, tmp_path
):
    await _seed_db(tmp_path, "refused.db")
    async with db_base.async_session_maker() as session:
        u1 = User(id=uuid.uuid4(), email="op-a@example.com")
        u2 = User(id=uuid.uuid4(), email="op-b@example.com")
        session.add_all([u1, u2])
        await session.flush()
        session.add(UserProfile(user_id=u1.id, openai_api_key_encrypted="tok-a"))
        session.add(UserProfile(user_id=u2.id, openai_api_key_encrypted="tok-b"))
        await session.commit()
        before = sorted(str(u) for u in (await session.execute(select(User.id))).scalars().all())

    with (patch("api_service.main._initialize_oidc_provider"),):
        await startup_event()

    async with db_base.async_session_maker() as session:
        after = sorted(str(u) for u in (await session.execute(select(User.id))).scalars().all())
        assert after == before
        # Refusal performs no conversion-side mutation: no converted secrets.
        legacy = (
            (
                await session.execute(
                    select(ManagedSecret).where(
                        ManagedSecret.slug.like("legacy-user-profile-%")
                    )
                )
            )
            .scalars()
            .all()
        )
        assert legacy == []


@pytest.mark.asyncio
async def test_refused_release_workflows_and_operator_access_still_function(
    disabled_env_keys, tmp_path
):
    """Release-level refusal: old workflows readable, guard reports redacted."""
    from api_service.db.models import TemporalArtifact, WorkflowRun

    await _seed_db(tmp_path, "release-refused.db")
    async with db_base.async_session_maker() as session:
        u1 = User(id=uuid.uuid4(), email="rel-a@example.com")
        u2 = User(id=uuid.uuid4(), email="rel-b@example.com")
        session.add_all([u1, u2])
        await session.flush()
        session.add(UserProfile(user_id=u1.id))
        session.add(UserProfile(user_id=u2.id))
        session.add(WorkflowRun(feature_key="rel-feat", requested_by_user_id=u1.id))
        session.add(
            TemporalArtifact(
                artifact_id="rel-art-1",
                storage_key="rel-key-1",
                created_by_principal=str(u2.id),
            )
        )
        await session.commit()

    with (patch("api_service.main._initialize_oidc_provider"),):
        await startup_event()

    async with db_base.async_session_maker() as session:
        from api_service.services.single_user_conversion import (
            startup_guard_decision,
        )

        # Old-release workflows still function: rows preserved, no mutation.
        runs = (await session.execute(select(WorkflowRun))).scalars().all()
        assert len(runs) == 1
        assert runs[0].feature_key == "rel-feat"
        artifacts = (await session.execute(select(TemporalArtifact))).scalars().all()
        assert len(artifacts) == 1
        users = (await session.execute(select(User))).scalars().all()
        assert len(users) == 2
        assert (await session.execute(select(ManagedSecret))).scalars().all() == []
        # Operator access path: guard remains readable and reports refusal
        # with redacted evidence only. Startup's own admitted work (preset
        # seed catalog) may add unowned seed rows, so any refusal disposition
        # is a valid blocked outcome here.
        decision = await startup_guard_decision(session)
        assert decision.eligible is False
        assert decision.disposition in {"multi_person_refusal", "unresolved_refusal"}
        blob = str(decision.to_sanitized_dict())
        assert "rel-a@example.com" not in blob
        assert "rel-b@example.com" not in blob


@pytest.mark.asyncio
async def test_disabled_boundary_serves_after_proven_fresh_conversion(
    disabled_env_keys, tmp_path
):
    """Ledger-proven fresh state serves without minting a User row."""
    from api_service.auth_providers import _load_disabled_user
    from api_service.services.single_user_conversion import run_guarded_upgrade

    await _seed_db(tmp_path, "boundary-fresh.db")
    async with db_base.async_session_maker() as session:
        result = await run_guarded_upgrade(session, operator_authorized=True)
        assert result.published is True
    async with db_base.async_session_maker() as session:
        user = await _load_disabled_user(session)
        assert user.is_superuser is True
        assert user.is_active is True
        # No synthetic account was minted to satisfy the boundary.
        assert (await session.execute(select(User))).scalars().all() == []


@pytest.mark.asyncio
async def test_disabled_boundary_stays_setup_required_without_conversion(
    disabled_env_keys, tmp_path
):
    """Failed/missing conversion must not expose account-free data."""
    from fastapi import HTTPException

    from api_service.auth_providers import _load_disabled_user

    await _seed_db(tmp_path, "boundary-blocked.db")
    async with db_base.async_session_maker() as session:
        with pytest.raises(HTTPException) as exc:
            await _load_disabled_user(session)
        assert exc.value.status_code == 503
        assert exc.value.detail == "setup_required"
