import asyncio
import uuid
from typing import AsyncGenerator, Optional

from fastapi import Depends, HTTPException, Request, Response
from fastapi_users import BaseUserManager, FastAPIUsers, UUIDIDMixin
from fastapi_users import exceptions as fastapi_users_exceptions
from fastapi_users import schemas
from fastapi_users.authentication import (
    AuthenticationBackend,
    BearerTransport,
    JWTStrategy,
)
from fastapi_users.db import SQLAlchemyUserDatabase
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.db import base as db_base
from api_service.db.base import get_async_session
from api_service.db.models import User
from api_service.services.profile_service import ProfileService
from moonmind.config.settings import settings


class UserRead(schemas.BaseUser[uuid.UUID]):
    pass


class UserCreate(schemas.BaseUserCreate):
    pass


class UserUpdate(schemas.BaseUserUpdate):
    pass


class UserManager(UUIDIDMixin, BaseUserManager[User, uuid.UUID]):
    reset_password_token_secret = settings.security.JWT_SECRET_KEY
    verification_token_secret = settings.security.JWT_SECRET_KEY

    async def _ensure_profile(self, user: User) -> None:
        """Create a profile for the user if it doesn't exist."""
        async with db_base.async_session_maker() as session:
            service = ProfileService()
            try:
                await service.get_or_create_profile(session, user.id)
            finally:
                await session.close()

    async def on_after_register(self, user: User, request: Optional[Request] = None):
        # Create the user profile asynchronously so registration isn't delayed
        asyncio.create_task(self._ensure_profile(user))

    async def on_after_login(
        self,
        user: User,
        request: Optional[Request] = None,
        response: Optional[Response] = None,
    ) -> None:
        # Ensure the user has a profile on first login
        asyncio.create_task(self._ensure_profile(user))

    async def on_after_forgot_password(
        self, user: User, token: str, request: Optional[Request] = None
    ):
        print(f"User {user.id} has forgot their password. Reset token: {token}")

    async def on_after_request_verify(
        self, user: User, token: str, request: Optional[Request] = None
    ):
        print(f"Verification requested for user {user.id}. Verification token: {token}")


async def get_user_db(session: get_async_session = Depends(get_async_session)):
    yield SQLAlchemyUserDatabase(session, User)


async def get_user_manager(user_db: SQLAlchemyUserDatabase = Depends(get_user_db)):
    yield UserManager(user_db)


from contextlib import asynccontextmanager


@asynccontextmanager
async def get_user_manager_context(
    db_session: AsyncSession,
) -> AsyncGenerator[UserManager, None]:
    """Context manager for UserManager."""
    # Create a SQLAlchemyUserDatabase instance for the current session
    user_db = SQLAlchemyUserDatabase(db_session, User)
    # Yield a UserManager instance
    yield UserManager(user_db)
    # No specific cleanup needed for UserManager itself here, session is managed outside.


# Hardcoded default user details for disabled auth mode
_DEFAULT_USER_ID = "00000000-0000-0000-0000-000000000000"
_DEFAULT_USER_EMAIL = "default@example.com"


