import uuid
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.api.schemas import UserProfileUpdate
from api_service.db.models import User, UserProfile
from api_service.services.profile_service import ProfileService

@pytest.mark.asyncio
async def test_update_profile_enforces_ownership():
    service = ProfileService()
    db_session = AsyncMock(spec=AsyncSession)

    existing_profile = UserProfile(user_id=uuid.uuid4())
    service.get_profile_by_user_id = AsyncMock(return_value=existing_profile)

    with pytest.raises(HTTPException) as exc_info:
        await service.update_profile(
            db_session=db_session,
            user_id=uuid.uuid4(),
            profile_data=UserProfileUpdate(openai_api_key="new"),
        )

    assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN

@pytest.mark.asyncio
async def test_userprofile_read_populates_from_encrypted():
    profile = UserProfile(
        id=1,
        user_id=uuid.uuid4(),
        openai_api_key_encrypted="secret-openai",
        google_api_key_encrypted="secret-google",
        anthropic_api_key_encrypted="secret-anthropic",
    )

    from api_service.api.schemas import UserProfileRead

    read_schema = UserProfileRead.model_validate(profile)

    assert read_schema.openai_api_key == "secret-openai"
    assert read_schema.google_api_key == "secret-google"
    assert read_schema.anthropic_api_key == "secret-anthropic"

@pytest.mark.asyncio
async def test_sanitized_profile_lookup_does_not_decrypt_secret_columns():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async_session_maker = sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    user_id = uuid.uuid4()

    async with engine.begin() as conn:
        await conn.run_sync(User.__table__.create)
        await conn.run_sync(UserProfile.__table__.create)
        await conn.execute(
            text(
                """
                INSERT INTO user (
                    id,
                    email,
                    hashed_password,
                    is_active,
                    is_superuser,
                    is_verified
                )
                VALUES (:id, :email, :hashed_password, 1, 0, 1)
                """
            ),
            {
                "id": user_id.hex,
                "email": "settings@example.com",
                "hashed_password": "hashed",
            },
        )
        await conn.execute(
            text(
                """
                INSERT INTO user_profile (
                    user_id,
                    openai_api_key_encrypted,
                    google_api_key_encrypted,
                    anthropic_api_key_encrypted,
                    agent_skill_repo_sources_enabled,
                    agent_skill_local_sources_enabled
                )
                VALUES (:user_id, :openai, NULL, :anthropic, 1, 0)
                """
            ),
            {
                "user_id": user_id.hex,
                "openai": "not-valid-for-current-encryption-key",
                "anthropic": "also-not-valid",
            },
        )

    async with async_session_maker() as session:
        profile = await ProfileService().get_or_create_sanitized_profile(
            session, user_id
        )

    assert profile.user_id == user_id
    assert profile.openai_api_key_set is True
    assert profile.google_api_key_set is False
    assert profile.anthropic_api_key_set is True

    await engine.dispose()
