"""Built-in accounts HTTP boundary (MoonLadderStudios/MoonMind#4122).

Parent: #4116. Depends on #4118/#4119/#4120/#4121. Plan coverage: K3
account lifecycle in ``docs/tmp/KeycloakRemovalPlan.md``; declarative
target in ``docs/Security/AuthenticationContracts.md`` §7.

This router is the request-level surface over the already-landed #4122
layers: hermetic lifecycle rules in
``moonmind/security/account_lifecycle_4122.py`` (bootstrap/invite/
recovery capabilities, member administration, last-admin protection,
redacted audit) and durable User-table wiring in
``api_service/services/account_lifecycle_store_4122.py``. It adds only
the HTTP seams those layers intentionally leave to this owner:

* protected first-owner setup, login, logout, current-account status,
  password change, invitation-only enrollment, operator-initiated
  password recovery, and administrator member management;
* login resolves the login name to the existing ``User`` UUID before
  issuance and mints through the shared #4121 session authority
  (``mint_and_record_session``) — never a parallel session system;
* expensive password hashing/verification runs off the event loop
  through the qualified #4118 interface
  (``omnigent_auth_qualification.qualify_password_hash`` /
  ``verify_account_password``);
* bounded abuse controls (in-process rate limiting with 429) and
  consistent errors that do not leak account existence (unknown login
  and wrong password both fail as 401 ``auth_invalid``);
* browser responses carry the session only as an ``HttpOnly`` cookie —
  JSON bodies never contain session/refresh material, and requests
  cannot opt into upstream CLI refresh/token responses via extra
  fields (refresh-shaped keys are rejected at the boundary).

Mode gating: every endpoint fails closed with 404 ``auth_invalid``
unless the classified production mode is ``accounts``, so ``oidc``,
``header``, and ``disabled`` deployments never advertise these
journeys. Cookie-authenticated mutations clear the shared CSRF/origin
boundary. No SMTP service, no second account database, no hardcoded
password, and no parallel unguarded register/reset path.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Mapping

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.auth_providers import _strict_current_user
from api_service.db.base import get_async_session
from api_service.db.models import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/accounts", tags=["Accounts4122"])

# ---------------------------------------------------------------------------
# Mode gating + error contract (§8)
# ---------------------------------------------------------------------------


def _production_mode() -> str:
    from moonmind.security.auth_modes_4120 import get_request_production_mode

    return get_request_production_mode() or ""


def _error(status: int, code: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"code": code})


def _require_accounts_mode() -> JSONResponse | None:
    if _production_mode() != "accounts":
        return _error(404, "auth_invalid")
    return None


# Browser auth responses must not be cached (temporary credential
# delivery) and must never carry reusable token material in JSON.
_NO_STORE_HEADERS = {"Cache-Control": "no-store", "Pragma": "no-cache"}


def _auth_json(status: int, payload: Mapping[str, Any]) -> JSONResponse:
    from moonmind.security.session_authority_4121 import assert_no_token_in_json

    assert_no_token_in_json(payload)
    return JSONResponse(status_code=status, content=dict(payload), headers=dict(_NO_STORE_HEADERS))


# Refresh/CLI token surfaces the browser boundary never honors. A browser
# request carrying any of these keys is rejected instead of being
# reinterpreted as an upstream grant/delegated flow.
_REFRESH_SURFACE_KEYS = frozenset(
    {
        "refresh_token",
        "grant_id",
        "grant_type",
        "scope",
        "id_token",
        "session_token",
        "moonmind_session",
        "mm_session",
        "set_cookie",
    }
)


def _reject_refresh_surface(body: Mapping[str, Any]) -> JSONResponse | None:
    present = [k for k in body if str(k).lower() in _REFRESH_SURFACE_KEYS]
    if present:
        logger.info("auth_event mode=accounts reason=refresh_surface_rejected")
        return _error(401, "auth_invalid")
    return None


# ---------------------------------------------------------------------------
# Bounded abuse controls (in-process, mirrors the integration-callback
# limiter: burst protection, no new infrastructure)
# ---------------------------------------------------------------------------

_ACCOUNTS_RATE_LIMIT = 20
_ACCOUNTS_RATE_WINDOW_SECONDS = 60


@dataclass(slots=True)
class _AccountsRateBucket:
    timestamps: deque[float]


class _AccountsRateLimiter:
    """Simple in-process limiter for login/setup/invite/reset surfaces."""

    def __init__(self) -> None:
        self._buckets: dict[str, _AccountsRateBucket] = {}

    def allow(self, *, key: str, limit: int, window_seconds: int) -> bool:
        now = time.monotonic()
        bucket = self._buckets.get(key)
        if bucket is None:
            bucket = _AccountsRateBucket(timestamps=deque())
            self._buckets[key] = bucket
        floor = now - window_seconds
        while bucket.timestamps and bucket.timestamps[0] < floor:
            bucket.timestamps.popleft()
        if not bucket.timestamps and len(self._buckets) > 4096:
            self._buckets.pop(key, None)
            bucket = _AccountsRateBucket(timestamps=deque())
            self._buckets[key] = bucket
        if len(bucket.timestamps) >= limit:
            return False
        bucket.timestamps.append(now)
        return True


_accounts_rate_limiter = _AccountsRateLimiter()


def _rate_limit_key(request: Request, endpoint: str, login: str = "") -> str:
    host = ""
    try:
        host = (request.client.host if request.client else "") or ""
    except Exception:
        host = ""
    who = (login or "").strip().lower() or host or "unknown"
    return f"{endpoint}:{who}"


def _check_rate_limit(request: Request, endpoint: str, login: str = "") -> JSONResponse | None:
    if not _accounts_rate_limiter.allow(
        key=_rate_limit_key(request, endpoint, login),
        limit=_ACCOUNTS_RATE_LIMIT,
        window_seconds=_ACCOUNTS_RATE_WINDOW_SECONDS,
    ):
        logger.info("auth_event mode=accounts reason=rate_limited endpoint=%s", endpoint)
        return JSONResponse(status_code=429, content={"code": "rate_limited"})
    return None


# ---------------------------------------------------------------------------
# Lifecycle key material (deployment-owned, never logged or echoed)
# ---------------------------------------------------------------------------


def resolve_lifecycle_key(session_secret: bytes) -> bytes:
    """Resolve the HMAC key for bootstrap/invite/recovery capabilities.

    An explicit operator-held ``MOONMIND_ACCOUNTS_KEY`` (at least 32
    bytes) is preferred; otherwise a domain-separated key is derived
    from the durable session secret (single owner:
    ``derive_lifecycle_key`` in the hermetic lifecycle module — never
    the session key itself). Short explicit material fails closed.
    """
    from moonmind.security.account_lifecycle_4122 import (
        MIN_KEY_BYTES,
        derive_lifecycle_key,
    )

    explicit = (os.environ.get("MOONMIND_ACCOUNTS_KEY") or "").strip()
    if explicit:
        raw = explicit.encode("utf-8")
        if len(raw) < MIN_KEY_BYTES:
            raise ValueError("MOONMIND_ACCOUNTS_KEY must be at least 32 bytes")
        return raw
    if not isinstance(session_secret, (bytes, bytearray)) or len(session_secret) < MIN_KEY_BYTES:
        raise ValueError("session secret unavailable for lifecycle key derivation")
    return derive_lifecycle_key(bytes(session_secret))


# ---------------------------------------------------------------------------
# Password policy + qualified off-loop primitives (#4118)
# ---------------------------------------------------------------------------

_MIN_PASSWORD_CHARS = 12
_MAX_PASSWORD_CHARS = 256


def _check_password_policy(password: str) -> JSONResponse | None:
    if not isinstance(password, str) or not (_MIN_PASSWORD_CHARS <= len(password) <= _MAX_PASSWORD_CHARS):
        return _error(422, "password_rejected")
    return None


async def _hash_password(password: str) -> str:
    from moonmind.security.omnigent_auth_qualification import qualify_password_hash

    return await asyncio.to_thread(qualify_password_hash, password)


async def _verify_password(plaintext: str, password_hash: str) -> bool:
    from moonmind.security.omnigent_auth_qualification import verify_account_password

    return await asyncio.to_thread(verify_account_password, plaintext, password_hash)


async def _maybe_rehash(session: AsyncSession, user: User, plaintext: str, stored: str) -> None:
    """Refresh a compatible hash whose parameters have aged out."""
    try:
        from omnigent.server.passwords import needs_rehash
    except Exception:
        return
    try:
        stale = await asyncio.to_thread(needs_rehash, stored)
    except Exception:
        return
    if not stale:
        return
    try:
        user.hashed_password = await _hash_password(plaintext)
        await session.flush()
    except Exception:
        logger.info("auth_event mode=accounts reason=rehash_skipped")


# ---------------------------------------------------------------------------
# Session issuance through the shared #4121 authority
# ---------------------------------------------------------------------------


async def _issue_session_cookie(
    session: AsyncSession, user: User, *, login: str
) -> str:
    """Mint a session for an already-resolved user; return Set-Cookie value."""
    from api_service.auth_providers import build_moonmind_control_plane_config
    from api_service.services.session_store import (
        DbAccountStore,
        DbRevocationStore,
        mint_and_record_session,
    )
    from moonmind.security import omnigent_auth_qualification as q
    from moonmind.security.session_authority_4121 import build_set_cookie_header

    config = build_moonmind_control_plane_config()
    identity = q.ValidatedIdentity(issuer="moonmind-accounts", subject=login)
    token, _ = await mint_and_record_session(
        identity, DbAccountStore(session), DbRevocationStore(session), config, session
    )
    return build_set_cookie_header(
        token=token,
        cookie_name=config.cookie_name,
        require_secure_cookies=config.require_secure_cookies,
        max_age_seconds=config.session_ttl_seconds,
    )


def _lifecycle_key_for_request() -> bytes:
    from api_service.auth_providers import build_moonmind_control_plane_config

    config = build_moonmind_control_plane_config()
    return resolve_lifecycle_key(config.cookie_secret)


def _csrf_gate(request: Request) -> JSONResponse | None:
    """Enforce the shared CSRF/origin boundary where the cookie is the credential.

    Applied only on endpoints where the presented session cookie
    authorizes the mutation (logout, password change, invite/admin
    operations): a cross-site request carrying the victim's cookie must
    present a same-origin ``Origin``/``Referer``. Cookie-less callers
    pass through.
    """
    try:
        presented = bool(getattr(request, "cookies", None))
    except Exception:
        presented = False
    if not presented:
        return None
    from api_service.auth_providers import _enforce_cookie_csrf_origin
    from api_service.auth_providers import _session_http_exception

    try:
        _enforce_cookie_csrf_origin(request)
    except Exception as exc:  # noqa: BLE001 - fail closed with stable codes
        if hasattr(exc, "status_code"):
            detail = getattr(exc, "detail", None)
            code = detail.get("code") if isinstance(detail, dict) else None
            return _error(int(getattr(exc, "status_code", 401) or 401), str(code or "auth_invalid"))
        return _session_http_exception(exc)
    return None


def _require_admin(user: User) -> JSONResponse | None:
    if not bool(getattr(user, "is_active", False)):
        return _error(403, "inactive")
    if not bool(getattr(user, "is_superuser", False)):
        return _error(403, "forbidden")
    return None


# ---------------------------------------------------------------------------
# Request models (closed shapes: no refresh/token surfaces accepted)
# ---------------------------------------------------------------------------


class SetupRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    login: str = Field(min_length=1, max_length=320)
    password: str = Field(min_length=1, max_length=512)
    bootstrap_token: str = Field(min_length=1, max_length=8192)


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    login: str = Field(min_length=1, max_length=320)
    password: str = Field(min_length=1, max_length=512)


class PasswordChangeRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    current_password: str = Field(min_length=1, max_length=512)
    new_password: str = Field(min_length=1, max_length=512)


class InviteCreateRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    login: str = Field(min_length=1, max_length=320)


class EnrollRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    login: str = Field(min_length=1, max_length=320)
    password: str = Field(min_length=1, max_length=512)
    invite_token: str = Field(min_length=1, max_length=8192)


class RecoveryRequestRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    login: str = Field(min_length=1, max_length=320)


class RecoveryRedeemRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    login: str = Field(min_length=1, max_length=320)
    new_password: str = Field(min_length=1, max_length=512)
    recovery_token: str = Field(min_length=1, max_length=8192)


class MemberActionRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    target_login: str = Field(min_length=1, max_length=320)
    action: str = Field(min_length=1, max_length=32)


# ---------------------------------------------------------------------------
# Setup / login / logout / status
# ---------------------------------------------------------------------------


@router.post("/setup")
async def accounts_setup(
    request: Request, body: SetupRequest, session: AsyncSession = Depends(get_async_session)
):
    """Claim protected first-owner setup with an operator-held capability."""
    gated = _require_accounts_mode()
    if gated is not None:
        return gated
    denied = _reject_refresh_surface(body.model_dump())
    if denied is not None:
        return denied
    limited = _check_rate_limit(request, "setup", body.login)
    if limited is not None:
        return limited
    # No CSRF gate: the bootstrap capability in the body is the authority;
    # a presented cookie is never consulted here.
    policy = _check_password_policy(body.password)
    if policy is not None:
        return policy
    login = (body.login or "").strip()
    if not login:
        return _error(401, "auth_invalid")
    try:
        key = _lifecycle_key_for_request()
    except ValueError:
        logger.warning("auth_event mode=accounts reason=setup_unavailable")
        return _error(503, "unavailable")
    try:
        from api_service.services.account_lifecycle_store_4122 import (
            claim_first_owner_with_token,
        )
        from moonmind.security.account_lifecycle_4122 import redacted_lifecycle_event

        hashed = await _hash_password(body.password)
        user = await claim_first_owner_with_token(
            session, token=body.bootstrap_token, key=key, login=login, hashed_password=hashed
        )
        cookie = await _issue_session_cookie(session, user, login=login)
        redacted_lifecycle_event("bootstrap", action="setup_ok", login=login)
        logger.info("auth_event mode=accounts reason=setup_ok")
        resp = _auth_json(201, {"ok": True})
        resp.headers["Set-Cookie"] = cookie
        return resp
    except Exception as exc:  # noqa: BLE001 - fail closed with stable codes
        try:
            await session.rollback()
        except Exception:
            pass
        code = getattr(exc, "code", None)
        if code in ("bootstrap_closed", "bootstrap_consumed"):
            logger.info("auth_event mode=accounts reason=setup_denied code=%s", code)
            return _error(401, "auth_invalid")
        logger.info("auth_event mode=accounts reason=setup_denied code=auth_invalid")
        return _error(401, "auth_invalid")


@router.post("/login")
async def accounts_login(
    request: Request, body: LoginRequest, session: AsyncSession = Depends(get_async_session)
):
    """Verify a password and mint a session for the existing UUID."""
    gated = _require_accounts_mode()
    if gated is not None:
        return gated
    denied = _reject_refresh_surface(body.model_dump())
    if denied is not None:
        return denied
    limited = _check_rate_limit(request, "login", body.login)
    if limited is not None:
        return limited
    # No CSRF gate: body credentials are the authority; a presented
    # (possibly stale) cookie is never consulted here.
    login = (body.login or "").strip()
    if not login or not body.password:
        return _error(401, "auth_invalid")
    try:
        result = await session.execute(select(User).where(User.email == login))
        user = result.scalars().first()
        stored = str(getattr(user, "hashed_password", "") or "") if user is not None else ""
        # Consistent errors: unknown logins fail exactly like wrong
        # passwords. Incompatible/missing hashes never assume silent
        # compatibility — they take the explicit reset path.
        if user is None or not stored:
            logger.info("auth_event mode=accounts reason=login_denied code=auth_invalid")
            return _error(401, "auth_invalid")
        from moonmind.security.omnigent_auth_qualification import (
            password_enrollment_required,
        )

        if password_enrollment_required(stored):
            logger.info("auth_event mode=accounts reason=enrollment_required")
            return _auth_json(403, {"code": "enrollment_required"})
        ok = await _verify_password(body.password, stored)
        if not ok:
            logger.info("auth_event mode=accounts reason=login_denied code=auth_invalid")
            return _error(401, "auth_invalid")
        if not bool(user.is_active):
            logger.info("auth_event mode=accounts reason=login_denied code=inactive")
            return _error(403, "inactive")
        await _maybe_rehash(session, user, body.password, stored)
        # Mint re-resolves the live row and fails closed when the
        # account was disabled concurrently: no usable session escapes.
        cookie = await _issue_session_cookie(session, user, login=login)
        logger.info("auth_event mode=accounts reason=login_ok")
        resp = _auth_json(200, {"ok": True})
        resp.headers["Set-Cookie"] = cookie
        return resp
    except Exception as exc:  # noqa: BLE001 - fail closed with stable codes
        try:
            await session.rollback()
        except Exception:
            pass
        from moonmind.security import omnigent_auth_qualification as q

        if isinstance(exc, q.ForbiddenError):
            return _error(403, getattr(exc, "code", "inactive") or "inactive")
        if isinstance(exc, q.UnavailableError):
            return _error(503, "unavailable")
        logger.info("auth_event mode=accounts reason=login_denied code=auth_invalid")
        return _error(401, "auth_invalid")


@router.post("/logout")
async def accounts_logout(
    request: Request, session: AsyncSession = Depends(get_async_session)
):
    """Revoke the presented browser session only.

    Browser logout revokes browser authority: it never cancels admitted
    Temporal work and never revokes independent machine credentials
    (only the ``moonmind_sessions`` row is touched).
    """
    gated = _require_accounts_mode()
    if gated is not None:
        return gated
    csrf = _csrf_gate(request)
    if csrf is not None:
        return csrf
    from api_service.auth_providers import build_moonmind_control_plane_config
    from moonmind.security.session_authority_4121 import build_clear_cookie_header

    try:
        config = build_moonmind_control_plane_config()
    except Exception:
        return _error(503, "unavailable")
    cookie_name = config.cookie_name
    token = request.cookies.get(cookie_name)
    if not token:
        resp = _auth_json(200, {"ok": True})
        resp.headers["Set-Cookie"] = build_clear_cookie_header(
            cookie_name=cookie_name, require_secure_cookies=config.require_secure_cookies
        )
        return resp
    try:
        import jwt as _jwt

        claims = _jwt.decode(token, options={"verify_signature": False})
        from uuid import UUID as _UUID

        user_id = _UUID(str(claims.get("sub") or ""))
        jti = str(claims.get("jti") or "")
        if not jti:
            raise ValueError("session without jti")
        from api_service.services.session_store import DbRevocationStore

        await DbRevocationStore(session).revoke_session_for_user(jti, user_id, reason="logout")
        await session.commit()
    except Exception:
        try:
            await session.rollback()
        except Exception:
            pass
    logger.info("auth_event mode=accounts reason=logout")
    resp = _auth_json(200, {"ok": True})
    resp.headers["Set-Cookie"] = build_clear_cookie_header(
        cookie_name=cookie_name, require_secure_cookies=config.require_secure_cookies
    )
    return resp


@router.get("/me")
async def accounts_me(user: User = Depends(_strict_current_user)):
    """Return the current account status (never password or token material)."""
    gated = _require_accounts_mode()
    if gated is not None:
        return gated
    return _auth_json(
        200,
        {
            "ok": True,
            "user_id": str(user.id),
            "login": str(user.email),
            "is_active": bool(user.is_active),
            "is_superuser": bool(user.is_superuser),
        },
    )


@router.post("/password/change")
async def accounts_password_change(
    request: Request,
    body: PasswordChangeRequest,
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(_strict_current_user),
):
    """Change the caller's password and invalidate other sessions."""
    gated = _require_accounts_mode()
    if gated is not None:
        return gated
    denied = _reject_refresh_surface(body.model_dump())
    if denied is not None:
        return denied
    limited = _check_rate_limit(request, "password-change", str(user.email))
    if limited is not None:
        return limited
    csrf = _csrf_gate(request)
    if csrf is not None:
        return csrf
    policy = _check_password_policy(body.new_password)
    if policy is not None:
        return policy
    try:
        stored = str(getattr(user, "hashed_password", "") or "")
        if not stored or not await _verify_password(body.current_password, stored):
            logger.info("auth_event mode=accounts reason=password_change_denied")
            return _error(401, "auth_invalid")
        from api_service.services.session_store import DbRevocationStore

        user.hashed_password = await _hash_password(body.new_password)
        await session.flush()
        # Durable revocation bump: sessions issued before this change
        # stop validating; the caller receives a fresh session below.
        await DbRevocationStore(session).revoke_all_for_user(user.id)
        cookie = await _issue_session_cookie(session, user, login=str(user.email))
        logger.info("auth_event mode=accounts reason=password_changed")
        resp = _auth_json(200, {"ok": True})
        resp.headers["Set-Cookie"] = cookie
        return resp
    except Exception:  # noqa: BLE001 - fail closed with stable codes
        try:
            await session.rollback()
        except Exception:
            pass
        return _error(401, "auth_invalid")


