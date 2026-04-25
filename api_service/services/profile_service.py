import logging
import uuid
from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy import and_, func, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from api_service.api.schemas import (
    UserProfileCreateSchema,
    UserProfileRead,
    UserProfileReadSanitized,
    UserProfileUpdate,
)
from api_service.db.models import User, UserProfile

logger = logging.getLogger(__name__)


def _key_is_set(column):
    return and_(column.is_not(None), func.length(column) > 0)


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

    async def _get_profile_identity_by_user_id(
        self, db_session: AsyncSession, user_id: uuid.UUID
    ) -> dict | None:
        """Return profile identity columns without loading encrypted secret fields."""
        result = await db_session.execute(
            select(
                UserProfile.id.label("id"),
                UserProfile.user_id.label("user_id"),
            ).where(UserProfile.user_id == user_id)
        )
        row = result.mappings().first()
        return dict(row) if row is not None else None

    async def get_sanitized_profile_by_user_id(
        self, db_session: AsyncSession, user_id: uuid.UUID
    ) -> UserProfileReadSanitized | None:
        """Return profile metadata without decrypting stored secret columns."""
        result = await db_session.execute(
            select(
                UserProfile.id.label("id"),
                UserProfile.user_id.label("user_id"),
                _key_is_set(UserProfile.google_api_key_encrypted).label(
                    "google_api_key_set",
                ),
                _key_is_set(UserProfile.openai_api_key_encrypted).label(
                    "openai_api_key_set",
                ),
                _key_is_set(UserProfile.anthropic_api_key_encrypted).label(
                    "anthropic_api_key_set",
                ),
            ).where(UserProfile.user_id == user_id)
        )
        row = result.mappings().first()
        if row is None:
            return None
        return UserProfileReadSanitized(**row)

    async def _ensure_user_exists(
        self, db_session: AsyncSession, user_id: uuid.UUID
    ) -> None:
        user_exists = await db_session.get(User, user_id)
        if not user_exists:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"User with id {user_id} not found. Cannot create profile.",
            )

    def _new_profile(self, user_id: uuid.UUID) -> UserProfile:
        profile = UserProfile(user_id=user_id)
        new_profile_data = UserProfileCreateSchema()
        for key_in_schema, value in new_profile_data.model_dump(
            exclude_unset=False, by_alias=True
        ).items():
            if hasattr(profile, key_in_schema):
                setattr(profile, key_in_schema, value)
        return profile

    async def _commit_profile_creation(
        self, db_session: AsyncSession, user_id: uuid.UUID
    ) -> UserProfileReadSanitized:
        try:
            await db_session.commit()
        except Exception:
            logger.error(
                "Failed to commit transaction while creating profile", exc_info=True
            )
            await db_session.rollback()
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to create profile.",
            )

        profile = await self.get_sanitized_profile_by_user_id(db_session, user_id)
        if profile is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to create profile.",
            )
        return profile

    async def get_or_create_sanitized_profile(
        self, db_session: AsyncSession, user_id: uuid.UUID
    ) -> UserProfileReadSanitized:
        """
        Retrieves a user's profile metadata, or creates an empty profile if missing.

        This path intentionally avoids loading encrypted secret columns because the
        Settings profile panel only needs stable identity metadata and key-set flags.
        """
        profile = await self.get_sanitized_profile_by_user_id(db_session, user_id)
        if profile is not None:
            return profile

        await self._ensure_user_exists(db_session, user_id)
        new_profile = self._new_profile(user_id)
        db_session.add(new_profile)
        return await self._commit_profile_creation(db_session, user_id)

    async def get_or_create_profile(
        self, db_session: AsyncSession, user_id: uuid.UUID
    ) -> UserProfileRead:
        """
        Retrieves a user's profile by user_id, or creates a new one if it doesn't exist.
        The `user_id` is expected to be validated and exist in the `User`
        table upstream.
        """
        profile = await self.get_profile_by_user_id(db_session, user_id)

        if not profile:
            await self._ensure_user_exists(db_session, user_id)
            profile = self._new_profile(user_id)

            db_session.add(profile)
            try:
                await db_session.commit()
                await db_session.refresh(profile)
            except Exception:
                logger.error(
                    "Failed to commit transaction while creating profile", exc_info=True
                )
                await db_session.rollback()
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Failed to create profile.",
                )

        return UserProfileRead.model_validate(profile)  # Use UserProfileRead

    def _apply_update_data_to_profile(self, profile: UserProfile, update_data: dict):
        for key_in_schema, value in update_data.items():
            if hasattr(profile, key_in_schema):
                setattr(profile, key_in_schema, value)

    async def update_profile(
        self,
        db_session: AsyncSession,
        user_id: uuid.UUID,
        profile_data: UserProfileUpdate,
    ) -> UserProfileReadSanitized:
        """
        Updates an existing user's profile.
        If the profile doesn't exist, it will first be created.
        """
        profile = await self._get_profile_identity_by_user_id(db_session, user_id)
        update_data = profile_data.model_dump(exclude_unset=True, by_alias=True)

        if not profile:
            try:
                await self._ensure_user_exists(db_session, user_id)
            except HTTPException as exc:
                if exc.status_code == status.HTTP_404_NOT_FOUND:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail=(
                            f"User with id {user_id} not found. Cannot create or "
                            "update profile."
                        ),
                    )
                raise

            profile = self._new_profile(user_id)
            self._apply_update_data_to_profile(profile, update_data)
            db_session.add(profile)

        else:  # Profile exists, update it
            if not update_data:
                profile = await self.get_sanitized_profile_by_user_id(
                    db_session, user_id
                )
                if profile is None:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail=f"Profile for user with id {user_id} not found.",
                    )
                return profile

            await db_session.execute(
                update(UserProfile)
                .where(UserProfile.user_id == user_id)
                .values(**update_data)
            )

        try:
            await db_session.commit()
        except Exception:
            logger.error(
                "Failed to commit transaction while updating profile", exc_info=True
            )
            await db_session.rollback()
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to update profile.",
            )

        profile = await self.get_sanitized_profile_by_user_id(db_session, user_id)
        if profile is None:
            if update_data:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Failed to update profile.",
                )
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Profile for user with id {user_id} not found.",
            )
        return profile

# Optional: A function to get the service instance, useful for dependency injection
# async def get_profile_service() -> ProfileService:
# return ProfileService()
# This would typically be part of the API dependencies if used with FastAPI's Depends
