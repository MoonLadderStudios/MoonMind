"""Advanced-auth routes: generic OIDC login/callback/logout (#4124).

Source issue: MoonLadderStudios/MoonMind#4124 (parent #4116).

Mounted under ``/api/v1/oidc`` (never ``/api/v1/auth/*``: the legacy
application-login routes stay unmounted per the #4129 removal manifest).
Only active in ``oidc`` production mode; every other mode answers 404 so
the surface is not advertised where it cannot complete. Trusted-header
mode has no login routes — identity arrives per request through
:func:`get_trusted_proxy_user`, which enforces the explicitly trusted
ingress before the shared #4119/#4121 authority.

Request path (one vertical slice, no parallel validator):

browser -> this router (PKCE/state/nonce, claim validation) ->
#4119 ``identity_service`` ((issuer, subject) -> UUID, convergent binding) ->
#4121 session authority (cookie issue/clear, CSRF) -> existing per-resource
authorization.
"""

from __future__ import annotations

import ipaddress
import logging
import time
from typing import Any, Mapping

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import text

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/oidc", tags=["oidc"])

_METADATA_CACHE_ATTR = "oidc_4124_metadata_cache"
_JWKS_CACHE_ATTR = "oidc_4124_jwks_cache"
_TRANSACTION_STORE_ATTR = "oidc_4124_transaction_store"


# ---------------------------------------------------------------------------
# Production HTTP transport: scoped requests, timeouts, no open redirects
# ---------------------------------------------------------------------------


class HttpxOIDCTransport:
    """Scoped IdP transport over httpx with per-call timeouts."""

    async def get_json(self, url: str, *, timeout_seconds: float) -> dict[str, Any]:
        import httpx

        try:
            async with httpx.AsyncClient(
                timeout=timeout_seconds, follow_redirects=True, max_redirects=3
            ) as client:
                response = await client.get(
                    url, headers={"Accept": "application/json"}
                )
        except Exception as exc:
            from moonmind.security.advanced_identity_4124 import OIDCUnavailableError

            raise OIDCUnavailableError(
                "identity provider unreachable; login cannot proceed without "
                "changing auth mode, principal, or accepted tokens"
            ) from exc
        if response.status_code != 200:
            from moonmind.security.advanced_identity_4124 import OIDCLoginError

            raise OIDCLoginError(
                "invalid_metadata",
                "identity provider returned an error document; refusing to proceed",
            )
        try:
            raw = response.json()
        except Exception as exc:
            from moonmind.security.advanced_identity_4124 import OIDCLoginError

            raise OIDCLoginError(
                "invalid_metadata", "identity provider document is not JSON"
            ) from exc
        if not isinstance(raw, dict):
            from moonmind.security.advanced_identity_4124 import OIDCLoginError

            raise OIDCLoginError(
                "invalid_metadata", "identity provider document is malformed"
            )
        return raw

    async def post_form(
        self,
        url: str,
        form: dict[str, str],
        *,
        timeout_seconds: float,
        auth: tuple[str, str] | None = None,
    ) -> dict[str, Any]:
        import httpx

        try:
            async with httpx.AsyncClient(
                timeout=timeout_seconds, follow_redirects=False
            ) as client:
                response = await client.post(url, data=form, auth=auth)
        except Exception as exc:
            from moonmind.security.advanced_identity_4124 import OIDCUnavailableError

            raise OIDCUnavailableError(
                "identity provider token endpoint unreachable; login cannot proceed"
            ) from exc
        if response.status_code in (301, 302, 303, 307, 308):
            from moonmind.security.advanced_identity_4124 import OIDCLoginError

            raise OIDCLoginError(
                "invalid_token_response",
                "identity provider token endpoint redirected; refusing to "
                "follow with client credentials",
            )
        try:
            raw = response.json()
        except Exception as exc:
            from moonmind.security.advanced_identity_4124 import OIDCLoginError

            raise OIDCLoginError(
                "invalid_token_response", "identity provider token response is malformed"
            ) from exc
        if not isinstance(raw, dict):
            from moonmind.security.advanced_identity_4124 import OIDCLoginError

            raise OIDCLoginError(
                "invalid_token_response", "identity provider token response is malformed"
            )
        return raw


