import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException, Request, status
from fastapi.datastructures import FormData, QueryParams
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.status import HTTP_302_FOUND

from api_service.api.routers.profile import (
    templates,  # Import the templates object to mock it
    get_current_user_profile,
    get_profile_management_page,
    get_profile_service,
    handle_profile_update_form,
    update_current_user_profile,
)
from api_service.api.constants import MANAGED_PROVIDERS
from api_service.api.schemas import (
    UserProfileRead,
    UserProfileReadSanitized,
    UserProfileUpdate,
)
from api_service.services.profile_service import ProfileService

# Mock the templates object
# We need to mock TemplateResponse method of the templates object
# It's often easier to mock the specific function if it's imported directly,
# or patch the object where it's used.
# For this example, we'll assume `templates.TemplateResponse` can be patched
# or that the `templates` object itself can be replaced with a mock.

# Let's create a mock for the templates object used in profile router
mock_templates = AsyncMock()


# Custom side_effect for TemplateResponse mock
def mock_template_response_side_effect(
    template_name, context, status_code=200, headers=None
):
    # In a real scenario, you might render the template or do something more complex.
    # For testing, we just need to return an HTMLResponse that respects the status_code.
    # The content can be simple, as it's not the focus of this part of the test.
    return HTMLResponse(
        content=f"<html>mocked_template: {template_name}</html>",
        status_code=status_code,
        headers=headers,
    )


# Configure the mock TemplateResponse to use the custom side_effect
mock_templates.TemplateResponse = MagicMock(
    side_effect=mock_template_response_side_effect
)

# Original templates object from the router module, to be patched
from api_service.api.routers import profile as profile_router_module

profile_router_module.templates = (
    mock_templates  # Patching at module level for simplicity in tests
)

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
    # The endpoint function itself returns the object from the service (which includes keys)
    # FastAPI's response_model handles the serialization to the sanitized version.
    # When unit testing the function directly, it will return what the service returns.
    profile_from_service = await get_current_user_profile(
        user=MOCK_USER, db=mock_db_session, profile_service=mock_profile_service
    )

    # Assert
    # 1. Check the service was called correctly
    mock_profile_service.get_or_create_profile.assert_called_once_with(
        db_session=mock_db_session, user_id=USER_ID
    )
    # 2. Check that the object returned by the service, if serialized with UserProfileReadSanitized, matches expectations.
    # This simulates what FastAPI does with response_model.
    # The `get_current_user_profile` function returns a UserProfileRead instance (from the service).
    # We need to ensure this instance, when data is taken for UserProfileReadSanitized, is correct.
    assert profile_from_service.id == MOCK_PROFILE_READ_SANITIZED_SCHEMA.id
    assert profile_from_service.user_id == MOCK_PROFILE_READ_SANITIZED_SCHEMA.user_id
    # Ensure that the returned object (which is UserProfileRead) still contains the keys,
    # as the sanitization happens at the FastAPI serialization stage.
    assert hasattr(profile_from_service, "google_api_key")
    assert profile_from_service.google_api_key == MOCK_PROFILE_DATA["google_api_key"]


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
    update_data = UserProfileUpdate(
        google_api_key="new_google_key", openai_api_key="new_openai_key"
    )

    # Update mock_profile_service.update_profile to return a schema that reflects these new keys
    # For simplicity, we'll assume MOCK_PROFILE_READ_SCHEMA is what's returned,
    # but in a more detailed test, you might construct a specific UserProfileRead
    # instance that includes "new_google_key" and "new_openai_key".
    # However, the current MOCK_PROFILE_READ_SCHEMA already includes an openai_api_key.
    # The key is that the `update_data` object passed to the service method is correct.

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
    update_data = UserProfileUpdate(
        google_api_key="new_google_key", openai_api_key="new_openai_key"
    )
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


@pytest.fixture
def mock_request():
    # Create a mock request object.
    # For GET requests, query_params might be relevant.
    # For POST requests, form() method might be relevant.
    # Here, we mock basic attributes and methods.
    request = MagicMock(spec=Request)
    request.query_params = QueryParams()  # Default empty query params

    # Mock url_for chain: request.url_for(...).include_query_params(...)
    # The include_query_params method should return a string URL for RedirectResponse
    mock_url_for_object = MagicMock()

    # Make include_query_params a function that constructs a URL string based on its args
    def mock_include_query_params(**kwargs):
        # Base URL, can be anything representative for the test
        base_url = "/api/v1/profile/me/ui"
        if kwargs:
            # Convert QueryParams to string directly
            query_string = str(QueryParams(kwargs))
            return f"{base_url}?{query_string}"
        return base_url

    mock_url_for_object.include_query_params = MagicMock(
        side_effect=mock_include_query_params
    )
    request.url_for = MagicMock(return_value=mock_url_for_object)

    # For older FastAPI/Starlette versions, request.url_for might be on request.app.url_path_for
    # If that's the case, the mock setup would be:
    # request.app = MagicMock()
    # request.app.url_path_for = MagicMock(return_value=mock_url_for_object)
    # However, request.url_for is more standard for recent versions.

    # Mock the form() method to return an AsyncMock that resolves to FormData
    # This is important for the POST endpoint that uses Form data.
    async def mock_form_method():
        # Return a FormData object. It can be empty or pre-filled.
        # For this example, an empty FormData is fine.
        # If your test needs specific form fields, populate them: FormData([("key", "value")])
        return FormData()  # Empty form for default mock_request

    request.form = AsyncMock(side_effect=mock_form_method)
    return request


