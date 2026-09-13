"""Account lifecycle and operator recovery (MoonLadderStudios/MoonMind#4122).

Parent: #4116. Depends on #4118/#4119/#4120/#4121, composes #4124 identity
primitives. Plan coverage: enrollment and administration, K3/K4 in
``docs/tmp/KeycloakRemovalPlan.md``; declarative target in
``docs/Security/AuthenticationContracts.md`` §7.

This module is the single hermetic owner for the ``accounts``-mode
lifecycle rules the canonical contract declares: protected first-owner
setup, expiring one-use invitations, member administration with
last-admin protection, and local operator recovery. It owns no session,
cookie, OIDC, or proxy behavior (those stay with #4121/#4124) and no
database wiring: persistence crosses the small ``LifecycleStore``
protocol so production can back it with existing ``User``-table
transactions while unit tests run hermetically against the in-memory
implementation below.

Rules enforced here (never silently relaxed):

* First-owner setup is transactional and race-safe: exactly one winner
  under concurrent setup. Bootstrap capabilities are operator-held,
  expiring, and one-use; there is no public unauthenticated first-claim
  endpoint shape and no hardcoded default password.
* Invites are expiring and one-use, bound to one login name (exact,
  case-sensitive match). Redemption is atomic: a redeemed or expired
  invite never admits a second account.
* Member administration is server-owned. Changing privilege or active
  state requires an active superuser actor; the last active superuser
  can never be demoted, deactivated, or removed (tested recovery path
  stays available). Upstream rosters never promote here.
* Local operator recovery is an operator-held, expiring, one-use
  capability for a documented local administrative operation — never a
  network-exposed re-claim and never a security-disabling step. Password
  / MFA / Keycloak-credential migration stays a separate tested path
  (compatible-hash or controlled invitation/reset); nothing here assumes
  silent hash compatibility.
* No secret material is logged or embedded in diagnostics. Capability
  tokens are HMAC-bound to deployment-owned key material (at least
  32 bytes, same floor as the #4120 session secret) that is never shared
  with runtime-host, worker, or repository credentials.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import inspect
import json
import logging
import secrets
import time
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol

logger = logging.getLogger(__name__)

__all__ = [
    "AccountLifecycleError",
    "BootstrapError",
    "InviteError",
    "AdminRefusedError",
    "RecoveryError",
    "LifecycleMember",
    "LifecycleStore",
    "InMemoryLifecycleStore",
    "bootstrap_nonce_for_login",
    "invite_nonce_for_login",
    "recovery_nonce_for_login",
    "mint_bootstrap_capability",
    "claim_first_owner",
    "mint_invite",
    "redeem_invite",
    "apply_member_action",
    "mint_recovery_capability",
    "redeem_recovery_capability",
    "redacted_lifecycle_event",
    "assert_no_secret_leak",
    "TOKEN_TTL_BOOTSTRAP_SECONDS",
    "TOKEN_TTL_INVITE_SECONDS",
    "TOKEN_TTL_RECOVERY_SECONDS",
    "MIN_KEY_BYTES",
]

MIN_KEY_BYTES = 32

# Default bounds. Production callers pass explicit TTLs; these defaults keep
# the hermetic contract and the documented operator procedure identical.
TOKEN_TTL_BOOTSTRAP_SECONDS = 30 * 60
TOKEN_TTL_INVITE_SECONDS = 72 * 3600
TOKEN_TTL_RECOVERY_SECONDS = 15 * 60

_TOKEN_VERSION = "v1"
_TOKEN_SEPARATOR = "."


# ---------------------------------------------------------------------------
# Errors: one typed contract with actionable codes (maps to §8 semantics)
# ---------------------------------------------------------------------------


class AccountLifecycleError(ValueError):
    """Fail-closed account-lifecycle failure with a safe-to-surface code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class BootstrapError(AccountLifecycleError):
    """First-owner setup failure (invalid/expired/reused capability, race lost)."""


class InviteError(AccountLifecycleError):
    """Invitation mint/redeem failure (invalid/expired/reused/binding mismatch)."""


class AdminRefusedError(AccountLifecycleError):
    """Member-administration refusal (permission or last-admin protection)."""


class RecoveryError(AccountLifecycleError):
    """Operator-recovery failure (invalid/expired/reused capability)."""


