import uuid
from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

# Updated import to use UserProfileRead and UserProfileUpdate
from api_service.api.schemas import (
    UserProfileCreateSchema,
    UserProfileRead,
    UserProfileUpdate,
)
from api_service.db.models import (  # User model might be needed for context or future validation
    User,
    UserProfile,
)


class ProfileService:
    async def get_profile_by_user_id(
        self, db_session: AsyncSession, user_id: uuid.UUID
    ) -> Optional[UserProfile]:
        """
        Retrieves a user's profile by user_id.
        Returns the UserProfile model instance or None if not found.
        """
        result = await db_session.execute(
            select(UserProfile).where(UserProfile.user_id == user_id)
        )
        return result.scalars().first()

    async def get_or_create_profile(
        self, db_session: AsyncSession, user_id: uuid.UUID
    ) -> UserProfileRead:
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
                    detail=f"User with id {user_id} not found. Cannot create profile.",
                )

            # Create a new profile with default (empty) values initially
            # UserProfileCreateSchema can be used here if specific creation defaults are desired
            # For now, directly creating UserProfile model instance
            new_profile_data = (
                UserProfileCreateSchema()
            )  # Provides default values if any (e.g. None for keys)

            profile = UserProfile(user_id=user_id)
            # Initialize all keys from schema defaults (which should be None)
            # This ensures that if new keys are added to the schema and model,
            # they are initialized here during profile creation.
            for key_in_schema in new_profile_data.dict(exclude_unset=False):
                if key_in_schema.endswith("_api_key"):
                    model_field_name = f"{key_in_schema}_encrypted"
                    if hasattr(profile, model_field_name):
                        setattr(
                            profile,
                            model_field_name,
                            getattr(new_profile_data, key_in_schema),
                        )
                    # else: log warning or handle mismatch if necessary

            db_session.add(profile)
            try:
                await db_session.commit()
                await db_session.refresh(profile)
            except Exception as e:
                import logging

                logging.error(
                    "Failed to commit transaction while creating profile", exc_info=True
                )
                await db_session.rollback()
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"Failed to create profile: {str(e)}",
                )

        return UserProfileRead.model_validate(profile)  # Use UserProfileRead

    def _apply_update_data_to_profile(self, profile: UserProfile, update_data: dict):
        for key_in_schema, value in update_data.items():
            if key_in_schema.endswith(
                "_api_key"
            ):  # Handles google_api_key, openai_api_key, etc.
                model_field_name = f"{key_in_schema}_encrypted"
                # Assigning to this EncryptedType field handles encryption
                if hasattr(profile, model_field_name):
                    setattr(profile, model_field_name, value)
                # else: log warning or handle mismatch if necessary
            elif hasattr(
                profile, key_in_schema
            ):  # For other potential direct mapped fields
                setattr(profile, key_in_schema, value)
            # else:
            #     logger.warning(f"Field {key_in_schema} in profile_data not found on UserProfile model or not handled.")

    async def update_profile(
        self,
        db_session: AsyncSession,
        user_id: uuid.UUID,
        profile_data: UserProfileUpdate,
    ) -> UserProfileRead:  # Use UserProfileUpdate and UserProfileRead
        """
        Updates an existing user's profile.
        If the profile doesn't exist, it will first be created.
        """
        profile = await self.get_profile_by_user_id(db_session, user_id)
        update_data = profile_data.dict(exclude_unset=True)

        # Safety check: `get_profile_by_user_id` should return a profile for the
        # provided user_id. This guard protects against potential data
        # inconsistencies where a profile might be associated with a different
        # user.
        if profile and profile.user_id != user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Cannot update another user's profile.",
            )

        if not profile:
            user_exists = await db_session.get(User, user_id)
            if not user_exists:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"User with id {user_id} not found. Cannot create or update profile.",
                )

            profile = UserProfile(user_id=user_id)
            self._apply_update_data_to_profile(profile, update_data)
            db_session.add(profile)

        else:  # Profile exists, update it
            if not update_data:
                # If no data to update, just return the current profile
                return UserProfileRead.model_validate(profile)  # Use UserProfileRead

            self._apply_update_data_to_profile(profile, update_data)

        try:
            await db_session.commit()  # Commit changes (either new profile or updates)
            await db_session.refresh(profile)
        except Exception as e:
            await db_session.rollback()
            # Log error e
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to update profile: {str(e)}",
            )

        return UserProfileRead.model_validate(profile)  # Use UserProfileRead


# Optional: A function to get the service instance, useful for dependency injection
# async def get_profile_service() -> ProfileService:
# return ProfileService()
# This would typically be part of the API dependencies if used with FastAPI's Depends
