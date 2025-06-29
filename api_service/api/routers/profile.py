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
)

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
