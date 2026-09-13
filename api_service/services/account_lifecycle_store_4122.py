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

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.db.models import (
    MoonmindAccountNonce,
    User,
    UserProfile,
)
from moonmind.security.account_lifecycle_4122 import (
    AdminRefusedError,
    BootstrapError,
    InviteError,
    RecoveryError,
    apply_member_action,
    LifecycleMember,
)

__all__ = [
    "AsyncDbLifecycleStore",
    "claim_first_owner_and_create_user",
    "redeem_invite_and_create_user",
    "redeem_recovery_for_user",
    "apply_member_action_transactional",
]

_NONCE_PURPOSES = frozenset({"bootstrap", "invite", "recovery"})


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
    outcome = updated[target_login]
    target = next(user for user in users if str(user.email) == target_login)
    if action == "remove":
        target.is_active = False
        target.is_superuser = False
    else:
        target.is_active = outcome.is_active
        target.is_superuser = outcome.is_superuser
    await session.flush()
    if action in ("deactivate", "remove"):
        await DbRevocationStore(session).revoke_all_for_user(target.id)
    await session.commit()
    return target
