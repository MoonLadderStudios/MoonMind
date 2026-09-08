import asyncio
import logging
import os
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.auth import (
    _DEFAULT_USER_ID,
    UserCreate,
    UserRead,
    auth_backend,
    current_active_user,
    fastapi_users,
)
from api_service.db.base import get_async_session
from api_service.db.models import User
from api_service.services.profile_service import ProfileService
from moonmind.auth import AuthProviderManager, EnvAuthProvider, ProfileAuthProvider
from moonmind.config.settings import settings

logger = logging.getLogger(__name__)

_cached_current_user_dependency = None

# MoonLadderStudios/MoonMind#4116 (Keycloak removal epic, K3/K4 boundary):
# one AUTH_PROVIDER selector. Values below are the literals accepted by the
# current codebase; the planned cutover selector is
# accounts/oidc/header/disabled. Unknown values must fail fast at startup
# with migration guidance and must never silently become disabled auth.
SUPPORTED_AUTH_PROVIDERS = frozenset({"disabled", "default", "keycloak", "google"})

# Target selector values from docs/tmp/KeycloakRemovalPlan.md §3. Accepted
# here alongside legacy literals so operators can adopt the new names before
# the full K3/K4 cutover; unknown values still fail fast.
_PLANNED_AUTH_PROVIDERS = frozenset({"accounts", "oidc", "header"})


def validate_auth_provider(provider: str | None = None) -> str:
    """Fail fast on unknown AUTH_PROVIDER values (MoonLadderStudios/MoonMind#4116).

    Returns the normalized provider. Raises RuntimeError with migration
    guidance for unknown values instead of silently disabling
    authentication.
    """
    value = provider if provider is not None else settings.oidc.AUTH_PROVIDER
    normalized = (value or "").strip().lower()
    if normalized in SUPPORTED_AUTH_PROVIDERS or normalized in _PLANNED_AUTH_PROVIDERS:
        return normalized
    raise RuntimeError(
        f"Unknown AUTH_PROVIDER '{value}'. Supported values are "
        "'disabled', 'default', 'keycloak', 'google' (legacy) and the planned "
        "'accounts', 'oidc', 'header' (see docs/tmp/KeycloakRemovalPlan.md). "
        "Refusing to start rather than disabling authentication; set an "
        "explicit supported value before deploying."
    )


def _is_test_runtime() -> bool:
    return bool(settings.workflow.test_mode or os.getenv("PYTEST_CURRENT_TEST"))


def _disabled_auth_fallback_user():
    from types import SimpleNamespace

    user_id_str = settings.oidc.DEFAULT_USER_ID or _DEFAULT_USER_ID
    return SimpleNamespace(
        id=uuid.UUID(user_id_str),
        email=settings.oidc.DEFAULT_USER_EMAIL or "stub@example.com",
        is_superuser=True,
    )


def _disabled_auth_test_user():
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
    • In normal operation with AUTH_PROVIDER == "disabled" we still try to load the
      default user from the database (to keep behaviour unchanged for the running
      API).
    • **However** when running under unit-test environments the database is often
      unavailable. In test runtimes only (workflow test_mode or PYTEST_CURRENT_TEST)
      we return a lightweight stub user object so the rest of the application code
      continues to work without a real database.
    • In real runtimes a database outage or missing default-user row fails closed
      with HTTP 503 (MoonLadderStudios/MoonMind#4116 K4): it must never resolve to
      an administrator stub.
    This removes the hard DB dependency from the vast majority of unit tests that
    don’t need it, preventing the `[Errno 111] Connect call failed ('127.0.0.1',
    5432)` failures that appeared after switching back to
    `Depends(get_current_user())` in the routers.
    """

    global _cached_current_user_dependency
    if settings.oidc.AUTH_PROVIDER != "disabled":
        # Keycloak / default auth modes – just use the fastapi-users dependency
        return current_active_user

    if _cached_current_user_dependency is None:

        async def _current_user_fallback():  # pragma: no cover – simple helper
            if _is_test_runtime():
                return _disabled_auth_test_user()
            if os.getenv("MOONMIND_DISABLE_DEFAULT_USER_DB_LOOKUP") == "1":
                return _disabled_auth_fallback_user()

            async def _load_default_user() -> User | None:
                from api_service.db.base import get_async_session_context

                user_id_str = settings.oidc.DEFAULT_USER_ID or _DEFAULT_USER_ID
                user_uuid = uuid.UUID(user_id_str)
                async with get_async_session_context() as session:
                    return await session.get(User, user_uuid)

            try:
                user_obj = await asyncio.wait_for(_load_default_user(), timeout=1.0)
            except (Exception, asyncio.TimeoutError):
                # MoonLadderStudios/MoonMind#4116 (K4): a database outage
                # must fail closed with a bounded unavailable response, never
                # resolve to an administrator stub.
                logger.warning(
                    "Default user lookup unavailable in disabled auth mode; failing closed.",
                    exc_info=True,
                )
                raise HTTPException(
                    status_code=503,
                    detail="User store unavailable; authentication refused.",
                )
            if user_obj is None:
                # Missing row is a configuration/migration problem, not a
                # permission to run unauthenticated.
                logger.warning(
                    "Default user row missing in disabled auth mode; failing closed."
                )
                raise HTTPException(
                    status_code=503,
                    detail="Default user not configured; authentication refused.",
                )
            # Disabled auth is single-user local mode; treat that principal
            # as the local administrator even if the persisted row predates
            # this policy.
            user_obj.is_superuser = True
            return user_obj

        _cached_current_user_dependency = _current_user_fallback

    return _cached_current_user_dependency

current_active_user_optional = fastapi_users.current_user(active=True, optional=True)

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
