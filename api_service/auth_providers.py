import asyncio
import logging
import os
import uuid

from fastapi import Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.auth import _DEFAULT_USER_ID
from api_service.db.base import get_async_session
from api_service.db.models import User
from api_service.services.profile_service import ProfileService
from moonmind.auth import AuthProviderManager, EnvAuthProvider, ProfileAuthProvider
from moonmind.config.settings import settings
from moonmind.security.auth_modes_4120 import (
    get_request_production_mode,
    is_disabled_local_mode,
)

logger = logging.getLogger(__name__)


def _extract_session_credentials(request: Request) -> tuple[str | None, str | None]:
    """Extract the MoonMind session cookie and bearer credentials.

    Both MoonMind cookie names are honored (production ``__Host-`` cookie
    plus the separately named loopback development cookie); upstream
    runtime cookie names are never read here. Empty strings count as
    missing. Precedence/conflict semantics belong to the session
    authority, not this extractor.
    """
    from moonmind.security.session_authority_4121 import (
        MOONMIND_DEV_COOKIE,
        MOONMIND_PROD_COOKIE,
    )

    cookies = getattr(request, "cookies", None) or {}
    cookie_token = cookies.get(MOONMIND_PROD_COOKIE) or cookies.get(
        MOONMIND_DEV_COOKIE
    )
    if isinstance(cookie_token, str):
        cookie_token = cookie_token.strip() or None
    else:
        cookie_token = None
    authorization = request.headers.get("authorization", "")
    bearer_token: str | None = None
    if isinstance(authorization, str) and authorization.strip().lower().startswith(
        "bearer "
    ):
        bearer_token = authorization.strip()[7:].strip() or None
    return cookie_token, bearer_token


def _enforce_cookie_csrf_origin(request: Request) -> None:
    """Enforce the CSRF origin boundary for cookie-authenticated requests.

    Delegates to the qualified #4121 ``enforce_csrf_origin`` helper so
    cookie-authenticated mutations require an ``Origin``/``Referer`` that
    is same-origin with the configured ``MOONMIND_PUBLIC_BASE_URL``. Safe
    methods and bearer-only flows pass through inside the helper. When no
    public base URL is configured the deployment is local-only (see
    ``api_service.main`` startup classification) and there is no configured
    origin to check against, so the check is skipped; remote deployments
    must configure the base URL. Failures map to the §8 HTTP contract.
    """
    from moonmind.security.session_authority_4121 import enforce_csrf_origin

    base_url = os.environ.get("MOONMIND_PUBLIC_BASE_URL", "").strip()
    if not base_url:
        return
    try:
        host = request.headers.get("host")
        if not host and request.url is not None:
            host = request.url.hostname
        enforce_csrf_origin(
            method=request.method or "GET",
            cookie_present=True,
            origin=request.headers.get("origin"),
            referer=request.headers.get("referer"),
            host=host,
            base_url=base_url,
        )
    except Exception as exc:
        raise _session_http_exception(exc)


def _session_http_exception(exc: BaseException):
    """Map a session-authority error to the §8 HTTP contract."""
    from moonmind.security.session_authority_4121 import http_status_for_error

    status_code, code = http_status_for_error(exc)
    mode = get_request_production_mode() or "undecided"
    # Redacted audit event: mode + reason code only, never tokens/cookies.
    logger.info(
        "auth_event mode=%s reason=session_denial code=%s",
        mode,
        code,
    )
    if status_code == 403:
        return HTTPException(status_code=status_code, detail={"code": code})
    if status_code == 503:
        return HTTPException(status_code=status_code, detail=code)
    return HTTPException(status_code=status_code, detail={"code": code})


async def _load_disabled_user(session: AsyncSession) -> User:
    """Resolve the persisted local-mode principal, failing closed.

    No synthetic administrator is ever minted: identity-store outage
    returns 503 ``unavailable`` and a missing default row returns 503
    ``setup_required``. Test callers must use explicit dependency
    overrides; no environment-driven test identity shortcut exists on
    this path.
    """

    async def _fetch() -> User | None:
        user_id_str = settings.oidc.DEFAULT_USER_ID or _DEFAULT_USER_ID
        user_uuid = uuid.UUID(user_id_str)
        return await session.get(User, user_uuid)

    try:
        user_obj = await asyncio.wait_for(_fetch(), timeout=1.0)
    except (Exception, asyncio.TimeoutError):
        logger.info(
            "auth_event mode=disabled reason=session_denial code=unavailable",
        )
        raise HTTPException(status_code=503, detail="unavailable")
    if user_obj is None:
        logger.info(
            "auth_event mode=disabled reason=session_denial code=setup_required",
        )
        raise HTTPException(status_code=503, detail="setup_required")
    # Disabled auth is single-user local mode; treat that principal as the
    # local administrator even if the persisted row predates this policy.
    user_obj.is_superuser = True
    return user_obj


