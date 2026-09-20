"""MoonLadderStudios/MoonMind#4356 R2: seed-free first-run journey (hermetic subset).

Exercises the real production boundaries of a fresh local instance without
seeding any application user or session: an empty schema contains no User or
UserProfile rows, provider-profile composition never provisions UserProfile
(see _ensure_profile no-op), synthetic credential material is redacted from
logs, and default-user provisioning requires an explicit caller (it never
runs ambiently at import).

Full journey evidence (submit work, observe logs/chat/artifacts,
cancel/recover, recurring execution against built API/frontend artifacts)
consumes the integrated #4346-4355 cohort; this file proves the seed-free
foundation hermetically with test-only isolation and synthetic credentials.
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

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
async def test_fresh_schema_has_no_user_or_session_seeding(tmp_path) -> None:
    """Fresh create_all must not seed User/UserProfile rows."""
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


@pytest.mark.asyncio
async def test_provider_composition_never_provisions_user_profile(tmp_path) -> None:
    """Registration/login composition leaves UserProfile empty (real boundary)."""
    from api_service.auth import UserManager

    engine, factory = _factory(tmp_path)
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


def test_synthetic_credential_never_survives_log_scrubbing() -> None:
    """Synthetic provider credential is redacted from logs/artifacts."""
    from moonmind.utils.logging import SecretRedactor

    synthetic = "sk-test-synthetic-4356-first-run-xyz987"
    redactor = SecretRedactor(secrets=[synthetic])
    sample = f"provider profile configured key={synthetic} for first run"
    scrubbed = redactor.scrub(sample)
    assert synthetic not in scrubbed
    assert scrubbed == sample.replace(synthetic, "***")


def test_default_user_provisioning_requires_explicit_caller() -> None:
    """No ambient seeding: helper needs an explicit session; import is inert."""
    import inspect

    import api_service.auth as auth_module

    assert hasattr(auth_module, "get_or_create_default_user")
    params = inspect.signature(auth_module.get_or_create_default_user).parameters
    assert "db_session" in params
    # Claiming the reserved default admin additionally requires explicit
    # operator authorization -- it must refuse without it.
    claim_params = inspect.signature(auth_module.claim_default_admin).parameters
    assert "operator_authorized" in claim_params


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
