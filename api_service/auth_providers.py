import asyncio
import logging
import os
import uuid

from fastapi import Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.auth import (
    _DEFAULT_USER_ID,
    current_active_user,
    current_active_user_optional,
)
from api_service.db.base import get_async_session
from api_service.db.models import User
from api_service.services.profile_service import ProfileService
from moonmind.auth import AuthProviderManager, EnvAuthProvider, ProfileAuthProvider
from moonmind.config.settings import settings

logger = logging.getLogger(__name__)

_cached_current_user_dependency = None


def _disabled_auth_test_user():
    """Explicit test-only principal (never the production path).

    Used only when ``settings.workflow.test_mode`` or ``PYTEST_CURRENT_TEST``
    marks an explicit test override. Production database failures raise
    ``503`` instead of synthesizing this stub.
    """
    from types import SimpleNamespace

    return SimpleNamespace(id=None, email="stub@example.com", is_superuser=True)


async def get_default_user_from_db(
    session: AsyncSession = Depends(get_async_session),
) -> User:
    """Retrieve the default user from the database."""
    user_id_str = settings.oidc.DEFAULT_USER_ID or _DEFAULT_USER_ID
    try:
        user_uuid = uuid.UUID(user_id_str)
    except ValueError as exc:
        raise HTTPException(status_code=500, detail="Invalid DEFAULT_USER_ID") from exc

    user = await session.get(User, user_uuid)
    if user is None:
        raise HTTPException(status_code=500, detail="Default user not found")
    return user

def get_current_user():
    """Return a dependency that yields the current user.

    Behaviour:
    • In normal operation with AUTH_PROVIDER == "disabled" we load the
      default user from the database (explicit local single-user mode).
    • Missing identity data or database unavailability fails closed with
      ``503`` (``unavailable``) on the production path. It never synthesizes
      an administrator stub (MoonLadderStudios/MoonMind#4120 R5): a missing
      credential may be optional only at explicitly optional boundaries, and
      test-only principals belong in explicit test dependency overrides
      (``settings.workflow.test_mode``), never in production fallbacks.
    """

    global _cached_current_user_dependency
    if settings.oidc.AUTH_PROVIDER != "disabled":
        # Authenticated modes share the current bearer validation until the
        # #4124-era session contracts replace it; retired selectors fail at
        # startup via OIDCSettings.validate_auth_provider, never here.
        return current_active_user

    if _cached_current_user_dependency is None:

        async def _current_user_fallback():
            if settings.workflow.test_mode or os.getenv("PYTEST_CURRENT_TEST"):
                # Explicit test-only override: unit-test environments without
                # a database use a stub principal instead of requiring DB.
                # Production (test_mode False, no PYTEST_CURRENT_TEST) never
                # takes this path.
                return _disabled_auth_test_user()

            async def _load_default_user() -> User | None:
                from api_service.db.base import get_async_session_context

                user_id_str = settings.oidc.DEFAULT_USER_ID or _DEFAULT_USER_ID
                user_uuid = uuid.UUID(user_id_str)
                async with get_async_session_context() as session:
                    return await session.get(User, user_uuid)

            try:
                user_obj = await asyncio.wait_for(_load_default_user(), timeout=1.0)
                if user_obj is not None:
                    # Disabled auth is single-user local mode; treat that principal
                    # as the local administrator even if the persisted row predates
                    # this policy.
                    user_obj.is_superuser = True
                    return user_obj
            except (Exception, asyncio.TimeoutError):
                logger.warning(
                    "Failed to load default user in disabled auth mode; failing closed.",
                    exc_info=True,
                )
                raise HTTPException(
                    status_code=503, detail="Identity store unavailable"
                )

            # No synthetic admin fallback: missing identity data fails closed.
            raise HTTPException(status_code=503, detail="Default user not found")

        _cached_current_user_dependency = _current_user_fallback

    return _cached_current_user_dependency

def get_current_user_optional():
    """Return an auth dependency that tolerates missing bearer credentials.

    Worker-token authenticated endpoints use this helper so header-only workers
    are not blocked by FastAPI resolving a strict bearer-auth dependency first.
    """

    if settings.oidc.AUTH_PROVIDER != "disabled":
        return current_active_user_optional
    return get_current_user()

async def get_auth_manager(
    db: AsyncSession = Depends(get_async_session),
) -> AuthProviderManager:
    """Return an AuthProviderManager wired to the given DB session."""
    profile_provider = ProfileAuthProvider(db, ProfileService())
    env_provider = EnvAuthProvider()
    return AuthProviderManager(profile_provider, env_provider)

