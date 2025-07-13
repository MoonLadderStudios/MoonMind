from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import (  # For HTMLResponse and RedirectResponse
    HTMLResponse,
    RedirectResponse,
)
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.status import HTTP_302_FOUND

from api_service.api.constants import MANAGED_PROVIDERS
from api_service.api.schemas import (
    UserProfileRead,
    UserProfileReadSanitized,
    UserProfileUpdate,
)
from api_service.auth_providers import get_current_user
from api_service.db.base import get_async_session  # Dependency for DB session
from api_service.db.models import User as DBUser  # User model from DB
from api_service.services.profile_service import ProfileService

# This assumes your templates are in a directory named "templates" at the root of `api_service`
# Adjust the path if your structure is different.
TEMPLATES_DIR = "api_service/templates"
templates = Jinja2Templates(directory=TEMPLATES_DIR)


router = APIRouter()


# Dependency to get ProfileService instance
# This could also be a simple function if ProfileService doesn't have dependencies itself
async def get_profile_service() -> ProfileService:
    return ProfileService()


@router.get("/me", response_model=UserProfileReadSanitized)
async def get_current_user_profile(
    user: DBUser = Depends(get_current_user()),  # Updated dependency
    db: AsyncSession = Depends(get_async_session),
    profile_service: ProfileService = Depends(get_profile_service),
):
    """
    Retrieves the profile of the current authenticated user.
    If a profile does not exist, it will be created.
    """
    if user.id is None:  # Should not happen if current_active_user works correctly
        raise HTTPException(status_code=400, detail="User ID is missing.")
    profile = await profile_service.get_or_create_profile(
        db_session=db, user_id=user.id
    )
    return profile


@router.put("/me", response_model=UserProfileRead)
async def update_current_user_profile(
    profile_update_data: UserProfileUpdate,
    user: DBUser = Depends(get_current_user()),  # Updated dependency
    db: AsyncSession = Depends(get_async_session),
    profile_service: ProfileService = Depends(get_profile_service),
):
    """
    Updates the profile of the current authenticated user.
    """
    if user.id is None:  # Should not happen
        raise HTTPException(status_code=400, detail="User ID is missing.")
    updated_profile = await profile_service.update_profile(
        db_session=db, user_id=user.id, profile_data=profile_update_data
    )
    return updated_profile


@router.get("/settings", response_class=HTMLResponse, name="settings_ui")
async def get_profile_management_page(
    request: Request,
    user: DBUser = Depends(get_current_user()),  # Changed dependency
    db: AsyncSession = Depends(get_async_session),
    profile_service: ProfileService = Depends(get_profile_service),
):
    """
    Renders the profile management page.
    """
    if user.id is None:
        raise HTTPException(status_code=400, detail="User ID is missing.")

    profile = await profile_service.get_or_create_profile(
        db_session=db, user_id=user.id
    )

    keys_status = {
        f"{provider}_api_key_set": bool(getattr(profile, f"{provider}_api_key", None))
        for provider in MANAGED_PROVIDERS
        if hasattr(profile, f"{provider}_api_key")
    }

    message = request.query_params.get("message", None)

    return templates.TemplateResponse(
        "profile.html",
        {
            "request": request,
            "user": user,
            "keys_status": keys_status,
            "provider_list": [
                p for p in MANAGED_PROVIDERS if hasattr(profile, f"{p}_api_key")
            ],
            "message": message,
        },
    )


@router.post("/settings", response_class=HTMLResponse, name="update_settings_ui")
async def handle_profile_update_form(
    request: Request,  # Added request parameter
    user: DBUser = Depends(get_current_user()),  # Changed dependency
    db: AsyncSession = Depends(get_async_session),
    profile_service: ProfileService = Depends(get_profile_service),
):
    """
    Handles form submission for updating API keys.
    """
    if user.id is None:
        raise HTTPException(status_code=400, detail="User ID is missing.")

    form_data = await request.form()
    update_data = {
        f"{provider}_api_key": form_data.get(f"{provider}_api_key")
        for provider in MANAGED_PROVIDERS
        if form_data.get(f"{provider}_api_key")
        and f"{provider}_api_key" in UserProfileUpdate.model_fields
    }

    message = "No changes submitted."
    if update_data:
        try:
            profile_update = UserProfileUpdate(**update_data)
            await profile_service.update_profile(
                db_session=db, user_id=user.id, profile_data=profile_update
            )
            message = "API keys updated successfully."
        except Exception as e:
            # Log the error e
            message = f"Error updating API keys: {e}"
            # It might be better to redirect to the GET page with an error message
            # For now, returning the form again with an error message
            profile = await profile_service.get_or_create_profile(
                db_session=db, user_id=user.id
            )
            keys_status = {
                f"{provider}_api_key_set": bool(
                    getattr(profile, f"{provider}_api_key", None)
                )
                for provider in MANAGED_PROVIDERS
                if hasattr(profile, f"{provider}_api_key")
            }
            return templates.TemplateResponse(
                "profile.html",
                {
                    "request": request,
                    "user": user,
                    "keys_status": keys_status,
                    "provider_list": [
                        p for p in MANAGED_PROVIDERS if hasattr(profile, f"{p}_api_key")
                    ],
                    "message": message,
                },
                status_code=400,  # Or another appropriate error code
            )

    # Redirect to the profile page with a success/info message
    redirect_url = request.url_for("settings_ui").include_query_params(message=message)
    return RedirectResponse(url=str(redirect_url), status_code=HTTP_302_FOUND)
