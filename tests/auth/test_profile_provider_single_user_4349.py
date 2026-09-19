"""Issue #4349: provider-profile credential resolution without application users.

Verifies:
- ProfileAuthProvider resolves via explicit profile_id + managed-secret refs
  without User/UserProfile reads or implicit profile creation.
- Missing/revoked/wrong-profile credentials block only the affected operation.
- Two synthetic profile credentials stay isolated through cache, refresh,
  rotation, and concurrent use.
- Secrets never appear in repr/logs (RedactedSecret).
"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import (
    Base,
    ManagedAgentProviderProfile,
    ManagedSecret,
    SecretStatus,
)
from api_service.services.profile_service import ProfileService
from moonmind.auth.profile_provider import ProfileAuthProvider


def _make_session_factory(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/t4349.db")
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    return engine, factory


async def _init_db(engine):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


def _profile_row(profile_id, secret_refs, generation=1, enabled=True):
    return ManagedAgentProviderProfile(
        profile_id=profile_id,
        runtime_id="codex_cli",
        provider_id="openai",
        credential_source="secret_ref",
        runtime_materialization_mode="api_key_env",
        secret_refs=secret_refs,
        credential_generation=generation,
        enabled=enabled,
        auth_state="connected",
    )


@pytest.mark.asyncio
async def test_profile_secret_resolves_without_user(tmp_path):
    engine, factory = _make_session_factory(tmp_path)
    await _init_db(engine)
    async with factory() as session:
        session.add(ManagedSecret(slug="prof-a-key", ciphertext="synthetic-A", status=SecretStatus.ACTIVE))
        session.add(_profile_row("prof-a", {"OPENAI_API_KEY": "db://prof-a-key"}))
        await session.commit()
        provider = ProfileAuthProvider(session, ProfileService())
        secret = await provider.get_secret(key="OPENAI_API_KEY", profile_id="prof-a")
        assert secret == "synthetic-A"
        assert "synthetic-A" not in repr(secret)


@pytest.mark.asyncio
async def test_missing_revoked_wrong_profile_block_only_affected(tmp_path):
    engine, factory = _make_session_factory(tmp_path)
    await _init_db(engine)
    async with factory() as session:
        session.add(ManagedSecret(slug="prof-a-key", ciphertext="synthetic-A", status=SecretStatus.ACTIVE))
        session.add(ManagedSecret(slug="revoked-key", ciphertext="revoked", status=SecretStatus.DISABLED))
        session.add(_profile_row("prof-a", {"OPENAI_API_KEY": "db://prof-a-key"}))
        session.add(_profile_row("prof-b", {"OPENAI_API_KEY": "db://revoked-key"}))
        session.add(_profile_row("prof-c", {"OTHER_KEY": "db://prof-a-key"}))
        await session.commit()
        provider = ProfileAuthProvider(session, ProfileService())
        # revoked blocks affected operation
        assert await provider.get_secret(key="OPENAI_API_KEY", profile_id="prof-b") is None
        # wrong-profile key does not fall back to another profile's credential
        assert await provider.get_secret(key="OPENAI_API_KEY", profile_id="prof-c") is None
        # unaffected profile still resolves
        assert await provider.get_secret(key="OPENAI_API_KEY", profile_id="prof-a") == "synthetic-A"
        # unknown profile blocks without selecting another profile
        assert await provider.get_secret(key="OPENAI_API_KEY", profile_id="nope") is None


@pytest.mark.asyncio
async def test_two_credentials_isolated_through_cache_refresh_rotation(tmp_path):
    engine, factory = _make_session_factory(tmp_path)
    await _init_db(engine)
    async with factory() as session:
        session.add(ManagedSecret(slug="k1", ciphertext="synthetic-1", status=SecretStatus.ACTIVE))
        session.add(ManagedSecret(slug="k2", ciphertext="synthetic-2", status=SecretStatus.ACTIVE))
        session.add(_profile_row("p1", {"OPENAI_API_KEY": "db://k1"}))
        session.add(_profile_row("p2", {"OPENAI_API_KEY": "db://k2"}))
        await session.commit()
        provider = ProfileAuthProvider(session, ProfileService())
        assert await provider.get_secret(key="OPENAI_API_KEY", profile_id="p1") == "synthetic-1"
        assert await provider.get_secret(key="OPENAI_API_KEY", profile_id="p2") == "synthetic-2"
        # cache hits stay isolated
        assert await provider.get_secret(key="OPENAI_API_KEY", profile_id="p1") == "synthetic-1"
        assert await provider.get_secret(key="OPENAI_API_KEY", profile_id="p2") == "synthetic-2"
        # rotation of p1 must not leak into p2 and must invalidate p1 cache
        row = await session.get(ManagedSecret, (await session.execute(
            __import__("sqlalchemy").select(ManagedSecret).where(ManagedSecret.slug == "k1")
        )).scalar_one().id)
        row.ciphertext = "synthetic-1-rotated"
        prof = await session.get(ManagedAgentProviderProfile, "p1")
        prof.credential_generation = 2
        await session.commit()
        assert await provider.get_secret(key="OPENAI_API_KEY", profile_id="p1") == "synthetic-1-rotated"
        assert await provider.get_secret(key="OPENAI_API_KEY", profile_id="p2") == "synthetic-2"


@pytest.mark.asyncio
async def test_no_user_no_implicit_profile_creation(tmp_path):
    engine, factory = _make_session_factory(tmp_path)
    await _init_db(engine)
    async with factory() as session:
        session.add(ManagedSecret(slug="k1", ciphertext="synthetic-1", status=SecretStatus.ACTIVE))
        session.add(_profile_row("p1", {"OPENAI_API_KEY": "db://k1"}))
        await session.commit()
        provider = ProfileAuthProvider(session, ProfileService())
        # legacy user-less call without profile must not create anything or resolve
        assert await provider.get_secret(key="OPENAI_API_KEY") is None
        from sqlalchemy import select
        from api_service.db.models import UserProfile

        assert (await session.execute(select(UserProfile))).scalars().all() == []


@pytest.mark.asyncio
async def test_concurrent_profile_resolution_isolated(tmp_path):
    import asyncio

    engine, factory = _make_session_factory(tmp_path)
    await _init_db(engine)
    async with factory() as session:
        session.add(ManagedSecret(slug="k1", ciphertext="synthetic-1", status=SecretStatus.ACTIVE))
        session.add(ManagedSecret(slug="k2", ciphertext="synthetic-2", status=SecretStatus.ACTIVE))
        session.add(_profile_row("p1", {"OPENAI_API_KEY": "db://k1"}))
        session.add(_profile_row("p2", {"OPENAI_API_KEY": "db://k2"}))
        await session.commit()
    async with factory() as session:
        provider = ProfileAuthProvider(session, ProfileService())
        r1, r2 = await asyncio.gather(
            provider.get_secret(key="OPENAI_API_KEY", profile_id="p1"),
            provider.get_secret(key="OPENAI_API_KEY", profile_id="p2"),
        )
        assert r1 == "synthetic-1"
        assert r2 == "synthetic-2"
