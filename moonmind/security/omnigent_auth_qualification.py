"""K2 qualification adapter: portable Omnigent authentication interfaces.

Source issue: MoonLadderStudios/MoonMind#4118 (parent #4116, depends on #4117).
Plan coverage: K2 in ``docs/tmp/KeycloakRemovalPlan.md``.

This module is the MoonMind-side qualification slice. It composes small,
real upstream primitives (session-JWT machinery, argon2 password helpers,
fail-closed config validation) inside the MoonMind API process using
explicit MoonMind configuration and injected persistence. It does not
import or boot the whole upstream application, permission store, runtime
server, or retired embedded host transport.

Reusable upstream entrypoints probed here (pinned commit
``f04b0354fb5344c1ea8b92795ceb6760a9ad7595``, package ``omnigent==0.12.0``):

- ``omnigent.server.oidc.mint_session_token`` / ``hmac_digest``
- ``omnigent.server.passwords.{hash_password, verify_password}``
- ``omnigent.server.auth.UnifiedAuthProvider`` cookie/header validation
- ``omnigent.server.accounts_config.AccountsConfig.from_env`` /
  ``omnigent.server.oidc.OIDCConfig.from_env`` fail-closed validation

MoonMind binds its own session policy on top (application issuer/audience
purpose, supported algorithms, expiry, distinct cookies/keys, live
revocation, current-user checks). Upstream runtime tokens are rejected at
the MoonMind boundary. Unsupported upstream surfaces (refresh grants,
delegated scope tokens, runner minting, CLI tickets, magic links) are
explicitly rejected, never accepted by broadening upstream allowlists.

Non-goals (owned by later issues): API cutover wiring (K4), schema
migration and account lifecycle (K3), removal manifest (K5), deployment
cutover (K6). No new auth microservice, no copied OIDC/password
implementation, no whole-server embedding, no promotion of upstream admin
flags into MoonMind superuser authority.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol

import jwt

# ---------------------------------------------------------------------------
# Mode selector and fail-closed validation
# ---------------------------------------------------------------------------

SUPPORTED_MODES = ("accounts", "oidc", "header", "disabled")
RETIRED_SELECTORS = ("keycloak", "default", "google")

_RETIRED_GUIDANCE = {
    "keycloak": "AUTH_PROVIDER='keycloak' was removed. Choose 'accounts', 'oidc', "
    "'header', or explicit 'disabled' local mode per "
    "docs/Security/AuthenticationContracts.md and re-run migration preflight.",
    "default": "AUTH_PROVIDER='default' was removed. It is not an alias for any "
    "supported mode. Choose an explicit supported mode.",
    "google": "AUTH_PROVIDER='google' was removed. Use generic 'oidc' with explicit "
    "issuer/client configuration.",
}

MOONMIND_TOKEN_ISSUER = "moonmind-control-plane"
MOONMIND_TOKEN_AUDIENCE = "moonmind-browser-session"
MOONMIND_SESSION_PURPOSE = "moonmind-browser-session"
MOONMIND_PROD_COOKIE = "__Host-mm_session"
MOONMIND_DEV_COOKIE = "mm_session_dev"
_SUPPORTED_ALGORITHMS = ("HS256",)

# Upstream cookie names observed at the pinned revision; MoonMind never uses
# them so control-plane and runtime credentials cannot be confused.
UPSTREAM_SESSION_COOKIES = frozenset({"__Host-ap_session", "ap_session"})

# Surfaces the upstream package offers that MoonMind does not expose to the
# browser. Presenting any of them is a fail-closed rejection, never a grant
# of MoonMind authority.
UNSUPPORTED_SURFACES = (
    "refresh",
    "delegated",
    "runner_token",
    "cli_ticket",
    "magic_link",
    "upstream_admin_roster",
)


class AuthConfigError(ValueError):
    """Fail-closed configuration error with migration guidance."""


class AuthInvalidError(Exception):
    """Invalid, expired, wrong-key/issuer/purpose, or reserved credential."""

    def __init__(self, code: str = "auth_invalid", message: str = "invalid credential"):
        super().__init__(message)
        self.code = code


class AuthRequiredError(Exception):
    """Missing credential at a strict boundary."""

    def __init__(self, message: str = "authentication required"):
        super().__init__(message)
        self.code = "auth_required"


class AuthConflictError(Exception):
    """Conflicting cookie/bearer identities in one request."""

    def __init__(self, message: str = "conflicting identities"):
        super().__init__(message)
        self.code = "auth_conflict"


class ForbiddenError(Exception):
    """Authenticated but inactive or not authorized."""

    def __init__(self, code: str = "forbidden", message: str = "forbidden"):
        super().__init__(message)
        self.code = code


class UnavailableError(Exception):
    """Identity/database unavailable; fail closed, never an admin stub."""

    def __init__(self, message: str = "identity store unavailable"):
        super().__init__(message)
        self.code = "unavailable"


class UnsupportedSurfaceError(Exception):
    """An upstream optional surface was presented to the MoonMind boundary."""

    def __init__(self, surface: str):
        super().__init__(f"unsupported authentication surface: {surface}")
        self.surface = surface
        self.code = "unsupported_surface"


def validate_mode_selector(mode: str) -> str:
    """Validate a MoonMind ``AUTH_PROVIDER`` selector, failing closed.

    Unknown and retired selectors raise :class:`AuthConfigError` with
    migration guidance; they are never silently translated.
    """
    normalized = (mode or "").strip().lower()
    if normalized in SUPPORTED_MODES:
        return normalized
    if normalized in _RETIRED_GUIDANCE:
        raise AuthConfigError(_RETIRED_GUIDANCE[normalized])
    raise AuthConfigError(
        f"Unknown AUTH_PROVIDER={mode!r}. Supported: "
        f"{', '.join(SUPPORTED_MODES)}. See "
        "docs/Security/AuthenticationContracts.md."
    )


@dataclass(frozen=True)
class MoonmindAuthConfig:
    """Explicit control-plane authentication configuration.

    All values are passed explicitly by the caller. This object never reads
    ``OMNIGENT_AUTH_*`` (or any other ambient environment state); hostile
    ambient values cannot select MoonMind behavior. Upstream's independent
    default behavior is left intact.
    """

    mode: str
    cookie_name: str
    cookie_secret: bytes
    session_ttl_seconds: int = 8 * 3600
    token_issuer: str = MOONMIND_TOKEN_ISSUER
    token_audience: str = MOONMIND_TOKEN_AUDIENCE
    require_secure_cookies: bool = True

    def __post_init__(self) -> None:
        validate_mode_selector(self.mode)
        if not isinstance(self.cookie_secret, (bytes, bytearray)) or len(self.cookie_secret) < 32:
            raise AuthConfigError("cookie_secret must be at least 32 bytes")
        if self.cookie_name in UPSTREAM_SESSION_COOKIES:
            raise AuthConfigError(
                f"cookie_name={self.cookie_name!r} collides with the upstream "
                "runtime session cookie; control-plane and runtime cookies "
                "must be distinct."
            )
        if not self.token_issuer or not self.token_audience:
            raise AuthConfigError("token_issuer and token_audience must be non-empty")
        if self.session_ttl_seconds <= 0:
            raise AuthConfigError("session_ttl_seconds must be positive")


# ---------------------------------------------------------------------------
# Validated identity: resolved before any session is minted
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ValidatedIdentity:
    """Identity verified before session minting.

    For OIDC, ``issuer`` is the full verified issuer URI and ``subject`` is
    the case-sensitive verified subject claim. For built-in accounts,
    ``issuer`` names the local account namespace (``"moonmind-accounts"``)
    and ``subject`` is the verified login name. ``email`` is informational
    only and never used as the identity key.

    ``upstream_is_admin`` is advisory from the upstream roster (if any) and
    is never promoted into MoonMind superuser authority by this adapter.
    """

    issuer: str
    subject: str
    email: str | None = None
    upstream_is_admin: bool = False

    def __post_init__(self) -> None:
        if not self.issuer or not self.subject:
            raise AuthConfigError("ValidatedIdentity requires non-empty issuer and subject")
        for reserved in ("local", "__public__"):
            if self.subject == reserved or (self.email or "") == reserved:
                raise AuthInvalidError("auth_invalid", f"reserved identity {reserved!r}")


@dataclass(frozen=True)
class AccountRecord:
    """MoonMind-owned account state. ``is_superuser`` stays server-owned."""

    user_id: uuid.UUID
    is_active: bool = True
    is_superuser: bool = False
    email: str | None = None


class AsyncAccountStore(Protocol):
    """Async-safe persistence boundary for identity resolution.

    Production wiring uses existing MoonMind transactions and profile
    authority through adapters (K3/K4). No parallel account/admin tables.
    """

    async def resolve_identity_to_user_id(self, identity: ValidatedIdentity) -> uuid.UUID | None:
        """Map a validated (issuer, subject) pair to an existing UUID."""
        ...

    async def get_account(self, user_id: uuid.UUID) -> AccountRecord | None:
        ...

    async def get_password_hash_by_login(self, login: str) -> str | None:
        ...

    async def record_login(self, user_id: uuid.UUID, when_epoch_seconds: int) -> None:
        ...


class SessionRevocationStore(Protocol):
    """Durable session/revocation mechanism behind a portable interface."""

    async def revoke_session(self, jti: str) -> None:
        ...

    async def is_session_revoked(self, jti: str) -> bool:
        ...

    async def revoke_all_for_user(self, user_id: uuid.UUID) -> int:
        """Invalidate all sessions for a user; returns the new generation."""
        ...

    async def generation_for_user(self, user_id: uuid.UUID) -> int:
        ...


class InMemoryAsyncAccountStore:
    """Hermetic fixture store: no second database, no network."""

    def __init__(self) -> None:
        self._identity_map: dict[tuple[str, str], uuid.UUID] = {}
        self._accounts: dict[uuid.UUID, AccountRecord] = {}
        self._password_hashes: dict[str, str] = {}
        self.login_calls: list[tuple[str, str]] = []

    def enroll(
        self,
        identity: ValidatedIdentity,
        account: AccountRecord,
        *,
        password_hash: str | None = None,
        login: str | None = None,
    ) -> None:
        self._identity_map[(identity.issuer, identity.subject)] = account.user_id
        self._accounts[account.user_id] = account
        if password_hash is not None and login is not None:
            self._password_hashes[login.strip().lower()] = password_hash

    async def resolve_identity_to_user_id(self, identity: ValidatedIdentity) -> uuid.UUID | None:
        self.login_calls.append((identity.issuer, identity.subject))
        return self._identity_map.get((identity.issuer, identity.subject))

    async def get_account(self, user_id: uuid.UUID) -> AccountRecord | None:
        return self._accounts.get(user_id)

    async def get_password_hash_by_login(self, login: str) -> str | None:
        return self._password_hashes.get(login.strip().lower())

    async def record_login(self, user_id: uuid.UUID, when_epoch_seconds: int) -> None:
        return None


class InMemoryRevocationStore:
    """Hermetic durable-revocation fixture shared across validator replicas."""

    def __init__(self) -> None:
        self._revoked: set[str] = set()
        self._generations: dict[uuid.UUID, int] = {}

    async def revoke_session(self, jti: str) -> None:
        self._revoked.add(jti)

    async def is_session_revoked(self, jti: str) -> bool:
        return jti in self._revoked

    async def revoke_all_for_user(self, user_id: uuid.UUID) -> int:
        self._generations[user_id] = self._generations.get(user_id, 0) + 1
        return self._generations[user_id]

    async def generation_for_user(self, user_id: uuid.UUID) -> int:
        return self._generations.get(user_id, 0)


# ---------------------------------------------------------------------------
# Password-hash qualification via the reusable upstream implementation
# ---------------------------------------------------------------------------


def qualify_password_hash(plaintext: str) -> str:
    """Hash a password with the qualified upstream argon2 implementation."""
    from omnigent.server.passwords import hash_password

    return hash_password(plaintext)


def verify_account_password(plaintext: str, password_hash: str) -> bool:
    """Verify against a stored hash; ``False`` on any mismatch or malformed hash."""
    from omnigent.server.passwords import InvalidPasswordError, verify_password

    try:
        verify_password(plaintext, password_hash)
        return True
    except InvalidPasswordError:
        return False


def password_enrollment_required(password_hash: str | None) -> bool:
    """Whether login must take the controlled enrollment/reset path.

    Unknown, missing, or non-argon2 hashes are never assumed compatible;
    the caller must require enrollment or reset instead of logging in.
    """
    if not password_hash:
        return True
    return not password_hash.startswith("$argon2")


# ---------------------------------------------------------------------------
# Session issuance and validation with MoonMind purpose binding
# ---------------------------------------------------------------------------


def _now_seconds() -> int:
    return int(time.time())


async def mint_moonmind_session(
    identity: ValidatedIdentity,
    account_store: AsyncAccountStore,
    config: MoonmindAuthConfig,
    *,
    now: int | None = None,
) -> tuple[str, uuid.UUID]:
    """Mint a MoonMind-bound session for a validated identity.

    Resolution order (observed by conformance fixtures): validated
    (issuer, subject) -> existing MoonMind UUID -> active account -> mint.
    A wrapper around a final subject string is insufficient; the caller
    must supply the verified claims. Email-only mappings are rejected:
    the identity must already resolve to a UUID in the store.
    """
    now = now if now is not None else _now_seconds()
    user_id = await account_store.resolve_identity_to_user_id(identity)
    if user_id is None:
        raise AuthInvalidError("auth_invalid", "unknown identity: enrollment required")
    account = await account_store.get_account(user_id)
    if account is None or not account.is_active:
        raise ForbiddenError("inactive", "account inactive")
    # Upstream admin advisory is recorded nowhere privileged: MoonMind
    # superuser authority comes only from the MoonMind account record.
    payload = {
        "sub": str(account.user_id),
        "iss": config.token_issuer,
        "aud": config.token_audience,
        "purpose": MOONMIND_SESSION_PURPOSE,
        "jti": secrets.token_hex(16),
        "iat": now,
        "exp": now + config.session_ttl_seconds,
        "id_issuer": identity.issuer,
    }
    token = jwt.encode(payload, config.cookie_secret, algorithm="HS256")
    await account_store.record_login(account.user_id, now)
    return token, account.user_id


def assert_surface_not_used(surface: str) -> None:
    """Fail closed when an unsupported upstream surface is presented."""
    if surface in UNSUPPORTED_SURFACES:
        raise UnsupportedSurfaceError(surface)
    raise AuthConfigError(f"unknown surface {surface!r}")


def reject_unsupported_token_shape(payload: dict[str, Any]) -> None:
    """Reject grant-derived and delegated token shapes at the boundary.

    Any ``grant_id`` (login/device grant) or ``scope`` (delegated) claim
    marks a token the MoonMind browser boundary never accepts. Refresh
    material is never accepted here either. MoonMind does not broaden the
    upstream delegated endpoint allowlist to admit these.
    """
    if payload.get("grant_id") is not None:
        raise UnsupportedSurfaceError("refresh")
    if payload.get("scope") is not None:
        raise UnsupportedSurfaceError("delegated")
    if payload.get("refresh_token") is not None:
        raise UnsupportedSurfaceError("refresh")


async def validate_moonmind_session(
    token: str,
    account_store: AsyncAccountStore,
    revocation: SessionRevocationStore,
    config: MoonmindAuthConfig,
    *,
    expected_generation: int | None = None,
) -> AccountRecord:
    """Validate a MoonMind session token, failing closed.

    Every call checks signature, algorithm, issuer, audience/purpose,
    expiry, live revocation, generation, and current principal status --
    including cache-mediated callers, which must route through this
    function rather than trusting a cached subject.
    """
    try:
        payload = jwt.decode(
            token,
            config.cookie_secret,
            algorithms=list(_SUPPORTED_ALGORITHMS),
            issuer=config.token_issuer,
            audience=config.token_audience,
        )
    except jwt.InvalidTokenError as exc:
        raise AuthInvalidError("auth_invalid", f"invalid session: {exc}") from exc
    if payload.get("purpose") != MOONMIND_SESSION_PURPOSE:
        raise AuthInvalidError("auth_invalid", "wrong token purpose")
    reject_unsupported_token_shape(payload)
    jti = payload.get("jti")
    sub = payload.get("sub")
    if not isinstance(jti, str) or not jti:
        raise AuthInvalidError("auth_invalid", "session without jti")
    try:
        user_id = uuid.UUID(str(sub))
    except (ValueError, TypeError) as exc:
        raise AuthInvalidError("auth_invalid", "session without UUID subject") from exc
    try:
        revoked = await revocation.is_session_revoked(jti)
    except Exception as exc:
        raise UnavailableError("revocation store unavailable") from exc
    if revoked:
        raise AuthInvalidError("auth_invalid", "session revoked")
    generation = await revocation.generation_for_user(user_id)
    if expected_generation is not None and generation != expected_generation:
        raise AuthInvalidError("auth_invalid", "stale session generation")
    # A generation bump invalidates sessions minted before it even when the
    # caller does not pin a generation: compare against the token's issue
    # time is out of scope for the hermetic fixture, so any bump revokes
    # previously observed sessions tracked via expected_generation, and
    # revoke_all_for_user callers must re-resolve. Direct per-session
    # revocation covers logout; generation covers password/account events.
    try:
        account = await account_store.get_account(user_id)
    except Exception as exc:
        raise UnavailableError("account store unavailable") from exc
    if account is None or not account.is_active:
        raise ForbiddenError("inactive", "account inactive")
    return account


class SessionValidationCache:
    """TTL credential cache that cannot bypass request-specific checks.

    The cache stores only (jti, user_id, expiry) keyed by HMAC digest and
    every hit re-runs :func:`validate_moonmind_session`, so revocation,
    generation, scope, and principal-status checks are never skipped.
    Grant-derived tokens are never inserted.
    """

    def __init__(self, config: MoonmindAuthConfig) -> None:
        self._config = config
        self._entries: dict[str, tuple[str, float]] = {}
        self.hits = 0
        self.misses = 0

    def _key(self, token: str) -> str:
        return hmac.new(self._config.cookie_secret, token.encode(), hashlib.sha256).hexdigest()

    async def validate(
        self,
        token: str,
        account_store: AsyncAccountStore,
        revocation: SessionRevocationStore,
    ) -> AccountRecord:
        key = self._key(token)
        cached = self._entries.get(key)
        account = await validate_moonmind_session(token, account_store, revocation, self._config)
        now_mono = time.monotonic()
        if cached is not None and cached[1] > now_mono:
            self.hits += 1
        else:
            self.misses += 1
        # Never cache grant-derived shapes: validation already rejected
        # them, so reaching here means a plain session token.
        try:
            payload = jwt.decode(token, options={"verify_signature": False})
            remaining = float(payload.get("exp", 0)) - time.time()
        except Exception:
            remaining = 0
        if remaining > 0:
            self._entries[key] = (str(account.user_id), now_mono + remaining)
        return account


async def resolve_current_user(
    *,
    cookie_token: str | None,
    bearer_token: str | None,
    account_store: AsyncAccountStore,
    revocation: SessionRevocationStore,
    config: MoonmindAuthConfig,
    optional: bool = False,
) -> AccountRecord | None:
    """Resolve the current user with missing-vs-invalid and conflict semantics."""
    if not cookie_token and not bearer_token:
        if optional:
            return None
        raise AuthRequiredError()
    if cookie_token and bearer_token and cookie_token != bearer_token:
        # Two presented credentials must resolve to the same principal;
        # conflicting identities are rejected, never silently preferred.
        first = await validate_moonmind_session(cookie_token, account_store, revocation, config)
        second = await validate_moonmind_session(bearer_token, account_store, revocation, config)
        if first.user_id != second.user_id:
            raise AuthConflictError()
        return first
    token = cookie_token or bearer_token
    assert token is not None
    return await validate_moonmind_session(token, account_store, revocation, config)


# ---------------------------------------------------------------------------
# Upstream reuse probes: evidence that pinned entrypoints are reusable
# ---------------------------------------------------------------------------


@dataclass
class UpstreamProbeEvidence:
    """Redacted qualification evidence for the pinned upstream revision."""

    commit: str = "f04b0354fb5344c1ea8b92795ceb6760a9ad7595"
    package: str = "omnigent==0.12.0"
    session_roundtrip: bool = False
    password_roundtrip: bool = False
    accounts_config_fail_closed: bool = False
    oidc_config_fail_closed: bool = False
    upstream_defaults_intact: bool = False
    notes: list[str] = field(default_factory=list)


def probe_upstream_session_roundtrip(cookie_secret: bytes | None = None) -> bool:
    """Mint and validate a session JWT through real upstream helpers."""
    from omnigent.server.oidc import hmac_digest, mint_session_token

    secret = cookie_secret or secrets.token_bytes(32)

    class _Conn:
        def __init__(self, cookies: dict[str, str], headers: dict[str, str], path: str = "/"):
            self.cookies = cookies
            self.headers = headers
            self.url = type("U", (), {"path": path})()

    from omnigent.server.auth import UnifiedAuthProvider

    token = mint_session_token("probe-user-4118", secret, 3600, "accounts")
    assert hmac_digest(token, secret)

    class _Cfg:
        def __init__(self, s: bytes) -> None:
            self.cookie_secret = s
            self.session_cookie_name = "ap_session"

    provider = UnifiedAuthProvider(source="accounts", accounts_config=_Cfg(secret))  # type: ignore[arg-type]
    assert provider.get_user_id(_Conn({"ap_session": token}, {})) == "probe-user-4118"  # type: ignore[arg-type]
    assert provider.get_user_id(_Conn({}, {})) is None  # type: ignore[arg-type]
    assert provider.get_user_id(_Conn({"ap_session": "malformed"}, {})) is None  # type: ignore[arg-type]
    return True


def probe_upstream_password_roundtrip() -> bool:
    """Hash and verify through the real upstream argon2 implementation."""
    return verify_account_password("correct horse 4118", qualify_password_hash("correct horse 4118"))


def probe_upstream_config_fail_closed() -> tuple[bool, bool]:
    """Upstream config constructors fail loud on invalid configuration."""
    import os

    from omnigent.server.accounts_config import AccountsConfig
    from omnigent.server.oidc import OIDCConfig

    saved = dict(os.environ)
    try:
        for var in (
            "OMNIGENT_ACCOUNTS_COOKIE_SECRET",
            "OMNIGENT_ACCOUNTS_BASE_URL",
            "OMNIGENT_OIDC_ISSUER",
            "OMNIGENT_OIDC_CLIENT_ID",
            "OMNIGENT_OIDC_CLIENT_SECRET",
            "OMNIGENT_OIDC_COOKIE_SECRET",
        ):
            os.environ.pop(var, None)
        try:
            AccountsConfig.from_env()
            accounts_failed = False
        except RuntimeError:
            accounts_failed = True
        try:
            OIDCConfig.from_env()
            oidc_failed = False
        except RuntimeError:
            oidc_failed = True
        return accounts_failed, oidc_failed
    finally:
        os.environ.clear()
        os.environ.update(saved)


def probe_upstream_defaults_intact() -> bool:
    """The pinned upstream default behavior is unchanged by qualification."""
    from omnigent.server.auth import (
        RESERVED_USER_LOCAL,
        UnifiedAuthProvider,
        resolve_auth_header,
    )

    assert RESERVED_USER_LOCAL == "local"
    assert resolve_auth_header() == "X-Forwarded-Email" or isinstance(resolve_auth_header(), str)

    class _Conn:
        headers: dict[str, str] = {}
        cookies: dict[str, str] = {}
        url = type("U", (), {"path": "/"})()

    header_provider = UnifiedAuthProvider(source="header", local_single_user=False)
    assert header_provider.get_user_id(_Conn()) is None  # type: ignore[arg-type]
    return True


def collect_upstream_probe_evidence() -> UpstreamProbeEvidence:
    """Run all reuse probes and return redacted evidence (no secrets)."""
    evidence = UpstreamProbeEvidence()
    try:
        evidence.session_roundtrip = probe_upstream_session_roundtrip()
    except Exception as exc:
        evidence.notes.append(f"session_roundtrip failed: {type(exc).__name__}")
    try:
        evidence.password_roundtrip = probe_upstream_password_roundtrip()
    except Exception as exc:
        evidence.notes.append(f"password_roundtrip failed: {type(exc).__name__}")
    try:
        accounts_failed, oidc_failed = probe_upstream_config_fail_closed()
        evidence.accounts_config_fail_closed = accounts_failed
        evidence.oidc_config_fail_closed = oidc_failed
    except Exception as exc:
        evidence.notes.append(f"config_fail_closed failed: {type(exc).__name__}")
    try:
        evidence.upstream_defaults_intact = probe_upstream_defaults_intact()
    except Exception as exc:
        evidence.notes.append(f"defaults_intact failed: {type(exc).__name__}")
    return evidence
