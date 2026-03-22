from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.api.schemas import (
    UserProfileReadSanitized,
    UserProfileUpdate,
)
from api_service.auth_providers import get_current_user
from api_service.db.base import get_async_session  # Dependency for DB session
from api_service.db.models import User as DBUser  # User model from DB
from api_service.services.profile_service import ProfileService


router = APIRouter()


# Dependency to get ProfileService instance
async def get_profile_service() -> ProfileService:
    return ProfileService()


def _build_sanitized_response(profile) -> UserProfileReadSanitized:
    """Build a sanitized response with key-set boolean flags."""
    return UserProfileReadSanitized(
        id=profile.id,
        user_id=profile.user_id,
        google_api_key_set=bool(getattr(profile, "google_api_key", None)),
        openai_api_key_set=bool(getattr(profile, "openai_api_key", None)),
    )


@router.get("/me", response_model=UserProfileReadSanitized)
async def get_current_user_profile(
    user: DBUser = Depends(get_current_user()),
    db: AsyncSession = Depends(get_async_session),
    profile_service: ProfileService = Depends(get_profile_service),
):
    """
    Retrieves the profile of the current authenticated user.
    If a profile does not exist, it will be created.
    Returns a sanitized response with key-set flags (no actual key values).
    """
    if user.id is None:
        raise HTTPException(status_code=400, detail="User ID is missing.")
    profile = await profile_service.get_or_create_profile(
        db_session=db, user_id=user.id
    )
    return _build_sanitized_response(profile)


@router.put("/me", response_model=UserProfileReadSanitized)
async def update_current_user_profile(
    profile_update_data: UserProfileUpdate,
    user: DBUser = Depends(get_current_user()),
    db: AsyncSession = Depends(get_async_session),
    profile_service: ProfileService = Depends(get_profile_service),
):
    """
    Updates the profile of the current authenticated user.
    Returns a sanitized response with key-set flags (no actual key values).
    """
    if user.id is None:
        raise HTTPException(status_code=400, detail="User ID is missing.")
    updated_profile = await profile_service.update_profile(
        db_session=db, user_id=user.id, profile_data=profile_update_data
    )
    return _build_sanitized_response(updated_profile)
