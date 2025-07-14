import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import Base, User, UserProfile
from api_service.services.profile_service import ProfileService
from moonmind.auth.profile_provider import ProfileAuthProvider


@pytest.mark.asyncio
async def test_profile_provider_returns_secret(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/test.db")
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with async_session() as session:
        user = User(id=uuid.uuid4(), email="t@example.com")
        session.add(user)
        session.add(UserProfile(user_id=user.id, github_token_encrypted="tok"))
        await session.commit()
        svc = ProfileService()
        provider = ProfileAuthProvider(session, svc)
        secret = await provider.get_secret(key="GITHUB_TOKEN", user=user)
        assert secret == "tok"
        assert "redacted-sha256" in repr(secret)