async def get_or_create_default_user(
    db_session: AsyncSession, user_manager: UserManager
) -> User:
    """
    Retrieves or creates the default user if AUTH_PROVIDER is "disabled".
    Falls back to built-in default ID and email unless overridden in settings.
    """
    default_user_uuid = uuid.UUID(settings.oidc.DEFAULT_USER_ID or _DEFAULT_USER_ID)
    default_email = settings.oidc.DEFAULT_USER_EMAIL or _DEFAULT_USER_EMAIL
    default_password = (
        settings.oidc.DEFAULT_USER_PASSWORD
    )  # Can be None if not set, UserManager handles it

    try:
        # Attempt to get user by ID first
        user = await user_manager.get(default_user_uuid)
        if user:
            return user
    except (
        Exception
    ):  # Catch potential errors if user_manager.get fails for non-existent user (though it usually returns None)
        pass  # User not found by ID, proceed to check by email or create

    # Attempt to get user by email if not found by ID
    # This handles cases where ID might change or not be the primary lookup for creation path
    try:
        user_by_email = await user_manager.get_by_email(default_email)
        if user_by_email:
            # If user exists by email but ID doesn't match, this is a conflict.
            # For simplicity, we'll assume if email matches, it's the intended default user.
            # Ideally, ID should be authoritative.
            if user_by_email.id != default_user_uuid:
                # Log a warning about ID mismatch if logging is available
                print(
                    f"Warning: Default user email {default_email} exists with ID {user_by_email.id}, but expected ID {default_user_uuid}."
                )
                # Potentially update the existing user's ID if that's desired and feasible,
                # or raise an error. For now, we'll return the user found by email.
                pass  # Or handle more robustly
            return user_by_email
    except Exception:
        pass  # User not found by email, proceed to create

    # If user not found by ID or email, create them
    user_create_data = {
        "email": default_email,
        "password": default_password,
        "is_active": True,
        "is_verified": True,
        # We are setting the ID directly in the User model instance before adding to DB
        # as UserCreate schema might not support 'id'.
        # This requires careful handling with user_manager or direct DB interaction.
    }

    # UserManager.create typically expects a UserCreate schema object.
    # We need to ensure the ID is set correctly.
    # One way is to create the user object directly if UserManager allows it,
    # or use a transaction with direct DB interaction for the ID part.

    # Revised approach: Create with UserManager, then update ID if necessary, or ensure ID is part of UserCreate if allowed.
    # FastAPI-Users UserCreate does not allow specifying 'id'.
    # The UserManager will generate an ID.
    # This means we cannot pre-determine the ID as settings.oidc.DEFAULT_USER_ID
    # unless we modify how UserManager works or directly interact with the DB.

    # Let's try a more direct DB interaction approach for default user setup
    # to enforce the DEFAULT_USER_ID.

    # Check if user with default_user_uuid exists (again, to be safe in direct DB context)
    user = await db_session.get(User, default_user_uuid)
    if user:
        # If user somehow got created between earlier checks and now, return them
        return user

    # Check if user with default_email exists
    try:
        existing_user_by_email = await user_manager.get_by_email(default_email)
        if existing_user_by_email and existing_user_by_email.id != default_user_uuid:
            raise ValueError(
                f"A user with email {default_email} already exists but with a different ID ({existing_user_by_email.id}) than the configured DEFAULT_USER_ID ({default_user_uuid})."
            )
        elif existing_user_by_email and existing_user_by_email.id == default_user_uuid:
            return existing_user_by_email  # Should have been caught by user_manager.get(default_user_uuid)
    except fastapi_users_exceptions.UserNotExists:
        # User doesn't exist by email, which is expected for new setup
        pass

    # Create the user directly with the specified ID
    # Note: Hashing the password should be handled by UserManager or a utility.
    # For simplicity in this step, we assume user_manager.password_helper.hash can be used.
    # If not, we might need to adjust.
    hashed_password = user_manager.password_helper.hash(default_password)

    user = User(
        id=default_user_uuid,
        email=default_email,
        hashed_password=hashed_password,
        is_active=True,
        is_superuser=False,  # Default user is not superuser unless specified
        is_verified=True,
        # oidc_provider and oidc_subject can be null or set to 'disabled_auth_default'
        oidc_provider="default",
        oidc_subject=str(default_user_uuid),
    )
    db_session.add(user)
    try:
        await db_session.commit()
        await db_session.refresh(user)
        return user
    except Exception as e:
        await db_session.rollback()
        # Log error details here
        print(f"Error creating default user: {e}")
        raise HTTPException(
            status_code=500, detail=f"Could not create default user: {e}"
        )


bearer_transport = BearerTransport(tokenUrl="auth/jwt/login")


def get_jwt_strategy() -> JWTStrategy:
    return JWTStrategy(secret=settings.security.JWT_SECRET_KEY, lifetime_seconds=3600)


auth_backend = AuthenticationBackend(
    name="jwt",
    transport=bearer_transport,
    get_strategy=get_jwt_strategy,
)

fastapi_users = FastAPIUsers[User, uuid.UUID](get_user_manager, [auth_backend])

current_active_user = fastapi_users.current_user(active=True)