# ---------------------------------------------------------------------------
# Invitation-only enrollment (no open registration, no SMTP)
# ---------------------------------------------------------------------------


@router.post("/invites")
async def accounts_invite_create(
    request: Request,
    body: InviteCreateRequest,
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(_strict_current_user),
):
    """Mint an expiring one-use invitation (active administrators only).

    Invitations confer plain membership, never administrator authority:
    privilege changes go through the protected member actions below.
    Delivery is the authenticated operator channel (no SMTP service):
    the token is returned only on this admin-only endpoint and is never
    echoed through ordinary account APIs.
    """
    gated = _require_accounts_mode()
    if gated is not None:
        return gated
    admin = _require_admin(user)
    if admin is not None:
        return admin
    denied = _reject_refresh_surface(body.model_dump())
    if denied is not None:
        return denied
    limited = _check_rate_limit(request, "invite-create", str(user.email))
    if limited is not None:
        return limited
    csrf = _csrf_gate(request)
    if csrf is not None:
        return csrf
    login = (body.login or "").strip()
    if not login:
        return _error(422, "password_rejected")
    try:
        key = _lifecycle_key_for_request()
    except ValueError:
        return _error(503, "unavailable")
    try:
        from moonmind.security.account_lifecycle_4122 import (
            mint_invite,
            redacted_lifecycle_event,
        )

        token = mint_invite(login, key=key)
        redacted_lifecycle_event("invite", action="minted", login=login)
        logger.info("auth_event mode=accounts reason=invite_minted")
        return _auth_json(201, {"ok": True, "invite_token": token})
    except Exception:
        return _error(401, "auth_invalid")


