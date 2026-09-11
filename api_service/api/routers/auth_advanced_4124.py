"""Advanced-auth HTTP boundary (MoonLadderStudios/MoonMind#4124).

Generic OIDC login/callback/logout plus trusted-proxy identity
resolution through the shared #4118/#4119/#4120/#4121 authorities.
Mounted at ``/api/v1/auth``; every endpoint fails closed when the
classified production mode does not select it, so ``disabled`` and
``accounts`` deployments never advertise these journeys.

* OIDC uses authorization-code + PKCE with single-use DB transactions
  (replica/restart-safe), exact same-origin callbacks, and fail-closed
  claim validation. Verified ``(issuer, subject)`` resolves through
  #4119; sessions mint through #4121 cookie/CSRF rules.
* Trusted-proxy endpoints validate the asserted identity only from a
  trusted peer behind explicitly trusted ingress; direct connections,
  duplicates, reserved, missing, and unenrolled identities fail closed
  with the §8 error contract (401/403/503, never fallback).
* Browser responses never carry session/refresh tokens in JSON and never
  log raw IdP material.
"""

from __future__ import annotations

import logging
import os

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.db.base import get_async_session
from moonmind.security import omnigent_auth_qualification as q

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/auth", tags=["AdvancedAuth4124"])


def _production_mode() -> str:
    from moonmind.security.auth_modes_4120 import get_request_production_mode

    return get_request_production_mode() or ""


def _error(status: int, code: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"code": code})


def _oidc_config_from_env():
    from api_service.services.advanced_auth_service_4124 import (
        validate_advanced_mode_config,
    )

    return validate_advanced_mode_config("oidc")


def _proxy_config_from_env():
    from api_service.services.advanced_auth_service_4124 import (
        validate_advanced_mode_config,
    )

    return validate_advanced_mode_config("header")


def _moonmind_session_config():
    from api_service.auth_providers import build_moonmind_control_plane_config

    return build_moonmind_control_plane_config()


@router.get("/oidc/login")
async def oidc_login(
    request: Request,
    return_path: str = Query(default="/"),
    session: AsyncSession = Depends(get_async_session),
) -> Response:
    """Start a generic-OIDC authorization transaction (302 to the IdP)."""
    if _production_mode() != "oidc":
        return _error(404, "auth_invalid")
    from moonmind.security.oidc_advanced_4124 import (
        BoundedMetadataCache,
        OidcLoginError,
        fetch_discovery,
        redacted_oidc_error,
        validate_return_path,
    )
    from api_service.services.advanced_auth_service_4124 import begin_oidc_login
    from api_service.services.advanced_auth_service_4124 import DbOidcTransactionStore

    try:
        config = _oidc_config_from_env()
        try:
            safe_return = validate_return_path(return_path)
        except OidcLoginError:
            safe_return = "/"

        import asyncio as _asyncio
        import httpx as _httpx

        def _get(url: str, timeout: float):
            return _httpx.get(url, timeout=timeout, follow_redirects=False)

        cache: BoundedMetadataCache = getattr(
            request.app.state, "oidc_metadata_cache", None
        ) or BoundedMetadataCache()
        request.app.state.oidc_metadata_cache = cache
        # Keep synchronous IdP I/O off the async event loop: the API
        # entrypoint runs a single Uvicorn worker, so a slow IdP must not
        # stall health/dashboard/API requests on the loop.
        discovery = await _asyncio.to_thread(
            fetch_discovery, config, http_get=_get, cache=cache
        )
        txn, url = begin_oidc_login(config, discovery, return_path=safe_return)
        store = DbOidcTransactionStore(session)
        await store.save(txn)
        await session.commit()
        # Bounded maintenance: login traffic would otherwise grow
        # moonmind_oidc_transactions with expired rows (prune_expired has
        # no other caller). Best-effort; a prune failure must not fail
        # the login itself.
        try:
            await store.prune_expired()
            await session.commit()
        except Exception:
            try:
                await session.rollback()
            except Exception:
                # Best-effort maintenance only; login already succeeded.
                pass
    except Exception as exc:  # noqa: BLE001 - fail closed with stable codes
        try:
            await session.rollback()
        except Exception:
            # Best-effort cleanup only: the outer handler already fails
            # closed below, so a rollback failure must not mask it.
            pass
        code = getattr(exc, "code", None)
        if code == "idp_unavailable":
            logger.warning("auth_event mode=oidc reason=idp_unavailable")
            return _error(503, "unavailable")
        if code in ("misconfigured",):
            logger.warning("auth_event mode=oidc reason=misconfigured")
            return _error(503, "unavailable")
        logger.info("auth_event mode=oidc reason=login_start_failed")
        _ = redacted_oidc_error(exc)
        return _error(401, "auth_invalid")
    return RedirectResponse(url=url, status_code=302)