def _metadata_cache(request: Request):
    from moonmind.security.advanced_identity_4124 import OIDCMetadataCache

    cache = getattr(request.app.state, _METADATA_CACHE_ATTR, None)
    if cache is None:
        cache = OIDCMetadataCache()
        request.app.state.__dict__[_METADATA_CACHE_ATTR] = cache
    return cache


def _jwks_cache(request: Request):
    from moonmind.security.advanced_identity_4124 import JWKSCache

    cache = getattr(request.app.state, _JWKS_CACHE_ATTR, None)
    if cache is None:
        cache = JWKSCache()
        request.app.state.__dict__[_JWKS_CACHE_ATTR] = cache
    return cache


def get_transaction_store(request: Request):
    """Process-local single-use store (multi-replica truth is the DB table).

    The ``moonmind_oidc_transactions`` table (ensured idempotently) is the
    replica/restart-shared truth; this process handle batches through it.
    """
    store = getattr(request.app.state, _TRANSACTION_STORE_ATTR, None)
    if store is None:
        store = DbAuthTransactionStore()
        request.app.state.__dict__[_TRANSACTION_STORE_ATTR] = store
    return store


class DbAuthTransactionStore:
    """Durable single-use transaction store over the existing database.

    Established secure state mechanism: rows in
    ``moonmind_oidc_transactions`` (ensured with ``CREATE TABLE IF NOT
    EXISTS``, mirroring the #4120 migration-decision pattern). Consume is
    one atomic ``DELETE ... RETURNING`` so concurrent/replayed callbacks
    converge on exactly one winner across replicas; the loser fails closed.
    """

    def __init__(self, table: str = "moonmind_oidc_transactions"):
        from moonmind.security.advanced_identity_4124 import (
            OIDC_TRANSACTION_TABLE,
            ensure_oidc_transaction_table_sql,
        )

        self._table = table or OIDC_TRANSACTION_TABLE
        self._ddl = ensure_oidc_transaction_table_sql(self._table)

    async def create(self, transaction) -> None:  # type: ignore[no-untyped-def]
        import time as _time

        from api_service.db.base import get_async_session_context

        async with get_async_session_context() as session:
            await session.execute(text(self._ddl))
            # Bounded cleanup: purge expired rows on every creation so
            # abandoned logins, crawlers, or repeated public hits cannot grow
            # the table without bound. Expiration uses the same TTL the
            # consumer enforces.
            try:
                from moonmind.security.advanced_identity_4124 import (
                    DEFAULT_TRANSACTION_TTL_SECONDS as _TTL,
                )

                _cutoff = float(transaction.created_at) - float(_TTL) - 1.0
                await session.execute(
                    text(f"DELETE FROM {self._table} WHERE created_at < :cutoff"),
                    {"cutoff": _cutoff},
                )
            except Exception:
                # Cleanup is best-effort; the insert below still proceeds.
                pass
            await session.execute(
                text(
                    f"INSERT INTO {self._table} "
                    "(state, nonce, code_verifier, redirect_uri, return_path, created_at) "
                    "VALUES (:state, :nonce, :code_verifier, :redirect_uri, :return_path, :created_at) "
                    "ON CONFLICT (state) DO NOTHING"
                ),
                {
                    "state": transaction.state,
                    "nonce": transaction.nonce,
                    "code_verifier": transaction.code_verifier,
                    "redirect_uri": transaction.redirect_uri,
                    "return_path": transaction.return_path,
                    "created_at": transaction.created_at,
                },
            )
            await session.commit()

    async def consume(self, state: str, *, now: float | None = None):  # type: ignore[no-untyped-def]
        from moonmind.security.advanced_identity_4124 import (
            AuthorizationTransaction,
            OIDCTransactionError,
        )

        from api_service.db.base import get_async_session_context

        moment = time.time() if now is None else now
        key = (state or "").strip()
        if not key:
            raise OIDCTransactionError("unknown or already-consumed transaction")
        async with get_async_session_context() as session:
            await session.execute(text(self._ddl))
            try:
                row = (
                    await session.execute(
                        text(
                            f"DELETE FROM {self._table} WHERE state = :state "
                            "RETURNING nonce, code_verifier, redirect_uri, return_path, created_at"
                        ),
                        {"state": key},
                    )
                ).first()
            except Exception:
                # SQLite builds without RETURNING support fall back to a
                # locked read-then-delete inside one transaction; the DELETE
                # still makes consumption single-use per connection.
                row = (
                    await session.execute(
                        text(
                            f"SELECT nonce, code_verifier, redirect_uri, return_path, created_at "
                            f"FROM {self._table} WHERE state = :state"
                        ),
                        {"state": key},
                    )
                ).first()
                if row is not None:
                    await session.execute(
                        text(f"DELETE FROM {self._table} WHERE state = :state"),
                        {"state": key},
                    )
            await session.commit()
        if row is None:
            raise OIDCTransactionError("unknown or already-consumed transaction")
        from moonmind.security.advanced_identity_4124 import DEFAULT_TRANSACTION_TTL_SECONDS

        transaction = AuthorizationTransaction(
            state=key,
            nonce=str(row[0]),
            code_verifier=str(row[1]),
            redirect_uri=str(row[2]),
            return_path=str(row[3]),
            created_at=float(row[4]),
        )
        if moment - transaction.created_at > DEFAULT_TRANSACTION_TTL_SECONDS:
            raise OIDCTransactionError("expired authorization transaction")
        return transaction


