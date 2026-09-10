"""DB-backed advanced-auth orchestration (MoonLadderStudios/MoonMind#4124).

Composes the portable #4124 capabilities
(``moonmind.security.oidc_advanced_4124`` /
``moonmind.security.trusted_proxy_4124``) with the shared authorities:

* #4119 ``api_service/services/identity_service.py`` for ``(issuer,
  subject)`` -> ``User.id`` resolution and uniqueness-protected
  provisioning (email never merges, subjects case-sensitive, UUIDs
  preserved, inactive/demoted accounts never promoted);
* #4118 ``moonmind.security.omnigent_auth_qualification`` session
  primitives plus the DB revocation adapter
  (``api_service/services/session_store.py``) for #4121 session
  issuance/revocation on one durable truth;
* #4120 ``moonmind.security.auth_modes_4120`` for mode/config ownership.

Replica/restart safety: authorization transactions live in the existing
database (table ``moonmind_oidc_transactions``, ensured idempotently like
the #4120 migration-decision table), consumed exactly once with an atomic
``UPDATE ... WHERE consumed_at IS NULL RETURNING`` guard. Concurrent or
replayed callbacks converge on the first consumer; losers observe replay,
never a second login or a duplicate user.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.db.models import User
from api_service.services.identity_service import (
    ControlledEnrollmentRequiredError,
    IdentityValidationError,
    get_or_create_user_for_identity,
    resolve_user_id_for_identity,
)
from moonmind.security import omnigent_auth_qualification as q
from moonmind.security.oidc_advanced_4124 import (
    OidcDiscovery,
    OidcLoginError,
    OidcProviderConfig,
    OidcTransaction,
    OidcTransactionStore,
    new_transaction,
    resolve_oidc_config,
)
from moonmind.security.trusted_proxy_4124 import (
    OIDC_LOGOUT_LIMITATION,
    PROXY_LOGOUT_LIMITATION,
    ProxyAuthError,
    ProxyConfigError,
    TrustedProxyConfig,
    extract_proxy_identity,
    resolve_trusted_proxy_config,
)

logger = logging.getLogger(__name__)

OIDC_TRANSACTION_TABLE = "moonmind_oidc_transactions"


def ensure_oidc_transaction_table_sql(
    table: str = OIDC_TRANSACTION_TABLE,
) -> str:
    """Return idempotent DDL for single-use authorization transactions."""
    return (
        f"CREATE TABLE IF NOT EXISTS {table} ("
        "state VARCHAR(128) PRIMARY KEY, "
        "nonce VARCHAR(128) NOT NULL, "
        "code_verifier VARCHAR(256) NOT NULL, "
        "code_challenge VARCHAR(256) NOT NULL, "
        "redirect_uri VARCHAR(2048) NOT NULL, "
        "return_path VARCHAR(2048) NOT NULL DEFAULT '/', "
        "created_at DOUBLE PRECISION NOT NULL, "
        "expires_at DOUBLE PRECISION NOT NULL, "
        "consumed_at DOUBLE PRECISION NULL)"
    )


class DbOidcTransactionStore(OidcTransactionStore):
    """Replica-safe transaction store over the MoonMind database."""

    def __init__(self, session: AsyncSession):
        self._session = session

    async def save(self, txn: OidcTransaction) -> None:
        await self._session.execute(text(ensure_oidc_transaction_table_sql()))
        await self._session.execute(
            text(
                f"INSERT INTO {OIDC_TRANSACTION_TABLE} "
                "(state, nonce, code_verifier, code_challenge, redirect_uri, "
                "return_path, created_at, expires_at, consumed_at) "
                "VALUES (:state, :nonce, :verifier, :challenge, :redirect, "
                ":return_path, :created, :expires, NULL)"
            ),
            {
                "state": txn.state,
                "nonce": txn.nonce,
                "verifier": txn.code_verifier,
                "challenge": txn.code_challenge,
                "redirect": txn.redirect_uri,
                "return_path": txn.return_path,
                "created": txn.created_at,
                "expires": txn.expires_at,
            },
        )
        await self._session.flush()

    async def consume(
        self, state: str, *, now: float | None = None
    ) -> OidcTransaction:
        moment = time.time() if now is None else now
        await self._session.execute(text(ensure_oidc_transaction_table_sql()))
        row = (
            await self._session.execute(
                text(
                    f"UPDATE {OIDC_TRANSACTION_TABLE} SET consumed_at = :now "
                    "WHERE state = :state AND consumed_at IS NULL "
                    "AND expires_at > :now "
                    "RETURNING state, nonce, code_verifier, code_challenge, "
                    "redirect_uri, return_path, created_at, expires_at"
                ),
                {"state": (state or '').strip(), "now": moment},
            )
        ).first()
        if row is None:
            # Distinguish expiry/missing (invalid) from replay: a consumed
            # row that still exists is a replay; anything else is invalid.
            probe = (
                await self._session.execute(
                    text(
                        f"SELECT consumed_at FROM {OIDC_TRANSACTION_TABLE} "
                        "WHERE state = :state"
                    ),
                    {"state": (state or '').strip()},
                )
            ).first()
            if probe is not None and probe[0] is not None:
                raise OidcLoginError("replay_detected", "transaction already used")
            raise OidcLoginError("auth_invalid", "unknown or expired transaction")
        await self._session.flush()
        return OidcTransaction(
            state=str(row[0]),
            nonce=str(row[1]),
            code_verifier=str(row[2]),
            code_challenge=str(row[3]),
            redirect_uri=str(row[4]),
            return_path=str(row[5]),
            created_at=float(row[6]),
            expires_at=float(row[7]),
        )

    async def prune_expired(self, *, now: float | None = None) -> int:
        moment = time.time() if now is None else now
        await self._session.execute(text(ensure_oidc_transaction_table_sql()))
        result = await self._session.execute(
            text(f"DELETE FROM {OIDC_TRANSACTION_TABLE} WHERE expires_at <= :now"),
            {"now": moment},
        )
        await self._session.flush()
        return int(result.rowcount or 0)


# ---------------------------------------------------------------------------
# Admission policy: unknown external users need explicit enrollment
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AdvancedAdmissionPolicy:
    """Explicit admission policy for unknown external identities.

    Default denies automatic provisioning: a verified IdP account, a
    matching email, a verified domain, or an upstream admin list never
    grants MoonMind authority and never claims an existing account.
    Unknown identities raise ``enrollment_required`` (a
    :class:`ControlledEnrollmentRequiredError`) for the operator path.
    Set ``auto_provision=True`` only with explicit operator opt-in
    (``MOONMIND_OIDC_AUTO_PROVISION=1``); provisioned users are always
    ``is_superuser=False`` and keep current active/admin rules.
    """

    auto_provision: bool = False

    def require_enrollment(self, detail: str) -> ControlledEnrollmentRequiredError:
        return ControlledEnrollmentRequiredError("enrollment_required", detail)


async def resolve_oidc_user(
    session: AsyncSession,
    identity: q.ValidatedIdentity,
    *,
    policy: AdvancedAdmissionPolicy | None = None,
) -> User:
    """Resolve a verified OIDC identity to its MoonMind user (fail-closed).

    Existing mappings return the current row unchanged (UUID preserved;
    active/admin flags untouched). Unknown identities raise
    ``enrollment_required`` unless ``policy.auto_provision`` is set, in
    which case they are provisioned via the #4119 transactional path with
    ``is_superuser=False``. Inactive accounts raise ``inactive`` and are
    never promoted. Email is informational: same-email-across-issuers and
    changed-email cases follow #4119 ``email_taken`` semantics (raise,
    never merge).
    """
    policy = policy or AdvancedAdmissionPolicy()
    try:
        user_id = await resolve_user_id_for_identity(
            session, identity.issuer, identity.subject
        )
    except IdentityValidationError as exc:
        raise OidcLoginError("auth_invalid", "reserved identity") from exc
    if user_id is not None:
        user = await session.get(User, user_id)
        if user is None:  # pragma: no cover - ledger inconsistency guard
            raise OidcLoginError("auth_invalid", "identity points at a missing user")
        if not user.is_active:
            forbidden = q.ForbiddenError("inactive", "account inactive")
            raise forbidden
        # Refresh informational email only when uncontested (#4119 owns the
        # email_taken guard); never touch is_superuser/is_active here.
        if identity.email and user.email != identity.email:
            from sqlalchemy import select as _select

            taken = await session.execute(
                _select(User.id).where(User.email == identity.email)
            )
            owner = taken.scalars().first()
            if owner is not None and owner != user.id:
                raise ControlledEnrollmentRequiredError(
                    "email_taken",
                    "email is already owned by a different user; explicit "
                    "operator enrollment is required, automatic linking is refused",
                )
            user.email = identity.email
            await session.flush()
        return user
    if not policy.auto_provision:
        raise policy.require_enrollment(
            "Unknown external identity. Explicit operator enrollment is "
            "required before first login; a valid IdP account does not "
            "grant MoonMind authority."
        )
    user, _ = await get_or_create_user_for_identity(
        session,
        identity.issuer,
        identity.subject,
        email=identity.email,
    )
    if not user.is_active:
        raise q.ForbiddenError("inactive", "account inactive")
    return user


async def resolve_proxy_user(
    session: AsyncSession,
    identity: q.ValidatedIdentity,
    *,
    policy: AdvancedAdmissionPolicy | None = None,
) -> User:
    """Resolve a trusted-proxy identity (never auto-provisions email-only).

    Proxy identities resolve through the same #4119 authority. Unknown
    identities raise ``enrollment_required``; email-shaped subjects never
    auto-provision even when ``auto_provision`` is set (explicit operator
    enrollment with a documented reassignment policy is required). Local
    account disablement blocks every request (``inactive``).
    """
    policy = policy or AdvancedAdmissionPolicy()
    if "@" in (identity.subject or ""):
        # Email-only proxy integrations need explicit enrollment and
        # reassignment policy; silent merges are refused even in
        # auto-provision deployments.
        existing = await resolve_user_id_for_identity(
            session, identity.issuer, identity.subject
        )
        if existing is None:
            raise ControlledEnrollmentRequiredError(
                "enrollment_required",
                "Email-asserted proxy identity requires explicit operator "
                "enrollment with a documented reassignment policy; automatic "
                "account merging is refused.",
            )
        user = await session.get(User, existing)
        if user is None or not user.is_active:
            raise q.ForbiddenError("inactive", "account inactive")
        return user
    return await resolve_oidc_user(session, identity, policy=policy)


# ---------------------------------------------------------------------------
# Session issuance / logout (shared #4118/#4121 authority)
# ---------------------------------------------------------------------------


async def issue_session_for_user(
    session: AsyncSession,
    user: User,
    identity: q.ValidatedIdentity,
    config: q.MoonmindAuthConfig,
    *,
    now: int | None = None,
) -> tuple[str, UUID]:
    """Mint a #4121 session for an already-resolved user.

    Re-resolves through the authoritative stores at mint time so a
    concurrent disable/reset bump before commit invalidates correctly.
    """
    from api_service.services.session_store import DbAccountStore, DbRevocationStore

    accounts = DbAccountStore(session)
    revocation = DbRevocationStore(session)
    token, user_id = await q.mint_moonmind_session(
        identity, accounts, config, now=now, revocation=revocation
    )
    import jwt as _jwt

    claims = _jwt.decode(token, options={"verify_signature": False})
    await revocation.record_issued_session(
        jti=str(claims["jti"]),
        user_id=user_id,
        generation=int(claims.get("gen", 0)),
        expires_at_epoch=int(claims["exp"]),
    )
    await session.commit()
    return token, user_id


@dataclass(frozen=True)
class AdvancedLogoutResult:
    """Honest logout outcome: local revocation always happens first."""

    local_revoked: bool
    idp_logout_attempted: bool
    idp_logout_ok: bool
    limitation: str


async def logout_session(
    session: AsyncSession,
    *,
    jti: str,
    user_id: UUID,
    idp_end_session_url: str = "",
    idp_logout_http_get: object = None,
    timeout_seconds: float = 10.0,
    proxy_mode: bool = False,
) -> AdvancedLogoutResult:
    """Revoke the MoonMind session, then best-effort IdP end-session.

    Local revocation commits before any IdP call, so logout invalidates
    the MoonMind session even when the optional IdP logout fails. IdP
    failures are swallowed into ``idp_logout_ok=False`` with the honest
    limitation note; they never resurrect the local session.
    """
    from api_service.services.session_store import DbRevocationStore

    store = DbRevocationStore(session)
    await store.revoke_session_for_user(jti, user_id, reason="logout")
    await session.commit()
    if proxy_mode:
        return AdvancedLogoutResult(
            local_revoked=True,
            idp_logout_attempted=False,
            idp_logout_ok=False,
            limitation=PROXY_LOGOUT_LIMITATION,
        )
    if not idp_end_session_url or idp_logout_http_get is None:
        return AdvancedLogoutResult(
            local_revoked=True,
            idp_logout_attempted=False,
            idp_logout_ok=False,
            limitation=OIDC_LOGOUT_LIMITATION,
        )
    try:
        resp = idp_logout_http_get(  # type: ignore[operator]
            idp_end_session_url, timeout=timeout_seconds
        )
        ok = int(getattr(resp, "status_code", 500) or 500) < 400
    except Exception:  # noqa: BLE001 - IdP failure never blocks local logout
        logger.warning("oidc_event idp_logout_failed code=idp_unavailable")
        ok = False
    return AdvancedLogoutResult(
        local_revoked=True,
        idp_logout_attempted=True,
        idp_logout_ok=bool(ok),
        limitation=OIDC_LOGOUT_LIMITATION,
    )


# ---------------------------------------------------------------------------
# Startup validation for advanced modes (no outbound network here)
# ---------------------------------------------------------------------------


def validate_advanced_mode_config(
    mode: str, *, environ=None
) -> OidcProviderConfig | TrustedProxyConfig | None:
    """Validate ``oidc``/``header`` operator configuration at startup.

    Shape validation only (no discovery fetch, no network): ``oidc``
    requires ``MOONMIND_OIDC_ISSUER``/``MOONMIND_OIDC_CLIENT_ID``/
    ``MOONMIND_OIDC_CLIENT_SECRET`` plus a same-origin callback against
    ``MOONMIND_PUBLIC_BASE_URL``; ``header`` requires trusted ingress,
    a proxy set, and a namespace. Other modes return ``None``. Raises
    with actionable guidance; never guesses or translates selectors.
    """
    import os as _os

    env = _os.environ if environ is None else environ
    normalized = (mode or "").strip().lower()
    if normalized == "oidc":
        issuer = (env.get("MOONMIND_OIDC_ISSUER") or env.get("OIDC_ISSUER_URL") or "").strip()
        client_id = (env.get("MOONMIND_OIDC_CLIENT_ID") or env.get("OIDC_CLIENT_ID") or "").strip()
        client_secret = (
            env.get("MOONMIND_OIDC_CLIENT_SECRET")
            or env.get("OIDC_CLIENT_SECRET")
            or ""
        )
        base_url = (env.get("MOONMIND_PUBLIC_BASE_URL") or "").strip()
        callback = (
            env.get("MOONMIND_OIDC_CALLBACK_URL")
            or env.get("MOONMIND_OIDC_REDIRECT_URI")
            or ""
        ).strip()
        if not callback and base_url:
            callback = base_url.rstrip("/") + "/api/v1/auth/oidc/callback"
        try:
            return resolve_oidc_config(
                issuer=issuer or None,
                client_id=client_id or None,
                client_secret=client_secret or None,
                redirect_uri=callback or None,
                base_url=base_url or None,
            )
        except Exception as exc:
            from moonmind.security.omnigent_auth_qualification import AuthConfigError

            raise AuthConfigError(
                f"Invalid 'oidc' configuration: {exc}. Set MOONMIND_OIDC_ISSUER, "
                "MOONMIND_OIDC_CLIENT_ID, MOONMIND_OIDC_CLIENT_SECRET, and "
                "MOONMIND_PUBLIC_BASE_URL (callback must be same-origin). See "
                "docs/Security/AuthenticationContracts.md."
            ) from exc
    if normalized == "header":
        namespace = (
            env.get("MOONMIND_PROXY_IDENTITY_NAMESPACE")
            or env.get("MOONMIND_HEADER_IDENTITY_NAMESPACE")
            or ""
        )
        trusted_ingress = (env.get("MOONMIND_TRUSTED_INGRESS", "") or "").lower() in (
            "1",
            "true",
            "yes",
        )
        header_name = (
            env.get("MOONMIND_PROXY_IDENTITY_HEADER") or "X-Moonmind-User"
        ).strip()
        allow_email = (env.get("MOONMIND_PROXY_ALLOW_EMAIL_IDENTITIES", "") or "").lower() in (
            "1",
            "true",
            "yes",
        )
        try:
            return resolve_trusted_proxy_config(
                namespace=namespace or None,
                trusted_proxies=(env.get("MOONMIND_TRUSTED_PROXIES") or ""),
                trusted_ingress=trusted_ingress,
                header_name=header_name or "X-Moonmind-User",
                allow_email_identities=allow_email,
            )
        except (ProxyConfigError, Exception) as exc:
            from moonmind.security.omnigent_auth_qualification import AuthConfigError

            raise AuthConfigError(
                f"Invalid 'header' configuration: {exc}. Set "
                "MOONMIND_TRUSTED_INGRESS=1, MOONMIND_TRUSTED_PROXIES, and "
                "MOONMIND_PROXY_IDENTITY_NAMESPACE behind an audited ingress "
                "that strips and replaces identity headers. See "
                "docs/Security/AuthenticationContracts.md."
            ) from exc
    return None


def extract_proxy_identity_from_request(
    headers_multi: list[tuple[str, str]],
    *,
    peer_ip: str,
    config: TrustedProxyConfig,
    forwarded_host: str | None = None,
    forwarded_proto: str | None = None,
):
    """Thin wrapper translating proxy errors to authority errors."""
    try:
        return extract_proxy_identity(
            headers_multi=headers_multi,
            peer_ip=peer_ip,
            config=config,
            forwarded_host=forwarded_host,
            forwarded_proto=forwarded_proto,
        )
    except ProxyAuthError as exc:
        if exc.code == "auth_required":
            raise q.AuthRequiredError(exc.detail) from exc
        raise q.AuthInvalidError(exc.code, exc.detail) from exc


def begin_oidc_login(
    config: OidcProviderConfig,
    discovery: OidcDiscovery,
    *,
    return_path: str = "/",
    now: float | None = None,
) -> tuple[OidcTransaction, str]:
    """Create a transaction and its authorization URL (no I/O)."""
    from moonmind.security.oidc_advanced_4124 import build_authorization_url

    txn = new_transaction(
        redirect_uri=config.redirect_uri, return_path=return_path, now=now
    )
    return txn, build_authorization_url(config, discovery, txn)
