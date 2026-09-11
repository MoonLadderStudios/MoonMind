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
from moonmind.security.auth_modes_4120 import (
    get_request_production_mode,
    is_disabled_local_mode,
    resolve_moonmind_auth_config,
)

logger = logging.getLogger(__name__)

_cached_current_user_dependency = None


def _disabled_auth_test_user():
    # Test-only principal. Production never mints administrator stubs:
    # missing identity/DB data fails closed with 503 (see _current_user_fallback).
    # Tests must use explicit dependency overrides for authenticated paths.
    from types import SimpleNamespace

    return SimpleNamespace(id=None, email="stub@example.com", is_superuser=True)


def build_moonmind_control_plane_config(environ=None, mode=None):
    """Resolve the MoonMind control-plane auth config explicitly (#4120 req 2).

    Only MoonMind-owned inputs (AUTH_PROVIDER mode, MOONMIND_* cookie/key
    material) are consumed. ``OMNIGENT_AUTH_*`` ambient values -- even
    contradictory ones -- cannot select MoonMind behavior; simultaneous
    same-origin use stays isolated through distinct cookies/keys/purposes.
    Callers must pass the durable session secret explicitly; this helper
    never reads runtime-server secrets.

    ``mode`` accepts the startup-classified production mode so the
    omitted-fresh path (blank storage, classified ``accounts``) resolves
    through the same production code as explicit ``accounts``. When omitted,
    the startup-classified mode is read via the auth-modes owner and blank
    storage fails closed with 503 (startup owns the fresh-vs-upgrade
    decision).

    Production wiring: ``api_service.main._initialize_oidc_provider`` calls
    :func:`resolve_moonmind_auth_config` directly with the classified
    production mode at startup, so this helper is the request-time
    counterpart of the same production boundary, not dead code.
    """
    from moonmind.security.auth_modes_4120 import default_session_key_path

    if mode is not None:
        from moonmind.security.omnigent_auth_qualification import (
            validate_mode_selector,
        )

        effective = validate_mode_selector(str(mode).strip().lower())
    else:
        effective = get_request_production_mode()
    if not effective:
        # Omitted selector at request time: the startup classifier owns the
        # fresh-vs-upgrade decision. Request code fails closed rather than
        # guessing a mode.
        raise HTTPException(status_code=503, detail="auth_undecided")
    mode = effective
    secret_env = os.environ.get("MOONMIND_SESSION_SECRET", "").strip()
    cookie_secret: bytes
    if secret_env:
        from moonmind.security.auth_modes_4120 import resolve_session_secret

        cookie_secret = resolve_session_secret(explicit_secret=secret_env)
    else:
        # Durable deployment-owned key; never a per-process placeholder.
        from moonmind.security.auth_modes_4120 import resolve_session_secret

        cookie_secret = resolve_session_secret(
            explicit_secret=None,
            key_path=default_session_key_path(),
            allow_generate=False,
            for_remote_production=False,
        )
    runtime_environ = dict(environ) if environ is not None else dict(os.environ)
    return resolve_moonmind_auth_config(
        mode=mode,
        cookie_secret=cookie_secret,
        require_secure_cookies=os.environ.get("MOONMIND_REQUIRE_SECURE_COOKIES", "1")
        != "0",
        environ=runtime_environ,
    )


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

async def _validate_oidc_cookie_user(request, session) -> User:
    """Validate the MoonMind OIDC session cookie through shared authority.

    Used by every protected endpoint in ``oidc`` mode so the browser cookie
    minted by ``/api/v1/oidc/callback`` authenticates workflows, settings,
    secrets, and other routes — not only the login router.
    """
    control_plane = build_moonmind_control_plane_config(mode="oidc")
    token = request.cookies.get(control_plane.cookie_name) or (
        (request.headers.get("authorization", "") or "").removeprefix("Bearer ").strip()
        or None
    )
    if not token:
        raise HTTPException(status_code=401, detail="auth_required")
    from api_service.services.session_store import (
        DbAccountStore,
        DbRevocationStore,
    )
    from moonmind.security import omnigent_auth_qualification as _q

    account_store = DbAccountStore(session)
    revocation = DbRevocationStore(session)
    try:
        account = await _q.validate_moonmind_session(
            token, account_store, revocation, control_plane
        )
    except HTTPException:
        raise
    except Exception as exc:
        # Distinguish infrastructure outage (503) from bad credentials (401).
        from moonmind.security.omnigent_auth_qualification import UnavailableError

        if isinstance(exc, UnavailableError):
            raise HTTPException(status_code=503, detail="unavailable") from exc
        raise HTTPException(status_code=401, detail="auth_invalid") from exc
    user = await session.get(User, account.user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="auth_invalid")
    return user


