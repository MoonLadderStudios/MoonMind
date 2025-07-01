import uuid
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.main import app, startup_event
from api_service.db import base as db_base
from api_service.db.models import Base, User
from api_service.services.profile_service import ProfileService
from api_service.api.schemas import UserProfileUpdate
from moonmind.config.settings import settings


@pytest.mark.asyncio
async def test_ui_keys_used_in_chat(disabled_env_keys, tmp_path):
    db_url = f"sqlite+aiosqlite:///{tmp_path}/test.db"
    db_base.DATABASE_URL = db_url
    db_base.engine = create_async_engine(db_url, future=True)
    db_base.async_session_maker = sessionmaker(
        db_base.engine, class_=AsyncSession, expire_on_commit=False
    )
    async with db_base.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    with (
        patch("api_service.main._initialize_embedding_model"),
        patch("api_service.main._initialize_vector_store"),
        patch("api_service.main._initialize_contexts"),
        patch("api_service.main._load_or_create_vector_index"),
        patch("api_service.main._initialize_oidc_provider"),
    ):
        await startup_event()

    async with db_base.async_session_maker() as session:
        service = ProfileService()
        user = await session.get(User, uuid.UUID(settings.oidc.DEFAULT_USER_ID))
        await service.update_profile(
            db_session=session,
            user_id=user.id,
            profile_data=UserProfileUpdate(openai_api_key="user-key"),
        )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        with (
            patch("api_service.api.routers.chat.model_cache.get_model_provider", return_value="OpenAI"),
            patch("api_service.api.routers.chat.AsyncOpenAI") as mock_ai,
        ):
            mock_client = AsyncMock()
            mock_client.chat.completions.create = AsyncMock(
                return_value=type("Resp", (), {"choices": [type("C", (), {"message": type("M", (), {"content": "ok"})()})], "model": "gpt", "usage": None})()
            )
            mock_ai.return_value = mock_client
            payload = {
                "model": "gpt",
                "messages": [{"role": "user", "content": "hi"}],
                "max_tokens": 10,
            }
            resp = await client.post("/v1/chat/completions", json=payload)
            assert resp.status_code == 200
            mock_ai.assert_called_with(api_key="user-key")


@pytest.mark.asyncio
async def test_keycloak_user_profile_empty(keycloak_mode, tmp_path):
    db_url = f"sqlite+aiosqlite:///{tmp_path}/test.db"
    db_base.DATABASE_URL = db_url
    db_base.engine = create_async_engine(db_url, future=True)
    db_base.async_session_maker = sessionmaker(
        db_base.engine, class_=AsyncSession, expire_on_commit=False
    )
    async with db_base.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    with (
        patch("api_service.main._initialize_embedding_model"),
        patch("api_service.main._initialize_vector_store"),
        patch("api_service.main._initialize_contexts"),
        patch("api_service.main._load_or_create_vector_index"),
        patch("api_service.main._initialize_oidc_provider"),
    ):
        await startup_event()

    async with db_base.async_session_maker() as session:
        user = User(id=uuid.uuid4(), email="k@example.com")
        session.add(user)
        await session.commit()
        await session.refresh(user)
        service = ProfileService()
        profile = await service.get_or_create_profile(session, user.id)
        assert profile.openai_api_key is None
        assert profile.google_api_key is None
        assert getattr(profile, "openai_api_key_encrypted", None) is None
        assert getattr(profile, "google_api_key_encrypted", None) is None