@router.get("/oidc/callback")
async def oidc_callback(
    request: Request,
    code: str = Query(default=""),
    state: str = Query(default=""),
    session: AsyncSession = Depends(get_async_session),
) -> Response:
    """Consume a transaction, validate the IdP result, mint a session."""
    if _production_mode() != "oidc":
        return _error(404, "auth_invalid")
    from moonmind.security import oidc_advanced_4124 as oidc_mod
    from moonmind.security.session_authority_4121 import build_set_cookie_header
    from api_service.services.advanced_auth_service_4124 import (
        AdvancedAdmissionPolicy,
        DbOidcTransactionStore,
        issue_session_for_user,
        resolve_oidc_user,
    )

    try:
        config = _oidc_config_from_env()
        moonmind_config = _moonmind_session_config()
        store = DbOidcTransactionStore(session)
        txn = await store.consume(state)
        # Single-use guarantee: commit the consumption in its own
        # transaction before any fallible exchange/validation work, so a
        # later failure (and its rollback) cannot resurrect this state
        # for replay.
        await session.commit()
        if txn.redirect_uri != config.redirect_uri:
            raise oidc_mod.OidcLoginError("auth_invalid", "redirect mismatch")

        import asyncio as _asyncio

        import httpx as _httpx

        cache: oidc_mod.BoundedMetadataCache = getattr(
            request.app.state, "oidc_metadata_cache", None
        ) or oidc_mod.BoundedMetadataCache()
        request.app.state.oidc_metadata_cache = cache

        def _sync_get(url: str, timeout: float):
            return _httpx.get(url, timeout=timeout, follow_redirects=False)

        # Keep synchronous IdP I/O off the async event loop (single
        # Uvicorn worker): discovery and JWKS fetches run on threads.
        discovery = await _asyncio.to_thread(
            oidc_mod.fetch_discovery, config, http_get=_sync_get, cache=cache
        )

        async def _post(url: str, data: dict, timeout: float):
            async with _httpx.AsyncClient(follow_redirects=False) as client:
                resp = await client.post(url, data=data, timeout=timeout)
                body: dict = {}
                try:
                    parsed = resp.json()
                    body = parsed if isinstance(parsed, dict) else {}
                except Exception:
                    body = {}
                # staticmethod: resp.json() must return the captured body,
                # not the bound response instance.
                return type(
                    "R",
                    (),
                    {
                        "status_code": resp.status_code,
                        "json": staticmethod(lambda _body=body: _body),
                    },
                )()

        tokens = await oidc_mod.exchange_code_for_tokens(
            code, config=config, discovery=discovery, txn=txn, http_post=_post
        )
        jwks = await _asyncio.to_thread(
            oidc_mod.fetch_jwks,
            discovery.jwks_uri,
            timeout_seconds=config.timeout_seconds,
            http_get=_sync_get,
            cache=cache,
        )
        # Bounded rotation retry: on unknown-key, refresh once and retry.
        try:
            claims = oidc_mod.validate_id_token(
                tokens["id_token"],
                config=config,
                discovery=discovery,
                expected_nonce=txn.nonce,
                jwks=jwks,
            )
        except oidc_mod.OidcLoginError as exc:
            if exc.code == "auth_invalid" and "key" in (exc.detail or ""):
                cache.invalidate_jwks(discovery.jwks_uri)
                jwks = await _asyncio.to_thread(
                    oidc_mod.fetch_jwks,
                    discovery.jwks_uri,
                    timeout_seconds=config.timeout_seconds,
                    http_get=_sync_get,
                    cache=cache,
                    max_retries=0,
                )
                claims = oidc_mod.validate_id_token(
                    tokens["id_token"],
                    config=config,
                    discovery=discovery,
                    expected_nonce=txn.nonce,
                    jwks=jwks,
                )
            else:
                raise
        identity = oidc_mod.validated_identity_from_claims(
            claims, issuer=config.issuer
        )
        auto = (os.environ.get("MOONMIND_OIDC_AUTO_PROVISION", "") or "").lower() in (
            "1",
            "true",
            "yes",
        )
        user = await resolve_oidc_user(
            session, identity, policy=AdvancedAdmissionPolicy(auto_provision=auto)
        )
        token, _ = await issue_session_for_user(
            session, user, identity, moonmind_config
        )
        # Browser flow: navigate back to the validated same-origin return
        # path (txn.return_path was validated at transaction creation) and
        # attach the session cookie to the redirect. There is no frontend
        # handler for raw callback JSON, so returning JSON would strand
        # the browser on the callback URL.
        resp = RedirectResponse(url=txn.return_path or "/", status_code=302)
        resp.headers["Set-Cookie"] = build_set_cookie_header(
            token=token,
            cookie_name=moonmind_config.cookie_name,
            require_secure_cookies=moonmind_config.require_secure_cookies,
            max_age_seconds=moonmind_config.session_ttl_seconds,
        )
        logger.info("auth_event mode=oidc reason=login_ok")
        return resp
    except Exception as exc:  # noqa: BLE001 - fail closed with stable codes
        try:
            await session.rollback()
        except Exception:
            # Best-effort cleanup only: the callback already failed, so a
            # rollback failure must not mask the stable error mapping below.
            # Note: the consumed transaction row was committed independently
            # before the exchange, so this rollback cannot resurrect it.
            pass
        code = getattr(exc, "code", None)
        if code == "idp_unavailable":
            logger.warning("auth_event mode=oidc reason=idp_unavailable")
            return _error(503, "unavailable")
        if code in ("enrollment_required", "email_taken"):
            logger.info("auth_event mode=oidc reason=enrollment_required")
            return JSONResponse(
                status_code=403, content={"code": "enrollment_required"}
            )
        if code == "replay_detected":
            logger.info("auth_event mode=oidc reason=replay_detected")
            return _error(401, "auth_invalid")
        if isinstance(exc, q.ForbiddenError):
            return _error(403, getattr(exc, "code", "forbidden") or "forbidden")
        if isinstance(exc, q.UnavailableError):
            return _error(503, "unavailable")
        if code == "misconfigured":
            return _error(503, "unavailable")
        logger.info("auth_event mode=oidc reason=callback_failed")
        return _error(401, "auth_invalid")