def _require_oidc_mode() -> None:
    from moonmind.security.auth_modes_4120 import get_request_production_mode

    if get_request_production_mode() != "oidc":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "oidc_not_enabled",
                "message": "Generic OIDC is not the active authentication mode; "
                "select AUTH_PROVIDER=oidc with explicit issuer/client configuration.",
            },
        )


def _http_status_for_login_error(exc: BaseException) -> int:
    from moonmind.security.advanced_identity_4124 import (
        EnrollmentRequiredError,
        OIDCMFACutoverBlockedError,
        OIDCLoginError,
        OIDCUnavailableError,
    )

    if isinstance(exc, OIDCUnavailableError):
        return status.HTTP_503_SERVICE_UNAVAILABLE
    if isinstance(exc, (EnrollmentRequiredError, OIDCMFACutoverBlockedError)):
        return status.HTTP_403_FORBIDDEN
    if isinstance(exc, OIDCLoginError):
        return status.HTTP_401_UNAUTHORIZED
    # Database, session-store, driver, or otherwise unclassified failures
    # are retryable infrastructure errors, never bad credentials.
    return status.HTTP_503_SERVICE_UNAVAILABLE


def _login_error_body(exc: BaseException) -> dict[str, Any]:
    from moonmind.security.advanced_identity_4124 import OIDCLoginError

    code = getattr(exc, "code", "login_failed")
    # Controlled OIDC failures carry safe, operator-authored messages.
    if isinstance(exc, OIDCLoginError):
        return {"code": code, "message": str(exc)}
    # Unclassified/infrastructure failures never expose raw diagnostics
    # (SQL, bound values, hostnames, driver details) to unauthenticated
    # callers; report a sanitized retryable error instead.
    safe_code = code if isinstance(code, str) and code else "idp_unavailable"
    if safe_code not in (
        "idp_unavailable",
        "login_failed",
        "auth_required",
        "auth_invalid",
    ):
        safe_code = "idp_unavailable"
    return {
        "code": safe_code,
        "message": "identity service temporarily unavailable; retry later",
    }


