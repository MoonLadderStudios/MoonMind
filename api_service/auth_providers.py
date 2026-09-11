import asyncio
import logging
import os
import uuid

from fastapi import Depends, HTTPException, Request
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

async def _resolve_advanced_user(
    request: Request,
    session: AsyncSession,
    bearer_user,
    *,
    optional: bool,
):
    """Resolve the current user in `oidc`/`header` advanced modes.

    Shared cookie/session-store and trusted-proxy authorities participate
    in the main per-request current-user boundary (not only in the
    dedicated login/proxy-me routes):

    * `oidc`: the MoonMind session cookie issued by the callback is
      validated through the shared ``resolve_current_user`` authority
      (signature/purpose/expiry/revocation/generation/principal checks).
      Requests without the cookie fall back to the bearer dependency so
      worker/API-key callers keep working.
    * `header`: the trusted-proxy asserted identity is validated (trusted
      peer, single well-formed header, enrolled principal) and resolved
      through the same #4119 authority as ``/proxy/me``. Requests without
      the identity header fall back to the bearer dependency.

    A presented-but-invalid advanced credential fails closed (401/403);
    a missing one falls back to bearer, and a missing bearer fails with
    401 (or ``None`` when ``optional``).
    """
    from moonmind.security import omnigent_auth_qualification as _q
    from moonmind.security.auth_modes_4120 import get_request_production_mode as _mode

    mode = _mode()

    if mode == "oidc":
        try:
            control_config = build_moonmind_control_plane_config()
        except Exception:
            control_config = None
        cookie_token = None
        if control_config is not None:
            try:
                cookie_token = request.cookies.get(control_config.cookie_name)
            except Exception:
                cookie_token = None
        if cookie_token:
            from api_service.services.session_store import (
                DbAccountStore,
                DbRevocationStore,
            )

            account = await _q.resolve_current_user(
                cookie_token=cookie_token,
                bearer_token=None,
                account_store=DbAccountStore(session),
                revocation=DbRevocationStore(session),
                config=control_config,
                optional=False,
            )
            user = await session.get(User, account.user_id)
            if user is None or not user.is_active:
                from fastapi import HTTPException as _HTTP

                raise _HTTP(status_code=403, detail="inactive")
            return user
    elif mode == "header":
        presented: list[str] = []
        try:
            from api_service.services.advanced_auth_service_4124 import (
                AdvancedAdmissionPolicy,
                extract_proxy_identity_from_request,
                resolve_proxy_user,
                validate_advanced_mode_config,
            )

            proxy_config = validate_advanced_mode_config("header")
            try:
                raw_headers: list[tuple[str, str]] = [
                    (k.decode("latin-1"), v.decode("latin-1"))
                    for (k, v) in request.scope.get("headers", [])
                ]
            except Exception:
                raw_headers = list(request.headers.items())
            wanted = proxy_config.header_name.strip().lower()
            presented = [
                v for (k, v) in raw_headers if str(k).strip().lower() == wanted
            ]
            if presented:
                peer_ip = (
                    (request.client.host if request.client else "") or ""
                )
                identity = extract_proxy_identity_from_request(
                    raw_headers,
                    peer_ip=peer_ip,
                    config=proxy_config,
                    forwarded_host=request.headers.get("x-forwarded-host"),
                    forwarded_proto=request.headers.get("x-forwarded-proto"),
                )
                user = await resolve_proxy_user(
                    session, identity, policy=AdvancedAdmissionPolicy()
                )
                if not user.is_active:
                    from fastapi import HTTPException as _HTTP

                    raise _HTTP(status_code=403, detail="inactive")
                return user
        except Exception as exc:
            from fastapi import HTTPException as _HTTP

            # A presented proxy assertion that fails validation fails
            # closed; only a fully missing header falls back to bearer.
            if isinstance(exc, _HTTP):
                raise
            code = getattr(exc, "code", None)
            if code == "misconfigured":
                raise _HTTP(status_code=503, detail="unavailable")
            if code in ("enrollment_required", "email_taken"):
                raise _HTTP(status_code=403, detail="enrollment_required")
            if isinstance(exc, _q.ForbiddenError):
                raise _HTTP(
                    status_code=403,
                    detail=getattr(exc, "code", "forbidden") or "forbidden",
                )
            if isinstance(exc, _q.UnavailableError):
                raise _HTTP(status_code=503, detail="unavailable")
            if presented:
                raise _HTTP(status_code=401, detail="auth_invalid")
            # Missing header (AuthRequiredError with nothing presented):
            # fall through to the bearer dependency below.

    if bearer_user is not None:
        return bearer_user
    if optional:
        return None
    from fastapi import HTTPException as _HTTP

    raise _HTTP(status_code=401, detail="auth_required")


async def _advanced_current_user(
    request: Request,
    session: AsyncSession = Depends(get_async_session),
    bearer_user=Depends(current_active_user_optional),
):
    """Strict advanced-mode current-user dependency (401 when missing)."""
    return await _resolve_advanced_user(
        request, session, bearer_user, optional=False
    )


async def _advanced_current_user_optional(
    request: Request,
    session: AsyncSession = Depends(get_async_session),
    bearer_user=Depends(current_active_user_optional),
):
    """Optional advanced-mode current-user dependency (None when missing)."""
    return await _resolve_advanced_user(
        request, session, bearer_user, optional=True
    )


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

    if get_request_production_mode() in ("oidc", "header"):
        # #4124 advanced modes: the shared cookie/session-store (oidc) and
        # trusted-proxy (header) authorities participate in the main
        # per-request boundary with bearer fallback, so sessions minted by
        # the callback and proxy assertions are honored by every protected
        # router, not only the dedicated auth routes.
        return _advanced_current_user
    if get_request_production_mode() != "disabled":
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
    from moonmind.security.auth_modes_4120 import get_request_production_mode as _m

    if get_request_production_mode() in ("oidc", "header"):
        return _advanced_current_user_optional
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