async def _resolve_session_principal(
    request: Request,
    session: AsyncSession,
    *,
    optional: bool,
) -> User | None:
    """Validate the presented MoonMind session and return the live User."""
    from moonmind.security.session_authority_4121 import resolve_session_user

    from api_service.services.session_store import (
        DbAccountStore,
        DbRevocationStore,
    )

    cookie_token, bearer_token = _extract_session_credentials(request)
    if optional and cookie_token is None and bearer_token is None:
        # Optional boundary with no presented credential: the separately
        # authenticated worker path authorizes below; nothing is swallowed
        # and no session configuration is required to observe absence.
        return None
    if cookie_token is not None:
        # Cookie-authenticated requests must clear the CSRF origin boundary
        # before session validation: no HTTP middleware performs this check,
        # so a same-site sibling origin could otherwise submit credentialed
        # mutations with the victim's cookie. Safe methods and bearer-only
        # flows pass through inside the helper.
        _enforce_cookie_csrf_origin(request)
    try:
        config = build_moonmind_control_plane_config()
    except HTTPException:
        mode = get_request_production_mode() or "undecided"
        logger.info(
            "auth_event mode=%s reason=session_denial code=unavailable",
            mode,
        )
        raise
    except Exception as exc:
        logger.info(
            "auth_event mode=%s reason=session_denial code=unavailable",
            get_request_production_mode() or "undecided",
        )
        raise HTTPException(status_code=503, detail="unavailable") from exc
    account_store = DbAccountStore(session)
    revocation_store = DbRevocationStore(session)
    try:
        account = await resolve_session_user(
            cookie_token=cookie_token,
            bearer_token=bearer_token,
            account_store=account_store,
            revocation=revocation_store,
            config=config,
            optional=optional,
        )
    except Exception as exc:
        raise _session_http_exception(exc)
    if account is None:
        # Optional boundary with no presented credential: the separately
        # authenticated worker path authorizes below; nothing is swallowed.
        return None
    try:
        user = await session.get(User, account.user_id)
    except Exception as exc:
        logger.info(
            "auth_event mode=%s reason=session_denial code=unavailable",
            config.mode,
        )
        raise HTTPException(status_code=503, detail="unavailable") from exc
    if user is None:
        logger.info(
            "auth_event mode=%s reason=session_denial code=auth_invalid",
            config.mode,
        )
        raise HTTPException(
            status_code=401, detail={"code": "auth_invalid"}
        )
    # Authoritative live flags: the persisted row decides active/admin
    # status at request time, never upstream advisory claims.
    if not bool(user.is_active):
        logger.info(
            "auth_event mode=%s reason=session_denial code=inactive",
            config.mode,
        )
        raise HTTPException(status_code=403, detail={"code": "inactive"})
    return user


async def _strict_current_user(
    request: Request,
    session: AsyncSession = Depends(get_async_session),
) -> User:
    """Shared strict boundary: every user-facing path resolves here.

    The production mode is evaluated per request (never captured at
    import/app-construction time), so a classified mode change cannot
    retain another provider's resolver. Authenticated modes validate
    MoonMind sessions through the qualified #4121 authority; legacy
    application JWTs fail closed as ``auth_invalid``.
    """
    if is_disabled_local_mode():
        return await _load_disabled_user(session)
    user = await _resolve_session_principal(request, session, optional=False)
    assert user is not None
    return user


async def _optional_current_user(
    request: Request,
    session: AsyncSession = Depends(get_async_session),
) -> User | None:
    """Optional counterpart for the separately authenticated worker path.

    Missing credentials return ``None``; a bad presented credential is
    never swallowed as anonymous success.

    Workflow-scoped execution fan-out requests carry their capability in
    the same ``Authorization: Bearer`` header plus the
    ``X-MoonMind-Execution-Fanout: v1`` marker. The session extractor
    would reject that bearer as ``auth_invalid`` before the route can
    verify it, so a marked request skips session validation here and the
    route authorizes via ``resolve_execution_fanout_capability``.
    """
    if is_disabled_local_mode():
        return await _load_disabled_user(session)
    try:
        marker = request.headers.get("x-moonmind-execution-fanout", "")
    except Exception:
        marker = ""
    if isinstance(marker, str) and marker.strip() == "v1":
        return None
    return await _resolve_session_principal(request, session, optional=True)


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
    secret_env = (
        os.environ.get("MOONMIND_SESSION_SECRET", "").strip()
        or os.environ.get("JWT_SECRET", "").strip()
    )
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
    from moonmind.security.auth_modes_4120 import resolve_moonmind_auth_config

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
    """Return the shared strict principal dependency.

    A stable function reference is returned (no per-call closure and no
    cached foreign resolver) so route registration and test dependency
    overrides share one identity while the production mode is still
    evaluated per request inside the dependency.

    ``oidc`` and ``header`` modes dispatch to the qualified #4124
    dependencies so the generic OIDC session cookie and the trusted-proxy
    identity honor the same principal as their login/diagnostic flows.
    All other modes resolve through the #4125 strict session authority
    (``_strict_current_user``), which evaluates the production mode per
    request and fails closed without legacy JWT acceptance.
    """
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
    return _strict_current_user


def get_current_user_optional():
    """Return the shared optional principal dependency.

    Worker-token authenticated endpoints use this helper so header-only workers
    are not blocked by FastAPI resolving a strict bearer-auth dependency first.
    A missing credential returns ``None``; an invalid or conflicting
    presented credential raises instead of falling back to anonymous.
    """
    return _optional_current_user


async def get_auth_manager(
    db: AsyncSession = Depends(get_async_session),
) -> AuthProviderManager:
    """Return an AuthProviderManager wired to the given DB session."""
    profile_provider = ProfileAuthProvider(db, ProfileService())
    env_provider = EnvAuthProvider()
    return AuthProviderManager(profile_provider, env_provider)
