"""Generic OIDC advanced identity source (MoonLadderStudios/MoonMind#4124).

Parent: #4116. Depends on #4118, #4119, #4120, #4121. Plan coverage: K3/K4
advanced modes in ``docs/tmp/KeycloakRemovalPlan.md``.

Portable, runtime-neutral capability: authorization-code + PKCE login,
single-use transaction-bound state/nonce, exact redirect validation, and
fail-closed ID-token claim validation. Maps verified ``(issuer, subject)``
through the #4119 identity authority before any #4121 session is minted.

Non-goals: no HTTP framework, no database, no session minting here. The
API service layer (``api_service/services/advanced_auth_service_4124.py``)
owns persistence (DB transaction table), enrollment policy enforcement via
``api_service/services/identity_service.py``, and session issuance via the
#4118/#4121 authority. This module owns protocol decisions only.

Security properties (mirrors AuthenticationContracts §§3-6,10):

* ``issuer`` must be ``https://`` except explicit loopback (hermetic
  fixtures use ``http://127.0.0.1`` / ``http://localhost``).
* Redirect/callback destinations are validated with the #4120 owner
  (:func:`validate_callback_origin`); open redirects are rejected.
* ID tokens accept only ``RS256``/``ES256`` (allowlist); ``none``/``HS256``
  and any other algorithm fail closed. ``iss`` must match exactly,
  ``aud`` must contain ``client_id``, time claims are enforced, ``sub``
  is matched case-sensitively and never derived from email.
* Email/display attributes are informational only; they never select
  identity and never grant superuser authority.
* Raw token responses, codes, verifiers, and keys are never logged: errors
  carry stable codes (``idp_unavailable``, ``auth_invalid``) with redacted
  detail. Callers must use :func:`redacted_oidc_error`.
* Network scope: discovery/JWKS/token HTTP uses explicit timeouts, follows
  no open redirects, and caches metadata/keys with bounded TTL + bounded
  rotation retries. IdP outage raises ``OidcLoginError("idp_unavailable")``
  without changing auth mode, principal, or accepted token classes.
* PKCE reuses the qualified upstream helpers
  (``omnigent.server.oidc.generate_code_verifier`` /
  ``derive_code_challenge``) with a local RFC 7636 fallback so hermetic
  fixtures never depend on the submodule checkout.

Primary references: OpenID Connect Core 1.0, RFC 9700 (OAuth security
BCP). Required CI uses local hermetic providers; live-provider
qualification is separately recorded and never required here.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import secrets
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Protocol
from urllib.parse import urlencode, urlsplit, urlunsplit

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Errors: stable codes, redacted detail
# ---------------------------------------------------------------------------

OIDC_ERROR_CODES = frozenset(
    {
        "auth_invalid",
        "auth_required",
        "idp_unavailable",
        "misconfigured",
        "enrollment_required",
        "replay_detected",
    }
)


class OidcConfigError(ValueError):
    """Fail-closed operator configuration error (no secrets in message)."""

    def __init__(self, message: str):
        super().__init__(message)
        self.code = "misconfigured"


class OidcLoginError(RuntimeError):
    """Fail-closed login/callback failure with a stable code.

    ``code`` is one of ``OIDC_ERROR_CODES``. ``detail`` is redacted
    (never carries tokens, codes, verifiers, or keys).
    """

    def __init__(self, code: str, detail: str = ""):
        if code not in OIDC_ERROR_CODES:
            raise ValueError(f"unknown OIDC error code {code!r}")
        super().__init__(detail or code)
        self.code = code
        self.detail = detail or code


def redacted_oidc_error(exc: BaseException) -> dict[str, str]:
    """Return a secret-free error projection for logs/API responses."""
    code = getattr(exc, "code", None) or "auth_invalid"
    if code not in OIDC_ERROR_CODES:
        code = "auth_invalid"
    return {"code": code, "detail": "login failed"}


# ---------------------------------------------------------------------------
# PKCE (qualified upstream reuse with local fallback)
# ---------------------------------------------------------------------------


def generate_code_verifier() -> str:
    """Generate a PKCE code verifier (43-128 URL-safe chars)."""
    try:
        from omnigent.server.oidc import generate_code_verifier as _upstream

        return _upstream()
    except Exception:
        return secrets.token_urlsafe(64)[:96]


def derive_code_challenge(verifier: str) -> str:
    """Derive the S256 code challenge for a verifier (RFC 7636)."""
    try:
        from omnigent.server.oidc import derive_code_challenge as _upstream

        return _upstream(verifier)
    except Exception:
        digest = hashlib.sha256(verifier.encode("ascii")).digest()
        return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


# ---------------------------------------------------------------------------
# Provider configuration (explicit MoonMind inputs only)
# ---------------------------------------------------------------------------

_ALLOWED_ID_TOKEN_ALGORITHMS = ("RS256", "ES256")
_DEFAULT_SCOPES = "openid email profile"
_DEFAULT_TIMEOUT_SECONDS = 10.0
_DEFAULT_METADATA_TTL_SECONDS = 3600.0
_DEFAULT_JWKS_TTL_SECONDS = 600.0
_MAX_TIMEOUT_SECONDS = 30.0
_MIN_TIMEOUT_SECONDS = 1.0


def _is_loopback_url(value: str) -> bool:
    try:
        host = (urlsplit(value).hostname or "").strip().lower().strip("[]")
    except ValueError:
        return False
    if host in ("localhost", "127.0.0.1", "::1"):
        return True
    try:
        import ipaddress

        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


@dataclass(frozen=True)
class OidcProviderConfig:
    """Explicit generic-OIDC provider configuration.

    All values are operator configuration. ``OMNIGENT_OIDC_*`` ambient
    values never populate this object; the service layer maps explicit
    ``MOONMIND_OIDC_*`` / ``OIDC_*`` inputs here.
    """

    issuer: str
    client_id: str
    client_secret: str
    redirect_uri: str
    authorization_endpoint: str = ""
    token_endpoint: str = ""
    jwks_uri: str = ""
    scopes: str = _DEFAULT_SCOPES
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS
    metadata_ttl_seconds: float = _DEFAULT_METADATA_TTL_SECONDS
    jwks_ttl_seconds: float = _DEFAULT_JWKS_TTL_SECONDS
    end_session_endpoint: str = ""

    def __post_init__(self) -> None:
        if not self.issuer or not self.client_id or not self.client_secret:
            raise OidcConfigError(
                "OIDC issuer, client_id, and client_secret are all required."
            )
        parts = urlsplit(self.issuer)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise OidcConfigError(
                "OIDC issuer must be an absolute http(s) URL; refusing "
                "arbitrary destinations."
            )
        if parts.scheme == "http" and not _is_loopback_url(self.issuer):
            raise OidcConfigError(
                "OIDC issuer uses plain HTTP on a non-loopback host; "
                "production issuers require HTTPS."
            )
        if not self.redirect_uri:
            raise OidcConfigError("OIDC redirect_uri is required.")
        # Exact same-origin redirect enforcement lives with the #4120
        # owner; here we require an absolute http(s) callback shape.
        cb = urlsplit(self.redirect_uri)
        if cb.scheme not in ("http", "https") or not cb.hostname:
            raise OidcConfigError(
                "OIDC redirect_uri must be an absolute http(s) URL."
            )
        for name in (
            "authorization_endpoint",
            "token_endpoint",
            "jwks_uri",
            "end_session_endpoint",
        ):
            value = getattr(self, name)
            if not value:
                continue
            ep = urlsplit(value)
            if ep.scheme not in ("http", "https") or not ep.hostname:
                raise OidcConfigError(
                    f"OIDC {name} must be an absolute http(s) URL."
                )
            if ep.scheme == "http" and not _is_loopback_url(value):
                raise OidcConfigError(
                    f"OIDC {name} uses plain HTTP on a non-loopback host."
                )
        if not (
            _MIN_TIMEOUT_SECONDS
            <= float(self.timeout_seconds)
            <= _MAX_TIMEOUT_SECONDS
        ):
            raise OidcConfigError(
                "OIDC timeout_seconds must be within "
                f"{_MIN_TIMEOUT_SECONDS}-{_MAX_TIMEOUT_SECONDS}s."
            )
        if float(self.metadata_ttl_seconds) <= 0 or float(self.jwks_ttl_seconds) <= 0:
            raise OidcConfigError("OIDC cache TTLs must be positive.")


def resolve_oidc_config(
    *,
    issuer: str | None,
    client_id: str | None,
    client_secret: str | None,
    redirect_uri: str | None,
    base_url: str | None = None,
    authorization_endpoint: str = "",
    token_endpoint: str = "",
    jwks_uri: str = "",
    end_session_endpoint: str = "",
    scopes: str = _DEFAULT_SCOPES,
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
) -> OidcProviderConfig:
    """Build a validated provider config from explicit inputs.

    When ``base_url`` is given, ``redirect_uri`` must be same-origin with
    it (exact scheme/host/port via the #4120 owner). Missing/blank
    issuer/client material raises :class:`OidcConfigError`, never a
    silent default. Secrets are validated for presence only; they are
    never echoed back in errors.
    """
    from moonmind.security.auth_modes_4120 import validate_callback_origin

    if not (issuer or "").strip() or not (client_id or "").strip():
        raise OidcConfigError(
            "OIDC issuer and client_id are required for 'oidc' mode."
        )
    if not (client_secret or "").strip():
        raise OidcConfigError(
            "OIDC client_secret is required for 'oidc' mode."
        )
    if not (redirect_uri or "").strip():
        raise OidcConfigError("OIDC redirect_uri is required for 'oidc' mode.")
    if base_url:
        try:
            validate_callback_origin(str(redirect_uri).strip(), base_url=base_url)
        except Exception as exc:
            raise OidcConfigError(
                "OIDC redirect_uri is not same-origin with the configured "
                f"base URL; rejecting open redirect: {exc}"
            ) from exc
    return OidcProviderConfig(
        issuer=str(issuer).strip(),
        client_id=str(client_id).strip(),
        client_secret=str(client_secret),
        redirect_uri=str(redirect_uri).strip(),
        authorization_endpoint=(authorization_endpoint or "").strip(),
        token_endpoint=(token_endpoint or "").strip(),
        jwks_uri=(jwks_uri or "").strip(),
        scopes=(scopes or _DEFAULT_SCOPES).strip() or _DEFAULT_SCOPES,
        timeout_seconds=float(timeout_seconds),
        end_session_endpoint=(end_session_endpoint or "").strip(),
    )


# ---------------------------------------------------------------------------
# Discovery / JWKS: trusted config + verified protocol results
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OidcDiscovery:
    """Verified protocol endpoints for an issuer."""

    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    jwks_uri: str
    end_session_endpoint: str = ""
    userinfo_endpoint: str = ""


@dataclass
class BoundedMetadataCache:
    """Bounded TTL cache for discovery documents and JWKS sets.

    Keyed by issuer; evicts oldest-first beyond ``max_entries``. Rotation
    retries are bounded by the caller (see ``fetch_jwks``).
    """

    max_entries: int = 8
    metadata_ttl_seconds: float = _DEFAULT_METADATA_TTL_SECONDS
    jwks_ttl_seconds: float = _DEFAULT_JWKS_TTL_SECONDS
    _discovery: dict[str, tuple[OidcDiscovery, float]] = field(default_factory=dict)
    _jwks: dict[str, tuple[dict[str, Any], float]] = field(default_factory=dict)

    def get_discovery(self, issuer: str) -> OidcDiscovery | None:
        entry = self._discovery.get(issuer)
        if entry is None:
            return None
        doc, expires = entry
        if expires <= time.time():
            self._discovery.pop(issuer, None)
            return None
        return doc

    def put_discovery(self, doc: OidcDiscovery) -> None:
        while len(self._discovery) >= max(1, self.max_entries):
            oldest = next(iter(self._discovery))
            self._discovery.pop(oldest)
        self._discovery[doc.issuer] = (doc, time.time() + self.metadata_ttl_seconds)

    def get_jwks(self, jwks_uri: str) -> dict[str, Any] | None:
        entry = self._jwks.get(jwks_uri)
        if entry is None:
            return None
        keys, expires = entry
        if expires <= time.time():
            self._jwks.pop(jwks_uri, None)
            return None
        return keys

    def put_jwks(self, jwks_uri: str, keys: Mapping[str, Any]) -> None:
        while len(self._jwks) >= max(1, self.max_entries):
            oldest = next(iter(self._jwks))
            self._jwks.pop(oldest)
        self._jwks[jwks_uri] = (dict(keys), time.time() + self.jwks_ttl_seconds)

    def invalidate_jwks(self, jwks_uri: str) -> None:
        self._jwks.pop(jwks_uri, None)


HttpGet = Callable[..., Any]


def _http_status(resp: Any) -> int:
    return int(getattr(resp, "status_code", 200) or 200)


def fetch_discovery(
    config: OidcProviderConfig,
    *,
    http_get: HttpGet,
    cache: BoundedMetadataCache | None = None,
) -> OidcDiscovery:
    """Fetch (or reuse cached) discovery for the configured issuer.

    Explicitly configured endpoints win without network. Otherwise the
    ``/.well-known/openid-configuration`` document is fetched with the
    configured timeout; failures raise ``idp_unavailable`` without
    changing mode or principal. Discovered endpoints must be absolute
    http(s) URLs; non-loopback plain-HTTP endpoints are rejected.
    """
    if config.authorization_endpoint and config.token_endpoint and config.jwks_uri:
        return OidcDiscovery(
            issuer=config.issuer,
            authorization_endpoint=config.authorization_endpoint,
            token_endpoint=config.token_endpoint,
            jwks_uri=config.jwks_uri,
            end_session_endpoint=config.end_session_endpoint,
        )
    if cache is not None:
        cached = cache.get_discovery(config.issuer)
        if cached is not None:
            return cached
    url = config.issuer.rstrip("/") + "/.well-known/openid-configuration"
    try:
        resp = http_get(url, timeout=config.timeout_seconds)
        if _http_status(resp) >= 400:
            raise OidcLoginError(
                "idp_unavailable", "identity provider returned an error"
            )
        doc = resp.json() if hasattr(resp, "json") else dict(resp)
    except OidcLoginError:
        raise
    except Exception as exc:
        logger.warning("oidc_event discovery_unavailable issuer_set code=idp_unavailable")
        raise OidcLoginError("idp_unavailable", "identity provider unavailable") from exc
    try:
        discovery = OidcDiscovery(
            issuer=config.issuer,
            authorization_endpoint=str(doc["authorization_endpoint"]),
            token_endpoint=str(doc["token_endpoint"]),
            jwks_uri=str(doc["jwks_uri"]),
            end_session_endpoint=str(doc.get("end_session_endpoint") or ""),
            userinfo_endpoint=str(doc.get("userinfo_endpoint") or ""),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise OidcLoginError("idp_unavailable", "identity provider misconfigured") from exc
    # Scope check: discovered endpoints are trusted protocol results only
    # when they are absolute http(s) URLs (https outside loopback).
    for endpoint in (
        discovery.authorization_endpoint,
        discovery.token_endpoint,
        discovery.jwks_uri,
    ):
        parts = urlsplit(endpoint)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise OidcLoginError("idp_unavailable", "identity provider misconfigured")
        if parts.scheme == "http" and not _is_loopback_url(endpoint):
            raise OidcLoginError("idp_unavailable", "identity provider misconfigured")
    if cache is not None:
        cache.put_discovery(discovery)
    return discovery


def fetch_jwks(
    jwks_uri: str,
    *,
    timeout_seconds: float,
    http_get: HttpGet,
    cache: BoundedMetadataCache | None = None,
    max_retries: int = 1,
) -> dict[str, Any]:
    """Fetch a JWKS document with bounded rotation retries.

    ``max_retries`` bounds key-rotation refetch attempts (default one
    retry). Failures raise ``idp_unavailable``; key material is never
    logged.
    """
    if cache is not None:
        cached = cache.get_jwks(jwks_uri)
        if cached is not None:
            return cached
    attempts = max(0, int(max_retries)) + 1
    last: Exception | None = None
    for _ in range(attempts):
        try:
            resp = http_get(jwks_uri, timeout=timeout_seconds)
            if _http_status(resp) >= 400:
                raise OidcLoginError("idp_unavailable", "key endpoint returned an error")
            doc = resp.json() if hasattr(resp, "json") else dict(resp)
            if not isinstance(doc, dict) or "keys" not in doc:
                raise OidcLoginError("idp_unavailable", "key endpoint misconfigured")
            if cache is not None:
                cache.put_jwks(jwks_uri, doc)
            return dict(doc)
        except OidcLoginError as exc:
            last = exc
            break
        except Exception as exc:  # noqa: BLE001 - bounded retry then fail closed
            last = exc
            continue
    logger.warning("oidc_event jwks_unavailable code=idp_unavailable")
    raise OidcLoginError("idp_unavailable", "identity provider unavailable") from last


# ---------------------------------------------------------------------------
# Authorization transactions: single-use, replica-safe protocol
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OidcTransaction:
    """One authorization transaction (state-bound, single-use)."""

    state: str
    nonce: str
    code_verifier: str
    code_challenge: str
    redirect_uri: str
    return_path: str
    created_at: float
    expires_at: float


TRANSACTION_TTL_SECONDS = 600.0
_MAX_RETURN_PATH_LENGTH = 2048


def validate_return_path(value: str) -> str:
    """Validate a deep-link return path (strict same-origin relative path).

    Only absolute paths (``/…``) without scheme/host are preserved, and
    only after rejecting ``//``, backslashes, control characters, and
    overlong values. Anything else falls back to ``/`` by the caller;
    this function raises so the caller records the safe default.
    """
    text = (value or "").strip()
    if not text.startswith("/") or len(text) > _MAX_RETURN_PATH_LENGTH:
        raise OidcLoginError("auth_invalid", "unsafe return path")
    if text.startswith("//") or "\\" in text or "\n" in text or "\r" in text:
        raise OidcLoginError("auth_invalid", "unsafe return path")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in text):
        raise OidcLoginError("auth_invalid", "unsafe return path")
    return text


class OidcTransactionStore(Protocol):
    """Durable single-use transaction mechanism behind a portable interface.

    Production uses the DB-backed store (one truth across replicas and
    restarts); hermetic fixtures share one in-memory store across
    validator replicas. ``consume`` must be atomic: the second consumer
    of the same ``state`` observes replay, never a second login.
    """

    async def save(self, txn: OidcTransaction) -> None:
        raise NotImplementedError

    async def consume(self, state: str, *, now: float | None = None) -> OidcTransaction:
        """Atomically consume ``state`` or raise (replay/expiry/missing)."""
        raise NotImplementedError


class InMemoryOidcTransactionStore:
    """Hermetic transaction store shared across validator replicas."""

    def __init__(self) -> None:
        self._txns: dict[str, OidcTransaction] = {}

    async def save(self, txn: OidcTransaction) -> None:
        if txn.state in self._txns:
            raise OidcLoginError("auth_invalid", "duplicate transaction")
        self._txns[txn.state] = txn

    async def consume(
        self, state: str, *, now: float | None = None
    ) -> OidcTransaction:
        moment = time.time() if now is None else now
        txn = self._txns.pop((state or "").strip(), None)
        if txn is None:
            raise OidcLoginError("replay_detected", "unknown or reused transaction")
        if txn.expires_at <= moment:
            raise OidcLoginError("auth_invalid", "expired transaction")
        return txn


def new_transaction(
    *, redirect_uri: str, return_path: str = "/", now: float | None = None
) -> OidcTransaction:
    """Create a transaction with fresh state/nonce/PKCE material."""
    moment = time.time() if now is None else now
    try:
        safe_return = validate_return_path(return_path)
    except OidcLoginError:
        safe_return = "/"
    verifier = generate_code_verifier()
    return OidcTransaction(
        state=secrets.token_urlsafe(32),
        nonce=secrets.token_urlsafe(32),
        code_verifier=verifier,
        code_challenge=derive_code_challenge(verifier),
        redirect_uri=redirect_uri,
        return_path=safe_return,
        created_at=moment,
        expires_at=moment + TRANSACTION_TTL_SECONDS,
    )


def build_authorization_url(
    config: OidcProviderConfig,
    discovery: OidcDiscovery,
    txn: OidcTransaction,
) -> str:
    """Build the IdP authorization URL for a transaction."""
    params = {
        "response_type": "code",
        "client_id": config.client_id,
        "redirect_uri": txn.redirect_uri,
        "scope": config.scopes,
        "state": txn.state,
        "nonce": txn.nonce,
        "code_challenge": txn.code_challenge,
        "code_challenge_method": "S256",
    }
    return discovery.authorization_endpoint + "?" + urlencode(params)


# ---------------------------------------------------------------------------
# ID-token validation: issuer/audience/signature/time, replay-safe
# ---------------------------------------------------------------------------


def _b64url_json(segment: str) -> dict[str, Any]:
    import json as _json

    padded = segment + "=" * (-len(segment) % 4)
    return _json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))


def decode_id_token_header_unverified(raw_token: str) -> dict[str, Any]:
    """Decode the JWT header without verification (algorithm gate only)."""
    try:
        header_segment = str(raw_token).split(".")[0]
        header = _b64url_json(header_segment)
    except Exception as exc:
        raise OidcLoginError("auth_invalid", "malformed identity token") from exc
    if not isinstance(header, dict):
        raise OidcLoginError("auth_invalid", "malformed identity token")
    return header


def validate_id_token(
    raw_token: str,
    *,
    config: OidcProviderConfig,
    discovery: OidcDiscovery,
    expected_nonce: str,
    jwks: Mapping[str, Any],
    leeway_seconds: int = 60,
    now: int | None = None,
) -> dict[str, Any]:
    """Validate an ID token, returning verified claims (fail-closed).

    Checks: allowlisted algorithm, key id present in ``jwks``, signature,
    exact ``iss``, ``aud`` containing ``client_id``, expiry/issued-at
    (with bounded leeway), ``sub`` present, and ``nonce`` bound to the
    consumed transaction. Missing/invalid claims and replays raise
    ``auth_invalid`` before any enrollment or privilege mutation. The raw
    token is never logged.
    """
    import jwt as _jwt

    header = decode_id_token_header_unverified(raw_token)
    alg = str(header.get("alg") or "")
    if alg not in _ALLOWED_ID_TOKEN_ALGORITHMS:
        raise OidcLoginError("auth_invalid", "unsupported token algorithm")
    kid = str(header.get("kid") or "")
    if not kid:
        raise OidcLoginError("auth_invalid", "token key id missing")
    keys = (jwks.get("keys") or []) if isinstance(jwks, Mapping) else []
    if not any(isinstance(k, dict) and k.get("kid") == kid for k in keys):
        raise OidcLoginError("auth_invalid", "unknown token key")
    try:
        key = _jwt.algorithms.RSAAlgorithm.from_jwk(
            next(k for k in keys if isinstance(k, dict) and k.get("kid") == kid)
        )
    except Exception:
        try:
            from jwt.algorithms import ECAlgorithm as _EC

            key = _EC.from_jwk(
                next(k for k in keys if isinstance(k, dict) and k.get("kid") == kid)
            )
        except Exception as exc:
            raise OidcLoginError("auth_invalid", "unusable token key") from exc
    moment = int(time.time()) if now is None else int(now)
    try:
        claims = _jwt.decode(
            raw_token,
            key=key,
            algorithms=list(_ALLOWED_ID_TOKEN_ALGORITHMS),
            issuer=config.issuer,
            audience=config.client_id,
            leeway=leeway_seconds,
            options={"require": ["exp", "iat", "iss", "aud", "sub"]},
        )
    except _jwt.InvalidTokenError as exc:
        raise OidcLoginError("auth_invalid", "invalid identity token") from exc
    if str(claims.get("iss") or "") != config.issuer:
        raise OidcLoginError("auth_invalid", "wrong token issuer")
    aud = claims.get("aud")
    audiences = [aud] if isinstance(aud, str) else list(aud or [])
    if config.client_id not in audiences:
        raise OidcLoginError("auth_invalid", "wrong token audience")
    subject = str(claims.get("sub") or "")
    if not subject:
        raise OidcLoginError("auth_invalid", "token subject missing")
    if claims.get("nonce") != expected_nonce:
        raise OidcLoginError("auth_invalid", "token nonce mismatch")
    # Time-claim sanity beyond library enforcement: tokens minted in the
    # distant future or without iat fail closed.
    try:
        iat = int(claims.get("iat"))
    except (TypeError, ValueError) as exc:
        raise OidcLoginError("auth_invalid", "token issued-at missing") from exc
    if iat > moment + leeway_seconds:
        raise OidcLoginError("auth_invalid", "token issued in the future")
    _ = discovery  # endpoint set already scope-checked at fetch time
    return dict(claims)


def validated_identity_from_claims(claims: Mapping[str, Any], *, issuer: str):
    """Build the #4118 :class:`ValidatedIdentity` from verified claims.

    Identity is exactly ``(issuer, subject)`` (case-sensitive subject,
    issuer boundary preserved). Email/display claims are carried as
    informational fields only; reserved subjects fail closed here before
    any #4119 lookup.
    """
    from moonmind.security.omnigent_auth_qualification import ValidatedIdentity

    subject = str(claims.get("sub") or "")
    if not subject:
        raise OidcLoginError("auth_invalid", "token subject missing")
    email = claims.get("email")
    email_text = str(email).strip() if isinstance(email, str) and email.strip() else None
    try:
        return ValidatedIdentity(
            issuer=issuer, subject=subject, email=email_text
        )
    except Exception as exc:
        raise OidcLoginError("auth_invalid", "reserved identity") from exc


# ---------------------------------------------------------------------------
# Token exchange: scoped HTTP, no raw response logging
# ---------------------------------------------------------------------------


async def exchange_code_for_tokens(
    code: str,
    *,
    config: OidcProviderConfig,
    discovery: OidcDiscovery,
    txn: OidcTransaction,
    http_post: Callable[..., Any],
) -> dict[str, Any]:
    """Exchange an authorization code for tokens (fail-closed).

    ``code`` and PKCE verifier travel only to the configured token
    endpoint with the configured timeout. Failures (including IdP
    outage) raise ``idp_unavailable`` or ``auth_invalid`` with redacted
    detail; raw token responses are never logged or returned verbatim
    in errors.
    """
    if not (code or "").strip():
        raise OidcLoginError("auth_invalid", "authorization code missing")
    payload = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": txn.redirect_uri,
        "client_id": config.client_id,
        "client_secret": config.client_secret,
        "code_verifier": txn.code_verifier,
    }
    try:
        resp = await http_post(
            discovery.token_endpoint,
            data=payload,
            timeout=config.timeout_seconds,
        )
        status = _http_status(resp)
        body = resp.json() if hasattr(resp, "json") else {}
    except OidcLoginError:
        raise
    except Exception as exc:
        logger.warning("oidc_event token_exchange_unavailable code=idp_unavailable")
        raise OidcLoginError("idp_unavailable", "identity provider unavailable") from exc
    if status >= 500 or not isinstance(body, dict):
        raise OidcLoginError("idp_unavailable", "identity provider unavailable")
    if status >= 400 or "id_token" not in body:
        # Bad verifier/state/nonce surfaces from the IdP as a 4xx without
        # an id_token; report it as invalid, never with provider detail.
        raise OidcLoginError("auth_invalid", "authorization code rejected")
    return {"id_token": str(body["id_token"]), "access_token": body.get("access_token")}


def sanitize_token_response_for_log(body: Mapping[str, Any]) -> dict[str, Any]:
    """Return a secret-free projection of a token response (keys only)."""
    return {str(k): "(present)" for k in body.keys()}


def canonical_issuer(value: str) -> str:
    """Return the issuer with trailing slashes stripped (no lowercasing)."""
    return str(value or "").strip().rstrip("/")