def http_status_for_lifecycle_error(exc: BaseException) -> tuple[int, str]:
    """Map a lifecycle error to ``(status, code)`` per AuthenticationContracts §8."""
    if isinstance(exc, AdminRefusedError):
        return 403, getattr(exc, "code", "forbidden") or "forbidden"
    if isinstance(exc, AccountLifecycleError):
        return 401, getattr(exc, "code", "auth_invalid") or "auth_invalid"
    return 500, "internal"


# ---------------------------------------------------------------------------
# Member model and administration (pure, no I/O)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LifecycleMember:
    """Minimal server-owned membership view (backed by the existing ``User`` row)."""

    login: str
    is_active: bool = True
    is_superuser: bool = False


_MEMBER_ACTIONS = frozenset({"grant_admin", "revoke_admin", "deactivate", "reactivate", "remove"})


def apply_member_action(
    members: Mapping[str, LifecycleMember],
    *,
    actor_login: str,
    target_login: str,
    action: str,
) -> dict[str, LifecycleMember]:
    """Apply one member-administration action, returning the new roster.

    * ``actor_login`` must name an active superuser (server-owned check;
      upstream admin claims never satisfy this).
    * ``target_login`` must exist (exact, case-sensitive match).
    * Last-admin protection: ``revoke_admin`` / ``deactivate`` / ``remove``
      on the final active superuser raises :class:`AdminRefusedError`
      with code ``last_admin_protected`` — the tested recovery path stays
      available instead of stranding the deployment.
    * ``remove`` deletes the roster entry (production keeps the ``User`` row
      and ownership records; nothing here rewrites UUIDs or histories).
    """
    if action not in _MEMBER_ACTIONS:
        raise AdminRefusedError("admin_invalid_action", f"unknown member action {action!r}")
    actor = members.get(actor_login)
    if actor is None or not actor.is_active or not actor.is_superuser:
        raise AdminRefusedError("forbidden", "member administration requires an active administrator")
    target = members.get(target_login)
    if target is None:
        raise AdminRefusedError("admin_unknown_member", f"unknown member {target_login!r}")
    active_admins = [m for m in members.values() if m.is_active and m.is_superuser]
    last_admin = len(active_admins) == 1 and active_admins[0].login == target_login
    if action in ("revoke_admin", "deactivate", "remove") and last_admin:
        raise AdminRefusedError(
            "last_admin_protected",
            "refusing to remove the last active administrator; use the tested "
            "local operator recovery procedure instead",
        )
    updated = dict(members)
    if action == "grant_admin":
        updated[target_login] = LifecycleMember(
            login=target.login, is_active=target.is_active, is_superuser=True
        )
    elif action == "revoke_admin":
        updated[target_login] = LifecycleMember(
            login=target.login, is_active=target.is_active, is_superuser=False
        )
    elif action == "deactivate":
        updated[target_login] = LifecycleMember(
            login=target.login, is_active=False, is_superuser=target.is_superuser
        )
    elif action == "reactivate":
        updated[target_login] = LifecycleMember(
            login=target.login, is_active=True, is_superuser=target.is_superuser
        )
    elif action == "remove":
        del updated[target_login]
    return updated


# ---------------------------------------------------------------------------
# Persistence boundary (production: existing User-table transactions)
# ---------------------------------------------------------------------------


class LifecycleStore(Protocol):
    """Single-use + owner-claim persistence behind a portable interface.

    Production implementations back this with the existing database
    (one ``User`` row per login; nonce consumption and owner claim in the
    same transaction as user creation). No second account database.

    Synchronous only: implementations must use plain ``def`` methods. The
    hermetic rules reject ``async def`` stores fail-closed so a truthy
    coroutine can never read as an existing owner or a consumed nonce;
    asynchronous persistence goes through the ``*_with_token`` boundary in
    ``api_service.services.account_lifecycle_store_4122``.
    """

    def has_owner(self) -> bool:
        """Whether any owner account already exists."""
        raise NotImplementedError

    def try_claim_owner(self, login: str, nonce: str) -> bool:
        """Atomically claim first ownership; ``False`` when already claimed."""
        raise NotImplementedError

    def consume_nonce(self, nonce: str) -> bool:
        """Atomically consume a one-use nonce; ``False`` when already used."""
        raise NotImplementedError

    def is_nonce_consumed(self, nonce: str) -> bool:
        """Whether a nonce was already consumed."""
        raise NotImplementedError