@router.get("/login")
async def oidc_login(request: Request, return_path: str = "/") -> RedirectResponse:
    """Start authorization-code + PKCE login at the verified IdP."""
    from moonmind.security.advanced_identity_4124 import (
        build_authorization_url,
        emit_advanced_auth_event,
        new_authorization_transaction,
        redacted_oidc_diagnostics,
        resolve_oidc_provider_config,
    )

    _require_oidc_mode()
    try:
        config = resolve_oidc_provider_config()
        transaction = new_authorization_transaction(config, return_path=return_path)
        await get_transaction_store(request).create(transaction)
        metadata = await _metadata_cache(request).get(
            config, HttpxOIDCTransport()
        )
        target = build_authorization_url(config, metadata, transaction)
    except HTTPException:
        raise
    except Exception as exc:
        emit_advanced_auth_event(
            "denial", mode="oidc", reason=getattr(exc, "code", "login_failed")
        )
        logger.info(
            "oidc login start failed: %s",
            redacted_oidc_diagnostics({"reason": getattr(exc, "code", "login_failed")}),
        )
        raise HTTPException(
            status_code=_http_status_for_login_error(exc),
            detail=_login_error_body(exc),
        ) from exc
    emit_advanced_auth_event("success", mode="oidc", reason="login_started")
    response = RedirectResponse(url=target, status_code=302)
    # Bind the transaction to the initiating browser: the callback must
    # present the same state cookie, otherwise an attacker could start a
    # login with their own IdP account and lure a victim into consuming it
    # (login CSRF / account confusion, session installed in victim browser).
    import os as _os

    _secure = _os.environ.get("MOONMIND_REQUIRE_SECURE_COOKIES", "1") != "0"
    response.set_cookie(
        key="moonmind_oidc_state",
        value=transaction.state,
        httponly=True,
        secure=_secure,
        samesite="lax",
        path="/api/v1/oidc/callback",
        max_age=600,
    )
    return response


@router.get("/callback", response_model=None)
async def oidc_callback(
    request: Request, code: str = "", state: str = "", error: str = ""
) -> Response:
    """Consume one transaction, validate claims, mint a #4121 session.

    Concurrent/replayed callbacks converge on one winner: the second
    ``consume`` fails closed before any enrollment or privilege mutation.
    """
    from moonmind.security import omnigent_auth_qualification as q
    from moonmind.security.advanced_identity_4124 import (
        EnrollmentRequiredError,
        claims_to_validated_identity,
        emit_advanced_auth_event,
        evaluate_oidc_enrollment,
        exchange_code_for_tokens,
        resolve_oidc_provider_config,
        validate_id_token,
    )

    _require_oidc_mode()
    try:
        config = resolve_oidc_provider_config()
        if error:
            from moonmind.security.advanced_identity_4124 import OIDCLoginError

            raise OIDCLoginError("idp_error", "identity provider refused authorization")
        # Verify browser binding before consuming: the presenting browser
        # must own the state cookie issued at /login time.
        from moonmind.security.advanced_identity_4124 import OIDCTransactionError

        _bound = (request.cookies.get("moonmind_oidc_state") or "").strip()
        if not _bound or _bound != (state or "").strip():
            raise OIDCTransactionError(
                "authorization transaction is not bound to this browser; "
                "refusing login-CSRF replay"
            )
        transaction = await get_transaction_store(request).consume(state)
        metadata = await _metadata_cache(request).get(config, HttpxOIDCTransport())
        tokens = await exchange_code_for_tokens(
            code=code,
            transaction=transaction,
            config=config,
            metadata=metadata,
            transport=HttpxOIDCTransport(),
        )
        import jwt as _jwt

        kid = str(_jwt.get_unverified_header(tokens["id_token"]).get("kid", ""))
        jwk = await _jwks_cache(request).get_key(
            kid,
            metadata,
            HttpxOIDCTransport(),
            timeout_seconds=config.timeout_seconds,
        )
        claims = validate_id_token(
            tokens["id_token"],
            jwk=jwk,
            config=config,
            expected_nonce=transaction.nonce,
        )
        identity = claims_to_validated_identity(claims, issuer=config.issuer)
        user = await _admit_oidc_identity(identity, config)
        token = await _mint_session_for_user(user, mode="oidc", identity=identity)
    except HTTPException:
        raise
    except Exception as exc:
        emit_advanced_auth_event(
            "denial", mode="oidc", reason=getattr(exc, "code", "login_failed")
        )
        # Never render raw token responses: the body carries codes only.
        return JSONResponse(
            status_code=_http_status_for_login_error(exc),
            content=_login_error_body(exc),
        )
    emit_advanced_auth_event(
        "success", mode="oidc", reason="login_completed", user_id=str(user.id)
    )
    response = RedirectResponse(url=transaction.return_path, status_code=302)
    _attach_session_cookie(response, token, mode="oidc")
    response.delete_cookie(key="moonmind_oidc_state", path="/api/v1/oidc/callback")
    return response


