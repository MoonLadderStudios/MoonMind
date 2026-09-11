"""Generic OIDC and trusted-proxy identity through the shared auth boundary.

Source issue: MoonLadderStudios/MoonMind#4124 (parent #4116; depends on
#4118, #4119, #4120, #4121). Plan coverage: advanced modes, K3 and K4 in
``docs/tmp/KeycloakRemovalPlan.md``.

This module owns both advanced identity sources. They use the same
UUID/account/session authority rather than separate user stores or
parallel validators:

* generic OIDC (authorization-code + PKCE) validates
  issuer/audience/signature/time claims, maps the verified
  ``(issuer, subject)`` through #4119, and mints #4121 sessions;
* trusted-header mode extracts one operator-namespaced stable identifier
  only behind an explicitly trusted ingress and maps it through the same
  #4119 ``proxy:<namespace>:<stable-id>`` form.

Composed authorities (never reimplemented here):

* #4118 ``moonmind.security.omnigent_auth_qualification`` — MoonMind
  session mint/validate primitives, ``ValidatedIdentity``/``AccountRecord``
  shapes, ``MoonmindAuthConfig``;
* #4119 ``api_service.services.identity_service`` — the single active
  ``(issuer, subject) -> User.id`` relation (exact case-sensitive subjects,
  issuer boundaries, convergent binding, email-taken refusal);
* #4120 ``moonmind.security.auth_modes_4120`` — selector ownership,
  callback-origin/proxy/base-URL validation, redacted diagnostics;
* #4121 ``moonmind.security.session_authority_4121`` — cookie issue/clear,
  CSRF/origin, credentialed-CORS, revocation/stream bounds.

Non-goals: no new user store, no parallel validator, no per-request IdP
call on ordinary authenticated requests (OIDC discovery/JWKS/token calls
happen only on the login/callback path), no raw token responses in logs,
no ``google``-specific discovery branch (retired selectors fail through
#4120), no new MFA or proxy product (MFA stays a verified-IdP-policy gate
that blocks cutover when unsatisfied).
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol
from urllib.parse import urlencode, urlsplit

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Errors: one typed contract, no enumerated-edge-case branches downstream
# ---------------------------------------------------------------------------


class OIDCConfigError(ValueError):
    """Fail-closed OIDC/trusted-proxy configuration error."""


class OIDCLoginError(Exception):
    """Actionable login failure; safe to surface (no token material)."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class OIDCUnavailableError(OIDCLoginError):
    """IdP outage: login cannot proceed; auth mode/principal unchanged."""

    def __init__(self, message: str = "identity provider unavailable"):
        super().__init__("idp_unavailable", message)


class OIDCTransactionError(OIDCLoginError):
    """Bad PKCE/state/nonce, replay, or unsafe redirect."""

    def __init__(self, message: str = "invalid authorization transaction"):
        super().__init__("invalid_transaction", message)


class OIDCClaimError(OIDCLoginError):
    """Missing/invalid issuer, audience, algorithm, signature, or time claim."""

    def __init__(self, message: str = "invalid identity token"):
        super().__init__("invalid_claim", message)


class OIDCMFACutoverBlockedError(OIDCLoginError):
    """Deployment requires MFA but the IdP policy evidence is absent."""

    def __init__(self, message: str = "mfa policy unsatisfied; cutover blocked"):
        super().__init__("mfa_required", message)


class EnrollmentRequiredError(OIDCLoginError):
    """Unknown identity needs explicit operator enrollment, not auto-grant."""

    def __init__(self, code: str, message: str):
        super().__init__(code, message)
        self.enrollment_code = code


