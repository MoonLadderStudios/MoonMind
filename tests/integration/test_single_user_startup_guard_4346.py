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
