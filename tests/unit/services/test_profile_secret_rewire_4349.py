"""Issue #4349 R2: transactional secret_refs rewiring + eligibility gating.

Verifies:
- ``check_single_operator_eligibility`` allows empty/single-operator legacy
  secret sets and blocks multi-operator sets without mutating anything.
- ``rewire_provider_profile_secret_refs`` publishes ``db://`` references
  only after verifying each secret exists ACTIVE (create-before-reference),
  is idempotent (retry safe), bumps ``credential_generation`` exactly when
  bindings change, preserves profile IDs/bindings and effective settings,
  and keeps distinct same-value credentials distinct.
- No plaintext appears in rewire/eligibility results.
"""

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import (
    Base,
    ManagedAgentProviderProfile,
    ManagedSecret,
    SecretStatus,
    User,
    UserProfile,
)


def _factory(tmp_path, name="rewire4349.db"):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/{name}")
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    return engine, factory


def _profile_row(profile_id, **overrides):
    params = {
        "profile_id": profile_id,
        "runtime_id": "codex_cli",
        "provider_id": "openai",
        "credential_source": "secret_ref",
        "runtime_materialization_mode": "api_key_env",
        "credential_generation": 1,
        "enabled": True,
        "auth_state": "connected",
        "default_model": "gpt-5",
        "default_effort": "high",
    }
    params.update(overrides)
    return ManagedAgentProviderProfile(**params)


@pytest.mark.asyncio
async def test_eligibility_empty_and_single_operator(tmp_path):
    from api_service.services.profile_secret_migration import (
        check_single_operator_eligibility,
        legacy_secret_slug,
    )
    from moonmind.auth.secret_refs import SecretBackend, parse_secret_ref

    # Deterministic slugs are immediately usable as db:// references.
    slug = legacy_secret_slug(uuid.uuid4(), "openai_api_key")
    assert parse_secret_ref(f"db://{slug}").backend == SecretBackend.DB_ENCRYPTED

    engine, factory = _factory(tmp_path)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with factory() as session:
        result = await check_single_operator_eligibility(session)
        assert result["eligible"] is True
        u1 = User(id=uuid.uuid4(), email="solo@example.com")
        session.add(u1)
        await session.flush()
        session.add(UserProfile(user_id=u1.id, openai_api_key_encrypted="s3cret-1"))
        await session.commit()
        result = await check_single_operator_eligibility(session)
        assert result["eligible"] is True
        assert result["secret_holders"] == 1
        assert "s3cret-1" not in str(result)


