from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
import uuid

from api_service.auth import (
    UserCreate,
    UserRead,
    auth_backend,
    current_active_user,
    fastapi_users,
)
from api_service.db.models import User
from api_service.db.base import get_async_session
from moonmind.config.settings import settings


async def get_default_user_from_db(
    session: AsyncSession = Depends(get_async_session),
) -> User:
    """Retrieve the default user from the database."""
    user_id_str = settings.oidc.DEFAULT_USER_ID
    if not user_id_str:
        raise HTTPException(status_code=500, detail="DEFAULT_USER_ID not configured")

    try:
        user_uuid = uuid.UUID(user_id_str)
    except ValueError as exc:
        raise HTTPException(status_code=500, detail="Invalid DEFAULT_USER_ID") from exc

    user = await session.get(User, user_uuid)
    if user is None:
        raise HTTPException(status_code=500, detail="Default user not found")
    return user


def get_current_user():
    if settings.oidc.AUTH_PROVIDER == "disabled":
        return get_default_user_from_db
    else:
        return current_active_user


def get_auth_router():
    router = APIRouter()
    if settings.oidc.AUTH_PROVIDER == "keycloak":
        # Keycloak routes would be included here
        pass
    elif settings.oidc.AUTH_PROVIDER == "default":
        router.include_router(
            fastapi_users.get_auth_router(auth_backend),
            prefix="/auth/jwt",
            tags=["auth"],
        )
        router.include_router(
            fastapi_users.get_register_router(UserRead, UserCreate),
            prefix="/auth",
            tags=["auth"],
        )
    return router
