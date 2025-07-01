from fastapi import APIRouter, Depends

from api_service.auth import (UserCreate, UserRead, auth_backend,
                              current_active_user, fastapi_users)
from api_service.db.models import User
from moonmind.config.settings import settings


class MockUser:

    def __init__(self, id, email, is_active=True, is_superuser=False, is_verified=True):
        self.id = id
        self.email = email
        self.is_active = is_active
        self.is_superuser = is_superuser
        self.is_verified = is_verified

    def __call__(self):
        return self


def get_current_user():
    if settings.oidc.AUTH_PROVIDER == "disabled":
        # When auth is disabled, we return a mock user object.
        # This allows endpoints to function without requiring a real user session.
        # The default user is created in the database if it doesn't exist.
        return MockUser(
            id="00000000-0000-0000-0000-000000000000", email="default@example.com")
    else:
        return Depends(current_active_user)


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
