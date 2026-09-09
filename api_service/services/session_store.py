"""DB-backed session/revocation adapters for #4121.

Production wiring of the portable ``SessionRevocationStore`` /
``AsyncAccountStore`` protocols against the existing MoonMind database
(one durable mechanism: ``moonmind_sessions`` +
``moonmind_user_session_generations``). Composes the qualified #4118
token primitives with #4119 UUID resolution (``UserExternalIdentity`` for
OIDC/proxy, existing ``User`` row for ``moonmind-accounts`` logins) and
#4120 configuration. Used by the session authority; account, OIDC, API,
machine, and dashboard issues consume this interface rather than
implementing independent validators.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.db.models import (
    MoonmindSession,
    MoonmindUserSessionGeneration,
    User,
    UserExternalIdentity,
)
from moonmind.security import omnigent_auth_qualification as q

ACCOUNTS_ISSUER = "moonmind-accounts"

UTC = timezone.utc


def _utcnow() -> datetime:
    return datetime.now(UTC)


class DbAccountStore:
    """Production ``AsyncAccountStore`` over the MoonMind database."""

    def __init__(self, session_factory: Callable[[], AsyncSession] | AsyncSession):
        self._sessions = session_factory

    def _session(self) -> AsyncSession:
        if isinstance(self._sessions, AsyncSession):
            return self._sessions
        produced = self._sessions()
        assert isinstance(produced, AsyncSession)
        return produced

    async def resolve_identity_to_user_id(
        self, identity: q.ValidatedIdentity
    ) -> uuid.UUID | None:
        session = self._session()
        if identity.issuer == ACCOUNTS_ISSUER:
            result = await session.execute(
                select(User.id).where(User.email == identity.subject)
            )
            return result.scalars().first()
        result = await session.execute(
            select(UserExternalIdentity.user_id).where(
                UserExternalIdentity.issuer == identity.issuer,
                UserExternalIdentity.subject == identity.subject,
            )
        )
        return result.scalars().first()

    async def get_account(self, user_id: uuid.UUID) -> q.AccountRecord | None:
        session = self._session()
        user = await session.get(User, user_id)
        if user is None:
            return None
        return q.AccountRecord(
            user_id=user.id,
            is_active=bool(user.is_active),
            is_superuser=bool(user.is_superuser),
            email=user.email,
        )

    async def get_password_hash_by_login(self, login: str) -> str | None:
        session = self._session()
        result = await session.execute(
            select(User.hashed_password).where(User.email == login.strip())
        )
        return result.scalars().first()

    async def record_login(self, user_id: uuid.UUID, when_epoch_seconds: int) -> None:
        return None


class DbRevocationStore:
    """Production ``SessionRevocationStore`` over the MoonMind database.

    Unknown session/grant state, store outage, and unregistered tokens fail
    closed at the authority layer (``UnavailableError`` / ``auth_invalid``);
    this store never synthesizes an administrator and never returns stale
    authority on error.
    """

    def __init__(self, session_factory: Callable[[], AsyncSession] | AsyncSession):
        self._sessions = session_factory

    def _session(self) -> AsyncSession:
        if isinstance(self._sessions, AsyncSession):
            return self._sessions
        produced = self._sessions()
        assert isinstance(produced, AsyncSession)
        return produced

    async def revoke_session(self, jti: str) -> None:
        session = self._session()
        row = await session.get(MoonmindSession, jti)
        if row is None:
            # Unknown jti: nothing durable to mark, and validation already
            # fails closed on unknown state (is_session_revoked -> True).
            # No synthetic row is inserted (it would violate the user FK).
            return
        row.revoked_at = _utcnow()
        row.revoked_reason = row.revoked_reason or "logout"
        await session.flush()

    async def revoke_session_for_user(
        self, jti: str, user_id: UUID, *, reason: str = "logout"
    ) -> None:
        """Revoke one session row owned by ``user_id`` (logout path)."""
        session = self._session()
        row = await session.get(MoonmindSession, jti)
        if row is None:
            return
        if row.user_id != user_id:
            return
        row.revoked_at = _utcnow()
        row.revoked_reason = reason
        await session.flush()

    async def is_session_revoked(self, jti: str) -> bool:
        session = self._session()
        row = await session.get(MoonmindSession, jti)
        if row is None:
            # Unknown session state fails closed upstream only when the jti
            # was never issued here; rows are recorded at mint, so absence
            # means unregistered token material.
            return True
        return row.revoked_at is not None

    async def revoke_all_for_user(self, user_id: uuid.UUID) -> int:
        from sqlalchemy import update

        session = self._session()
        # Atomic increment: concurrent revocations serialize on the row
        # instead of lost-updating a read-modify-write cycle.
        result = await session.execute(
            update(MoonmindUserSessionGeneration)
            .where(MoonmindUserSessionGeneration.user_id == user_id)
            .values(
                generation=MoonmindUserSessionGeneration.generation + 1,
                updated_at=_utcnow(),
            )
            .returning(MoonmindUserSessionGeneration.generation)
        )
        current = result.scalar_one_or_none()
        if current is not None:
            await session.flush()
            return int(current)
        # No row yet: insert generation 1, converging on the winner when a
        # concurrent inserter wins the race.
        nested = await session.begin_nested()
        try:
            session.add(
                MoonmindUserSessionGeneration(user_id=user_id, generation=1)
            )
            await session.flush()
            await nested.commit()
            return 1
        except IntegrityError:
            await nested.rollback()
            result = await session.execute(
                update(MoonmindUserSessionGeneration)
                .where(MoonmindUserSessionGeneration.user_id == user_id)
                .values(
                    generation=MoonmindUserSessionGeneration.generation + 1,
                    updated_at=_utcnow(),
                )
                .returning(MoonmindUserSessionGeneration.generation)
            )
            current = result.scalar_one_or_none()
            assert current is not None
            await session.flush()
            return int(current)

    async def generation_for_user(self, user_id: uuid.UUID) -> int:
        session = self._session()
        row = await session.get(MoonmindUserSessionGeneration, user_id)
        return int(row.generation) if row is not None else 0

    async def record_issued_session(
        self,
        *,
        jti: str,
        user_id: UUID,
        generation: int,
        expires_at_epoch: int,
    ) -> None:
        """Record an issued session so logout/replicas share one truth.

        Converges on the existing row when a concurrent mint reuses a jti
        (jtis are random; collisions converge rather than resurrect).
        """
        session = self._session()
        expires_at = datetime.fromtimestamp(expires_at_epoch, tz=UTC)
        existing = await session.get(MoonmindSession, jti)
        if existing is not None:
            return
        session.add(
            MoonmindSession(
                jti=jti,
                user_id=user_id,
                generation=generation,
                created_at=_utcnow(),
                expires_at=expires_at,
            )
        )
        try:
            await session.flush()
        except IntegrityError:
            await session.rollback()


async def mint_and_record_session(
    identity: q.ValidatedIdentity,
    account_store: DbAccountStore,
    revocation: DbRevocationStore,
    config: q.MoonmindAuthConfig,
    session: AsyncSession,
    *,
    now: int | None = None,
) -> tuple[str, UUID]:
    """Mint via the #4118 primitive and record the row in one transaction.

    The mint re-checks account active status; the generation captured at
    mint is the live generation, so a concurrent disable/reset bump before
    commit invalidates the token at the next validation (no resurrection).
    """
    token, user_id = await q.mint_moonmind_session(
        identity, account_store, config, now=now, revocation=revocation
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


async def disable_user_and_revoke_sessions(
    session: AsyncSession, user_id: UUID
) -> int:
    """Deactivate a user and invalidate all sessions transactionally."""
    user = await session.get(User, user_id)
    if user is not None:
        user.is_active = False
    store = DbRevocationStore(session)
    generation = await store.revoke_all_for_user(user_id)
    await session.commit()
    return generation


async def count_active_sessions(session: AsyncSession, user_id: UUID) -> int:
    result = await session.execute(
        select(func.count())
        .select_from(MoonmindSession)
        .where(
            MoonmindSession.user_id == user_id,
            MoonmindSession.revoked_at.is_(None),
        )
    )
    return int(result.scalar() or 0)
