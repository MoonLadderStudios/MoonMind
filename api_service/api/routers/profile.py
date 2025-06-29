from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.auth import current_active_user  # Dependency for authenticated user
from api_service.db.base import get_async_session  # Dependency for DB session
from api_service.db.models import User as DBUser  # User model from DB
from api_service.services.profile_service import ProfileService
from api_service.api.schemas import (
    UserProfileRead,
    UserProfileUpdate,
    UserProfileReadSanitized,
    ApiKeyStatus,
)
from fastapi.templating import Jinja2Templates
from fastapi import Request, Form # For HTMLResponse and Form
from fastapi.responses import HTMLResponse, RedirectResponse # For HTMLResponse and RedirectResponse
from starlette.status import HTTP_302_FOUND


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
    user: DBUser = Depends(current_active_user),
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
    user: DBUser = Depends(current_active_user),
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


@router.get("/me/ui", response_class=HTMLResponse, name="profile_ui")
async def get_profile_management_page(
    request: Request,
    user: DBUser = Depends(current_active_user),
    db: AsyncSession = Depends(get_async_session),
    profile_service: ProfileService = Depends(get_profile_service),
):
    """
    Renders the profile management page.
    """
    if user.id is None:
        raise HTTPException(status_code=400, detail="User ID is missing.")

    profile = await profile_service.get_or_create_profile(db_session=db, user_id=user.id)

    # UserProfileRead schema contains openai_api_key (decrypted if set, else None)
    # So, we check for its truthiness.
    keys_status = ApiKeyStatus(
        openai_api_key_set=bool(profile.openai_api_key)
        # Add other keys here, e.g., anthropic_api_key_set=bool(profile.anthropic_api_key)
    )

    message = request.query_params.get("message", None)

    return templates.TemplateResponse(
        "profile.html",
        {
            "request": request,
            "user": user,
            "keys_status": keys_status,
            "message": message
        }
    )

@router.post("/me/ui", response_class=HTMLResponse, name="update_profile_ui")
async def handle_profile_update_form(
    request: Request, # Added request parameter
    user: DBUser = Depends(current_active_user),
    db: AsyncSession = Depends(get_async_session),
    profile_service: ProfileService = Depends(get_profile_service),
    openai_api_key: str = Form(None),
    # anthropic_api_key: str = Form(None) # Example for another key
):
    """
    Handles form submission for updating API keys.
    """
    if user.id is None:
        raise HTTPException(status_code=400, detail="User ID is missing.")

    update_data = {}
    if openai_api_key: # Only include if a new key is provided
        update_data["openai_api_key"] = openai_api_key
    # if anthropic_api_key:
    #     update_data["anthropic_api_key"] = anthropic_api_key

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
            profile = await profile_service.get_or_create_profile(db_session=db, user_id=user.id)
            keys_status = ApiKeyStatus(
                openai_api_key_set=bool(profile.openai_api_key) # Check truthiness of the key in the schema
            )
            return templates.TemplateResponse(
                "profile.html",
                {"request": request, "user": user, "keys_status": keys_status, "message": message},
                status_code=400 # Or another appropriate error code
            )

    # Redirect to the profile page with a success/info message
    redirect_url = request.url_for('profile_ui').include_query_params(message=message)
    return RedirectResponse(url=str(redirect_url), status_code=HTTP_302_FOUND)
