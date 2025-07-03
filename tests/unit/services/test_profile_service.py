import uuid
import pytest
from unittest.mock import AsyncMock

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.services.profile_service import ProfileService
from api_service.db.models import UserProfile
from api_service.api.schemas import UserProfileUpdate


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
    )

    from api_service.api.schemas import UserProfileRead

    read_schema = UserProfileRead.model_validate(profile)

    assert read_schema.openai_api_key == "secret-openai"
    assert read_schema.google_api_key == "secret-google"