async def _admit_oidc_identity(identity, config):  # type: ignore[no-untyped-def]
    """Resolve-or-provision one UUID through the #4119 authority.

    Returning users keep their UUID and current local active/admin flags;
    email changes never transfer ownership (email-taken fails closed);
    unknown users enroll only under the explicit admission policy and never
    as superusers.
    """
    from sqlalchemy import select

    from api_service.db.base import get_async_session_context
    from api_service.db.models import User
    from api_service.services.identity_service import (
        ControlledEnrollmentRequiredError,
    )
    from moonmind.security.advanced_identity_4124 import (
        EnrollmentRequiredError,
        evaluate_oidc_enrollment,
    )

    async with get_async_session_context() as session:
        from api_service.services.identity_service import (
            get_or_create_user_for_identity,
            resolve_user_id_for_identity,
        )

        existing_id = await resolve_user_id_for_identity(
            session, identity.issuer, identity.subject
        )
        account = await session.get(User, existing_id) if existing_id else None
        email_taken = False
        if identity.email:
            owner = (
                await session.execute(
                    select(User.id).where(User.email == identity.email)
                )
            ).scalars().first()
            email_taken = owner is not None and (
                existing_id is None or owner != existing_id
            )
        decision = evaluate_oidc_enrollment(
            existing_user_id=existing_id,
            account=account,
            email_taken_by_other=email_taken,
            allow_unknown_users=config.allow_unknown_users,
        )
        if not decision.allowed:
            if decision.code == "inactive":
                from fastapi import HTTPException as _HTTP

                raise _HTTP(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail={"code": "inactive", "message": decision.detail},
                )
            raise EnrollmentRequiredError(decision.code, decision.detail)
        if existing_id is not None and account is not None:
            if identity.email is not None and account.email != identity.email:
                if email_taken:
                    raise EnrollmentRequiredError(
                        "enrollment_required",
                        "email is owned by a different user; explicit operator "
                        "enrollment is required, automatic linking is refused",
                    )
                account.email = identity.email
                await session.flush()
            await session.commit()
            return account
        try:
            user, _ = await get_or_create_user_for_identity(
                session,
                identity.issuer,
                identity.subject,
                email=identity.email,
            )
            await session.commit()
            return user
        except ControlledEnrollmentRequiredError as exc:
            await session.rollback()
            raise EnrollmentRequiredError(exc.code, str(exc)) from exc


async def _mint_session_for_user(user, *, mode: str, identity=None) -> str:  # type: ignore[no-untyped-def]
    from api_service.auth_providers import build_moonmind_control_plane_config
    from api_service.db.base import get_async_session_context
    from api_service.services.session_store import (
        DbAccountStore,
        DbRevocationStore,
        mint_and_record_session,
    )

    config = build_moonmind_control_plane_config(mode=mode)
    async with get_async_session_context() as session:
        # Resolve through the live UUID (the session subject stays the
        # stable MoonMind UUID, never the external subject or email).
        account_store = DbAccountStore(session)
        revocation = DbRevocationStore(session)

        from api_service.db.models import User

        row = await session.get(User, user.id)
        if row is None or not row.is_active:
            from fastapi import HTTPException as _HTTP

            raise _HTTP(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "inactive", "message": "account inactive"},
            )
        token, _ = await mint_and_record_session(
            await _session_identity_for_user(row, validated_identity=identity),
            account_store,
            revocation,
            config,
            session,
        )
        return token