@router.post("/enroll")
async def accounts_enroll(
    request: Request, body: EnrollRequest, session: AsyncSession = Depends(get_async_session)
):
    """Redeem an invitation and enroll exactly one member account."""
    gated = _require_accounts_mode()
    if gated is not None:
        return gated
    denied = _reject_refresh_surface(body.model_dump())
    if denied is not None:
        return denied
    limited = _check_rate_limit(request, "enroll", body.login)
    if limited is not None:
        return limited
    # No CSRF gate: the invitation capability is the authority; a
    # presented cookie is never consulted here.
    policy = _check_password_policy(body.password)
    if policy is not None:
        return policy
    login = (body.login or "").strip()
    if not login:
        return _error(401, "auth_invalid")
    try:
        key = _lifecycle_key_for_request()
    except ValueError:
        return _error(503, "unavailable")
    try:
        from api_service.services.account_lifecycle_store_4122 import (
            redeem_invite_with_token,
        )
        from moonmind.security.account_lifecycle_4122 import redacted_lifecycle_event

        hashed = await _hash_password(body.password)
        user = await redeem_invite_with_token(
            session, token=body.invite_token, key=key, login=login, hashed_password=hashed
        )
        cookie = await _issue_session_cookie(session, user, login=login)
        redacted_lifecycle_event("invite", action="redeemed", login=login)
        logger.info("auth_event mode=accounts reason=enroll_ok")
        resp = _auth_json(201, {"ok": True})
        resp.headers["Set-Cookie"] = cookie
        return resp
    except Exception as exc:  # noqa: BLE001 - fail closed with stable codes
        try:
            await session.rollback()
        except Exception:
            pass
        code = getattr(exc, "code", None)
        if code == "email_taken":
            # Already enrolled (including a lost-acknowledgment retry
            # after success): the login path owns the retry, never a
            # duplicate profile.
            logger.info("auth_event mode=accounts reason=enroll_email_taken")
            return _auth_json(409, {"code": "email_taken"})
        logger.info("auth_event mode=accounts reason=enroll_denied")
        return _error(401, "auth_invalid")