class TrustedProxyError(Exception):
    """Fail-closed trusted-header rejection (forged/direct/missing/reserved)."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


# ---------------------------------------------------------------------------
# OIDC provider configuration: explicit MoonMind-owned inputs only
# ---------------------------------------------------------------------------

DEFAULT_OIDC_CALLBACK_PATH = "/api/v1/oidc/callback"
DEFAULT_OIDC_SCOPES = ("openid", "email", "profile")
DEFAULT_OIDC_TIMEOUT_SECONDS = 10.0
DEFAULT_METADATA_TTL_SECONDS = 300.0
DEFAULT_JWKS_TTL_SECONDS = 300.0
DEFAULT_TRANSACTION_TTL_SECONDS = 600.0
MAX_TRANSACTION_TTL_SECONDS = 3600.0

# Production IdPs sign with asymmetric keys. Hermetic fixtures may opt into
# ``HS256`` explicitly; the default never does (wrong-algorithm tokens fail
# closed instead of being accepted through a symmetric fallback).
DEFAULT_ALLOWED_ALGORITHMS = ("RS256", "ES256")

# Substrings that must never appear in a diagnostics payload.
_TOKEN_MARKERS = ("eyJ", "code_verifier", "refresh_token", "id_token")


@dataclass(frozen=True)
class OIDCProviderConfig:
    """Explicit generic-OIDC configuration for one deployment.

    ``issuer`` is the exact verified issuer URI (never truncated);
    ``callback_base_url`` is the configured public base URL that owns the
    exact approved redirect destination
    (``callback_base_url + callback_path``). ``allow_unknown_users`` is the
    explicit admission policy for previously unknown external users
    (default deny). ``require_mfa`` keeps deployments with an MFA/SSO
    requirement on their verified IdP policy: when true, the ID token must
    carry MFA evidence or cutover is blocked.
    """

    issuer: str
    client_id: str
    client_secret: str
    callback_base_url: str
    callback_path: str = DEFAULT_OIDC_CALLBACK_PATH
    scopes: tuple[str, ...] = DEFAULT_OIDC_SCOPES
    timeout_seconds: float = DEFAULT_OIDC_TIMEOUT_SECONDS
    metadata_ttl_seconds: float = DEFAULT_METADATA_TTL_SECONDS
    jwks_ttl_seconds: float = DEFAULT_JWKS_TTL_SECONDS
    transaction_ttl_seconds: float = DEFAULT_TRANSACTION_TTL_SECONDS
    allowed_algorithms: tuple[str, ...] = DEFAULT_ALLOWED_ALGORITHMS
    allow_unknown_users: bool = False
    require_mfa: bool = False
    mfa_acr_values: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.issuer or not self.issuer.strip():
            raise OIDCConfigError("OIDC issuer must be a non-empty URI")
        # Reject plaintext issuers at startup: discovery over http would let
        # an on-path attacker replace token/JWKS endpoints and capture the
        # code, PKCE verifier, and client secret. Only explicit loopback
        # http is tolerated for hermetic development.
        from urllib.parse import urlsplit as _urlsplit

        _issuer_parts = _urlsplit(self.issuer.strip())
        if _issuer_parts.scheme == "https" and _issuer_parts.hostname:
            pass
        elif (
            _issuer_parts.scheme == "http"
            and _issuer_parts.hostname in ("127.0.0.1", "localhost", "::1")
        ):
            pass
        else:
            raise OIDCConfigError(
                "OIDC issuer must be an https URI "
                "(loopback http only for explicit development)"
            )
        if not self.client_id or not self.client_id.strip():
            raise OIDCConfigError("OIDC client_id must be non-empty")
        if not self.client_secret:
            raise OIDCConfigError("OIDC client_secret must be non-empty")
        if self.timeout_seconds <= 0 or self.timeout_seconds > 60:
            raise OIDCConfigError("OIDC timeout must be within (0, 60] seconds")
        for ttl_name in (
            "metadata_ttl_seconds",
            "jwks_ttl_seconds",
            "transaction_ttl_seconds",
        ):
            ttl = getattr(self, ttl_name)
            if ttl <= 0 or ttl > MAX_TRANSACTION_TTL_SECONDS:
                raise OIDCConfigError(f"OIDC {ttl_name} must be within (0, 3600]s")
        if not self.allowed_algorithms or "none" in [
            a.lower() for a in self.allowed_algorithms
        ]:
            raise OIDCConfigError("OIDC allowed_algorithms must be non-empty, never 'none'")
        # The exact approved redirect destination is same-origin with the
        # configured base URL; open redirects are rejected here, not at the
        # browser.
        from moonmind.security.auth_modes_4120 import validate_callback_origin

        try:
            validate_callback_origin(
                self.callback_url, base_url=self.callback_base_url
            )
        except Exception as exc:
            raise OIDCConfigError(f"Invalid OIDC callback destination: {exc}") from exc

    @property
    def callback_url(self) -> str:
        base = self.callback_base_url.rstrip("/")
        return f"{base}{self.callback_path}"


def _env_flag(environ: Mapping[str, str], name: str) -> bool:
    return str(environ.get(name, "")).strip().lower() in ("1", "true", "yes")


def resolve_oidc_provider_config(
    environ: Mapping[str, str] | None = None,
) -> OIDCProviderConfig:
    """Build the OIDC config from explicit MoonMind-owned inputs.

    Consumes only ``OIDC_ISSUER_URL``/``OIDC_CLIENT_ID``/``OIDC_CLIENT_SECRET``
    plus ``MOONMIND_PUBLIC_BASE_URL`` (callback origin) and ``MOONMIND_OIDC_*``
    knobs. ``OMNIGENT_OIDC_*`` ambient values — even contradictory ones —
    cannot select MoonMind behavior. The retired ``google`` selector is never
    translated here; it fails through #4120 before this owner is consulted.
    """
    import os

    env: Mapping[str, str] = os.environ if environ is None else environ
    for leaked in ("OMNIGENT_OIDC_ISSUER", "OMNIGENT_OIDC_CLIENT_ID"):
        if leaked in env:
            logger.debug(
                "Ignoring ambient runtime OIDC variable for MoonMind config: %s",
                leaked,
            )
    issuer = str(env.get("OIDC_ISSUER_URL", "") or "").strip()
    client_id = str(env.get("OIDC_CLIENT_ID", "") or "").strip()
    client_secret = str(env.get("OIDC_CLIENT_SECRET", "") or "")
    base_url = str(env.get("MOONMIND_PUBLIC_BASE_URL", "") or "").strip()
    if not issuer or not client_id or not client_secret or not base_url:
        raise OIDCConfigError(
            "Generic OIDC requires OIDC_ISSUER_URL, OIDC_CLIENT_ID, "
            "OIDC_CLIENT_SECRET, and MOONMIND_PUBLIC_BASE_URL; refusing to "
            "derive endpoints or redirect destinations from browser input."
        )
    try:
        timeout = float(
            str(env.get("MOONMIND_OIDC_TIMEOUT_SECONDS", "") or "").strip()
            or DEFAULT_OIDC_TIMEOUT_SECONDS
        )
    except ValueError as exc:
        raise OIDCConfigError("MOONMIND_OIDC_TIMEOUT_SECONDS must be numeric") from exc
    _mfa_raw = str(env.get("MOONMIND_OIDC_MFA_ACR_VALUES", "") or "").strip()
    _mfa_values = tuple(
        part.strip() for part in _mfa_raw.split(",") if part.strip()
    )
    return OIDCProviderConfig(
        issuer=issuer,
        client_id=client_id,
        client_secret=client_secret,
        callback_base_url=base_url,
        timeout_seconds=timeout,
        allow_unknown_users=_env_flag(env, "MOONMIND_OIDC_ALLOW_UNKNOWN_USERS"),
        require_mfa=_env_flag(env, "MOONMIND_OIDC_REQUIRE_MFA"),
        mfa_acr_values=_mfa_values,
    )


# ---------------------------------------------------------------------------
# Portable OIDC transport: discovery/JWKS/token as trusted config + verified
# protocol results, never arbitrary browser-selected destinations
# ---------------------------------------------------------------------------


class OIDCTransport(Protocol):
    """Scoped network boundary for IdP metadata/keys/token calls."""

    async def get_json(self, url: str, *, timeout_seconds: float) -> dict[str, Any]:
        raise NotImplementedError

    async def post_form(
        self,
        url: str,
        form: dict[str, str],
        *,
        timeout_seconds: float,
        auth: tuple[str, str] | None = None,
    ) -> dict[str, Any]:
        raise NotImplementedError


@dataclass(frozen=True)
class OIDCMetadata:
    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    jwks_uri: str
    end_session_endpoint: str | None = None
    token_endpoint_auth_method: str = "client_secret_post"


def _require_https_url(value: str, *, what: str, allow_loopback_http: bool) -> str:
    text = (value or "").strip()
    if not text:
        raise OIDCLoginError("invalid_metadata", f"IdP {what} is missing")
    parts = urlsplit(text)
    if parts.scheme == "https" and parts.hostname:
        return text
    if allow_loopback_http and parts.scheme == "http" and parts.hostname in (
        "127.0.0.1",
        "localhost",
        "::1",
    ):
        return text
    raise OIDCLoginError(
        "invalid_metadata", f"IdP {what} must be https (got {text[:64]!r})"
    )


class OIDCMetadataCache:
    """Bounded discovery cache: one verified document per issuer."""

    def __init__(self, ttl_seconds: float = DEFAULT_METADATA_TTL_SECONDS):
        if ttl_seconds <= 0 or ttl_seconds > MAX_TRANSACTION_TTL_SECONDS:
            raise OIDCConfigError("metadata TTL must be within (0, 3600]s")
        self._ttl = ttl_seconds
        self._entries: dict[str, tuple[float, OIDCMetadata]] = {}

    async def get(
        self,
        config: OIDCProviderConfig,
        transport: OIDCTransport,
        *,
        allow_loopback_http: bool = False,
    ) -> OIDCMetadata:
        now = time.time()
        cached = self._entries.get(config.issuer)
        if cached is not None and cached[0] > now:
            return cached[1]
        try:
            raw = await transport.get_json(
                f"{config.issuer.rstrip('/')}/.well-known/openid-configuration",
                timeout_seconds=config.timeout_seconds,
            )
        except OIDCLoginError:
            raise
        except Exception as exc:
            raise OIDCUnavailableError(
                "identity provider discovery failed; login cannot proceed "
                "without changing auth mode, principal, or accepted tokens"
            ) from exc
        if not isinstance(raw, dict):
            raise OIDCLoginError("invalid_metadata", "IdP discovery is not a JSON object")
        if str(raw.get("issuer", "")).strip() != config.issuer:
            raise OIDCLoginError(
                "invalid_metadata", "IdP discovery issuer mismatch; refusing to proceed"
            )
        _advertised = raw.get("token_endpoint_auth_methods_supported") or []
        if isinstance(_advertised, str):
            _advertised = [_advertised]
        _methods = [str(m).strip() for m in _advertised if str(m).strip()]
        # Honor the provider's advertised auth method: use basic only when
        # the provider does not support post. Defaults to post for
        # backward compatibility with providers that omit the field.
        if _methods and "client_secret_post" not in _methods and "client_secret_basic" in _methods:
            _auth_method = "client_secret_basic"
        else:
            _auth_method = "client_secret_post"
        metadata = OIDCMetadata(
            issuer=config.issuer,
            authorization_endpoint=_require_https_url(
                str(raw.get("authorization_endpoint", "")),
                what="authorization_endpoint",
                allow_loopback_http=allow_loopback_http,
            ),
            token_endpoint=_require_https_url(
                str(raw.get("token_endpoint", "")),
                what="token_endpoint",
                allow_loopback_http=allow_loopback_http,
            ),
            jwks_uri=_require_https_url(
                str(raw.get("jwks_uri", "")),
                what="jwks_uri",
                allow_loopback_http=allow_loopback_http,
            ),
            end_session_endpoint=(
                _require_https_url(
                    str(raw.get("end_session_endpoint", "")),
                    what="end_session_endpoint",
                    allow_loopback_http=allow_loopback_http,
                )
                if raw.get("end_session_endpoint")
                else None
            ),
            token_endpoint_auth_method=_auth_method,
        )
        self._entries[config.issuer] = (now + self._ttl, metadata)
        return metadata


class JWKSCache:
    """Bounded key cache with one rotation retry.

    Keys are cached for ``ttl_seconds``; a ``kid`` miss refreshes once
    before failing closed, so rotation converges without unbounded retries
    or indefinite acceptance of retired keys.
    """

    def __init__(self, ttl_seconds: float = DEFAULT_JWKS_TTL_SECONDS):
        if ttl_seconds <= 0 or ttl_seconds > MAX_TRANSACTION_TTL_SECONDS:
            raise OIDCConfigError("JWKS TTL must be within (0, 3600]s")
        self._ttl = ttl_seconds
        self._entries: dict[str, tuple[float, dict[str, Any]]] = {}
        self.refreshes = 0

    async def _load(
        self, metadata: OIDCMetadata, transport: OIDCTransport, timeout: float
    ) -> dict[str, Any]:
        try:
            raw = await transport.get_json(
                metadata.jwks_uri, timeout_seconds=timeout
            )
        except OIDCLoginError:
            raise
        except Exception as exc:
            raise OIDCUnavailableError(
                "identity provider key fetch failed; login cannot proceed"
            ) from exc
        if not isinstance(raw, dict) or not isinstance(raw.get("keys"), list):
            raise OIDCLoginError("invalid_metadata", "IdP JWKS document is malformed")
        return {str(k.get("kid")): k for k in raw["keys"] if isinstance(k, dict)}

    async def get_key(
        self,
        kid: str,
        metadata: OIDCMetadata,
        transport: OIDCTransport,
        *,
        timeout_seconds: float,
    ) -> dict[str, Any]:
        now = time.time()
        cached = self._entries.get(metadata.jwks_uri)
        if cached is not None and cached[0] > now and kid in cached[1]:
            return cached[1][kid]
        had_entry = cached is not None
        keys = await self._load(metadata, transport, timeout_seconds)
        if had_entry:
            self.refreshes += 1
        self._entries[metadata.jwks_uri] = (now + self._ttl, keys)
        if kid in keys:
            return keys[kid]
        # One bounded rotation retry: refresh once, then fail closed.
        self.refreshes += 1
        keys = await self._load(metadata, transport, timeout_seconds)
        self._entries[metadata.jwks_uri] = (now + self._ttl, keys)
        if kid in keys:
            return keys[kid]
        raise OIDCClaimError("unknown signing key; refusing rotated-out or forged key")


# ---------------------------------------------------------------------------
# Authorization transactions: single-use, replica/restart-safe, replay-safe
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AuthorizationTransaction:
    state: str
    nonce: str
    code_verifier: str
    redirect_uri: str
    return_path: str
    created_at: float


def _pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def validate_return_path(return_path: str, *, base_url: str) -> str:
    """Preserve deep-link returns only after strict same-origin/path validation.

    Accepts same-origin absolute URLs or root-relative paths. Rejects
    protocol-relative, absolute-foreign, and control-character destinations.
    """
    candidate = (return_path or "").strip()
    if not candidate:
        return "/"
    if any(ch in candidate for ch in ("\n", "\r", "\x00")):
        raise OIDCTransactionError("unsafe return destination")
    if candidate.startswith("/"):
        if candidate.startswith("//"):
            raise OIDCTransactionError("unsafe return destination")
        return candidate
    from moonmind.security.auth_modes_4120 import validate_callback_origin

    try:
        validate_callback_origin(candidate, base_url=base_url)
    except Exception as exc:
        raise OIDCTransactionError("unsafe return destination") from exc
    return candidate


def new_authorization_transaction(
    config: OIDCProviderConfig, *, return_path: str = "/", now: float | None = None
) -> AuthorizationTransaction:
    return AuthorizationTransaction(
        state=secrets.token_urlsafe(32),
        nonce=secrets.token_urlsafe(32),
        code_verifier=secrets.token_urlsafe(64),
        redirect_uri=config.callback_url,
        return_path=validate_return_path(
            return_path, base_url=config.callback_base_url
        ),
        created_at=time.time() if now is None else now,
    )


def build_authorization_url(
    config: OIDCProviderConfig,
    metadata: OIDCMetadata,
    transaction: AuthorizationTransaction,
) -> str:
    """Exact approved redirect destination plus transaction-bound parameters."""
    if transaction.redirect_uri != config.callback_url:
        raise OIDCTransactionError("transaction redirect is not the approved destination")
    params = {
        "client_id": config.client_id,
        "response_type": "code",
        "redirect_uri": transaction.redirect_uri,
        "scope": " ".join(config.scopes),
        "state": transaction.state,
        "nonce": transaction.nonce,
        "code_challenge": _pkce_challenge(transaction.code_verifier),
        "code_challenge_method": "S256",
    }
    # Preserve provider-supplied query parameters: discovery documents may
    # legally publish an authorization_endpoint that already contains a
    # query string (e.g. ?tenant=x). Merging avoids a second `?` that would
    # nest OAuth parameters inside the existing value and break every login.
    from urllib.parse import parse_qsl as _parse_qsl
    from urllib.parse import urlsplit as _split
    from urllib.parse import urlunsplit as _unsplit

    _parts = _split(metadata.authorization_endpoint)
    _existing = _parse_qsl(_parts.query, keep_blank_values=True)
    _merged = _existing + sorted(params.items())
    _query = urlencode(_merged)
    return _unsplit(
        (_parts.scheme, _parts.netloc, _parts.path, _query, _parts.fragment)
    )


class AuthTransactionStore(Protocol):
    """Durable single-use authorization-transaction mechanism."""

    async def create(self, transaction: AuthorizationTransaction) -> None:
        raise NotImplementedError

    async def consume(self, state: str, *, now: float | None = None) -> AuthorizationTransaction:
        """Return and invalidate the transaction; replay fails closed."""
        raise NotImplementedError


class InMemoryAuthTransactionStore:
    """Hermetic single-use store; replicas must share one durable instance."""

    def __init__(self, ttl_seconds: float = DEFAULT_TRANSACTION_TTL_SECONDS):
        self._ttl = ttl_seconds
        self._entries: dict[str, AuthorizationTransaction] = {}
        self._lock = asyncio.Lock()

    async def create(self, transaction: AuthorizationTransaction) -> None:
        async with self._lock:
            # Bounded cleanup: drop expired entries so abandoned logins
            # cannot grow the store without bound.
            _now = time.time()
            _expired = [
                key
                for key, entry in self._entries.items()
                if _now - entry.created_at > self._ttl
            ]
            for key in _expired:
                self._entries.pop(key, None)
            self._entries[transaction.state] = transaction

    async def consume(
        self, state: str, *, now: float | None = None
    ) -> AuthorizationTransaction:
        moment = time.time() if now is None else now
        async with self._lock:
            transaction = self._entries.pop((state or "").strip(), None)
        if transaction is None:
            raise OIDCTransactionError("unknown or already-consumed transaction")
        if moment - transaction.created_at > self._ttl:
            raise OIDCTransactionError("expired authorization transaction")
        return transaction


class FileBackedAuthTransactionStore:
    """Cross-instance durable fixture: two objects over one file.

    Models two API replicas (or a pre/post-restart process) observing one
    durable transaction truth without a shared in-memory object. Writes are
    atomic (tmp file + rename) so a concurrent consumer cannot resurrect a
    consumed transaction.
    """

    def __init__(self, path: Path, ttl_seconds: float = DEFAULT_TRANSACTION_TTL_SECONDS):
        self._path = path
        self._ttl = ttl_seconds
        if not path.exists():
            path.write_text(json.dumps({}), encoding="utf-8")

    def _load(self) -> dict[str, Any]:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        return raw if isinstance(raw, dict) else {}

    def _save(self, state: dict[str, Any]) -> None:
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")
        tmp.replace(self._path)

    async def create(self, transaction: AuthorizationTransaction) -> None:
        entries = self._load()
        _now = time.time()
        # Bounded cleanup: drop expired entries so abandoned logins cannot
        # grow the backing file without bound.
        for key in list(entries.keys()):
            try:
                _created = float(entries[key].get("created_at", 0))
            except Exception:
                _created = 0.0
            if _now - _created > self._ttl:
                entries.pop(key, None)
        entries[transaction.state] = {
            "nonce": transaction.nonce,
            "code_verifier": transaction.code_verifier,
            "redirect_uri": transaction.redirect_uri,
            "return_path": transaction.return_path,
            "created_at": transaction.created_at,
        }
        self._save(entries)

    async def consume(
        self, state: str, *, now: float | None = None
    ) -> AuthorizationTransaction:
        moment = time.time() if now is None else now
        key = (state or "").strip()
        entries = self._load()
        raw = entries.pop(key, None)
        if raw is None:
            raise OIDCTransactionError("unknown or already-consumed transaction")
        # Persist the consumption before validating TTL so a replay of an
        # expired transaction cannot succeed on another replica either.
        self._save(entries)
        try:
            transaction = AuthorizationTransaction(
                state=key,
                nonce=str(raw["nonce"]),
                code_verifier=str(raw["code_verifier"]),
                redirect_uri=str(raw["redirect_uri"]),
                return_path=str(raw["return_path"]),
                created_at=float(raw["created_at"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise OIDCTransactionError("malformed authorization transaction") from exc
        if moment - transaction.created_at > self._ttl:
            raise OIDCTransactionError("expired authorization transaction")
        return transaction


OIDC_TRANSACTION_TABLE = "moonmind_oidc_transactions"


def ensure_oidc_transaction_table_sql(
    table: str = OIDC_TRANSACTION_TABLE,
) -> str:
    """Idempotent DDL for the durable OIDC transaction record.

    The production ``DbAuthTransactionStore`` ensures this table the same
    way #4120 ensures its migration-decision table, so restarts and
    concurrent replicas share one single-use truth without a separate
    migration dependency.
    """
    return (
        f"CREATE TABLE IF NOT EXISTS {table} ("
        "state VARCHAR(128) PRIMARY KEY, "
        "nonce VARCHAR(128) NOT NULL, "
        "code_verifier VARCHAR(256) NOT NULL, "
        "redirect_uri TEXT NOT NULL, "
        "return_path TEXT NOT NULL, "
        "created_at DOUBLE PRECISION NOT NULL)"
    )


# ---------------------------------------------------------------------------
# Token exchange + ID-token validation (reject before enrollment/privilege)
# ---------------------------------------------------------------------------


async def exchange_code_for_tokens(
    *,
    code: str,
    transaction: AuthorizationTransaction,
    config: OIDCProviderConfig,
    metadata: OIDCMetadata,
    transport: OIDCTransport,
) -> dict[str, Any]:
    """Exchange one authorization code with PKCE at the verified endpoint."""
    if not (code or "").strip():
        raise OIDCTransactionError("missing authorization code")
    use_basic = (
        getattr(metadata, "token_endpoint_auth_method", "client_secret_post")
        == "client_secret_basic"
    )
    form = {
        "grant_type": "authorization_code",
        "code": code.strip(),
        "redirect_uri": transaction.redirect_uri,
        "client_id": config.client_id,
        "code_verifier": transaction.code_verifier,
    }
    auth: tuple[str, str] | None = None
    if use_basic:
        # Providers that only support client_secret_basic expect HTTP Basic
        # authentication; the secret never travels in the form body.
        auth = (config.client_id, config.client_secret)
    else:
        form["client_secret"] = config.client_secret
    try:
        try:
            raw = await transport.post_form(
                metadata.token_endpoint,
                form,
                timeout_seconds=config.timeout_seconds,
                auth=auth,
            )
        except TypeError:
            # Backward compatibility with transports that predate the auth
            # parameter (hermetic fixtures); they only support post.
            raw = await transport.post_form(
                metadata.token_endpoint, form, timeout_seconds=config.timeout_seconds
            )
    except OIDCLoginError:
        raise
    except Exception as exc:
        raise OIDCUnavailableError(
            "identity provider token exchange failed; login cannot proceed "
            "without changing auth mode, principal, or accepted tokens"
        ) from exc
    if not isinstance(raw, dict) or not raw.get("id_token"):
        raise OIDCLoginError("invalid_token_response", "IdP token response is malformed")
    return raw


def validate_id_token(
    id_token: str,
    *,
    jwk: dict[str, Any],
    config: OIDCProviderConfig,
    expected_nonce: str,
    now: float | None = None,
    leeway_seconds: int = 60,
) -> dict[str, Any]:
    """Validate issuer/audience/signature/time claims; fail closed.

    Every rejection happens before any enrollment or privilege mutation.
    Subjects are preserved case-sensitively; email/display claims are never
    treated as identity.
    """
    import jwt as _jwt

    moment = time.time() if now is None else now
    if not (id_token or "").strip():
        raise OIDCClaimError("missing identity token")
    token = id_token.strip()
    try:
        header = _jwt.get_unverified_header(token)
    except _jwt.InvalidTokenError as exc:
        raise OIDCClaimError("malformed identity token") from exc
    alg = str(header.get("alg", ""))
    if alg not in config.allowed_algorithms:
        raise OIDCClaimError(f"unexpected signing algorithm {alg!r}")
    try:
        kty = str(jwk.get("kty", ""))
        if kty == "oct":
            raw_b64 = str(jwk.get("k", ""))
            key_material = base64.urlsafe_b64decode(raw_b64 + "=" * (-len(raw_b64) % 4))
        else:
            # Generic JWK conversion handles RSA, EC (ES256), and OKP.
            # Falls back to RSA-only loader for backward compatibility.
            try:
                from jwt import PyJWK as _PyJWK

                key_material = _PyJWK(jwk).key
            except Exception:
                key_material = _jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(jwk))
    except OIDCClaimError:
        raise
    except Exception as exc:
        raise OIDCClaimError("unusable signing key") from exc
    try:
        claims = _jwt.decode(
            token,
            key_material,
            algorithms=[alg],
            issuer=config.issuer,
            audience=config.client_id,
            leeway=leeway_seconds,
            options={"require": ["exp", "iat", "iss", "aud", "sub"]},
        )
    except _jwt.InvalidTokenError as exc:
        raise OIDCClaimError(f"invalid identity token: {exc}") from exc
    if not isinstance(claims.get("sub"), str) or not claims["sub"]:
        raise OIDCClaimError("identity token has no subject")
    if claims.get("nonce") != expected_nonce:
        raise OIDCTransactionError("nonce mismatch; refusing replayed transaction")
    # Belt-and-braces time check against the caller's clock (PyJWT already
    # enforces exp/iat with leeway above).
    try:
        if float(claims["exp"]) < moment - leeway_seconds:
            raise OIDCClaimError("expired identity token")
    except (KeyError, TypeError, ValueError) as exc:
        raise OIDCClaimError("identity token has no usable expiry") from exc
    if config.require_mfa and not _has_mfa_evidence(claims, config=config):
        raise OIDCMFACutoverBlockedError(
            "deployment requires MFA but the identity token carries no MFA "
            "evidence (acr/amr); refusing cutover until the verified IdP "
            "policy asserts it"
        )
    # NOTE: the token itself lives only in this local scope and is never
    # logged. Diagnostics and event payloads go through
    # ``assert_no_raw_token``/``redacted_oidc_diagnostics`` instead; calling
    # the guard here would flag the in-memory validation input itself.
    return claims


def _has_mfa_evidence(
    claims: Mapping[str, Any], *, config: OIDCProviderConfig | None = None
) -> bool:
    """Recognize MFA evidence only from configured or known MFA values.

    ACR values are provider-specific: a password-only context such as
    ``acr="1"`` must not pass. When ``MOONMIND_OIDC_MFA_ACR_VALUES`` configures
    an explicit allowlist, only those exact ACRs pass. Otherwise only
    recognized MFA markers pass (InCommon silver/gold, explicit ``mfa``
    substrings, and OASIS MFA context classes). ``amr`` passes only when it
    contains a recognized second-factor method (otp/totp/hotp, sms, webauthn,
    fido, biometric, etc.), never any unknown non-password value.
    """
    configured = (
        tuple(v.lower() for v in (config.mfa_acr_values or ()))
        if config is not None
        else ()
    )
    acr_raw = str(claims.get("acr", "") or "").strip()
    acr = acr_raw.lower()
    if acr:
        if configured:
            if acr in configured:
                return True
        else:
            # Known MFA indicators; bare numeric/password values fail closed.
            _mfa_acr_markers = (
                "mfa",
                "silver",
                "gold",
                "urn:mace:incommon:iap:silver",
                "urn:mace:incommon:iap:gold",
                "urn:oasis:names:tc:saml:2.0:ac:classes",
                "time-synctoken",
                "mobiletwofactor",
            )
            if acr not in ("0", "1", "password", "pwd", "unspecified") and any(
                marker in acr for marker in _mfa_acr_markers
            ):
                return True
    amr = claims.get("amr") or []
    if isinstance(amr, list):
        _mfa_amr = {
            "mfa",
            "otp",
            "totp",
            "hotp",
            "sms",
            "webauthn",
            "fido",
            "fido2",
            "face",
            "fingerprint",
            "iris",
            "voice",
            "smartcard",
            "hwk",
            "swk",
        }
        for value in amr:
            token = str(value or "").strip().lower()
            if token in _mfa_amr:
                return True
    return False


def claims_to_validated_identity(claims: Mapping[str, Any], *, issuer: str):
    """Map verified ``(issuer, subject)`` to the shared identity shape.

    Email and display attributes stay informational: they are never the
    identity key, never normalized into the subject, and never merged
    across issuers. Case-sensitive subjects and issuer boundaries are
    preserved exactly; reserved subjects fail closed.
    """
    from moonmind.security.omnigent_auth_qualification import ValidatedIdentity

    subject = claims.get("sub")
    if not isinstance(subject, str) or not subject:
        raise OIDCClaimError("identity token has no subject")
    email = claims.get("email")
    return ValidatedIdentity(
        issuer=issuer,
        subject=subject,
        email=str(email) if isinstance(email, str) and email else None,
        upstream_is_admin=False,
    )


@dataclass(frozen=True)
class EnrollmentDecision:
    allowed: bool
    code: str
    detail: str


def evaluate_oidc_enrollment(
    *,
    existing_user_id: Any | None,
    account: Any | None,
    email_taken_by_other: bool,
    allow_unknown_users: bool,
) -> EnrollmentDecision:
    """Explicit admission policy for verified OIDC identities.

    * Returning identities keep their UUID and current local
      ``is_active``/``is_superuser`` flags (checked by the caller after
      this decision; inactive accounts fail closed there).
    * A valid IdP account, matching email, verified domain, or upstream
      admin list never grants superuser authority and never claims another
      account: ``email_taken_by_other`` fails closed with
      ``enrollment_required`` instead of merging.
    * Previously unknown users enroll only when ``allow_unknown_users``
      is explicitly enabled; otherwise they receive an actionable denial.
    """
    if existing_user_id is not None:
        if account is not None and not getattr(account, "is_active", True):
            return EnrollmentDecision(
                allowed=False,
                code="inactive",
                detail="account is disabled; explicit operator action required",
            )
        if email_taken_by_other:
            return EnrollmentDecision(
                allowed=False,
                code="enrollment_required",
                detail="email is owned by a different user; explicit operator "
                "enrollment is required, automatic linking is refused",
            )
        return EnrollmentDecision(
            allowed=True, code="returning", detail="known identity; UUID preserved"
        )
    if email_taken_by_other:
        return EnrollmentDecision(
            allowed=False,
            code="enrollment_required",
            detail="email is owned by a different user; explicit operator "
            "enrollment is required, automatic linking is refused",
        )
    if not allow_unknown_users:
        return EnrollmentDecision(
            allowed=False,
            code="enrollment_required",
            detail="unknown external user; enable MOONMIND_OIDC_ALLOW_UNKNOWN_USERS "
            "or enroll through explicit operator action",
        )
    return EnrollmentDecision(
        allowed=True, code="new_enrollment", detail="explicit admission policy allows enrollment"
    )


# ---------------------------------------------------------------------------
# Logout: MoonMind session always dies; IdP logout is best-effort
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LogoutPlan:
    """Honest logout semantics for OIDC mode.

    Browser logout revokes MoonMind browser authority only. An optional
    IdP logout redirect is best-effort: when it fails or is unconfigured,
    the MoonMind session stays revoked and the failure is reported, never
    retried into a resurrected session. IdP-wide logout is not guaranteed
    (downstream SSO sessions may survive); this limitation is surfaced to
    the operator instead of claimed away.
    """

    idp_logout_url: str | None
    idp_logout_configured: bool
    note: str = (
        "MoonMind session revoked; IdP-wide logout is best-effort and not "
        "guaranteed — upstream SSO sessions may survive. Local account "
        "disablement still blocks every request."
    )


def build_logout_plan(
    config: OIDCProviderConfig,
    metadata: OIDCMetadata | None,
    *,
    post_logout_redirect: str | None = None,
) -> LogoutPlan:
    if metadata is None or not metadata.end_session_endpoint:
        return LogoutPlan(idp_logout_url=None, idp_logout_configured=False)
    target = post_logout_redirect or config.callback_base_url
    separator = "&" if "?" in metadata.end_session_endpoint else "?"
    return LogoutPlan(
        idp_logout_url=(
            f"{metadata.end_session_endpoint}{separator}post_logout_redirect_uri={target}"
        ),
        idp_logout_configured=True,
    )


# ---------------------------------------------------------------------------
# Trusted-header mode: trust the ingress/network, never the header text
# ---------------------------------------------------------------------------

DEFAULT_TRUSTED_PROXY_HEADER = "X-MoonMind-Proxy-User"
TRUSTED_PROXY_PREFIX = "proxy:"
MAX_PROXY_IDENTITY_LENGTH = 1024


@dataclass(frozen=True)
class TrustedProxyConfig:
    """Operator-configured trusted-header admission.

    ``namespace`` scopes the ``proxy:<namespace>:<stable-id>`` issuer mapped
    through #4119. ``trusted_ingress`` records trust in the connecting
    proxy/network (set via ``MOONMIND_TRUSTED_INGRESS=1`` with an audited
    ingress path); a header claiming trust never suffices. ``allow_email_only``
    gates email-only proxy integrations behind explicit enrollment policy;
    ``allow_unknown_users`` gates first-time provisioning.
    """

    namespace: str
    header_name: str = DEFAULT_TRUSTED_PROXY_HEADER
    trusted_ingress: bool = False
    trusted_proxies: tuple[str, ...] = ()
    allow_email_only: bool = False
    allow_unknown_users: bool = False

    def __post_init__(self) -> None:
        namespace = (self.namespace or "").strip()
        if not namespace or len(namespace) > 128:
            raise OIDCConfigError("trusted-proxy namespace must be 1..128 chars")
        if any(ch in namespace for ch in (":", "/", "\n", "\r", " ")):
            raise OIDCConfigError("trusted-proxy namespace must not contain ':', '/', or spaces")
        header = (self.header_name or "").strip()
        if not header or len(header) > 128:
            raise OIDCConfigError("trusted-proxy header name must be 1..128 chars")
        if not self.trusted_ingress:
            raise OIDCConfigError(
                "trusted-header mode requires MOONMIND_TRUSTED_INGRESS=1 with an "
                "audited ingress path; refusing to trust headers on direct ingress"
            )
        if not self.trusted_proxies:
            raise OIDCConfigError(
                "trusted-header mode requires a non-empty MOONMIND_TRUSTED_PROXIES "
                "allowlist; an empty allowlist would trust every direct client"
            )


def resolve_trusted_proxy_config(
    environ: Mapping[str, str] | None = None,
) -> TrustedProxyConfig:
    """Build the trusted-proxy config from explicit operator inputs."""
    import os

    from moonmind.security.auth_modes_4120 import validate_trusted_proxy_config

    env: Mapping[str, str] = os.environ if environ is None else environ
    namespace = str(env.get("MOONMIND_TRUSTED_PROXY_NAMESPACE", "") or "").strip()
    header_name = (
        str(env.get("MOONMIND_TRUSTED_PROXY_HEADER", "") or "").strip()
        or DEFAULT_TRUSTED_PROXY_HEADER
    )
    trusted_ingress = _env_flag(env, "MOONMIND_TRUSTED_INGRESS")
    proxies = validate_trusted_proxy_config(env.get("MOONMIND_TRUSTED_PROXIES", ""))
    return TrustedProxyConfig(
        namespace=namespace,
        header_name=header_name,
        trusted_ingress=trusted_ingress,
        trusted_proxies=proxies,
        allow_email_only=_env_flag(env, "MOONMIND_TRUSTED_PROXY_ALLOW_EMAIL_ONLY"),
        allow_unknown_users=_env_flag(env, "MOONMIND_TRUSTED_PROXY_ALLOW_UNKNOWN_USERS"),
    )


def proxy_issuer_for_namespace(namespace: str) -> str:
    return f"{TRUSTED_PROXY_PREFIX}{namespace}"


def extract_trusted_proxy_identity(
    headers: Mapping[str, Any],
    config: TrustedProxyConfig,
    *,
    trusted_ingress: bool,
) -> tuple[str, str]:
    """Extract and validate one asserted ``(issuer, subject)`` pair.

    The ingress must have stripped user-supplied identity headers and
    replaced them: ``trusted_ingress`` is trust in the connecting
    proxy/network, and ``False`` fails closed (direct API and alternative
    listener bypasses blocked). Duplicated or malformed identity headers,
    missing/reserved/unknown identities, and attacker-controlled forwarded
    host/proto values all fail closed. Never falls back to ``local`` or
    ``__public__``.
    """
    if not trusted_ingress or not config.trusted_ingress:
        raise TrustedProxyError(
            "untrusted_ingress",
            "trusted-header identity requires an explicitly trusted ingress; "
            "direct API connections never accept identity headers",
        )
    lowered = {str(k).lower(): v for k, v in dict(headers).items()}
    raw = lowered.get(config.header_name.lower(), None)
    if isinstance(raw, (list, tuple)):
        raise TrustedProxyError(
            "duplicate_header",
            "duplicated identity headers are rejected; the ingress must strip "
            "and replace them with exactly one asserted value",
        )
    asserted = str(raw or "").strip()
    if not asserted:
        raise TrustedProxyError(
            "missing_identity",
            "missing proxy identity fails closed; never resolves to a reserved "
            "or fallback principal",
        )
    if len(asserted) > MAX_PROXY_IDENTITY_LENGTH:
        raise TrustedProxyError("malformed_identity", "proxy identity is too long")
    if asserted in ("local", "__public__"):
        raise TrustedProxyError(
            "reserved_identity", f"reserved identity {asserted!r} is rejected"
        )
    if any(ch in asserted for ch in ("\n", "\r", "\x00", ":", "/")):
        raise TrustedProxyError("malformed_identity", "proxy identity is malformed")
    if "@" in asserted and not config.allow_email_only:
        raise TrustedProxyError(
            "email_only_requires_policy",
            "email-only proxy identities need explicit enrollment policy "
            "(MOONMIND_TRUSTED_PROXY_ALLOW_EMAIL_ONLY=1) and cannot silently "
            "merge existing users",
        )
    return proxy_issuer_for_namespace(config.namespace), asserted


def evaluate_trusted_proxy_enrollment(
    *,
    existing_user_id: Any | None,
    account: Any | None,
    email_taken_by_other: bool,
    config: TrustedProxyConfig,
) -> EnrollmentDecision:
    """Fail-closed enrollment for proxy-asserted identities.

    Missing/reserved/unknown identities never invoke an upstream fallback;
    email-only integrations need explicit enrollment and reassignment policy
    and cannot silently merge. Local account disablement still blocks every
    request (checked by the caller after this decision).
    """
    if existing_user_id is not None:
        if account is not None and not getattr(account, "is_active", True):
            return EnrollmentDecision(
                allowed=False,
                code="inactive",
                detail="account is disabled; proxy assertions cannot re-enable it",
            )
        if email_taken_by_other:
            return EnrollmentDecision(
                allowed=False,
                code="enrollment_required",
                detail="proxy identity email is owned by a different user; explicit "
                "operator enrollment and reassignment policy required",
            )
        return EnrollmentDecision(
            allowed=True, code="returning", detail="known proxy identity; UUID preserved"
        )
    if not config.allow_unknown_users:
        return EnrollmentDecision(
            allowed=False,
            code="enrollment_required",
            detail="unknown proxy identity; explicit operator enrollment required",
        )
    if email_taken_by_other:
        return EnrollmentDecision(
            allowed=False,
            code="enrollment_required",
            detail="proxy identity email is owned by a different user; explicit "
            "operator enrollment and reassignment policy required",
        )
    return EnrollmentDecision(
        allowed=True, code="new_enrollment", detail="explicit proxy admission allows enrollment"
    )


def trusted_proxy_logout_note() -> str:
    """Honest proxy logout/revocation semantics for operator surfaces."""
    return (
        "Local logout revokes the MoonMind session, but continued upstream "
        "proxy assertions will re-authenticate on the next request until the "
        "proxy stops asserting the identity. Local account disablement still "
        "blocks every request."
    )


def strip_asserted_identity_headers(
    headers: Mapping[str, str], *, header_names: tuple[str, ...]
) -> dict[str, str]:
    """Remove asserted identity headers before forwarding to other services.

    A trusted header is a transport assertion, not permanent authority: it
    and unrelated runtime credentials are never forwarded.
    """
    lowered = {name.lower() for name in header_names}
    return {k: v for k, v in dict(headers).items() if k.lower() not in lowered}


# ---------------------------------------------------------------------------
# Redacted observability: never print raw token responses
# ---------------------------------------------------------------------------


def assert_no_raw_token(container: Any, *, context: str) -> None:
    """Fail when a payload would leak token/secret material into logs."""
    try:
        rendered = json.dumps(container, default=str)
    except Exception:
        rendered = str(container)
    for marker in _TOKEN_MARKERS:
        if marker in rendered:
            raise OIDCConfigError(
                f"refusing to log token material in {context}: found {marker!r}"
            )


def redacted_oidc_diagnostics(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Secret-free OIDC diagnostics (issuer/endpoints/reasons only)."""
    from moonmind.security.auth_modes_4120 import redacted_diagnostics

    redacted = redacted_diagnostics(payload)
    assert_no_raw_token(redacted, context="redacted_oidc_diagnostics")
    return redacted