def get_current_user():
    """Return a dependency that yields the current user.

    Behaviour:
    • In normal operation with AUTH_PROVIDER == "disabled" we still try to load the
      default user from the database (to keep behaviour unchanged for the running
      API).
    • **However** when running under unit-test environments the database is often
      unavailable.  If we cannot reach it (e.g. connection refused) we gracefully
      fall back to returning a lightweight stub user object so the rest of the
      application code continues to work without a real database.
    This removes the hard DB dependency from the vast majority of unit tests that
    don’t need it, preventing the `[Errno 111] Connect call failed ('127.0.0.1',
    5432)` failures that appeared after switching back to
    `Depends(get_current_user())` in the routers.
    """

    global _cached_current_user_dependency
    from moonmind.security.auth_modes_4120 import get_request_production_mode

    _mode = get_request_production_mode()
    if _mode == "oidc":
        # OIDC mode authenticates the MoonMind session cookie minted by the
        # #4124 callback through the shared #4119/#4121 authority, so normal
        # API routes honor the same principal as the login flow instead of
        # requiring a legacy bearer token.
        from fastapi import Depends as _Depends
        from fastapi import Request as _Req
        from api_service.db.base import get_async_session as _GetAsyncSession

        async def _oidc_dependency(
            request: _Req, session=_Depends(_GetAsyncSession)
        ):
            return await _validate_oidc_cookie_user(request, session)

        return _oidc_dependency
    if _mode == "header":
        # Trusted-header mode authenticates every protected endpoint through
        # the same explicitly trusted ingress + #4119 mapping as the
        # diagnostic route, never only that route.
        from fastapi import Request as _Req2

        async def _header_dependency(request: _Req2):
            from api_service.api.routers.advanced_auth_4124 import (
                get_trusted_proxy_user as _proxy_user,
            )

            return await _proxy_user(request)

        return _header_dependency
    if _mode != "disabled":
        # Authenticated modes share the current bearer validation until the
        # #4124-era session contracts replace it; retired selectors fail at
        # startup via the auth-modes owner, never here.
        return current_active_user

    if _cached_current_user_dependency is None:

        async def _current_user_fallback():
            # Explicit test double only: unit tests without a database opt in
            # via test_mode/PYTEST_CURRENT_TEST. Production (and any
            # non-test caller) fails closed below -- missing identity/DB data
            # in local mode never mints a synthetic administrator.
            if settings.workflow.test_mode or os.getenv("PYTEST_CURRENT_TEST"):
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
                    "Identity store unavailable in disabled auth mode; failing closed.",
                    exc_info=True,
                )
                raise HTTPException(status_code=503, detail="unavailable")

            # No synthetic admin fallback: a missing default row in local mode
            # is a protected-setup signal, not an implicit grant.
            raise HTTPException(status_code=503, detail="setup_required")

        _cached_current_user_dependency = _current_user_fallback

    return _cached_current_user_dependency


def get_current_user_optional():
    """Return an auth dependency that tolerates missing bearer credentials.

    Worker-token authenticated endpoints use this helper so header-only workers
    are not blocked by FastAPI resolving a strict bearer-auth dependency first.
    """

    if not is_disabled_local_mode():
        return current_active_user_optional
    return get_current_user()

async def get_auth_manager(
    db: AsyncSession = Depends(get_async_session),
) -> AuthProviderManager:
    """Return an AuthProviderManager wired to the given DB session."""
    profile_provider = ProfileAuthProvider(db, ProfileService())
    env_provider = EnvAuthProvider()
    return AuthProviderManager(profile_provider, env_provider)