# ---------------------------------------------------------------------------
# Operator-initiated password recovery (no SMTP, no privilege change)
# ---------------------------------------------------------------------------


@router.post("/recovery/request")
async def accounts_recovery_request(
    request: Request,
    body: RecoveryRequestRequest,
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(_strict_current_user),
):
    """Mint a short-lived one-use recovery capability (admins only)."""
    gated = _require_accounts_mode()
    if gated is not None:
        return gated
    admin = _require_admin(user)
    if admin is not None:
        return admin
    denied = _reject_refresh_surface(body.model_dump())
    if denied is not None:
        return denied
    limited = _check_rate_limit(request, "recovery-request", str(user.email))
    if limited is not None:
        return limited
    csrf = _csrf_gate(request)
    if csrf is not None:
        return csrf
    login = (body.login or "").strip()
    if not login:
        return _error(422, "password_rejected")
    try:
        key = _lifecycle_key_for_request()
    except ValueError:
        return _error(503, "unavailable")
    try:
        result = await session.execute(select(User.id).where(User.email == login))
        if result.scalars().first() is None:
            # Admins can already enumerate membership, so an explicit
            # unknown-login code here leaks nothing new.
            return _auth_json(404, {"code": "unknown_login"})
        from moonmind.security.account_lifecycle_4122 import (
            mint_recovery_capability,
            redacted_lifecycle_event,
        )

        token = mint_recovery_capability(login, key=key)
        redacted_lifecycle_event("recovery", action="minted", login=login)
        logger.info("auth_event mode=accounts reason=recovery_minted")
        return _auth_json(201, {"ok": True, "recovery_token": token})
    except Exception:
        return _error(401, "auth_invalid")


