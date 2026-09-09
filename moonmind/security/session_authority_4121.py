"""Application-bound session authority (MoonLadderStudios/MoonMind#4121).

Parent: #4116. Depends on #4118, #4119, #4120. Plan coverage: session and
browser security, K2/K4 in ``docs/tmp/KeycloakRemovalPlan.md``.

This module owns the one reusable MoonMind session authority that account,
OIDC, API, machine, and dashboard issues consume rather than implementing
independent validators. It composes the qualified #4118 session primitives
(``moonmind.security.omnigent_auth_qualification``) with #4119 UUID
resolution semantics and #4120 configuration, and adds the production
seams the qualification library intentionally leaves to this owner:

* bounded validation cache (size + TTL) that re-runs full validation on
  every hit, so revocation/status changes take effect across replicas
  within the declared bound;
* key-ring validation for rotation without indefinite acceptance of old
  application tokens;
* MoonMind-specific cookie issue/clear policy (``__Host-`` production
  cookie, separately named loopback development cookie);
* shared CSRF/origin enforcement for cookie-authenticated unsafe requests
  plus credentialed-CORS resolution that rejects wildcard+credentials;
* single credential-precedence resolution (missing vs. invalid vs.
  conflicting identities);
* stream re-authorization interface for WebSocket/SSE reconnects and
  active-stream termination within the agreed bound (no second store);
* redacted authentication observability with negative secret-leak
  assertions.

No new auth service, no redundant JWT implementation (all token crypto
goes through the #4118 primitives), no per-request Omnigent Server call,
and no upstream runtime/delegated/worker credential is ever accepted as a
MoonMind user. Browser logout revokes browser authority only: it never
revokes independent worker credentials and never cancels admitted
Temporal work (this authority touches only session/revocation state).
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Mapping
from urllib.parse import urlsplit

from moonmind.security import omnigent_auth_qualification as q

logger = logging.getLogger(__name__)

# Re-exported from the #4120 owner so callers have one import surface.
# ``__all__`` marks the re-export as intentional (consumed via this module
# by account/OIDC/API/dashboard callers) for unused-import linters.
__all__ = [
    "MOONMIND_DEV_COOKIE",
    "MOONMIND_PROD_COOKIE",
    "MOONMIND_SESSION_PURPOSE",
    "AuthConflictError",
    "AuthConfigError",
    "AuthInvalidError",
    "AuthRequiredError",
    "ForbiddenError",
    "UnavailableError",
    "UnsupportedSurfaceError",
]
from moonmind.security.omnigent_auth_qualification import (  # noqa: F401
    MOONMIND_DEV_COOKIE,
    MOONMIND_PROD_COOKIE,
    MOONMIND_SESSION_PURPOSE,
    AuthConflictError,
    AuthConfigError,
    AuthInvalidError,
    AuthRequiredError,
    ForbiddenError,
    UnavailableError,
    UnsupportedSurfaceError,
)

# Stream/authz bound from docs/Security/AuthenticationContracts.md §5
# (inventory §5.2): revocation commits take effect at the final replica
# re-authorization check within 5 minutes of the commit timestamp.
SESSION_REVOCATION_INTERVAL_SECONDS = 5 * 60

# Upstream runtime credentials are never MoonMind users, even when their
# JWT shape resembles an application session.
UPSTREAM_RUNTIME_MARKERS = frozenset(
    {
        "__Host-ap_session",
        "ap_session",
        "omnigent-runtime",
        "omnigent_runtime",
        "ap-runtime",
    }
)

_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})


# ---------------------------------------------------------------------------
# HTTP mapping: one place maps authority errors to status/code pairs
# ---------------------------------------------------------------------------


def http_status_for_error(exc: BaseException) -> tuple[int, str]:
    """Map an authority error to ``(status, code)`` per AuthenticationContracts §8."""
    if isinstance(exc, AuthRequiredError):
        return 401, "auth_required"
    if isinstance(exc, AuthConflictError):
        return 401, "auth_conflict"
    if isinstance(exc, AuthInvalidError):
        return 401, "auth_invalid"
    if isinstance(exc, UnsupportedSurfaceError):
        return 401, "auth_invalid"
    if isinstance(exc, ForbiddenError):
        return 403, getattr(exc, "code", "forbidden") or "forbidden"
    if isinstance(exc, UnavailableError):
        return 503, "unavailable"
    return 500, "internal"


# ---------------------------------------------------------------------------
# Single credential-precedence resolution
# ---------------------------------------------------------------------------


def resolve_credential_source(
    cookie_token: str | None, bearer_token: str | None
) -> str | None:
    """Return which credential governs: ``"cookie"``, ``"bearer"``, or ``None``.

    Both-present with differing values is a conflict signal for the caller
    to resolve principal-equality on (never silent preference). Empty
    strings count as missing. Upstream runtime cookie names presented as
    the cookie credential are rejected as invalid, never reinterpreted.
    """
    cookie = (cookie_token or "").strip() or None
    bearer = (bearer_token or "").strip() or None
    if cookie is None and bearer is None:
        return None
    if cookie is not None and bearer is not None and cookie != bearer:
        return "conflict"
    if cookie is not None:
        return "cookie"
    return "bearer"


async def resolve_session_user(
    *,
    cookie_token: str | None,
    bearer_token: str | None,
    account_store: q.AsyncAccountStore,
    revocation: q.SessionRevocationStore,
    config: q.MoonmindAuthConfig,
    optional: bool = False,
) -> q.AccountRecord | None:
    """Resolve the current session user with single-precedence semantics.

    * Missing optional credentials proceed (worker-tolerant paths authorize
      separately); missing at strict boundaries raises ``AuthRequiredError``.
    * Invalid presented credentials never silently fall back to another
      path; conflicting cookie/bearer identities raise
      ``AuthConflictError``.
    """
    source = resolve_credential_source(cookie_token, bearer_token)
    if source is None:
        if optional:
            return None
        raise AuthRequiredError()
    if source == "conflict":
        assert cookie_token and bearer_token
        return await q.resolve_current_user(
            cookie_token=cookie_token,
            bearer_token=bearer_token,
            account_store=account_store,
            revocation=revocation,
            config=config,
        )
    token = cookie_token if source == "cookie" else bearer_token
    assert token is not None
    return await q.resolve_current_user(
        cookie_token=token if source == "cookie" else None,
        bearer_token=token if source == "bearer" else None,
        account_store=account_store,
        revocation=revocation,
        config=config,
    )


# ---------------------------------------------------------------------------
# Bounded validation cache (size + TTL, never skips checks)
# ---------------------------------------------------------------------------


@dataclass
class BoundedSessionCache:
    """Bounded TTL credential cache that cannot bypass validation.

    Every call re-runs :func:`validate_moonmind_session` (signature,
    algorithm, issuer, audience/purpose, expiry, live revocation,
    generation, current principal status), including cache hits, so
    revocation/status changes take effect across replicas within
    ``ttl_seconds``. Entries are keyed by HMAC digest (never raw token)
    and evicted oldest-first beyond ``max_entries``.
    """

    config: q.MoonmindAuthConfig
    max_entries: int = 1024
    ttl_seconds: float = SESSION_REVOCATION_INTERVAL_SECONDS
    hits: int = 0
    misses: int = 0
    evictions: int = 0
    _entries: OrderedDict[str, float] = field(default_factory=OrderedDict)

    def __post_init__(self) -> None:
        if self.max_entries <= 0:
            raise AuthConfigError("max_entries must be positive")
        if self.ttl_seconds <= 0:
            raise AuthConfigError("ttl_seconds must be positive")

    def _key(self, token: str) -> str:
        return hmac.new(
            self.config.cookie_secret, token.encode(), hashlib.sha256
        ).hexdigest()

    def _prune(self, now_mono: float) -> None:
        expired = [k for k, exp in self._entries.items() if exp <= now_mono]
        for key in expired:
            del self._entries[key]
        while len(self._entries) > self.max_entries:
            self._entries.popitem(last=False)
            self.evictions += 1

    async def validate(
        self,
        token: str,
        account_store: q.AsyncAccountStore,
        revocation: q.SessionRevocationStore,
    ) -> q.AccountRecord:
        key = self._key(token)
        now_mono = time.monotonic()
        # Full validation first: hits never skip revocation/status checks.
        account = await q.validate_moonmind_session(
            token, account_store, revocation, self.config
        )
        cached_expiry = self._entries.get(key)
        if cached_expiry is not None and cached_expiry > now_mono:
            self.hits += 1
            self._entries.move_to_end(key)
        else:
            self.misses += 1
            self._entries[key] = now_mono + min(
                self.ttl_seconds,
                max(1.0, _token_remaining_seconds(token)),
            )
            self._entries.move_to_end(key)
        self._prune(now_mono)
        return account

    @property
    def size(self) -> int:
        return len(self._entries)


def _token_remaining_seconds(token: str) -> float:
    try:
        import jwt as _jwt

        payload = _jwt.decode(token, options={"verify_signature": False})
        return float(payload.get("exp", 0)) - time.time()
    except Exception:
        return 0.0


# ---------------------------------------------------------------------------
# Key-ring validation for rotation (no indefinite old-token acceptance)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SessionKeyRing:
    """Current + bounded previous signing secret for rotation.

    Minting always uses ``current``. Validation tries ``current`` first,
    then ``previous`` only while ``now < previous_expires_at``. After the
    overlap window, old-key tokens fail closed instead of being accepted
    indefinitely.
    """

    current: bytes
    previous: bytes | None = None
    previous_expires_at: float | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.current, (bytes, bytearray)) or len(self.current) < 32:
            raise AuthConfigError("key-ring current secret must be at least 32 bytes")
        if self.previous is not None:
            if len(self.previous) < 32:
                raise AuthConfigError("key-ring previous secret must be at least 32 bytes")
            if self.previous_expires_at is None:
                raise AuthConfigError(
                    "key-ring previous secret requires previous_expires_at"
                )

    def rotate(self, new_secret: bytes, *, overlap_seconds: float = 600) -> SessionKeyRing:
        """Return the ring after rotation to ``new_secret``.

        The old current becomes the bounded previous generation; tokens
        signed with it validate only during the overlap window.
        """
        if not isinstance(new_secret, (bytes, bytearray)) or len(new_secret) < 32:
            raise AuthConfigError("rotation secret must be at least 32 bytes")
        if bytes(new_secret) == bytes(self.current):
            raise AuthConfigError("rotation requires a distinct secret")
        return SessionKeyRing(
            current=bytes(new_secret),
            previous=bytes(self.current),
            previous_expires_at=time.time() + overlap_seconds,
        )


async def validate_with_key_ring(
    token: str,
    account_store: q.AsyncAccountStore,
    revocation: q.SessionRevocationStore,
    base_config: q.MoonmindAuthConfig,
    ring: SessionKeyRing,
    *,
    now: float | None = None,
) -> q.AccountRecord:
    """Validate against the key ring, failing closed on old-key reuse.

    Tries the current secret; only on signature failure tries the bounded
    previous secret within its overlap window. Revocation, generation, and
    principal-status checks run on whichever secret verifies.
    """
    current_config = _config_with_secret(base_config, ring.current)
    try:
        return await q.validate_moonmind_session(
            token, account_store, revocation, current_config
        )
    except AuthInvalidError as current_exc:
        if ring.previous is None:
            raise
        moment = time.time() if now is None else now
        assert ring.previous_expires_at is not None
        if moment >= ring.previous_expires_at:
            raise AuthInvalidError(
                "auth_invalid", "session signed with a rotated-out key"
            ) from current_exc
        previous_config = _config_with_secret(base_config, ring.previous)
        return await q.validate_moonmind_session(
            token, account_store, revocation, previous_config
        )


def _config_with_secret(
    base: q.MoonmindAuthConfig, secret: bytes
) -> q.MoonmindAuthConfig:
    return q.MoonmindAuthConfig(
        mode=base.mode,
        cookie_name=base.cookie_name,
        cookie_secret=bytes(secret),
        session_ttl_seconds=base.session_ttl_seconds,
        token_issuer=base.token_issuer,
        token_audience=base.token_audience,
        require_secure_cookies=base.require_secure_cookies,
    )


# ---------------------------------------------------------------------------
# Cookie issue/clear policy
# ---------------------------------------------------------------------------


def cookie_name_for_policy(*, require_secure_cookies: bool) -> str:
    """Return the MoonMind cookie name for the transport policy."""
    return MOONMIND_PROD_COOKIE if require_secure_cookies else MOONMIND_DEV_COOKIE


def build_set_cookie_header(
    *,
    token: str,
    cookie_name: str,
    require_secure_cookies: bool,
    max_age_seconds: int,
) -> str:
    """Build a ``Set-Cookie`` value for an issued MoonMind session.

    Production uses the ``__Host-`` cookie with ``Secure`` (which requires
    ``Path=/`` and no ``Domain``); the separately named development cookie
    is for explicit loopback HTTP only. Both are ``HttpOnly`` with
    ``SameSite=Lax`` or stricter. Raises when a ``__Host-`` cookie would be
    set without ``Secure``/``Path=/`` or with a ``Domain``.
    """
    if not token or not cookie_name:
        raise AuthConfigError("cookie issuance requires a token and cookie name")
    if cookie_name in q.UPSTREAM_SESSION_COOKIES:
        raise AuthConfigError(
            f"cookie_name={cookie_name!r} collides with the upstream runtime cookie"
        )
    parts = [f"{cookie_name}={token}", "Path=/", "HttpOnly", "SameSite=Lax"]
    if require_secure_cookies:
        parts.append("Secure")
    if cookie_name.startswith("__Host-"):
        if not require_secure_cookies:
            raise AuthConfigError(
                f"{cookie_name!r} is Host-prefixed and requires Secure on HTTPS"
            )
        # Path=/ is set above; Domain must never be set for __Host-.
    elif require_secure_cookies and cookie_name == MOONMIND_DEV_COOKIE:
        raise AuthConfigError(
            "The development cookie must never be issued under Secure production policy"
        )
    parts.append(f"Max-Age={int(max_age_seconds)}")
    return "; ".join(parts)


def build_clear_cookie_header(
    *, cookie_name: str, require_secure_cookies: bool
) -> str:
    """Build a clearing ``Set-Cookie`` with matching scope attributes.

    Clearing must repeat the issue scope (``Path=/``, ``Secure`` on HTTPS,
    ``SameSite``) or the browser keeps the session cookie.
    """
    parts = [f"{cookie_name}=deleted", "Path=/", "HttpOnly", "SameSite=Lax"]
    if require_secure_cookies or cookie_name.startswith("__Host-"):
        parts.append("Secure")
    parts.append("Max-Age=0")
    parts.append("Expires=Thu, 01 Jan 1970 00:00:00 GMT")
    return "; ".join(parts)


def assert_no_token_in_json(payload: Mapping[str, Any]) -> None:
    """Fail when a browser response payload would leak session material.

    Browser responses must never expose session/refresh tokens merely
    because an upstream CLI mode offers them. Scans the top-level keys
    for token-shaped fields.
    """
    suspect = {
        "session_token",
        "refresh_token",
        "moonmind_session",
        "mm_session",
        "set_cookie",
        "id_token",
    }
    found = [k for k in payload if str(k).lower() in suspect]
    if found:
        raise AuthConfigError(
            f"browser payload must not carry session material: {sorted(found)}"
        )


# ---------------------------------------------------------------------------
# CSRF + origin enforcement at the shared cookie-unsafe boundary
# ---------------------------------------------------------------------------


def _effective_port(scheme: str, port: int | None) -> int | None:
    """Return the effective port, filling well-known defaults for http/https.

    Returns ``None`` when the scheme has no well-known default and no
    explicit port was supplied, so unknown schemes compare by explicit
    port only.
    """
    if port is not None:
        return port
    lowered = (scheme or "").lower()
    if lowered == "https":
        return 443
    if lowered == "http":
        return 80
    return None


def enforce_csrf_origin(
    *,
    method: str,
    cookie_present: bool,
    origin: str | None,
    referer: str | None,
    host: str | None,
    base_url: str,
) -> None:
    """Enforce CSRF/origin checks for cookie-authenticated unsafe requests.

    Safe methods and bearer-only flows (no cookie presented) pass through;
    cookie-authenticated mutations (including login/logout/invitation/
    recovery actions issued over the session cookie) require an ``Origin``
    or ``Referer`` that is same-origin with the configured ``base_url``.
    Same-origin compares scheme, hostname, and effective port: cookies and
    SameSite classification do not isolate ports, so an attacker-controlled
    service on another port of the same hostname must not pass. Cross-origin
    cookie mutations are rejected; authorization is never inferred from
    SameSite or CORS alone.
    """
    if method.upper() in _SAFE_METHODS or not cookie_present:
        return
    if not base_url:
        raise AuthInvalidError("auth_invalid", "cookie CSRF cannot be checked")
    try:
        expected = urlsplit(base_url.strip())
        expected_host = (expected.hostname or "").lower()
        expected_scheme = expected.scheme.lower()
        expected_port = _effective_port(expected.scheme, expected.port)
    except ValueError as exc:
        raise AuthInvalidError("auth_invalid", "cookie CSRF cannot be checked") from exc
    if not expected_host:
        raise AuthInvalidError("auth_invalid", "cookie CSRF cannot be checked")
    candidate = (origin or "").strip() or (referer or "").strip()
    if not candidate:
        raise AuthInvalidError("auth_invalid", "missing CSRF origin")
    try:
        parts = urlsplit(candidate)
        candidate_port = _effective_port(parts.scheme, parts.port)
    except ValueError as exc:
        raise AuthInvalidError("auth_invalid", "invalid CSRF origin") from exc
    candidate_host = (parts.hostname or "").lower()
    if not candidate_host or candidate_host != expected_host:
        raise AuthInvalidError("auth_invalid", "cross-origin cookie mutation rejected")
    if parts.scheme and expected_scheme and parts.scheme.lower() != expected_scheme:
        raise AuthInvalidError("auth_invalid", "cross-origin cookie mutation rejected")
    if candidate_port != expected_port:
        raise AuthInvalidError("auth_invalid", "cross-origin cookie mutation rejected")
    if host and candidate_host != host.strip().lower().split(":")[0]:
        # Trusted Host/forwarded-origin input is validated by the caller via
        # #4120 base-URL/proxy policy; a mismatch here fails closed.
        raise AuthInvalidError("auth_invalid", "untrusted host for cookie mutation")


def resolve_credentialed_cors_origins(
    explicit_origins: list[str] | tuple[str, ...] | None,
    *,
    allow_credentials: bool,
) -> list[str]:
    """Resolve credentialed CORS origins, rejecting wildcard+credentials.

    ``allow_origins=["*"]`` with ``allow_credentials=True`` would let any
    site issue credentialed cross-origin requests; it fails closed here.
    Credentialed deployments must enumerate trusted origins explicitly.
    """
    origins = [o.strip() for o in (explicit_origins or []) if str(o).strip()]
    if allow_credentials and "*" in origins:
        raise AuthConfigError(
            "credentialed CORS cannot use allow_origins=['*']: enumerate "
            "explicit trusted origins for cookie-authenticated browsers"
        )
    return origins


# ---------------------------------------------------------------------------
# Stream re-authorization (WebSocket/SSE reconnects, active termination)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StreamReauthPolicy:
    """Bound for re-authorizing active browser streams (#4121 req 7)."""

    max_reauth_interval_seconds: int = SESSION_REVOCATION_INTERVAL_SECONDS

    def __post_init__(self) -> None:
        if self.max_reauth_interval_seconds <= 0:
            raise AuthConfigError("max_reauth_interval_seconds must be positive")

    def reauth_due(self, last_check_epoch: float, *, now_epoch: float | None = None) -> bool:
        moment = time.time() if now_epoch is None else now_epoch
        return (moment - last_check_epoch) >= self.max_reauth_interval_seconds


async def reauthorize_stream_token(
    token: str,
    *,
    account_store: q.AsyncAccountStore,
    revocation: q.SessionRevocationStore,
    config: q.MoonmindAuthConfig,
) -> q.AccountRecord:
    """Re-authorize one active stream credential against live authority.

    Uses the same validation/revocation interface as HTTP (no second
    stream store). Stream owners call this on reconnect and at least every
    :attr:`StreamReauthPolicy.max_reauth_interval_seconds` to terminate
    revoked/expired streams within the agreed bound without changing
    workflow ownership.
    """
    return await q.validate_moonmind_session(token, account_store, revocation, config)


# ---------------------------------------------------------------------------
# Redacted authentication observability (no secret material)
# ---------------------------------------------------------------------------

_AUTH_EVENT_KINDS = frozenset({"success", "denial", "unavailable", "revocation"})

_SECRET_SUBSTRINGS = (
    "eyJ",
    "reset_token",
    "reset-",
    "verification_token",
    "verification-",
    "authorization_code",
    "refresh_token",
)


def emit_auth_event(
    kind: str,
    *,
    mode: str,
    reason: str,
    request_id: str | None = None,
    user_id: str | None = None,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Record a redacted authentication event through existing observability.

    Never logs tokens, cookies, auth codes, reset links, or raw IdP
    responses: any ``extra`` value containing secret-shaped material fails
    closed with ``AuthConfigError`` instead of being emitted.
    """
    normalized = kind.strip().lower()
    if normalized not in _AUTH_EVENT_KINDS:
        raise AuthConfigError(f"unknown auth event kind {kind!r}")
    event: dict[str, Any] = {
        "auth_event": normalized,
        "auth_mode": mode,
        "reason": reason,
        "request_id": request_id or "-",
        "user_id": user_id or "-",
    }
    for key, value in dict(extra or {}).items():
        lowered = str(key).lower()
        if any(
            hint in lowered
            for hint in (
                "token",
                "cookie",
                "secret",
                "password",
                "code",
                "link",
                "idp_response",
                "authorization",
            )
        ):
            raise AuthConfigError(
                f"auth event must not carry secret material: {key!r}"
            )
        text = str(value)
        if any(marker in text for marker in _SECRET_SUBSTRINGS):
            raise AuthConfigError(
                f"auth event value for {key!r} looks like secret material"
            )
        event[str(key)] = value
    logger.info("auth_event %s mode=%s reason=%s", normalized, mode, reason)
    return event


def assert_no_secret_leak(container: Any, secrets: list[str]) -> None:
    """Fail when ``container`` serializes any of ``secrets`` (negative test helper).

    Used by leakage-negative assertions with synthetic secrets across logs,
    traces, Temporal payloads, and artifacts.
    """
    try:
        import json as _json

        rendered = _json.dumps(container, default=str)
    except Exception:
        rendered = str(container)
    for secret in secrets:
        if secret and secret in rendered:
            raise AssertionError("secret material leaked into serialized payload")