async def _session_identity_for_user(user, validated_identity=None):  # type: ignore[no-untyped-def]
    """Validated identity shape that resolves to the live UUID.

    When the just-validated ``(issuer, subject)`` is supplied, the mint path
    re-resolves exactly that pair: removal of the authenticated mapping in
    the admission-to-mint window fails closed instead of falling back to an
    unrelated mapping or the email-based accounts fallback. External
    identities resolve through their verified (issuer, subject) pair; the
    mint path re-resolves the same mapping so a concurrent disable/reset
    bump between admission and mint still fails closed at validation
    instead of resurrecting authority.
    """

    from sqlalchemy import select

    from api_service.db.base import get_async_session_context
    from api_service.db.models import UserExternalIdentity
    from moonmind.security import omnigent_auth_qualification as q

    if validated_identity is not None:
        async with get_async_session_context() as session:
            exact = (
                await session.execute(
                    select(UserExternalIdentity).where(
                        UserExternalIdentity.user_id == user.id,
                        UserExternalIdentity.issuer
                        == validated_identity.issuer,
                        UserExternalIdentity.subject
                        == validated_identity.subject,
                    )
                )
            ).scalars().first()
        if exact is None:
            from fastapi import HTTPException as _HTTP

            raise _HTTP(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "identity_changed",
                    "message": "authenticated identity no longer mapped; refusing mint",
                },
            )
        return q.ValidatedIdentity(
            issuer=exact.issuer, subject=exact.subject, email=user.email
        )
    async with get_async_session_context() as session:
        mappings = (
            await session.execute(
                select(UserExternalIdentity).where(
                    UserExternalIdentity.user_id == user.id
                )
            )
        ).scalars().all()
    if mappings:
        first = sorted(mappings, key=lambda m: (m.issuer, m.subject))[0]
        return q.ValidatedIdentity(
            issuer=first.issuer, subject=first.subject, email=user.email
        )
    return q.ValidatedIdentity(
        issuer="moonmind-accounts", subject=user.email, email=user.email
    )


def _attach_session_cookie(response: RedirectResponse, token: str, *, mode: str) -> None:
    import os

    from api_service.auth_providers import build_moonmind_control_plane_config
    from moonmind.security.session_authority_4121 import build_set_cookie_header

    config = build_moonmind_control_plane_config(mode=mode)
    header = build_set_cookie_header(
        token=token,
        cookie_name=config.cookie_name,
        require_secure_cookies=config.require_secure_cookies,
        max_age_seconds=config.session_ttl_seconds,
    )
    response.headers.append("Set-Cookie", header)
    if os.environ.get("MOONMIND_REQUIRE_SECURE_COOKIES", "1") == "0":
        logger.debug("OIDC session cookie issued under loopback development policy")


@router.post("/logout")
async def oidc_logout(request: Request) -> JSONResponse:
    """Invalidate the MoonMind session even when IdP logout fails.

    CSRF-protected when a session cookie is presented (#4121 rules). The
    optional IdP logout URL is returned best-effort for the browser; its
    failure never resurrects the revoked local session.
    """

    _require_oidc_mode()
    from moonmind.security.advanced_identity_4124 import (
        build_logout_plan,
        emit_advanced_auth_event,
        resolve_oidc_provider_config,
    )

    try:
        from api_service.auth_providers import build_moonmind_control_plane_config

        control_plane = build_moonmind_control_plane_config(mode="oidc")
        await _enforce_logout_csrf(request, control_plane)
        jti, user_id = await _revoke_request_session(request, control_plane)
        try:
            config = resolve_oidc_provider_config()
            metadata = await _metadata_cache(request).get(config, HttpxOIDCTransport())
            plan = build_logout_plan(config, metadata)
        except Exception:
            plan = None
    except HTTPException:
        raise
    except Exception as exc:
        emit_advanced_auth_event("denial", mode="oidc", reason="logout_failed")
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED, content=_login_error_body(exc)
        )
    emit_advanced_auth_event(
        "revocation",
        mode="oidc",
        reason="logout",
        user_id=str(user_id) if user_id else None,
    )
    from moonmind.security.session_authority_4121 import build_clear_cookie_header

    try:
        control_plane_for_clear = build_moonmind_control_plane_config(mode="oidc")
    except Exception:
        control_plane_for_clear = control_plane
    clearing = build_clear_cookie_header(
        cookie_name=control_plane_for_clear.cookie_name,
        require_secure_cookies=control_plane_for_clear.require_secure_cookies,
    )
    body: dict[str, Any] = {
        "code": "logged_out",
        "note": (
            "MoonMind session revoked; IdP-wide logout is best-effort and not "
            "guaranteed."
        ),
    }
    if plan is not None and plan.idp_logout_configured:
        body["idp_logout_url"] = plan.idp_logout_url
    response = JSONResponse(status_code=200, content=body)
    response.headers.append("Set-Cookie", clearing)
    return response


