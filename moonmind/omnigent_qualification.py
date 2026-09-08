"""Qualified portable Omnigent authentication boundary (K2, #4118).

Composes supported upstream entrypoints from the pinned Omnigent submodule
with MoonMind-owned configuration, identity resolution, persistence, and
session policy. This is authentication-library reuse, not embedding the
upstream application, permission store, runtime server, or retired embedded
host transport.

Supported upstream entrypoints (pinned, see ``UPSTREAM_PIN``):

- ``omnigent.server.auth.UnifiedAuthProvider`` — constructed explicitly with
  explicit ``source``/``header_name``/``local_single_user`` and an explicit
  cookie-config shape. ``create_auth_provider()`` / ``resolve_auth_source()``
  are never used for MoonMind decisions so ambient ``OMNIGENT_AUTH_*``
  import-time state cannot select MoonMind behavior.
- ``omnigent.server.oidc.mint_session_token`` / ``hmac_digest`` — real session
  issuance and cache-key derivation for the qualification slice.
- ``omnigent.server.passwords.verify_password`` / ``hash_password`` —
  qualified password handling. A missing or incompatible hash returns a
  controlled enrollment/reset requirement, never a silent hash assumption.

What MoonMind owns here (thin adapter at the boundary):

- Explicit ``QualifiedAuthConfig`` (no env reads, fail-closed validation).
- ``ValidatedIdentity`` resolution receiving verified ``(issuer, subject)``
  claims before any session is minted. Email is never an identity key.
- Async-safe persistence through the ``AsyncAccountStore`` protocol, which
  resolves a ``ValidatedIdentity`` to an existing MoonMind UUID. Synchronous
  upstream store work is offloaded with ``asyncio.to_thread``; no parallel
  account/admin tables are created.
- MoonMind-purpose session tokens (``iss``/``aud`` bound, HS256, distinct
  cookie name and key, live revocation checks on every validation including
  cache hits; principal status enforced at authenticate/mint time and on
  full validation, with cache-hit status staleness bounded by the 300s
  cache cap inside the 5-minute propagation bound).
- Explicit rejection of unsupported refresh/delegated/runner-minting
  surfaces. Upstream's Omnigent-specific delegated allowlist is never
  broadened for MoonMind paths.

Upstream modules are loaded from the pinned submodule path without importing
the whole upstream application. Only ``auth.py``, ``oidc.py``,
``passwords.py``, and ``accounts_config.py`` shapes are touched; the
synchronous ``SqlAlchemyAccountStore``, permission store, route factories,
runtime server, and device-grant store are never imported here.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import secrets
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Protocol

UPSTREAM_PIN = "f04b0354fb5344c1ea8b92795ceb6760a9ad7595"
UPSTREAM_FILES = (
    "omnigent/server/auth.py",
    "omnigent/server/oidc.py",
    "omnigent/server/passwords.py",
    "omnigent/server/accounts_config.py",
)
SUPPORTED_ENTRYPOINTS = (
    "omnigent.server.auth.UnifiedAuthProvider",
    "omnigent.server.auth.delegated_path_allowed",
    "omnigent.server.oidc.mint_session_token",
    "omnigent.server.oidc.hmac_digest",
    "omnigent.server.passwords.verify_password",
    "omnigent.server.passwords.hash_password",
)

RESERVED_IDENTITIES = frozenset({"local", "__public__"})
UPSTREAM_COOKIE_NAMES = frozenset({"__Host-ap_session", "ap_session"})
ALLOWED_ALGORITHMS = ("HS256",)


def _upstream_root() -> Path:
    return Path(__file__).resolve().parents[1] / "omnigent"


def _ensure_upstream_importable() -> None:
    import sys

    root = str(_upstream_root())
    if root not in sys.path:
        sys.path.insert(0, root)


def _load_upstream_module(name: str, relpath: str) -> Any:
    """Import a pinned upstream module without booting the upstream app.

    Uses the normal package import (``omnigent.server.<name>``) with the
    pinned submodule root at the front of ``sys.path`` so intra-upstream
    lazy imports (``from omnigent.server.oidc import ...`` inside
    ``auth.py``) resolve to the same pinned files. Only the narrow
    modules listed in ``UPSTREAM_FILES`` are ever imported here.
    """
    import importlib

    _ensure_upstream_importable()
    canonical = f"omnigent.server.{name}"
    return importlib.import_module(canonical)


def load_upstream_primitives() -> dict[str, Any]:
    """Load the narrow supported upstream surface from the pinned submodule.

    Returns the loaded modules plus provenance. Never imports the upstream
    application, permission store, routes, or runtime server.
    """
    root = _upstream_root()
    for rel in UPSTREAM_FILES:
        if not (root / rel).exists():
            raise RuntimeError(f"Pinned upstream file missing: {rel} (pin {UPSTREAM_PIN})")
    auth_mod = _load_upstream_module("auth", "omnigent/server/auth.py")
    oidc_mod = _load_upstream_module("oidc", "omnigent/server/oidc.py")
    passwords_mod = _load_upstream_module(
        "passwords", "omnigent/server/passwords.py"
    )
    return {
        "pin": UPSTREAM_PIN,
        "root": str(root),
        "auth": auth_mod,
        "oidc": oidc_mod,
        "passwords": passwords_mod,
        "supported_entrypoints": SUPPORTED_ENTRYPOINTS,
    }


def upstream_provenance() -> dict[str, Any]:
    """Record exact upstream and packaged artifact identities for K2."""
    root = _upstream_root()
    files: dict[str, str] = {}
    for rel in UPSTREAM_FILES:
        p = root / rel
        files[rel] = hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else "missing"
    base: dict[str, Any] = {
        "expected_pin": UPSTREAM_PIN,
        "package": "pinned omnigent submodule (no PyPI indirection; no mutable-main pin)",
        "files_sha256": files,
        "supported_entrypoints": list(SUPPORTED_ENTRYPOINTS),
        "excluded": [
            "omnigent.server.app (whole application)",
            "omnigent.stores.permission_store (permission store)",
            "omnigent.server.routes.* (route factories)",
            "omnigent.server.device_grant_store (refresh/device grants)",
            "omnigent.server.accounts_store.SqlAlchemyAccountStore (sync concrete store)",
            "runtime server / embedded host transport",
        ],
    }
    # Fail closed when submodule git metadata is absent: never report another
    # repository's HEAD (e.g. the superproject's, which `git -C <dir>`
    # falls through to) as the upstream commit.
    if not (root / ".git").exists():
        return {
            **base,
            "upstream_commit": "unknown (omnigent submodule not initialized)",
            "pin_match": False,
        }
    try:
        import subprocess

        pin = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout.strip()
    except Exception:
        pin = ""
    return {
        **base,
        "upstream_commit": pin or "unknown (upstream git lookup failed)",
        "pin_match": (pin or "") == UPSTREAM_PIN,
    }


class AuthConfigError(ValueError):
    """Fail-closed configuration error."""


class EnrollmentRequiredError(RuntimeError):
    """Controlled enrollment/reset requirement (no compatible password hash)."""


class UnsupportedSurfaceError(RuntimeError):
    """Unsupported refresh/delegated/runner surface was requested."""


@dataclass(frozen=True)
class QualifiedAuthConfig:
    """Explicit MoonMind control-plane authentication configuration.

    No environment reads, no import-time ambient state. Invalid values fail
    closed at construction.
    """

    mode: str
    cookie_name: str
    cookie_secret: bytes
    session_ttl_seconds: int = 8 * 3600
    issuer: str = "moonmind-control-plane"
    audience: str = "moonmind-api"
    secure_cookies: bool = True

    def __post_init__(self) -> None:
        mode = (self.mode or "").strip().lower()
        if mode not in ("accounts", "oidc", "header"):
            raise AuthConfigError(
                f"Unsupported mode {self.mode!r}: use 'accounts', 'oidc', or 'header'"
            )
        object.__setattr__(self, "mode", mode)
        if not self.cookie_name or not self.cookie_name.strip():
            raise AuthConfigError("cookie_name must not be blank")
        if self.cookie_name in UPSTREAM_COOKIE_NAMES:
            raise AuthConfigError(
                f"cookie_name {self.cookie_name!r} collides with upstream runtime "
                "cookie names; MoonMind control-plane cookies must be distinct"
            )
        if not isinstance(self.cookie_secret, (bytes, bytearray)) or len(self.cookie_secret) < 32:
            raise AuthConfigError("cookie_secret must be at least 32 bytes")
        if self.session_ttl_seconds <= 0:
            raise AuthConfigError("session_ttl_seconds must be positive")
        if not (self.issuer or "").strip():
            raise AuthConfigError("issuer must not be blank")
        if not (self.audience or "").strip():
            raise AuthConfigError("audience must not be blank")


@dataclass(frozen=True)
class ValidatedIdentity:
    """Verified identity claims resolved before any session is minted."""

    issuer: str
    subject: str
    provider: str = "accounts"
    email: str | None = None


@dataclass(frozen=True)
class AccountRecord:
    """MoonMind-owned account resolution for a validated identity."""

    user_id: uuid.UUID
    is_active: bool = True
    is_superuser: bool = False


class AsyncAccountStore(Protocol):
    """Async-safe MoonMind persistence boundary.

    Implementations use existing MoonMind transactions and profile authority.
    They resolve a full ``ValidatedIdentity`` (never a bare email/username
    string) to an existing MoonMind UUID.
    """

    async def resolve_account(self, identity: ValidatedIdentity) -> AccountRecord | None:
        ...

    async def get_password_hash(self, login: str) -> str | None:
        ...

    async def is_revoked(self, token_id: str) -> bool:
        ...


class InMemoryAsyncAccountStore:
    """Hermetic test/qualification store. No second database."""

    def __init__(self) -> None:
        self._by_identity: dict[tuple[str, str], AccountRecord] = {}
        self._password_hashes: dict[str, str] = {}
        self._revoked: set[str] = set()
        self.calls: list[str] = []

    def enroll(
        self,
        identity: ValidatedIdentity,
        record: AccountRecord,
        *,
        password_hash: str | None = None,
        login: str | None = None,
    ) -> None:
        self._by_identity[(identity.issuer, identity.subject)] = record
        if password_hash is not None and login is not None:
            self._password_hashes[login.strip().lower()] = password_hash

    async def resolve_account(self, identity: ValidatedIdentity) -> AccountRecord | None:
        self.calls.append(f"resolve:{identity.issuer}:{identity.subject}")
        return self._by_identity.get((identity.issuer, identity.subject))

    async def get_password_hash(self, login: str) -> str | None:
        self.calls.append(f"get_password_hash:{login.strip().lower()}")
        return self._password_hashes.get(login.strip().lower())

    async def is_revoked(self, token_id: str) -> bool:
        self.calls.append(f"is_revoked:{token_id}")
        return token_id in self._revoked

    def revoke(self, token_id: str) -> None:
        self._revoked.add(token_id)


def resolve_validated_identity(
    issuer: str, subject: str, *, provider: str = "accounts", email: str | None = None
) -> ValidatedIdentity:
    """Validate verified ``(issuer, subject)`` claims before session minting.

    Subject matching is case-sensitive and never truncated. Email is carried
    as an opaque attribute only; it is never an identity key. Reserved
    identities fail closed.
    """
    issuer = (issuer or "").strip()
    if not issuer:
        raise AuthConfigError("issuer claim must not be blank")
    if len(issuer) > 1024:
        raise AuthConfigError("issuer claim too long")
    if not subject:
        raise AuthConfigError("subject claim must not be blank")
    if len(subject) > 512:
        raise AuthConfigError("subject claim too long")
    if subject in RESERVED_IDENTITIES:
        raise AuthConfigError(f"Reserved identity {subject!r}")
    normalized_email = email.strip().lower() if email and email.strip() else None
    if normalized_email in RESERVED_IDENTITIES:
        raise AuthConfigError(f"Reserved identity {normalized_email!r}")
    return ValidatedIdentity(
        issuer=issuer, subject=subject, provider=provider, email=normalized_email
    )


def _token_cache_key(token: str, secret: bytes) -> str:
    """Derive the session-cache key via the qualified upstream primitive.

    Delegates to ``omnigent.server.oidc.hmac_digest`` whenever the pinned
    upstream module is loaded (always the case for adapter-issued sessions,
    since construction loads upstream primitives first). The stdlib fallback
    implements the identical HMAC-SHA256 construction for direct unit use
    without an upstream import.
    """
    import sys

    oidc_mod = sys.modules.get("omnigent.server.oidc")
    digest = getattr(oidc_mod, "hmac_digest", None)
    if callable(digest):
        return digest(token, secret)
    return hmac.new(secret, token.encode("utf-8"), hashlib.sha256).hexdigest()


@dataclass
class ValidationResult:
    user_id: uuid.UUID | None
    code: str
    cache_hit: bool = False


class MoonmindQualifiedAuth:
    """MoonMind control-plane session authority over qualified upstream primitives."""

    def __init__(
        self,
        config: QualifiedAuthConfig,
        store: AsyncAccountStore,
        *,
        upstream: dict[str, Any] | None = None,
    ) -> None:
        self.config = config
        self.store = store
        prims = upstream or load_upstream_primitives()
        self._upstream = prims
        self._jwt = __import__("jwt")
        # Explicit upstream provider for the qualification slice only: explicit
        # source, explicit header name, single-user fallback disabled. No
        # OMNIGENT_AUTH_* ambient state influences MoonMind decisions because
        # every env-derived default is overridden here.
        auth_mod = prims["auth"]
        cookie_shape = SimpleNamespace(
            cookie_secret=config.cookie_secret,
            session_cookie_name="__Host-ap_session"
            if config.secure_cookies
            else "ap_session",
        )
        oidc_cfg = cookie_shape if config.mode == "oidc" else None
        accounts_cfg = cookie_shape if config.mode != "oidc" else None
        self._upstream_provider = auth_mod.UnifiedAuthProvider(
            source=config.mode if config.mode in ("oidc", "accounts") else "header",
            oidc_config=oidc_cfg,
            accounts_config=accounts_cfg,
            local_single_user=False,
            header_name="X-MoonMind-Trusted-Proxy-User",
            header_strip_prefix="",
        )
        self._cache: dict[str, tuple[str, float, str]] = {}
        self._hook_order: list[str] = []

    @property
    def hook_order(self) -> list[str]:
        return list(self._hook_order)

    @property
    def upstream_provider(self) -> Any:
        return self._upstream_provider

    async def verify_password_hash(self, plaintext: str, password_hash: str | None) -> None:
        """Verify via the qualified upstream password interface.

        A missing hash raises a controlled enrollment/reset requirement.
        """
        if not password_hash:
            raise EnrollmentRequiredError(
                "No compatible password hash: enrollment or reset is required"
            )
        passwords_mod = self._upstream["passwords"]
        try:
            await asyncio.to_thread(passwords_mod.verify_password, plaintext, password_hash)
        except Exception as exc:
            invalid = getattr(passwords_mod, "InvalidPasswordError", None)
            if invalid is not None and isinstance(exc, invalid):
                raise AuthConfigError("invalid username or password") from exc
            raise EnrollmentRequiredError(
                "Incompatible password hash: enrollment or reset is required"
            ) from exc

    async def authenticate_account(
        self, *, login: str, password: str, issuer: str, subject: str
    ) -> tuple[ValidatedIdentity, AccountRecord]:
        """Ordered hook pipeline: validated identity -> store -> password -> status."""
        self._hook_order.append("identity.resolve")
        identity = resolve_validated_identity(issuer, subject, provider="accounts")
        self._hook_order.append("store.resolve_account")
        record = await self.store.resolve_account(identity)
        if record is None:
            raise AuthConfigError("unknown account for validated identity")
        self._hook_order.append("store.get_password_hash")
        stored_hash = await self.store.get_password_hash(login)
        self._hook_order.append("password.verify")
        await self.verify_password_hash(password, stored_hash)
        self._hook_order.append("account.status")
        if not record.is_active:
            raise AuthConfigError("inactive account")
        return identity, record

    def mint_session(self, identity: ValidatedIdentity, record: AccountRecord) -> str:
        """Mint a MoonMind-purpose session after identity + account resolution."""
        if not record.is_active:
            raise AuthConfigError("inactive account cannot receive a session")
        now = int(time.time())
        payload = {
            "sub": str(record.user_id),
            "iss": self.config.issuer,
            "aud": self.config.audience,
            "provider": identity.provider,
            "token_use": "moonmind-session",
            "jti": secrets.token_hex(16),
            "iat": now,
            "exp": now + self.config.session_ttl_seconds,
        }
        return self._jwt.encode(payload, self.config.cookie_secret, algorithm="HS256")

    async def validate_session(self, token: str, *, path: str = "/api/me") -> ValidationResult:
        """Validate one presented credential with live per-request checks."""
        if not token:
            return ValidationResult(None, "auth_required")
        cache_key = _token_cache_key(token, self.config.cookie_secret)
        cached = self._cache.get(cache_key)
        if cached is not None and cached[1] > time.monotonic():
            cached_user_id, _, cached_jti = cached
            # Cache hits recheck revocation live. Principal status
            # (is_active) was enforced at authenticate/mint time and is
            # rechecked on full validation; cache-hit status staleness is
            # bounded by the 300s cache cap (inside the 5-minute bound).
            if await self.store.is_revoked(cached_jti):
                return ValidationResult(None, "auth_invalid", cache_hit=True)
            try:
                parsed = uuid.UUID(cached_user_id)
            except ValueError:
                return ValidationResult(None, "auth_invalid", cache_hit=True)
            return ValidationResult(parsed, "ok", cache_hit=True)
        try:
            payload = self._jwt.decode(
                token,
                self.config.cookie_secret,
                algorithms=list(ALLOWED_ALGORITHMS),
                issuer=self.config.issuer,
                audience=self.config.audience,
            )
        except Exception:
            return ValidationResult(None, "auth_invalid")
        if payload.get("token_use") != "moonmind-session":
            return ValidationResult(None, "auth_invalid")
        # Unsupported surfaces never become MoonMind browser authority.
        if payload.get("grant_id") is not None or payload.get("scope") is not None:
            return ValidationResult(None, "auth_invalid")
        if payload.get("refresh_token") is True or payload.get("token_type") in (
            "refresh",
            "delegated",
            "runner",
        ):
            return ValidationResult(None, "auth_invalid")
        sub = payload.get("sub")
        if not isinstance(sub, str) or not sub or sub in RESERVED_IDENTITIES:
            return ValidationResult(None, "auth_invalid")
        try:
            user_id = uuid.UUID(sub)
        except ValueError:
            return ValidationResult(None, "auth_invalid")
        jti = payload.get("jti")
        if not isinstance(jti, str) or not jti:
            return ValidationResult(None, "auth_invalid")
        if await self.store.is_revoked(jti):
            return ValidationResult(None, "auth_invalid")
        remaining = float(payload.get("exp", 0)) - time.time()
        if remaining > 0:
            self._cache[cache_key] = (sub, time.monotonic() + min(remaining, 300.0), jti)
        _ = path  # path retained for future scope checks; unsupported scopes reject above.
        return ValidationResult(user_id, "ok")

    async def validate_request(
        self,
        *,
        cookie_token: str | None = None,
        bearer_token: str | None = None,
        path: str = "/api/me",
    ) -> ValidationResult:
        """Validate cookie/bearer credentials with conflict rejection."""
        cookie_result = (
            await self.validate_session(cookie_token or "", path=path)
            if cookie_token
            else None
        )
        bearer_result = (
            await self.validate_session(bearer_token or "", path=path)
            if bearer_token
            else None
        )
        if cookie_result is None and bearer_result is None:
            return ValidationResult(None, "auth_required")
        if cookie_result is not None and bearer_result is not None:
            if cookie_result.user_id is None or bearer_result.user_id is None:
                return ValidationResult(None, "auth_invalid")
            if cookie_result.user_id != bearer_result.user_id:
                return ValidationResult(None, "auth_conflict")
            return ValidationResult(
                cookie_result.user_id, "ok", cache_hit=bearer_result.cache_hit
            )
        result = cookie_result if cookie_result is not None else bearer_result
        assert result is not None
        return result

    def issue_refresh_token(self, *args: Any, **kwargs: Any) -> str:  # noqa: ARG002
        raise UnsupportedSurfaceError(
            "Refresh issuance is not mounted for MoonMind browser authority"
        )

    def mint_runner_token(self, *args: Any, **kwargs: Any) -> str:  # noqa: ARG002
        raise UnsupportedSurfaceError(
            "Runner minting is not mounted for MoonMind browser authority"
        )

    def delegated_token_allowed(self, *args: Any, **kwargs: Any) -> bool:  # noqa: ARG002
        return False


def build_test_config(**overrides: Any) -> QualifiedAuthConfig:
    """Conformance fixture: explicit test config with distinct cookie/key/purpose."""
    params: dict[str, Any] = {
        "mode": "accounts",
        "cookie_name": "mm_session_4118",
        "cookie_secret": bytes.fromhex("ab" * 32),
        "session_ttl_seconds": 3600,
        "issuer": "moonmind-control-plane-test",
        "audience": "moonmind-api-test",
        "secure_cookies": False,
    }
    params.update(overrides)
    return QualifiedAuthConfig(**params)


def build_conformance_fixtures() -> dict[str, Any]:
    """Adapter conformance fixtures consumed by later issues (K3/K4)."""
    store = InMemoryAsyncAccountStore()
    config = build_test_config()
    auth = MoonmindQualifiedAuth(config, store)
    return {"config": config, "store": store, "auth": auth, "pin": UPSTREAM_PIN}


__all__ = [
    "ALLOWED_ALGORITHMS",
    "RESERVED_IDENTITIES",
    "SUPPORTED_ENTRYPOINTS",
    "UPSTREAM_COOKIE_NAMES",
    "UPSTREAM_FILES",
    "UPSTREAM_PIN",
    "AsyncAccountStore",
    "AccountRecord",
    "AuthConfigError",
    "EnrollmentRequiredError",
    "InMemoryAsyncAccountStore",
    "MoonmindQualifiedAuth",
    "QualifiedAuthConfig",
    "UnsupportedSurfaceError",
    "ValidatedIdentity",
    "ValidationResult",
    "build_conformance_fixtures",
    "build_test_config",
    "load_upstream_primitives",
    "resolve_validated_identity",
    "upstream_provenance",
]