@pytest.mark.asyncio
async def test_get_profile_management_page_success(
    mock_request, mock_db_session, mock_profile_service
):
    # Arrange
    # Ensure the mock_profile_service returns a profile that has an openai_api_key for status checking
    profile_with_key = MOCK_PROFILE_READ_SCHEMA  # This has openai_api_key set
    mock_profile_service.get_or_create_profile = AsyncMock(
        return_value=profile_with_key
    )

    # Act
    response = await get_profile_management_page(
        request=mock_request,
        user=MOCK_USER,
        db=mock_db_session,
        profile_service=mock_profile_service,
    )

    # Assert
    assert isinstance(
        response, HTMLResponse
    )  # Check if it returns an HTMLResponse (via mocked TemplateResponse)
    mock_profile_service.get_or_create_profile.assert_called_once_with(
        db_session=mock_db_session, user_id=USER_ID
    )
    # Check that TemplateResponse was called with the correct template name and context
    expected_keys_status = {"openai_api_key_set": bool(profile_with_key.openai_api_key)}

    mock_templates.TemplateResponse.assert_called_once()
    args, kwargs = mock_templates.TemplateResponse.call_args
    assert args[0] == "profile.html"  # Template name
    context = args[1]  # Context dict
    assert context["request"] == mock_request
    assert context["user"] == MOCK_USER
    assert context["keys_status"] == expected_keys_status
    assert context["message"] is None  # No message in query_params by default


@pytest.mark.asyncio
async def test_get_profile_management_page_with_message(
    mock_request, mock_db_session, mock_profile_service
):
    # Arrange
    mock_request.query_params = QueryParams(
        "message=Test+Message"
    )  # Simulate message in URL

    # Act
    await get_profile_management_page(
        request=mock_request,
        user=MOCK_USER,
        db=mock_db_session,
        profile_service=mock_profile_service,
    )

    # Assert
    # Check that TemplateResponse was called with the message in context
    args, kwargs = mock_templates.TemplateResponse.call_args
    context = args[1]
    assert context["message"] == "Test Message"


@pytest.mark.asyncio
async def test_handle_profile_update_form_success_new_key(
    mock_request, mock_db_session, mock_profile_service
):
    # Arrange
    new_openai_key = "new_test_openai_key"
    # Mock request.form() to return FormData with the new key
    # We need to ensure the mock_request.form() is an async function that returns FormData
    # The fixture for mock_request already sets up request.form as an AsyncMock.
    # We can configure its return_value for this specific test if needed,
    # or pass form fields directly to the endpoint.
    # The endpoint uses `openai_api_key: str = Form(None)`, so we pass it directly.

    # Act
    response = await handle_profile_update_form(
        request=mock_request,
        user=MOCK_USER,
        db=mock_db_session,
        profile_service=mock_profile_service,
        openai_api_key=new_openai_key,  # Pass the form data directly
    )

    # Assert
    assert isinstance(response, RedirectResponse)
    assert response.status_code == HTTP_302_FOUND
    # Check if url_for was used correctly for redirection
    # The mock_request.url_for should be configured to return the expected path
    # Or, more simply, check that the redirect URL contains the success message.
    assert "message=API+keys+updated+successfully." in response.headers["location"]

    # Verify that profile_service.update_profile was called with the correct data
    expected_update_data = UserProfileUpdate(openai_api_key=new_openai_key)
    mock_profile_service.update_profile.assert_called_once_with(
        db_session=mock_db_session, user_id=USER_ID, profile_data=expected_update_data
    )


@pytest.mark.asyncio
async def test_handle_profile_update_form_no_changes(
    mock_request, mock_db_session, mock_profile_service
):
    # Arrange - no new key provided, so openai_api_key will be None (default for Form)

    # Act
    response = await handle_profile_update_form(
        request=mock_request,
        user=MOCK_USER,
        db=mock_db_session,
        profile_service=mock_profile_service,
        openai_api_key=None,  # Explicitly pass None, as if form field was empty
    )

    # Assert
    assert isinstance(response, RedirectResponse)
    assert response.status_code == HTTP_302_FOUND
    assert "message=No+changes+submitted." in response.headers["location"]
    mock_profile_service.update_profile.assert_not_called()


@pytest.mark.asyncio
async def test_handle_profile_update_form_service_error(
    mock_request, mock_db_session, mock_profile_service
):
    # Arrange
    new_openai_key = "new_key_causing_error"
    error_message = "Simulated service error"
    mock_profile_service.update_profile.side_effect = Exception(error_message)
    # Ensure get_or_create_profile is still available for error path rendering
    mock_profile_service.get_or_create_profile = AsyncMock(
        return_value=MOCK_PROFILE_READ_SCHEMA
    )

    # Reset the mock to ensure isolation
    mock_templates.TemplateResponse.reset_mock()

    # Act
    response = await handle_profile_update_form(
        request=mock_request,
        user=MOCK_USER,
        db=mock_db_session,
        profile_service=mock_profile_service,
        openai_api_key=new_openai_key,
    )

    # Assert
    # In case of an error during update, the form should be re-rendered with an error message
    assert isinstance(
        response, HTMLResponse
    )  # Should be TemplateResponse rendering again
    assert (
        response.status_code == 400
    )  # Or the status code set in the endpoint's error handling

    # Check that TemplateResponse was called again for error display
    mock_templates.TemplateResponse.assert_called_with(
        "profile.html",
        {
            "request": mock_request,
            "user": MOCK_USER,
            "keys_status": {
                "openai_api_key_set": bool(MOCK_PROFILE_READ_SCHEMA.openai_api_key)
            },
            "provider_list": [
                p for p in MANAGED_PROVIDERS if p in ["openai", "google"]
            ],
            "message": f"Error updating API keys: {error_message}",
        },
        status_code=400,
    )