def emit_advanced_auth_event(
    kind: str,
    *,
    mode: str,
    reason: str,
    request_id: str | None = None,
    user_id: str | None = None,
) -> dict[str, Any]:
    """Redacted advanced-auth event through the #4121 observability owner."""
    from moonmind.security.session_authority_4121 import emit_auth_event

    return emit_auth_event(
        kind, mode=mode, reason=reason, request_id=request_id, user_id=user_id
    )


__all__ = [
    "DEFAULT_OIDC_CALLBACK_PATH",
    "DEFAULT_TRUSTED_PROXY_HEADER",
    "AuthTransactionStore",
    "AuthorizationTransaction",
    "EnrollmentDecision",
    "EnrollmentRequiredError",
    "FileBackedAuthTransactionStore",
    "InMemoryAuthTransactionStore",
    "JWKSCache",
    "LogoutPlan",
    "OIDCClaimError",
    "OIDCConfigError",
    "OIDCLoginError",
    "OIDCMFACutoverBlockedError",
    "OIDCMetadata",
    "OIDCMetadataCache",
    "OIDCProviderConfig",
    "OIDCTransactionError",
    "OIDCTransport",
    "OIDCUnavailableError",
    "TrustedProxyConfig",
    "TrustedProxyError",
    "build_authorization_url",
    "build_logout_plan",
    "claims_to_validated_identity",
    "ensure_oidc_transaction_table_sql",
    "evaluate_oidc_enrollment",
    "evaluate_trusted_proxy_enrollment",
    "exchange_code_for_tokens",
    "extract_trusted_proxy_identity",
    "emit_advanced_auth_event",
    "new_authorization_transaction",
    "proxy_issuer_for_namespace",
    "redacted_oidc_diagnostics",
    "assert_no_raw_token",
    "resolve_oidc_provider_config",
    "resolve_trusted_proxy_config",
    "strip_asserted_identity_headers",
    "trusted_proxy_logout_note",
    "validate_id_token",
    "validate_return_path",
]