@router.post("/oidc/logout")
async def oidc_logout(
    request: Request, session: AsyncSession = Depends(get_async_session)
) -> Response:
    """Revoke the MoonMind session; IdP end-session is best-effort."""
    if _production_mode() != "oidc":
        return _error(404, "auth_invalid")
    from moonmind.security.session_authority_4121 import build_clear_cookie_header
    from api_service.services.advanced_auth_service_4124 import logout_session

    moonmind_config = _moonmind_session_config()
    cookie_name = moonmind_config.cookie_name
    # CSRF/origin gate before consuming or revoking the session cookie:
    # SameSite=Lax does not isolate ports, so a same-site cross-origin
    # page could otherwise force a victim logout. Fail closed through
    # the shared authority (safe methods / missing base URL handled
    # there); a rejection returns 401, never revokes.
    try:
        from moonmind.security.session_authority_4121 import enforce_csrf_origin

        enforce_csrf_origin(
            method=request.method,
            cookie_present=cookie_name in request.cookies,
            origin=request.headers.get("origin"),
            referer=request.headers.get("referer"),
            host=request.headers.get("host"),
            base_url=(os.environ.get("MOONMIND_PUBLIC_BASE_URL") or "").strip(),
        )
    except Exception as exc:
        code = getattr(exc, "code", None) or "auth_invalid"
        logger.info("auth_event mode=oidc reason=logout_csrf_rejected")
        return _error(401, code if isinstance(code, str) else "auth_invalid")
    token = request.cookies.get(cookie_name)
    if not token:
        resp = JSONResponse(status_code=200, content={"ok": True})
        resp.headers["Set-Cookie"] = build_clear_cookie_header(
            cookie_name=cookie_name,
            require_secure_cookies=moonmind_config.require_secure_cookies,
        )
        return resp
    try:
        import jwt as _jwt

        claims = _jwt.decode(token, options={"verify_signature": False})
        jti, sub = str(claims.get("jti") or ""), str(claims.get("sub") or "")
        from uuid import UUID as _UUID

        user_id = _UUID(sub)
    except Exception:
        resp = JSONResponse(status_code=200, content={"ok": True})
        resp.headers["Set-Cookie"] = build_clear_cookie_header(
            cookie_name=cookie_name,
            require_secure_cookies=moonmind_config.require_secure_cookies,
        )
        return resp
    try:
        config = _oidc_config_from_env()
        end_session = getattr(config, "end_session_endpoint", "") or ""
        if not end_session:
            # Providers that advertise logout only through standard
            # discovery: resolve it from the cached discovery document
            # (no new config surface; best-effort, never blocks logout).
            try:
                import asyncio as _logout_asyncio

                import httpx as _discovery_httpx

                from moonmind.security import oidc_advanced_4124 as _oidc

                _cache = getattr(request.app.state, "oidc_metadata_cache", None)
                if _cache is None:
                    _cache = _oidc.BoundedMetadataCache()
                    request.app.state.oidc_metadata_cache = _cache

                def _dget(url: str, timeout: float):
                    return _discovery_httpx.get(
                        url, timeout=timeout, follow_redirects=False
                    )

                _discovery = await _logout_asyncio.to_thread(
                    _oidc.fetch_discovery, config, http_get=_dget, cache=_cache
                )
                end_session = getattr(_discovery, "end_session_endpoint", "") or ""
            except Exception:
                end_session = ""
    except Exception:
        end_session = ""
    import httpx as _httpx

    result = await logout_session(
        session,
        jti=jti,
        user_id=user_id,
        idp_end_session_url=end_session,
        idp_logout_http_get=(
            (lambda url, timeout: _httpx.get(url, timeout=timeout))
            if end_session
            else None
        ),
    )
    resp = JSONResponse(
        status_code=200,
        content={"ok": True, "idp_logout_ok": result.idp_logout_ok},
    )
    resp.headers["Set-Cookie"] = build_clear_cookie_header(
        cookie_name=cookie_name,
        require_secure_cookies=moonmind_config.require_secure_cookies,
    )
    logger.info(
        "auth_event mode=oidc reason=logout local_revoked=%s idp_ok=%s",
        result.local_revoked,
        result.idp_logout_ok,
    )
    return resp