@pytest.mark.asyncio
async def test_eligibility_blocks_multi_operator_without_mutation(tmp_path):
    from api_service.services.profile_secret_migration import (
        check_single_operator_eligibility,
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
        before = (await session.execute(select(ManagedSecret))).scalars().all()
        result = await check_single_operator_eligibility(session)
        assert result["eligible"] is False
        assert result["reason"] == "multi_operator_attribution"
        assert result["secret_holders"] == 2
        assert "tok-a" not in str(result) and "tok-b" not in str(result)
        after = (await session.execute(select(ManagedSecret))).scalars().all()
        assert len(before) == len(after) == 0


@pytest.mark.asyncio
async def test_rewire_publishes_refs_and_bumps_generation(tmp_path):
    from api_service.services.profile_secret_migration import (
        rewire_provider_profile_secret_refs,
    )

    engine, factory = _factory(tmp_path)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with factory() as session:
        session.add(
            ManagedSecret(
                slug="legacy-secret-a", ciphertext="synthetic-A", status=SecretStatus.ACTIVE
            )
        )
        session.add(_profile_row("prof-a"))
        await session.commit()
        result = await rewire_provider_profile_secret_refs(
            session,
            profile_id="prof-a",
            refs={"OPENAI_API_KEY": "db://legacy-secret-a"},
        )
        assert result["outcome"] == "rewired"
        assert result["credential_generation"] == 2
        assert result["applied_refs"] == {"OPENAI_API_KEY": "db://legacy-secret-a"}
        assert "synthetic-A" not in str(result)
        profile = await session.get(ManagedAgentProviderProfile, "prof-a")
        assert profile.secret_refs == {"OPENAI_API_KEY": "db://legacy-secret-a"}
        # IDs, bindings, effective settings preserved.
        assert profile.profile_id == "prof-a"
        assert profile.runtime_id == "codex_cli"
        assert profile.provider_id == "openai"
        assert profile.default_model == "gpt-5"
        assert profile.default_effort == "high"


@pytest.mark.asyncio
async def test_rewire_is_idempotent_and_retry_safe(tmp_path):
    from api_service.services.profile_secret_migration import (
        rewire_provider_profile_secret_refs,
    )

    engine, factory = _factory(tmp_path)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with factory() as session:
        session.add(
            ManagedSecret(
                slug="legacy-secret-a", ciphertext="synthetic-A", status=SecretStatus.ACTIVE
            )
        )
        session.add(_profile_row("prof-a"))
        await session.commit()
        first = await rewire_provider_profile_secret_refs(
            session, profile_id="prof-a", refs={"OPENAI_API_KEY": "db://legacy-secret-a"}
        )
        second = await rewire_provider_profile_secret_refs(
            session, profile_id="prof-a", refs={"OPENAI_API_KEY": "db://legacy-secret-a"}
        )
        assert first["credential_generation"] == 2
        assert second["outcome"] == "reused"
        assert second["credential_generation"] == 2


@pytest.mark.asyncio
async def test_rewire_blocks_missing_secret_and_unknown_profile(tmp_path):
    from api_service.services.profile_secret_migration import (
        rewire_provider_profile_secret_refs,
    )

    engine, factory = _factory(tmp_path)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with factory() as session:
        session.add(
            ManagedSecret(
                slug="revoked-key", ciphertext="revoked", status=SecretStatus.DISABLED
            )
        )
        session.add(
            ManagedSecret(
                slug="active-key",
                ciphertext="synthetic-active",
                status=SecretStatus.ACTIVE,
            )
        )
        session.add(_profile_row("prof-a"))
        await session.commit()
        # Missing slug: no reference published.
        with pytest.raises(ValueError, match="missing or not ACTIVE"):
            await rewire_provider_profile_secret_refs(
                session, profile_id="prof-a", refs={"OPENAI_API_KEY": "db://nope"}
            )
        # Revoked slug: no reference published.
        with pytest.raises(ValueError, match="missing or not ACTIVE"):
            await rewire_provider_profile_secret_refs(
                session, profile_id="prof-a", refs={"OPENAI_API_KEY": "db://revoked-key"}
            )
        profile = await session.get(ManagedAgentProviderProfile, "prof-a")
        assert (profile.secret_refs or {}) == {}
        assert profile.credential_generation == 1
        # Unknown profile: existing IDs only (reference an ACTIVE secret so
        # the profile-existence check is the one exercised).
        with pytest.raises(ValueError, match="does not exist"):
            await rewire_provider_profile_secret_refs(
                session, profile_id="ghost", refs={"OPENAI_API_KEY": "db://active-key"}
            )


@pytest.mark.asyncio
async def test_rewire_keeps_same_value_distinct_credentials_distinct(tmp_path):
    from api_service.services.profile_secret_migration import (
        legacy_secret_slug,
        migrate_legacy_profile_secrets,
        rewire_provider_profile_secret_refs,
    )

    engine, factory = _factory(tmp_path)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with factory() as session:
        u1 = User(id=uuid.uuid4(), email="a@example.com")
        u2 = User(id=uuid.uuid4(), email="b@example.com")
        session.add_all([u1, u2])
        await session.flush()
        # One legacy row carries two distinct credentials holding the same
        # value; a second operator holds the same value again. All three
        # source credentials stay distinct (slugs derive from existing IDs
        # + field, never value hashes).
        session.add(
            UserProfile(
                user_id=u1.id,
                openai_api_key_encrypted="same-value",
                github_token_encrypted="same-value",
            )
        )
        session.add(UserProfile(user_id=u2.id, openai_api_key_encrypted="same-value"))
        await session.commit()
        # Distinct legacy fields keep distinct slugs even for identical values.
        slug_openai_u1 = legacy_secret_slug(u1.id, "openai_api_key")
        slug_github_u1 = legacy_secret_slug(u1.id, "github_token")
        slug_openai_u2 = legacy_secret_slug(u2.id, "openai_api_key")
        assert len({slug_openai_u1, slug_github_u1, slug_openai_u2}) == 3
        result = await migrate_legacy_profile_secrets(session)
        assert result["migrated"] == 3
        session.add(_profile_row("prof-a"))
        session.add(_profile_row("prof-b"))
        await session.commit()
        await rewire_provider_profile_secret_refs(
            session, profile_id="prof-a", refs={"OPENAI_API_KEY": f"db://{slug_openai_u1}"}
        )
        await rewire_provider_profile_secret_refs(
            session, profile_id="prof-b", refs={"GITHUB_TOKEN": f"db://{slug_github_u1}"}
        )
        pa = await session.get(ManagedAgentProviderProfile, "prof-a")
        pb = await session.get(ManagedAgentProviderProfile, "prof-b")
        assert pa.secret_refs != pb.secret_refs
        assert pa.secret_refs["OPENAI_API_KEY"] == f"db://{slug_openai_u1}"
        assert pb.secret_refs["GITHUB_TOKEN"] == f"db://{slug_github_u1}"
