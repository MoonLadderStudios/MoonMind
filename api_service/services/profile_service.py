import uuid
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload # For eager loading if needed in the future
from fastapi import HTTPException, status

from api_service.db.models import UserProfile, User # User model might be needed for context or future validation
from api_service.api.schemas import UserProfileSchema, UserProfileUpdateSchema, UserProfileCreateSchema

class ProfileService:
    async def get_profile_by_user_id(self, db_session: AsyncSession, user_id: uuid.UUID) -> Optional[UserProfile]:
        """
        Retrieves a user's profile by user_id.
        Returns the UserProfile model instance or None if not found.
        """
        result = await db_session.execute(
            select(UserProfile).where(UserProfile.user_id == user_id)
        )
        return result.scalars().first()

    async def get_or_create_profile(self, db_session: AsyncSession, user_id: uuid.UUID) -> UserProfileSchema:
        """
        Retrieves a user's profile by user_id, or creates a new one if it doesn't exist.
        The `user_id` is expected to be validated and exist in the `User` table upstream.
        """
        profile = await self.get_profile_by_user_id(db_session, user_id)

        if not profile:
            # Ensure the user actually exists before creating a profile for them.
            # This check might be optional if user_id validity is guaranteed by the caller.
            user_exists = await db_session.get(User, user_id)
            if not user_exists:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"User with id {user_id} not found. Cannot create profile."
                )

            # Create a new profile with default (empty) values initially
            # UserProfileCreateSchema can be used here if specific creation defaults are desired
            # For now, directly creating UserProfile model instance
            new_profile_data = UserProfileCreateSchema() # Provides default values if any

            profile = UserProfile(
                user_id=user_id,
                # Set fields from new_profile_data if they exist and are not None
                # Example: profile.some_field = new_profile_data.some_field
                # For google_api_key, it's optional and defaults to None in the schema
                google_api_key_encrypted=new_profile_data.google_api_key # Will be None if not provided
            )
            db_session.add(profile)
            try:
                await db_session.commit()
                await db_session.refresh(profile)
            except Exception as e:
                import logging
                logging.error("Failed to commit transaction while creating profile", exc_info=True)
                await db_session.rollback()
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"Failed to create profile: {str(e)}"
                )

        return UserProfileSchema.from_orm(profile)

    async def update_profile(self, db_session: AsyncSession, user_id: uuid.UUID, profile_data: UserProfileUpdateSchema) -> UserProfileSchema:
        """
        Updates an existing user's profile.
        If the profile doesn't exist, it will first be created.
        """
        profile = await self.get_profile_by_user_id(db_session, user_id)

        if not profile:
            # Option 1: Raise an error if profile must exist
            # raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Profile not found to update.")
            # Option 2: Call get_or_create to ensure it exists (as per Story's implication for update)
            # However, get_or_create returns a schema, we need the model instance here.
            # Let's ensure it's created first, then proceed.
            # This might lead to creating an empty profile and then updating it.
            # A more direct approach might be to fetch, if not found, create with new data.
            # For now, let's try to fetch, and if not found, create with the update data.

            user_exists = await db_session.get(User, user_id)
            if not user_exists:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"User with id {user_id} not found. Cannot create or update profile."
                )

            profile = UserProfile(user_id=user_id)
            # Apply update data to the new profile
            update_data = profile_data.dict(exclude_unset=True)
            for key, value in update_data.items():
                if key == "google_api_key": # Schema field name
                    setattr(profile, "google_api_key_encrypted", value) # Model field name
                elif hasattr(profile, key):
                    setattr(profile, key, value)

            db_session.add(profile)
            # No commit yet, will commit after applying updates or if it was just created.

        else: # Profile exists, update it
            update_data = profile_data.dict(exclude_unset=True) # Get only provided fields
            if not update_data:
                 # If no data to update, just return the current profile
                return UserProfileSchema.from_orm(profile)

            for key, value in update_data.items():
                if key == "google_api_key": # Schema field name for input
                    # The model field is 'google_api_key_encrypted'.
                    # Assigning to it will trigger encryption via EncryptedType.
                    setattr(profile, "google_api_key_encrypted", value)
                elif hasattr(profile, key): # For other potential direct mapped fields
                    setattr(profile, key, value)
                # else:
                #     logger.warning(f"Field {key} in profile_data not found on UserProfile model or not handled.")

        try:
            await db_session.commit() # Commit changes (either new profile or updates)
            await db_session.refresh(profile)
        except Exception as e:
            await db_session.rollback()
            # Log error e
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to update profile: {str(e)}"
            )

        return UserProfileSchema.from_orm(profile)

# Optional: A function to get the service instance, useful for dependency injection
# async def get_profile_service() -> ProfileService:
# return ProfileService()
# This would typically be part of the API dependencies if used with FastAPI's Depends