@router.post("/recovery/redeem")
async def accounts_recovery_redeem(
    request: Request, body: RecoveryRedeemRequest, session: AsyncSession = Depends(get_async_session)
):
    """Redeem an operator-held recovery capability and rotate the password.

    The capability itself is the authority (operator-held, expiring,
    one-use, login-bound), so this endpoint is callable without an
    active session — the stranded-administrator path. Active/admin
    flags are preserved exactly: recovery rotates credentials and
    invalidates prior sessions, it never promotes and never mints a
    session (the operator logs in normally afterwards).
    """
    gated = _require_accounts_mode()
    if gated is not None:
        return gated
    denied = _reject_refresh_surface(body.model_dump())
    if denied is not None:
        return denied
    limited = _check_rate_limit(request, "recovery-redeem", body.login)
    if limited is not None:
        return limited
    # No CSRF gate: the recovery capability is the authority (this is the
    # stranded-administrator path, callable without a session); a
    # presented cookie is never consulted here.
    policy = _check_password_policy(body.new_password)
    if policy is not None:
        return policy
    login = (body.login or "").strip()
    if not login:
        return _error(401, "auth_invalid")
    try:
        key = _lifecycle_key_for_request()
    except ValueError:
        return _error(503, "unavailable")
    try:
        from api_service.services.account_lifecycle_store_4122 import (
            redeem_recovery_for_user,
        )
        from api_service.services.session_store import DbRevocationStore
        from moonmind.security.account_lifecycle_4122 import (
            recovery_nonce_for_login,
            redacted_lifecycle_event,
        )

        hashed = await _hash_password(body.new_password)
        nonce = recovery_nonce_for_login(body.recovery_token, key=key, login=login)
        user = await redeem_recovery_for_user(session, login=login, nonce=nonce)
        user.hashed_password = hashed
        await session.flush()
        await DbRevocationStore(session).revoke_all_for_user(user.id)
        await session.commit()
        redacted_lifecycle_event("recovery", action="redeemed", login=login)
        logger.info("auth_event mode=accounts reason=recovery_ok")
        return _auth_json(200, {"ok": True})
    except Exception:  # noqa: BLE001 - fail closed with stable codes
        try:
            await session.rollback()
        except Exception:
            pass
        logger.info("auth_event mode=accounts reason=recovery_denied")
        return _error(401, "auth_invalid")