async def _enforce_logout_csrf(request: Request, control_plane) -> None:  # type: ignore[no-untyped-def]
    import os

    from moonmind.security.session_authority_4121 import enforce_csrf_origin

    cookie_present = bool(request.cookies.get(control_plane.cookie_name))
    enforce_csrf_origin(
        method="POST",
        cookie_present=cookie_present,
        origin=request.headers.get("origin"),
        referer=request.headers.get("referer"),
        host=(request.headers.get("host") or "").split(":")[0] or None,
        base_url=os.environ.get("MOONMIND_PUBLIC_BASE_URL", ""),
    )


async def _revoke_request_session(request: Request, control_plane):  # type: ignore[no-untyped-def]
    """Revoke the presenting session; returns ``(jti, user_id)``.

    The session is fully validated (signature, issuer, audience, purpose,
    expiry, revocation, principal status) before any revocation or audit.
    Attacker-crafted JWTs fail closed here and never produce a ``logged_out``
    response or a ``revocation`` audit event for an arbitrary UUID/JTI.
    """

    import jwt as _jwt

    from api_service.db.base import get_async_session_context
    from api_service.services.session_store import (
        DbAccountStore,
        DbRevocationStore,
    )
    from moonmind.security import omnigent_auth_qualification as q

    token = request.cookies.get(control_plane.cookie_name) or (
        (request.headers.get("authorization", "") or "").removeprefix("Bearer ").strip()
        or None
    )
    if not token:
        from moonmind.security.advanced_identity_4124 import OIDCLoginError

        raise OIDCLoginError("auth_required", "no session to log out")
    async with get_async_session_context() as session:
        account_store = DbAccountStore(session)
        store = DbRevocationStore(session)
        try:
            account = await q.validate_moonmind_session(
                token, account_store, store, control_plane
            )
        except Exception as exc:
            from moonmind.security.advanced_identity_4124 import OIDCLoginError

            raise OIDCLoginError("auth_invalid", "invalid session") from exc
        # Validation passed: extract the verified jti for ownership-checked
        # revocation. Forged material was already rejected above, so reading
        # the jti here cannot be influenced by an attacker-crafted JWT.
        try:
            verified = _jwt.decode(token, options={"verify_signature": False})
            jti = str(verified.get("jti", ""))
        except Exception as exc:
            from moonmind.security.advanced_identity_4124 import OIDCLoginError

            raise OIDCLoginError("auth_invalid", "session without identity") from exc
        if not jti:
            from moonmind.security.advanced_identity_4124 import OIDCLoginError

            raise OIDCLoginError("auth_invalid", "session without identity") from None
        user_uuid = account.user_id
        # Ownership-checked revoke: a session row owned by another user is
        # never touched through this path.
        await store.revoke_session_for_user(jti, user_uuid, reason="logout")
        await session.commit()
    return jti, user_uuid


# ---------------------------------------------------------------------------
# Trusted-header request identity (header mode; no login routes by design)
# ---------------------------------------------------------------------------


def _request_peer_host(request: Request) -> str:
    client = request.client
    return (getattr(client, "host", "") or "").strip()