@router.get("/proxy/me")
async def proxy_me(
    request: Request, session: AsyncSession = Depends(get_async_session)
) -> Response:
    """Resolve the trusted-proxy asserted identity for this request."""
    if _production_mode() != "header":
        return _error(404, "auth_invalid")
    from api_service.services.advanced_auth_service_4124 import (
        AdvancedAdmissionPolicy,
        extract_proxy_identity_from_request,
        resolve_proxy_user,
    )

    try:
        proxy_config = _proxy_config_from_env()
        # Preserve duplicates: use raw headers list, not the collapsed dict.
        try:
            raw_headers: list[tuple[str, str]] = [
                (k.decode("latin-1"), v.decode("latin-1"))
                for (k, v) in request.scope.get("headers", [])
            ]
        except Exception:
            raw_headers = list(request.headers.items())
        peer_ip = (request.client.host if request.client else "") or ""
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
            return _error(403, "inactive")
        await session.commit()
    except Exception as exc:  # noqa: BLE001 - fail closed with stable codes
        try:
            await session.rollback()
        except Exception:
            # Best-effort cleanup only: proxy identity resolution already
            # failed, so a rollback failure must not mask it.
            pass
        code = getattr(exc, "code", None)
        if code == "auth_required":
            return _error(401, "auth_required")
        if code in ("enrollment_required", "email_taken"):
            return JSONResponse(
                status_code=403, content={"code": "enrollment_required"}
            )
        if code == "misconfigured":
            return _error(503, "unavailable")
        if isinstance(exc, q.ForbiddenError):
            return _error(403, getattr(exc, "code", "forbidden") or "forbidden")
        if isinstance(exc, q.UnavailableError):
            return _error(503, "unavailable")
        return _error(401, "auth_invalid")
    return JSONResponse(
        status_code=200, content={"ok": True, "user_id": str(user.id)}
    )
