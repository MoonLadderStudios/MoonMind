"""DB-backed account-lifecycle adapters for #4122.

Production wiring of the portable ``LifecycleStore`` protocol against the
existing MoonMind database (one ``User`` row per login; capability-nonce
consumption and owner claim in the same transaction as user creation —
no second account database). Composes the hermetic #4122 lifecycle rules
in ``moonmind/security/account_lifecycle_4122.py`` (capability mint and
verify, member-administration rules, error mapping) with the existing
``User``/``UserProfile`` rows and the ``MoonmindAccountNonce`` single-use
record.

Operated runbook primitives (fresh-install setup and local operator
recovery, AuthenticationContracts §7):

* :func:`claim_first_owner_and_create_user` — protected first-owner
  setup: capability-nonce claim plus owner ``User`` creation plus profile
  creation in one transaction. Exactly one winner under concurrent setup;
  closed on a populated database.
* :func:`redeem_invite_and_create_user` — invitation redemption plus
  member ``User`` creation plus profile creation in one transaction.
  Expiring one-use capability semantics come from the caller verifying
  the capability with :func:`redeem_invite` first; the nonce insert here
  is the durable single-use enforcement.
* :func:`redeem_recovery_for_user` — local operator recovery for an
  existing login: consumes the recovery nonce and returns the user.
  Never creates an account, never disables authentication.
* :func:`redeem_recovery_and_restore_access` — the tested recovery path
  the last-admin refusal points at: consumes the recovery nonce and, in
  the same transaction, reactivates and promotes the login so a stranded
  deployment regains an administrator. Never creates an account.
* :func:`claim_first_owner_with_token`,
  :func:`redeem_invite_with_token`, and :func:`redeem_recovery_with_token`
  — the async verification-and-persistence boundary: each verifies the
  capability (expiry, signature, login binding) with the hermetic rule,
  then persists the extracted nonce atomically with the ``User`` row in
  one transaction. ``AsyncDbLifecycleStore`` below is the async
  persistence primitive behind that boundary; it must never be passed to
  the synchronous hermetic functions (they reject ``async def`` stores
  fail-closed).
* :func:`apply_member_action_transactional` — server-owned member
  administration (last-admin protection from the pure rule) persisted to
  the ``User`` row; deactivation/removal also bumps the session
  revocation generation so existing sessions stop validating.

Password material is never generated here: callers pass an
operator-supplied ``hashed_password`` produced by the qualified upstream
primitive (``omnigent.server.passwords``), or ``None`` when the password
is set later through the controlled invitation/reset path. There is no
default password and no public re-claim shape.
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.db.models import (
    MoonmindAccountNonce,
    User,
    UserProfile,
)
from moonmind.security.account_lifecycle_4122 import (
    BootstrapError,
    InviteError,
    RecoveryError,
    apply_member_action,
    bootstrap_nonce_for_login,
    invite_nonce_for_login,
    LifecycleMember,
    recovery_nonce_for_login,
)

__all__ = [
    "AsyncDbLifecycleStore",
    "claim_first_owner_and_create_user",
    "claim_first_owner_with_token",
    "redeem_invite_and_create_user",
    "redeem_invite_with_token",
    "redeem_recovery_for_user",
    "redeem_recovery_and_rotate_password",
    "redeem_recovery_with_token",
    "redeem_recovery_and_restore_access",
    "apply_member_action_transactional",
]

_NONCE_PURPOSES = frozenset({"bootstrap", "invite", "recovery"})

# Transaction-scoped PostgreSQL advisory-lock keys serializing empty-set
# transitions that have no conflicting row to converge on (fresh-database
# first-owner claim, concurrent last-admin evaluations). Distinct families
# so owner setup never blocks member administration.
_FIRST_OWNER_LOCK_KEY = 4122001
_MEMBER_ADMIN_LOCK_KEY = 4122002


def _dialect_name(session: AsyncSession) -> str:
    dialect = getattr(getattr(session, "bind", None), "dialect", None)
    name = getattr(dialect, "name", "") or ""
    if name:
        return name
    get_bind = getattr(session, "get_bind", None)
    if callable(get_bind):
        try:
            return getattr(get_bind().dialect, "name", "") or ""
        except Exception:
            return ""
    return ""


async def _serialize_account_mutation(session: AsyncSession, lock_key: int) -> None:
    """Serialize an empty-set transition for the caller's transaction.

    PostgreSQL takes a transaction-scoped advisory lock, so a second
    claimant of the same family blocks until the first has written its
    row and is therefore counted. SQLite serializes writers and has no
    advisory locks, so the surrounding transaction already provides the
    guarantee there.
    """
    if _dialect_name(session).startswith("postgres"):
        await session.execute(
            text("SELECT pg_advisory_xact_lock(:key)"),
            {"key": lock_key},
        )


def _normalize_login(login: str, error_cls: type = BootstrapError) -> str:
    clean = (login or "").strip()
    if not clean:
        raise error_cls("auth_invalid", "account lifecycle requires a login name")
    return clean


class AsyncDbLifecycleStore:
    """Production ``LifecycleStore`` over the MoonMind database.

    Owner existence reads the existing ``User`` table; one-use
    enforcement writes ``MoonmindAccountNonce`` (primary-key convergence:
    a concurrent or replayed redemption loses instead of admitting a
    second account). Unknown state and store outage fail closed at the
    authority layer; this store never synthesizes an owner.
    """

    def __init__(self, session: AsyncSession):
        self._session = session

    async def has_owner(self) -> bool:
        result = await self._session.execute(
            select(func.count()).select_from(User)
        )
        return int(result.scalar() or 0) > 0

    async def try_claim_owner(self, login: str, nonce: str) -> bool:
        """Atomically claim first ownership; ``False`` when already claimed."""
        await _serialize_account_mutation(self._session, _FIRST_OWNER_LOCK_KEY)
        if await self.has_owner():
            return False
        return await self.consume_nonce(
            nonce, purpose="bootstrap", login=login
        )

    async def consume_nonce(self, nonce: str, *, purpose: str = "invite", login: str = "") -> bool:
        """Atomically consume a one-use nonce; ``False`` when already used."""
        if purpose not in _NONCE_PURPOSES:
            raise ValueError(f"unknown nonce purpose {purpose!r}")
        if not nonce:
            return False
        nested = await self._session.begin_nested()
        try:
            self._session.add(
                MoonmindAccountNonce(nonce=nonce, purpose=purpose, login=login or "")
            )
            await self._session.flush()
            await nested.commit()
            return True
        except IntegrityError:
            # A full session rollback, not a savepoint rollback: the failed
            # flush poisons the session transaction, and the next operation
            # must start clean so a replay fails closed with a typed error
            # instead of a session-state exception.
            await self._session.rollback()
            return False

    async def is_nonce_consumed(self, nonce: str) -> bool:
        """Whether a nonce was already consumed."""
        if not nonce:
            return False
        result = await self._session.execute(
            select(func.count())
            .select_from(MoonmindAccountNonce)
            .where(MoonmindAccountNonce.nonce == nonce)
        )
        return int(result.scalar() or 0) > 0


async def _ensure_profile(session: AsyncSession, user_id: uuid.UUID) -> None:
    nested = await session.begin_nested()
    try:
        session.add(UserProfile(user_id=user_id))
        await session.flush()
        await nested.commit()
    except IntegrityError:
        # A concurrent creator won the race; converge on the existing row.
        # Full rollback: the failed flush poisons the session otherwise.
        await session.rollback()


async def claim_first_owner_and_create_user(
    session: AsyncSession,
    *,
    login: str,
    nonce: str,
    hashed_password: str | None = None,
) -> User:
    """Claim protected first-owner setup and create the owner ``User`` atomically.

    Fails closed with :class:`BootstrapError` (``bootstrap_closed``) on a
    populated database and with ``bootstrap_consumed`` when the capability
    nonce was already redeemed — including a concurrent setup that won the
    race. The owner is created active and superuser with one profile; UUID
    and ownership records of existing rows are never touched.
    """
    clean = _normalize_login(login)
    if not nonce:
        raise BootstrapError("auth_invalid", "bootstrap claim requires a capability nonce")
    # Serialize the empty-to-owned transition: distinct concurrent claims
    # carry distinct nonces and logins, so neither the nonce primary key
    # nor the login constraint would converge them — the loser must block
    # here and then fail closed on the populated database below.
    await _serialize_account_mutation(session, _FIRST_OWNER_LOCK_KEY)
    outer = await session.begin_nested()
    try:
        existing = await session.execute(select(func.count()).select_from(User))
        if int(existing.scalar() or 0) > 0:
            raise BootstrapError(
                "bootstrap_closed",
                "first-owner setup is closed: an owner already exists; "
                "use invitation or recovery",
            )
        session.add(
            MoonmindAccountNonce(nonce=nonce, purpose="bootstrap", login=clean)
        )
        try:
            await session.flush()
        except IntegrityError as exc:
            raise BootstrapError(
                "bootstrap_consumed",
                "bootstrap capability was already consumed or ownership was "
                "claimed concurrently",
            ) from exc
        user = User(
            id=uuid.uuid4(),
            email=clean,
            hashed_password=hashed_password,
            is_active=True,
            is_superuser=True,
            is_verified=False,
        )
        session.add(user)
        try:
            await session.flush()
        except IntegrityError as exc:
            # A concurrent setup created the owner (or the login) first;
            # the winner owns the database, the loser fails closed.
            raise BootstrapError(
                "bootstrap_consumed",
                "ownership was claimed concurrently",
            ) from exc
        await outer.commit()
    except BootstrapError:
        # Full session rollback: the failed flush poisons the session, so a
        # savepoint-only rollback would leave the next operation raising a
        # session-state exception instead of the typed lifecycle error.
        await session.rollback()
        raise
    await _ensure_profile(session, user.id)
    await session.commit()
    return user


async def redeem_invite_and_create_user(
    session: AsyncSession,
    *,
    login: str,
    nonce: str,
    hashed_password: str | None = None,
) -> User:
    """Redeem an invitation nonce and create the member ``User`` atomically.

    Members are created active and non-superuser (never promoted by
    enrollment; privilege changes go through
    :func:`apply_member_action_transactional`). A replayed nonce raises
    :class:`InviteError`; an already-enrolled login raises
    ``email_taken`` instead of merging or transferring ownership.
    """
    clean = _normalize_login(login, InviteError)
    if not nonce:
        raise InviteError("auth_invalid", "invitation redemption requires a nonce")
    outer = await session.begin_nested()
    try:
        taken = await session.execute(select(User.id).where(User.email == clean))
        if taken.scalars().first() is not None:
            raise InviteError(
                "email_taken",
                "login is already enrolled; automatic linking is refused",
            )
        session.add(
            MoonmindAccountNonce(nonce=nonce, purpose="invite", login=clean)
        )
        try:
            await session.flush()
        except IntegrityError as exc:
            raise InviteError(
                "auth_invalid", "invitation was already redeemed"
            ) from exc
        user = User(
            id=uuid.uuid4(),
            email=clean,
            hashed_password=hashed_password,
            is_active=True,
            is_superuser=False,
            is_verified=False,
        )
        session.add(user)
        try:
            await session.flush()
        except IntegrityError as exc:
            raise InviteError(
                "email_taken",
                "login was enrolled concurrently; automatic linking is refused",
            ) from exc
        await outer.commit()
    except InviteError:
        # Full session rollback (see claim_first_owner_and_create_user).
        await session.rollback()
        raise
    await _ensure_profile(session, user.id)
    await session.commit()
    return user


async def redeem_recovery_for_user(
    session: AsyncSession, *, login: str, nonce: str
) -> User:
    """Redeem a recovery nonce for an existing login.

    Returns the existing user without modifying it (credential rotation
    itself is a separate controlled operation). Raises
    :class:`RecoveryError` for unknown logins and replayed nonces — never
    creates an account and never disables authentication.
    """
    clean = _normalize_login(login, RecoveryError)
    if not nonce:
        raise RecoveryError("auth_invalid", "recovery requires a capability nonce")
    result = await session.execute(select(User).where(User.email == clean))
    user = result.scalars().first()
    if user is None:
        raise RecoveryError("auth_invalid", "unknown login for recovery")
    store = AsyncDbLifecycleStore(session)
    if not await store.consume_nonce(nonce, purpose="recovery", login=clean):
        raise RecoveryError("auth_invalid", "recovery capability was already consumed")
    await session.commit()
    return user


async def redeem_recovery_and_rotate_password(
    session: AsyncSession, *, login: str, nonce: str, hashed_password: str
) -> User:
    """Atomically consume a recovery nonce, rotate credentials, revoke sessions.

    Single-transaction boundary for the HTTP recovery-redeem path: the
    one-use nonce insert, password update, and revocation-generation bump
    commit together. A database failure before commit leaves the nonce
    unconsumed so the operator can retry with the same capability instead
    of burning the token while leaving the password unchanged. Preserves
    active/admin flags exactly; never mints a session.
    """
    from api_service.services.session_store import DbRevocationStore

    clean = _normalize_login(login, RecoveryError)
    if not nonce:
        raise RecoveryError("auth_invalid", "recovery requires a capability nonce")
    if not hashed_password:
        raise RecoveryError("auth_invalid", "recovery requires a replacement password")
    outer = await session.begin_nested()
    try:
        result = await session.execute(select(User).where(User.email == clean))
        user = result.scalars().first()
        if user is None:
            raise RecoveryError("auth_invalid", "unknown login for recovery")
        session.add(
            MoonmindAccountNonce(nonce=nonce, purpose="recovery", login=clean)
        )
        try:
            await session.flush()
        except IntegrityError as exc:
            raise RecoveryError(
                "auth_invalid", "recovery capability was already consumed"
            ) from exc
        user.hashed_password = hashed_password
        await session.flush()
        await DbRevocationStore(session).revoke_all_for_user(user.id)
        await outer.commit()
    except RecoveryError:
        await session.rollback()
        raise
    await session.commit()
    return user


async def claim_first_owner_with_token(
    session: AsyncSession,
    *,
    token: str,
    key: bytes,
    login: str,
    hashed_password: str | None = None,
    now: float | None = None,
) -> User:
    """Verify a bootstrap capability and claim first ownership in one step.

    The async verification-and-persistence boundary for first-owner setup:
    the capability (signature, expiry, login binding) is verified with the
    hermetic rule, then the extracted nonce is claimed atomically with
    owner creation in a single transaction. ``BootstrapError`` on invalid,
    expired, mismatched, replayed, or concurrently-won capabilities.
    """
    nonce = bootstrap_nonce_for_login(token, key=key, login=login, now=now)
    return await claim_first_owner_and_create_user(
        session, login=login, nonce=nonce, hashed_password=hashed_password
    )


async def redeem_invite_with_token(
    session: AsyncSession,
    *,
    token: str,
    key: bytes,
    login: str,
    hashed_password: str | None = None,
    now: float | None = None,
) -> User:
    """Verify an invitation capability and create the member in one step.

    The async verification-and-persistence boundary for invitation
    redemption: the capability (signature, expiry, login binding) is
    verified with the hermetic rule, then the extracted nonce is redeemed
    atomically with member creation in a single transaction.
    ``InviteError`` on invalid, expired, mismatched, or replayed invites.
    """
    nonce = invite_nonce_for_login(token, key=key, login=login, now=now)
    return await redeem_invite_and_create_user(
        session, login=login, nonce=nonce, hashed_password=hashed_password
    )


async def redeem_recovery_with_token(
    session: AsyncSession,
    *,
    token: str,
    key: bytes,
    login: str,
    now: float | None = None,
) -> User:
    """Verify a recovery capability and consume it in one step.

    The async verification-and-persistence boundary for operator recovery:
    the capability (signature, expiry, login binding) is verified with the
    hermetic rule, then the extracted nonce is consumed in the same step
    that returns the existing user. Never creates or mutates the account;
    ``RecoveryError`` on invalid, expired, mismatched, or replayed
    capabilities.
    """
    nonce = recovery_nonce_for_login(token, key=key, login=login, now=now)
    return await redeem_recovery_for_user(session, login=login, nonce=nonce)


async def redeem_recovery_and_restore_access(
    session: AsyncSession,
    *,
    token: str,
    key: bytes,
    login: str,
    hashed_password: str | None = None,
    now: float | None = None,
) -> User:
    """Redeem a recovery capability and restore administrator access.

    The tested recovery path the last-admin refusal points at: when the
    sole administrator is inactive or has lost usable credentials, the
    operator-held recovery capability both proves operator authority and
    — in the same transaction — reactivates and promotes ``login`` so the
    deployment regains an administrator. Unknown logins and replayed
    nonces fail closed with :class:`RecoveryError`; no account is ever
    created and authentication is never disabled.

    When ``hashed_password`` is supplied it is applied atomically with the
    restore; otherwise the existing credential material is retained and
    rotation stays on the controlled invitation/reset path. Existing
    sessions are revoked in the same transaction so previously issued or
    attacker-held sessions cannot retain administrator authority after
    restoration.
    """
    clean = _normalize_login(login, RecoveryError)
    nonce = recovery_nonce_for_login(token, key=key, login=clean, now=now)
    await _serialize_account_mutation(session, _MEMBER_ADMIN_LOCK_KEY)
    outer = await session.begin_nested()
    try:
        result = await session.execute(select(User).where(User.email == clean))
        user = result.scalars().first()
        if user is None:
            raise RecoveryError("auth_invalid", "unknown login for recovery")
        session.add(
            MoonmindAccountNonce(nonce=nonce, purpose="recovery", login=clean)
        )
        try:
            await session.flush()
        except IntegrityError as exc:
            raise RecoveryError(
                "auth_invalid", "recovery capability was already consumed"
            ) from exc
        user.is_active = True
        user.is_superuser = True
        if hashed_password is not None:
            user.hashed_password = hashed_password
        await session.flush()
        from api_service.services.session_store import DbRevocationStore

        await DbRevocationStore(session).revoke_all_for_user(user.id)
        await outer.commit()
    except RecoveryError:
        # Full session rollback: the failed flush poisons the session, so a
        # savepoint-only rollback would leave the next operation raising a
        # session-state exception instead of the typed lifecycle error.
        await session.rollback()
        raise
    await session.commit()
    return user


async def apply_member_action_transactional(
    session: AsyncSession,
    *,
    actor_login: str,
    target_login: str,
    action: str,
) -> User:
    """Apply one server-owned member action and persist it to the ``User`` row.

    The pure last-admin rule in :func:`apply_member_action` owns the
    decision; this persists the resulting ``is_active``/``is_superuser``
    flags. ``remove`` deactivates instead of deleting the row (UUID and
    ownership records are never rewritten). Deactivation also bumps the
    session revocation generation so existing sessions stop validating
    within the contract bound.
    """
    from api_service.services.session_store import DbRevocationStore

    # Serialize the administrator-set read/evaluate/persist sequence so two
    # concurrent last-admin mutations cannot both pass the pure rule on the
    # same roster snapshot.
    await _serialize_account_mutation(session, _MEMBER_ADMIN_LOCK_KEY)
    result = await session.execute(select(User))
    users = result.scalars().all()
    members: dict[str, LifecycleMember] = {
        str(user.email): LifecycleMember(
            login=str(user.email),
            is_active=bool(user.is_active),
            is_superuser=bool(user.is_superuser),
        )
        for user in users
    }
    updated = apply_member_action(
        members,
        actor_login=actor_login,
        target_login=target_login,
        action=action,
    )
    target = next(user for user in users if str(user.email) == target_login)
    if action == "remove":
        # The pure rule deletes the roster entry, so there is no ``outcome``
        # to read back: the persisted equivalent is deactivation plus
        # privilege removal (the row itself is never deleted).
        target.is_active = False
        target.is_superuser = False
    else:
        outcome = updated[target_login]
        target.is_active = outcome.is_active
        target.is_superuser = outcome.is_superuser
    await session.flush()
    if action in ("deactivate", "remove"):
        await DbRevocationStore(session).revoke_all_for_user(target.id)
    await session.commit()
    return target
