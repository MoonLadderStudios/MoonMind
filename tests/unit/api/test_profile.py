import uuid
import pytest
from unittest.mock import AsyncMock, MagicMock
from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.api.routers.profile import (
    get_current_user_profile,
    update_current_user_profile,
    get_profile_service,
)
from api_service.api.schemas import UserProfileRead, UserProfileUpdate
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
MOCK_PROFILE_DATA = {"id": 1, "user_id": USER_ID, "google_api_key": "test_google_key"}
MOCK_PROFILE_READ_SCHEMA = UserProfileRead(**MOCK_PROFILE_DATA)


@pytest.fixture
def mock_db_session():
    return AsyncMock(spec=AsyncSession)


@pytest.fixture
def mock_profile_service():
    service = AsyncMock(spec=ProfileService)
    service.get_or_create_profile = AsyncMock(return_value=MOCK_PROFILE_READ_SCHEMA)
    service.update_profile = AsyncMock(return_value=MOCK_PROFILE_READ_SCHEMA)
    return service


# Override the dependency for tests
async def override_get_profile_service(mock_service: ProfileService):
    async def _override():
        return mock_service

    return _override


@pytest.mark.asyncio
async def test_get_current_user_profile_success(mock_db_session, mock_profile_service):
    # Arrange
    # The get_profile_service dependency will be overridden globally or per router instance in a real test setup
    # For this unit test, we can pass the mocked service directly if the function signature allows,
    # or mock the `Depends` mechanism if necessary. Here, we assume `get_profile_service` is simple.

    # Act
    profile = await get_current_user_profile(
        user=MOCK_USER, db=mock_db_session, profile_service=mock_profile_service
    )

    # Assert
    assert profile == MOCK_PROFILE_READ_SCHEMA
    mock_profile_service.get_or_create_profile.assert_called_once_with(
        db_session=mock_db_session, user_id=USER_ID
    )


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
            user=user_without_id,  # User with no ID
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
    update_data = UserProfileUpdate(google_api_key="new_google_key")

    # Act
    updated_profile = await update_current_user_profile(
        profile_update_data=update_data,
        user=MOCK_USER,
        db=mock_db_session,
        profile_service=mock_profile_service,
    )

    # Assert
    assert (
        updated_profile == MOCK_PROFILE_READ_SCHEMA
    )  # Assuming update_profile returns the same mock for simplicity
    mock_profile_service.update_profile.assert_called_once_with(
        db_session=mock_db_session, user_id=USER_ID, profile_data=update_data
    )


@pytest.mark.asyncio
async def test_update_current_user_profile_user_id_none(
    mock_db_session, mock_profile_service
):
    # Arrange
    update_data = UserProfileUpdate(google_api_key="new_google_key")
    user_without_id = MagicMock(spec=DBUser)
    user_without_id.id = None

    # Act & Assert
    with pytest.raises(HTTPException) as exc_info:
        await update_current_user_profile(
            profile_update_data=update_data,
            user=user_without_id,  # User with no ID
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


# To properly test endpoints with FastAPI TestClient, you would set up a test app
# and override dependencies at the app level. Example:
# from fastapi.testclient import TestClient
# from api_service.main import app # Your FastAPI app
# from api_service.api.routers.profile import get_profile_service as real_get_profile_service

# client = TestClient(app)

# @pytest.fixture(autouse=True) # autouse to apply to all tests in this module
# def override_profile_service_dependency(mock_profile_service):
#     app.dependency_overrides[real_get_profile_service] = lambda: mock_profile_service
#     yield
#     app.dependency_overrides.clear()

# Then write tests using client.get("/api/v1/profile/me", headers={"Authorization": "Bearer <token>"})
# This requires more setup for authentication (e.g., mocking current_active_user)
# For unit tests of the route functions themselves as shown above, direct calls are simpler.
# The `current_active_user` dependency would also need to be mocked/overridden for TestClient tests.
# For now, the tests focus on the route handler functions directly.
# To test `current_active_user` behavior (e.g. 401 unauthorized), TestClient is needed.
# These tests primarily cover the logic within the route handlers given a valid user.
# (Adding a placeholder for TestClient based tests if required in future)
# def test_get_profile_unauthorized_with_testclient():
#     # response = client.get("/api/v1/profile/me")
#     # assert response.status_code == 401
#     pass