class InMemoryLifecycleStore:
    """Hermetic single-threaded store for tests and documentation checks.

    Production uses compare-and-set transactions; this fixture serializes
    the same way so the race-safety rule is exercised, not assumed.
    """

    def __init__(self, *, owner_exists: bool = False) -> None:
        self._owner_exists = owner_exists
        self._consumed: set[str] = set()

    def has_owner(self) -> bool:
        return self._owner_exists

    def try_claim_owner(self, login: str, nonce: str) -> bool:  # noqa: ARG002
        if self._owner_exists:
            return False
        if nonce in self._consumed:
            return False
        self._consumed.add(nonce)
        self._owner_exists = True
        return True

    def consume_nonce(self, nonce: str) -> bool:
        if nonce in self._consumed:
            return False
        self._consumed.add(nonce)
        return True

    def is_nonce_consumed(self, nonce: str) -> bool:
        return nonce in self._consumed


# ---------------------------------------------------------------------------
# HMAC-bound capabilities (bootstrap / invite / recovery)
# ---------------------------------------------------------------------------


def _require_key(key: bytes) -> bytes:
    if not isinstance(key, (bytes, bytearray)) or len(key) < MIN_KEY_BYTES:
        raise AccountLifecycleError(
            "auth_invalid", "lifecycle key material must be at least 32 bytes"
        )
    return bytes(key)


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64decode(text: str) -> bytes:
    padded = text + "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(padded.encode("ascii"))


