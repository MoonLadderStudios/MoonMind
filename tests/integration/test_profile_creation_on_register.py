import uuid
import asyncio
import pytest
from httpx import AsyncClient, ASGITransport
from unittest.mock import patch
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy import select

from api_service.main import app, startup_event
from api_service.db import base as db_base
from api_service.db.models import Base, UserProfile
from moonmind.config.settings import settings

@pytest.mark.asyncio
async def test_profile_created_on_register(monkeypatch, tmp_path):
    db_url = f"sqlite+aiosqlite:///{tmp_path}/test.db"
    db_base.DATABASE_URL = db_url
    db_base.engine = create_async_engine(db_url, future=True)
    db_base.async_session_maker = sessionmaker(db_base.engine, class_=AsyncSession, expire_on_commit=False)
    async with db_base.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    monkeypatch.setattr(settings.oidc, "AUTH_PROVIDER", "default", raising=False)

    with patch("api_service.main._initialize_embedding_model"), \
         patch("api_service.main._initialize_vector_store"), \
         patch("api_service.main._initialize_contexts"), \
         patch("api_service.main._load_or_create_vector_index"), \
         patch("api_service.main._initialize_oidc_provider"):
        await startup_event()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp = await client.post("/api/v1/auth/register", json={"email": "user@example.com", "password": "pass"})
        assert resp.status_code == 201
        user_id = uuid.UUID(resp.json()["id"])

    # wait for background task to create the profile
    async def wait_for_profile_creation(user_id, session_maker, timeout=5):
        start_time = asyncio.get_event_loop().time()
        while asyncio.get_event_loop().time() - start_time < timeout:
            async with session_maker() as session:
                result = await session.execute(select(UserProfile).where(UserProfile.user_id == user_id))
                if result.scalars().first() is not None:
                    return True
            await asyncio.sleep(0.05)
        return False

    assert await wait_for_profile_creation(user_id, db_base.async_session_maker)
