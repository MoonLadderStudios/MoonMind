import asyncio
import uuid
from unittest.mock import patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.main import app, startup_event
from api_service.auth import _DEFAULT_USER_ID
from api_service.db import base as db_base
from api_service.db.models import Base, UserProfile
from moonmind.config.settings import settings


@pytest.mark.asyncio
async def test_startup_profile_seeding(monkeypatch, tmp_path):
    db_url = f"sqlite+aiosqlite:///{tmp_path}/test.db"
    db_base.DATABASE_URL = db_url
    db_base.engine = create_async_engine(db_url, future=True)
    db_base.async_session_maker = sessionmaker(
        db_base.engine, class_=AsyncSession, expire_on_commit=False
    )
    async with db_base.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    monkeypatch.setattr(settings.oidc, "AUTH_PROVIDER", "disabled", raising=False)
    monkeypatch.setattr(settings.oidc, "DEFAULT_USER_ID", _DEFAULT_USER_ID, raising=False)
    monkeypatch.setattr(settings.oidc, "DEFAULT_USER_EMAIL", "seed@example.com", raising=False)
    monkeypatch.setattr(settings.openai, "openai_api_key", "sk-test", raising=False)
    monkeypatch.setattr(settings.google, "google_api_key", "g-test", raising=False)

    with patch("api_service.main._initialize_embedding_model"), \
         patch("api_service.main._initialize_vector_store"), \
         patch("api_service.main._initialize_contexts"), \
         patch("api_service.main._load_or_create_vector_index"), \
         patch("api_service.main._initialize_oidc_provider"):
        await startup_event()

    async with db_base.async_session_maker() as session:
        result = await session.execute(
            select(UserProfile).where(UserProfile.user_id == uuid.UUID(_DEFAULT_USER_ID))
        )
        profile = result.scalars().first()
        assert profile is not None
        assert profile.openai_api_key_encrypted is not None
        assert profile.google_api_key_encrypted is not None
