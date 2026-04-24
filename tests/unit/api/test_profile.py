import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.api.routers.profile import (
    get_current_user_profile,
    get_profile_service,
    update_current_user_profile,
)
from api_service.api.schemas import (
    UserProfileRead,
    UserProfileReadSanitized,
    UserProfileUpdate,
)
from api_service.services.profile_service import ProfileService

from api_service.db.models import User as DBUser

# Mock user data
USER_ID = uuid.uuid4()
MOCK_USER = DBUser(
    id=USER_ID,
    email="test@example.com",
    is_active=True,
    is_superuser=False,
    is_verified=True,
    hashed_password="hashedpassword",
)

# Mock profile data
MOCK_PROFILE_DATA = {
    "id": 1,
    "user_id": USER_ID,
    "google_api_key": "test_google_key",
    "openai_api_key": "test_openai_key",
    "anthropic_api_key": "test_anthropic_key",
}
MOCK_PROFILE_READ_SCHEMA = UserProfileRead(**MOCK_PROFILE_DATA)

# Sanitized version for GET endpoint response testing
MOCK_PROFILE_READ_SANITIZED_SCHEMA = UserProfileReadSanitized(
    id=MOCK_PROFILE_DATA["id"], user_id=MOCK_PROFILE_DATA["user_id"]
)

@pytest.fixture
def mock_db_session():
    return AsyncMock(spec=AsyncSession)

@pytest.fixture
def mock_profile_service():
    service = AsyncMock(spec=ProfileService)
    service.get_or_create_profile = AsyncMock(return_value=MOCK_PROFILE_READ_SCHEMA)
    service.update_profile = AsyncMock(return_value=MOCK_PROFILE_READ_SCHEMA)
    return service

@pytest.mark.asyncio
async def test_get_current_user_profile_success(mock_db_session, mock_profile_service):
    # Act
    result = await get_current_user_profile(
        user=MOCK_USER, db=mock_db_session, profile_service=mock_profile_service
    )

    # Assert
    mock_profile_service.get_or_create_profile.assert_called_once_with(
        db_session=mock_db_session, user_id=USER_ID
    )
    # The result is now a UserProfileReadSanitized with key-set flags
    assert isinstance(result, UserProfileReadSanitized)
    assert result.id == MOCK_PROFILE_DATA["id"]
    assert result.user_id == MOCK_PROFILE_DATA["user_id"]
    assert result.google_api_key_set is True  # test data has google_api_key set
    assert result.openai_api_key_set is True  # test data has openai_api_key set
    assert result.anthropic_api_key_set is True

@pytest.mark.asyncio
async def test_get_current_user_profile_user_id_none(
    mock_db_session, mock_profile_service
):
    # Arrange
    user_without_id = MagicMock(spec=DBUser)
    user_without_id.id = None

    # Act & Assert
    with pytest.raises(HTTPException) as exc_info:
        await get_current_user_profile(
            user=user_without_id,
            db=mock_db_session,
            profile_service=mock_profile_service,
        )
    assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
    assert exc_info.value.detail == "User ID is missing."
    mock_profile_service.get_or_create_profile.assert_not_called()

@pytest.mark.asyncio
async def test_update_current_user_profile_success(
    mock_db_session, mock_profile_service
):
    # Arrange
    update_data = UserProfileUpdate(
        google_api_key="new_google_key", openai_api_key="new_openai_key"
    )

    # Act
    result = await update_current_user_profile(
        profile_update_data=update_data,
        user=MOCK_USER,
        db=mock_db_session,
        profile_service=mock_profile_service,
    )

    # Assert - result is now a UserProfileReadSanitized with key-set flags
    assert isinstance(result, UserProfileReadSanitized)
    assert result.google_api_key_set is True
    assert result.openai_api_key_set is True
    assert result.anthropic_api_key_set is True
    mock_profile_service.update_profile.assert_called_once_with(
        db_session=mock_db_session, user_id=USER_ID, profile_data=update_data
    )

@pytest.mark.asyncio
async def test_update_current_user_profile_user_id_none(
    mock_db_session, mock_profile_service
):
    # Arrange
    update_data = UserProfileUpdate(
        google_api_key="new_google_key", openai_api_key="new_openai_key"
    )
    user_without_id = MagicMock(spec=DBUser)
    user_without_id.id = None

    # Act & Assert
    with pytest.raises(HTTPException) as exc_info:
        await update_current_user_profile(
            profile_update_data=update_data,
            user=user_without_id,
            db=mock_db_session,
            profile_service=mock_profile_service,
        )
    assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
    assert exc_info.value.detail == "User ID is missing."
    mock_profile_service.update_profile.assert_not_called()

# Test for the get_profile_service dependency itself (simple test)
@pytest.mark.asyncio
async def test_get_profile_service_returns_instance():
    service = await get_profile_service()
    assert isinstance(service, ProfileService)