def _peer_trusted(peer: str, trusted_proxies: tuple[str, ...]) -> bool:
    # Fail closed on an empty allowlist: trusting every direct client would
    # let any caller impersonate any proxy subject.
    if not trusted_proxies:
        return False
    if not peer:
        return False
    lowered = peer.strip().lower()
    for entry in trusted_proxies:
        candidate = entry.strip().lower()
        if not candidate:
            continue
        if lowered == candidate:
            return True
        try:
            network = ipaddress.ip_network(candidate, strict=False)
        except ValueError:
            continue
        try:
            if ipaddress.ip_address(peer) in network:
                return True
        except ValueError:
            continue
    return False


async def get_trusted_proxy_user(request: Request):  # type: ignore[no-untyped-def]
    """Resolve the proxy-asserted user through the shared authority.

    Enforces the explicitly trusted ingress (deployment flag plus peer
    proxy membership), single asserted header, exact #4119 mapping, and
    live active status. Direct API and alternative-listener bypasses,
    duplicated/malformed headers, missing/reserved/unknown identities, and
    attacker-controlled forwarded host/proto values all fail closed. Never
    falls back to ``local``/``__public__`` and never merges by email.
    """
    from moonmind.security.advanced_identity_4124 import (
        TrustedProxyError,
        evaluate_trusted_proxy_enrollment,
        extract_trusted_proxy_identity,
        resolve_trusted_proxy_config,
    )
    from moonmind.security.auth_modes_4120 import get_request_production_mode

    if get_request_production_mode() != "header":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "header_not_enabled",
                "message": "Trusted-header mode is not active.",
            },
        )
    try:
        config = resolve_trusted_proxy_config()
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "misconfigured", "message": str(exc)},
        ) from exc
    peer_ok = _peer_trusted(_request_peer_host(request), config.trusted_proxies)
    try:
        issuer, subject = extract_trusted_proxy_identity(
            dict(request.headers), config, trusted_ingress=peer_ok
        )
    except TrustedProxyError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc
    # Attacker-controlled forwarded host/proto never decides trust: when
    # present they must validate against the configured base URL/proxies.
    forwarded_host = request.headers.get("x-forwarded-host")
    forwarded_proto = request.headers.get("x-forwarded-proto")
    if forwarded_host or forwarded_proto:
        import os

        from moonmind.security.auth_modes_4120 import validate_public_base_url

        base_url = os.environ.get("MOONMIND_PUBLIC_BASE_URL", "").strip()
        try:
            validate_public_base_url(
                base_url,
                trusted_proxies=list(config.trusted_proxies),
                forwarded_host=forwarded_host,
                forwarded_proto=forwarded_proto,
            )
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"code": "untrusted_forwarded", "message": str(exc)},
            ) from exc
    from api_service.db.base import get_async_session_context
    from api_service.db.models import User
    from api_service.services.identity_service import resolve_user_id_for_identity

    async with get_async_session_context() as session:
        existing_id = await resolve_user_id_for_identity(session, issuer, subject)
        account = await session.get(User, existing_id) if existing_id else None
        decision = evaluate_trusted_proxy_enrollment(
            existing_user_id=existing_id,
            account=account,
            email_taken_by_other=False,
            config=config,
        )
        if not decision.allowed:
            if decision.code == "inactive":
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail={"code": "inactive", "message": decision.detail},
                )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": decision.code, "message": decision.detail},
            )
        if account is None:
            from api_service.services.identity_service import (
                get_or_create_user_for_identity,
            )

            account, _ = await get_or_create_user_for_identity(
                session, issuer, subject
            )
            await session.commit()
        if not account.is_active:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "inactive", "message": "account inactive"},
            )
        session.expunge(account)
        return account


@router.get("/header-whoami")
async def header_whoami(user=Depends(get_trusted_proxy_user)) -> Mapping[str, Any]:  # type: ignore[no-untyped-def]
    """Prove the trusted-header mapping without exposing authority."""
    return {"user_id": str(user.id)}
