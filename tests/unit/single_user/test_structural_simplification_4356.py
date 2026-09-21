"""MoonLadderStudios/MoonMind#4356 R7 (early): structural-simplification probes.

Narrow behavioral regressions for the single-user contracts that #4349
already established. These tests exercise runtime behavior (profile
creation, secret handling) rather than matching text. Account-era route
inventory assertions were removed: the owning cohort deletes those
surfaces, and this suite must not pin their continued importability.
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import Base, User, UserProfile


def _factory(tmp_path, name="single4356.db"):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/{name}")
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    return engine, factory


@pytest.mark.asyncio
async def test_profile_paths_never_provision_user_profile(tmp_path) -> None:
    """Provider-profile composition must not create UserProfile rows."""
    from api_service.auth import UserManager

    engine, factory = _factory(tmp_path)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with factory() as session:
        user = User(id=uuid.uuid4(), email="operator4356@example.com")
        session.add(user)
        await session.commit()
        manager = UserManager(MagicMock())
        assert await manager._ensure_profile(user) is None
        rows = (await session.execute(select(UserProfile))).scalars().all()
        assert rows == []


def test_secret_redaction_hides_credential_material() -> None:
    """Registered credential material must not survive log scrubbing."""
    from moonmind.utils.logging import SecretRedactor

    secret = "sk-live-synthetic-4356-abcdef"
    redactor = SecretRedactor(secrets=[secret])
    sample = f"config updated with key={secret} for profile openai"
    assert redactor.scrub(sample) == sample.replace(secret, "***")
    assert secret not in redactor.scrub_sequence([sample])[0]
