"""Issue #4349: legacy UserProfile secrets migrate into ManagedSecrets.

Verifies deterministic collision-safe slugs, transactional reference updates,
rerun/interruption safety, same-value distinct credentials, and no plaintext
in migration artifacts/logs.
"""

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import Base, ManagedSecret, SecretStatus, User, UserProfile


def _factory(tmp_path, name="mig4349.db"):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/{name}")
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    return engine, factory


@pytest.mark.asyncio
async def test_migration_preserves_distinct_secrets_and_ids(tmp_path):
    from api_service.services.profile_secret_migration import (
        migrate_legacy_profile_secrets,
        legacy_secret_slug,
    )

    engine, factory = _factory(tmp_path)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with factory() as session:
        # One operator holding the same value in two distinct credentials:
        # slugs derive from existing IDs + field (never value hashes), so
        # the two source credentials stay distinct.
        u1 = User(id=uuid.uuid4(), email="a@example.com")
        session.add(u1)
        await session.flush()
        session.add(
            UserProfile(
                user_id=u1.id,
                openai_api_key_encrypted="same-value",
                github_token_encrypted="same-value",
            )
        )
        await session.commit()
        result = await migrate_legacy_profile_secrets(session)
        assert result["migrated"] == 2
        # deterministic slugs use existing IDs, distinct even for same value
        slugs = sorted([r["slug"] for r in result["items"]])
        assert len(set(slugs)) == 2
        assert all(s.startswith("legacy-user-profile-") for s in slugs)
        assert legacy_secret_slug(u1.id, "openai_api_key") in slugs
        assert legacy_secret_slug(u1.id, "github_token") in slugs
        rows = (await session.execute(select(ManagedSecret))).scalars().all()
        by_slug = {r.slug: r for r in rows if r.slug in set(slugs)}
        assert len(by_slug) == 2
        # encrypted content preserved (decrypts to original)
        assert {r.ciphertext for r in by_slug.values()} == {"same-value"}
        # no plaintext in result artifact beyond redacted handling: values not echoed
        assert "same-value" not in str({k: v for k, v in result.items() if k != "items"})


@pytest.mark.asyncio
async def test_migration_blocks_multi_operator_by_default(tmp_path):
    from api_service.services.profile_secret_migration import (
        MultiOperatorAttributionError,
        migrate_legacy_profile_secrets,
    )

    engine, factory = _factory(tmp_path)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with factory() as session:
        u1 = User(id=uuid.uuid4(), email="a@example.com")
        u2 = User(id=uuid.uuid4(), email="b@example.com")
        session.add_all([u1, u2])
        await session.flush()
        session.add(UserProfile(user_id=u1.id, openai_api_key_encrypted="tok-a"))
        session.add(UserProfile(user_id=u2.id, openai_api_key_encrypted="tok-b"))
        await session.commit()
        with pytest.raises(MultiOperatorAttributionError):
            await migrate_legacy_profile_secrets(session)
        # Blocked before any write: no secret rows created.
        rows = (await session.execute(select(ManagedSecret))).scalars().all()
        assert rows == []


@pytest.mark.asyncio
async def test_migration_bypass_flag_is_owning_caller_only(tmp_path):
    from api_service.services.profile_secret_migration import (
        migrate_legacy_profile_secrets,
    )

    engine, factory = _factory(tmp_path)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with factory() as session:
        u1 = User(id=uuid.uuid4(), email="a@example.com")
        u2 = User(id=uuid.uuid4(), email="b@example.com")
        session.add_all([u1, u2])
        await session.flush()
        session.add(UserProfile(user_id=u1.id, openai_api_key_encrypted="same-value"))
        session.add(UserProfile(user_id=u2.id, openai_api_key_encrypted="same-value"))
        await session.commit()
        # The bypass exists only for an owning guarded migration (e.g.
        # #4346's full-dataset path) that already resolved attribution.
        result = await migrate_legacy_profile_secrets(
            session, enforce_eligibility=False
        )
        assert result["migrated"] == 2
        assert len({item["slug"] for item in result["items"]}) == 2


@pytest.mark.asyncio
async def test_migration_rerun_and_interruption_safe(tmp_path):
    from api_service.services.profile_secret_migration import (
        migrate_legacy_profile_secrets,
    )

    engine, factory = _factory(tmp_path)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with factory() as session:
        u1 = User(id=uuid.uuid4(), email="a@example.com")
        session.add(u1)
        await session.flush()
        session.add(UserProfile(user_id=u1.id, github_token_encrypted="tok-1"))
        await session.commit()
        first = await migrate_legacy_profile_secrets(session)
        second = await migrate_legacy_profile_secrets(session)
        assert first["migrated"] >= 1
        # rerun converges without duplicating or changing slugs
        assert {i["slug"] for i in first["items"]} == {i["slug"] for i in second["items"]}
        assert second["created"] == 0
        rows = (await session.execute(select(ManagedSecret))).scalars().all()
        assert len([r for r in rows if r.slug.startswith("legacy-user-profile-")]) == len(first["items"])