# ---------------------------------------------------------------------------
# Administrator member management (server-owned flags, last-admin safe)
# ---------------------------------------------------------------------------


@router.get("/members")
async def accounts_members(
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(_strict_current_user),
):
    """List membership with server-owned flags (active administrators only)."""
    gated = _require_accounts_mode()
    if gated is not None:
        return gated
    admin = _require_admin(user)
    if admin is not None:
        return admin
    result = await session.execute(select(User))
    members = [
        {
            "user_id": str(member.id),
            "login": str(member.email),
            "is_active": bool(member.is_active),
            "is_superuser": bool(member.is_superuser),
        }
        for member in result.scalars().all()
    ]
    return _auth_json(200, {"ok": True, "members": members})


@router.post("/members/action")
async def accounts_member_action(
    request: Request,
    body: MemberActionRequest,
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(_strict_current_user),
):
    """Apply one server-owned member action with last-admin protection."""
    gated = _require_accounts_mode()
    if gated is not None:
        return gated
    admin = _require_admin(user)
    if admin is not None:
        return admin
    denied = _reject_refresh_surface(body.model_dump())
    if denied is not None:
        return denied
    csrf = _csrf_gate(request)
    if csrf is not None:
        return csrf
    try:
        from api_service.services.account_lifecycle_store_4122 import (
            apply_member_action_transactional,
        )
        from moonmind.security.account_lifecycle_4122 import (
            redacted_lifecycle_event,
        )

        target = await apply_member_action_transactional(
            session,
            actor_login=str(user.email),
            target_login=(body.target_login or "").strip(),
            action=(body.action or "").strip(),
        )
        redacted_lifecycle_event(
            "admin", action=(body.action or "").strip(), login=str(target.email)
        )
        logger.info("auth_event mode=accounts reason=member_action_ok")
        return _auth_json(
            200,
            {
                "ok": True,
                "login": str(target.email),
                "is_active": bool(target.is_active),
                "is_superuser": bool(target.is_superuser),
            },
        )
    except Exception as exc:  # noqa: BLE001 - fail closed with stable codes
        try:
            await session.rollback()
        except Exception:
            pass
        from moonmind.security.account_lifecycle_4122 import AdminRefusedError as _Refused

        if isinstance(exc, _Refused):
            code = getattr(exc, "code", "forbidden") or "forbidden"
            status = 403 if code in ("forbidden", "last_admin_protected") else 422
            logger.info("auth_event mode=accounts reason=member_action_refused code=%s", code)
            return _auth_json(status, {"code": code})
        logger.info("auth_event mode=accounts reason=member_action_denied")
        return _error(401, "auth_invalid")