def _mint_capability(
    *, kind: str, key: bytes, login: str, ttl_seconds: int, now: float | None = None
) -> str:
    secret = _require_key(key)
    clean = (login or "").strip()
    if not clean:
        raise AccountLifecycleError("auth_invalid", f"{kind} capability requires a login name")
    if ttl_seconds <= 0:
        raise AccountLifecycleError("auth_invalid", f"{kind} TTL must be positive")
    moment = time.time() if now is None else now
    payload = {
        "v": 1,
        "kind": kind,
        "login": clean,
        "nonce": secrets.token_hex(16),
        "iat": int(moment),
        "exp": int(moment) + int(ttl_seconds),
    }
    body = _b64encode(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
    sig = _b64encode(hmac.new(secret, f"{_TOKEN_VERSION}.{body}".encode(), hashlib.sha256).digest())
    return f"{_TOKEN_VERSION}{_TOKEN_SEPARATOR}{body}{_TOKEN_SEPARATOR}{sig}"


def _verify_capability(
    token: str, *, kind: str, key: bytes, now: float | None = None
) -> dict[str, Any]:
    secret = _require_key(key)
    parts = (token or "").strip().split(_TOKEN_SEPARATOR)
    if len(parts) != 3 or parts[0] != _TOKEN_VERSION:
        raise AccountLifecycleError("auth_invalid", f"malformed {kind} capability")
    _, body, sig = parts
    expected = _b64encode(
        hmac.new(secret, f"{_TOKEN_VERSION}.{body}".encode(), hashlib.sha256).digest()
    )
    if not hmac.compare_digest(expected, sig):
        raise AccountLifecycleError("auth_invalid", f"invalid {kind} capability signature")
    try:
        payload = json.loads(_b64decode(body).decode("utf-8"))
    except Exception as exc:
        raise AccountLifecycleError("auth_invalid", f"malformed {kind} capability") from exc
    if not isinstance(payload, dict) or payload.get("kind") != kind or payload.get("v") != 1:
        raise AccountLifecycleError("auth_invalid", f"invalid {kind} capability")
    moment = time.time() if now is None else now
    try:
        exp = int(payload["exp"])
    except (KeyError, TypeError, ValueError) as exc:
        raise AccountLifecycleError("auth_invalid", f"malformed {kind} capability") from exc
    if moment >= exp:
        raise AccountLifecycleError("auth_invalid", f"expired {kind} capability")
    login = str(payload.get("login", ""))
    nonce = str(payload.get("nonce", ""))
    if not login or not nonce:
        raise AccountLifecycleError("auth_invalid", f"malformed {kind} capability")
    return {"login": login, "nonce": nonce, "exp": exp}


def _require_sync_store(store: LifecycleStore, error_cls: type[AccountLifecycleError]) -> LifecycleStore:
    """Reject asynchronous stores at the synchronous persistence boundary.

    An ``async def`` store passed here would hand back truthy coroutine
    objects instead of real answers (``has_owner()`` always truthy,
    ``consume_nonce()`` never awaited or persisted). Fail closed with the
    caller's typed error instead; asynchronous persistence must go through
    the ``*_with_token`` boundary in
    ``api_service.services.account_lifecycle_store_4122``.
    """
    for method in ("has_owner", "try_claim_owner", "consume_nonce", "is_nonce_consumed"):
        if inspect.iscoroutinefunction(getattr(store, method, None)):
            raise error_cls(
                "auth_invalid",
                "synchronous lifecycle rule received an asynchronous store; "
                "use the async token boundary instead",
            )
    return store


def bootstrap_nonce_for_login(
    token: str, *, key: bytes, login: str, now: float | None = None
) -> str:
    """Verify a bootstrap capability and return its nonce for ``login``.

    Pure verification (no persistence): raises :class:`BootstrapError` on
    malformed, expired, wrong-key, or login-mismatched capabilities. The
    async database boundary verifies with this helper, then persists the
    nonce atomically with user creation in one transaction.
    """
    try:
        payload = _verify_capability(token, kind="bootstrap", key=key, now=now)
    except AccountLifecycleError as exc:
        raise BootstrapError(exc.code, str(exc)) from exc
    if payload["login"] != login:
        raise BootstrapError("auth_invalid", "bootstrap capability is bound to a different login name")
    return payload["nonce"]


def invite_nonce_for_login(
    token: str, *, key: bytes, login: str, now: float | None = None
) -> str:
    """Verify an invitation capability and return its nonce for ``login``."""
    try:
        payload = _verify_capability(token, kind="invite", key=key, now=now)
    except AccountLifecycleError as exc:
        raise InviteError(exc.code, str(exc)) from exc
    if payload["login"] != login:
        raise InviteError("auth_invalid", "invitation is bound to a different login name")
    return payload["nonce"]


def recovery_nonce_for_login(
    token: str, *, key: bytes, login: str, now: float | None = None
) -> str:
    """Verify a recovery capability and return its nonce for ``login``."""
    try:
        payload = _verify_capability(token, kind="recovery", key=key, now=now)
    except AccountLifecycleError as exc:
        raise RecoveryError(exc.code, str(exc)) from exc
    if payload["login"] != login:
        raise RecoveryError("auth_invalid", "recovery capability is bound to a different login name")
    return payload["nonce"]


def mint_bootstrap_capability(
    login: str, *, key: bytes, ttl_seconds: int = TOKEN_TTL_BOOTSTRAP_SECONDS, now: float | None = None
) -> str:
    """Mint an operator-held first-owner bootstrap capability (expiring, one-use)."""
    return _mint_capability(kind="bootstrap", key=key, login=login, ttl_seconds=ttl_seconds, now=now)


def claim_first_owner(
    token: str,
    *,
    key: bytes,
    store: LifecycleStore,
    login: str,
    now: float | None = None,
) -> str:
    """Redeem a bootstrap capability, claiming single first ownership.

    The capability login must exactly match ``login``; the store claim is
    atomic so concurrent setups produce exactly one owner. Reuse, expiry,
    login mismatch, or an already-populated database raises
    :class:`BootstrapError` without creating or modifying any owner.

    Synchronous persistence only: an asynchronous store raises
    :class:`BootstrapError` (use the async token boundary instead).
    """
    nonce = bootstrap_nonce_for_login(token, key=key, login=login, now=now)
    _require_sync_store(store, BootstrapError)
    if store.has_owner():
        raise BootstrapError(
            "bootstrap_closed",
            "first-owner setup is closed: an owner already exists; use invitation or recovery",
        )
    if not store.try_claim_owner(login, nonce):
        raise BootstrapError(
            "bootstrap_consumed",
            "bootstrap capability was already consumed or ownership was claimed concurrently",
        )
    return login


def mint_invite(
    login: str, *, key: bytes, ttl_seconds: int = TOKEN_TTL_INVITE_SECONDS, now: float | None = None
) -> str:
    """Mint an expiring one-use invitation bound to one login name."""
    return _mint_capability(kind="invite", key=key, login=login, ttl_seconds=ttl_seconds, now=now)


def redeem_invite(
    token: str, *, key: bytes, store: LifecycleStore, login: str, now: float | None = None
) -> str:
    """Redeem an invitation for ``login`` (exact match), consuming it atomically.

    Synchronous persistence only: an asynchronous store raises
    :class:`InviteError` (use the async token boundary instead).
    """
    nonce = invite_nonce_for_login(token, key=key, login=login, now=now)
    _require_sync_store(store, InviteError)
    if not store.consume_nonce(nonce):
        raise InviteError("auth_invalid", "invitation was already redeemed")
    return login


def mint_recovery_capability(
    login: str, *, key: bytes, ttl_seconds: int = TOKEN_TTL_RECOVERY_SECONDS, now: float | None = None
) -> str:
    """Mint an operator-held recovery capability (short-lived, one-use).

    This is a local administrative operation: the capability is held by the
    operator (same custody as session-key material) and redeemed through a
    controlled administrative path — never a public re-claim endpoint and
    never a step that disables authentication.
    """
    return _mint_capability(kind="recovery", key=key, login=login, ttl_seconds=ttl_seconds, now=now)


def redeem_recovery_capability(
    token: str, *, key: bytes, store: LifecycleStore, login: str, now: float | None = None
) -> str:
    """Redeem a recovery capability for ``login``, consuming it atomically.

    Synchronous persistence only: an asynchronous store raises
    :class:`RecoveryError` (use the async token boundary instead).
    """
    nonce = recovery_nonce_for_login(token, key=key, login=login, now=now)
    _require_sync_store(store, RecoveryError)
    if not store.consume_nonce(nonce):
        raise RecoveryError("auth_invalid", "recovery capability was already consumed")
    return login


# ---------------------------------------------------------------------------
# Redacted observability (no secret material)
# ---------------------------------------------------------------------------

_LIFECYCLE_EVENT_KINDS = frozenset({"bootstrap", "invite", "admin", "recovery"})

_SECRET_KEY_HINTS = (
    "token",
    "capability",
    "cookie",
    "secret",
    "password",
    "code",
    "link",
    "nonce",
    "authorization",
)


def redacted_lifecycle_event(
    kind: str, *, action: str, login: str, extra: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """Record a redacted lifecycle event (never carries capability material)."""
    normalized = kind.strip().lower()
    if normalized not in _LIFECYCLE_EVENT_KINDS:
        raise AccountLifecycleError("auth_invalid", f"unknown lifecycle event kind {kind!r}")
    event: dict[str, Any] = {
        "lifecycle_event": normalized,
        "action": action,
        "login": login,
    }
    for key, value in dict(extra or {}).items():
        lowered = str(key).lower()
        if any(hint in lowered for hint in _SECRET_KEY_HINTS):
            raise AccountLifecycleError(
                "auth_invalid",
                f"lifecycle event must not carry secret material: {key!r}",
            )
        text = str(value)
        if _TOKEN_SEPARATOR in text and text.startswith(_TOKEN_VERSION):
            raise AccountLifecycleError(
                "auth_invalid",
                f"lifecycle event value for {key!r} looks like capability material",
            )
        event[str(key)] = value
    logger.info("lifecycle_event %s action=%s login=%s", normalized, action, login)
    return event


def assert_no_secret_leak(container: Any, secrets_list: list[str]) -> None:
    """Fail when ``container`` serializes any of ``secrets_list``."""
    try:
        rendered = json.dumps(container, default=str)
    except Exception:
        rendered = str(container)
    for secret in secrets_list:
        if secret and secret in rendered:
            raise AssertionError("secret material leaked into serialized payload")


@dataclass
class LifecycleFixture:
    """Test fixture bundle: key + store + deterministic clock."""

    key: bytes = field(default_factory=lambda: secrets.token_bytes(32))
    now: float = field(default_factory=time.time)
    store: InMemoryLifecycleStore = field(default_factory=InMemoryLifecycleStore)
