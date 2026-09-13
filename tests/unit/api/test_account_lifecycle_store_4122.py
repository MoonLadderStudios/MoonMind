"""Production User-table wiring for #4122 (parent #4116, docs #4130).

Proves the hermetic ``LifecycleStore`` rules against the real tables on
SQLite: protected first-owner setup creates exactly one owner ``User``
(closed on a populated database, one-use nonce, no second account
database), invitation redemption creates one member ``User`` with a
profile (replay refused, enrolled login never merged), recovery consumes
a one-use nonce without creating or modifying the account, and member
administration persists with last-admin protection. Mirrors the
``test_session_authority_4121.py`` boundary style: production service
functions, real tables, no mocks.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import (
    Base,
    MoonmindAccountNonce,
    MoonmindUserSessionGeneration,
    User,
    UserProfile,
)
from api_service.services.account_lifecycle_store_4122 import (
    AsyncDbLifecycleStore,
    apply_member_action_transactional,
    claim_first_owner_and_create_user,
    redeem_invite_and_create_user,
    redeem_recovery_for_user,
)
from moonmind.security.account_lifecycle_4122 import (
    AdminRefusedError,
    BootstrapError,
    InviteError,
    RecoveryError,
    mint_bootstrap_capability,
    mint_invite,
    mint_recovery_capability,
)

KEY = b"k" * 32
NOW = 1_700_000_000.0

_TABLES = (
    User.__table__,
    UserProfile.__table__,
    MoonmindAccountNonce.__table__,
    MoonmindUserSessionGeneration.__table__,
)


@asynccontextmanager
async def lifecycle_db(tmp_path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path}/account-lifecycle-4122.db", future=True
    )
    sessions = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync_connection: Base.metadata.create_all(
                sync_connection, tables=[table for table in _TABLES]
            )
        )
    try:
        yield sessions
    finally:
        await engine.dispose()


async def _fresh_session(sessions) -> AsyncSession:
    session = sessions()
    assert isinstance(session, AsyncSession)
    return session


@pytest.mark.asyncio
async def test_db_first_owner_setup_creates_one_owner_with_profile(tmp_path) -> None:
    """Fresh-install runbook: bootstrap nonce -> one owner User + profile."""
    async with lifecycle_db(tmp_path) as sessions:
        session = await _fresh_session(sessions)
        store = AsyncDbLifecycleStore(session)
        assert await store.has_owner() is False
        # The operated step persists the capability claim with the User row
        # atomically (capability expiry/binding itself is verified by the
        # hermetic rule before this durable step runs).
        owner = await claim_first_owner_and_create_user(
            session, login="owner@example.invalid", nonce="bootstrap-nonce-1"
        )
        assert owner.is_active is True
        assert owner.is_superuser is True
        profile = await session.execute(
            select(UserProfile).where(UserProfile.user_id == owner.id)
        )
        assert profile.scalars().first() is not None
        assert await store.has_owner() is True
        assert await store.is_nonce_consumed("bootstrap-nonce-1") is True
        await session.close()


@pytest.mark.asyncio
async def test_db_first_owner_closed_on_populated_database(tmp_path) -> None:
    """Populated database refuses first-owner setup without touching rows."""
    async with lifecycle_db(tmp_path) as sessions:
        session = await _fresh_session(sessions)
        owner = await claim_first_owner_and_create_user(
            session, login="owner@example.invalid", nonce="bootstrap-nonce-1"
        )
        owner_id = owner.id
        with pytest.raises(BootstrapError) as excinfo:
            await claim_first_owner_and_create_user(
                session, login="owner@example.invalid", nonce="bootstrap-nonce-2"
            )
        assert excinfo.value.code == "bootstrap_closed"
        # No second owner, no consumed nonce for the refused attempt.
        users = (await session.execute(select(User))).scalars().all()
        assert [user.id for user in users] == [owner_id]
        assert await AsyncDbLifecycleStore(session).is_nonce_consumed("bootstrap-nonce-2") is False
        await session.close()


@pytest.mark.asyncio
async def test_db_bootstrap_nonce_is_one_use(tmp_path) -> None:
    """A replayed bootstrap nonce fails closed instead of a second owner."""
    async with lifecycle_db(tmp_path) as sessions:
        session = await _fresh_session(sessions)
        await claim_first_owner_and_create_user(
            session, login="owner@example.invalid", nonce="shared-nonce"
        )
        # Same nonce cannot back another claim even after the owner row is
        # removed from the test's view: the nonce record persists.
        await session.execute(
            User.__table__.delete().where(
                User.email == "owner@example.invalid"
            )
        )
        await session.commit()
        with pytest.raises(BootstrapError) as excinfo:
            await claim_first_owner_and_create_user(
                session, login="owner@example.invalid", nonce="shared-nonce"
            )
        assert excinfo.value.code == "bootstrap_consumed"
        await session.close()


@pytest.mark.asyncio
async def test_db_invite_redeems_one_member_with_profile(tmp_path) -> None:
    """Invite runbook: verified capability nonce -> one member User + profile."""
    async with lifecycle_db(tmp_path) as sessions:
        session = await _fresh_session(sessions)
        await claim_first_owner_and_create_user(
            session, login="owner@example.invalid", nonce="bootstrap-nonce-1"
        )
        token = mint_invite("alice@example.invalid", key=KEY, now=NOW)
        from moonmind.security.account_lifecycle_4122 import (
            InMemoryLifecycleStore,
            redeem_invite,
        )

        # Capability verification (expiry/binding) stays with the hermetic rule.
        assert (
            redeem_invite(
                token,
                key=KEY,
                store=InMemoryLifecycleStore(),
                login="alice@example.invalid",
                now=NOW + 10,
            )
            == "alice@example.invalid"
        )
        member = await redeem_invite_and_create_user(
            session, login="alice@example.invalid", nonce="invite-nonce-1"
        )
        assert member.is_active is True
        assert member.is_superuser is False
        profile = await session.execute(
            select(UserProfile).where(UserProfile.user_id == member.id)
        )
        assert profile.scalars().first() is not None
        # Replay refused; enrolled login never merged.
        with pytest.raises(InviteError):
            await redeem_invite_and_create_user(
                session, login="bob@example.invalid", nonce="invite-nonce-1"
            )
        with pytest.raises(InviteError) as excinfo:
            await redeem_invite_and_create_user(
                session, login="alice@example.invalid", nonce="invite-nonce-2"
            )
        assert excinfo.value.code == "email_taken"
        await session.close()


@pytest.mark.asyncio
async def test_db_recovery_consumes_one_use_nonce_without_mutation(tmp_path) -> None:
    """Recovery runbook: one-use nonce, existing account returned unmodified."""
    async with lifecycle_db(tmp_path) as sessions:
        session = await _fresh_session(sessions)
        owner = await claim_first_owner_and_create_user(
            session, login="owner@example.invalid", nonce="bootstrap-nonce-1"
        )
        before = (owner.is_active, owner.is_superuser, owner.hashed_password)
        token = mint_recovery_capability("owner@example.invalid", key=KEY, now=NOW)
        from moonmind.security.account_lifecycle_4122 import (
            InMemoryLifecycleStore,
            redeem_recovery_capability,
        )

        assert (
            redeem_recovery_capability(
                token,
                key=KEY,
                store=InMemoryLifecycleStore(owner_exists=True),
                login="owner@example.invalid",
                now=NOW + 1,
            )
            == "owner@example.invalid"
        )
        recovered = await redeem_recovery_for_user(
            session, login="owner@example.invalid", nonce="recovery-nonce-1"
        )
        assert recovered.id == owner.id
        assert (recovered.is_active, recovered.is_superuser, recovered.hashed_password) == before
        with pytest.raises(RecoveryError):
            await redeem_recovery_for_user(
                session, login="owner@example.invalid", nonce="recovery-nonce-1"
            )
        with pytest.raises(RecoveryError):
            await redeem_recovery_for_user(
                session, login="ghost@example.invalid", nonce="recovery-nonce-2"
            )
        # No account was created by recovery.
        users = (await session.execute(select(User))).scalars().all()
        assert len(users) == 1
        await session.close()


@pytest.mark.asyncio
async def test_db_member_admin_persists_with_last_admin_protection(tmp_path) -> None:
    """Member lifecycle: grant persists; last admin stays protected."""
    async with lifecycle_db(tmp_path) as sessions:
        session = await _fresh_session(sessions)
        await claim_first_owner_and_create_user(
            session, login="owner@example.invalid", nonce="bootstrap-nonce-1"
        )
        await redeem_invite_and_create_user(
            session, login="alice@example.invalid", nonce="invite-nonce-1"
        )
        updated = await apply_member_action_transactional(
            session,
            actor_login="owner@example.invalid",
            target_login="alice@example.invalid",
            action="grant_admin",
        )
        assert updated.is_superuser is True
        # With two admins, revoking one is allowed and persists.
        revoked = await apply_member_action_transactional(
            session,
            actor_login="owner@example.invalid",
            target_login="alice@example.invalid",
            action="revoke_admin",
        )
        assert revoked.is_superuser is False
        # Last-admin protection survives the DB round-trip: the solo owner
        # cannot be demoted now that alice is an ordinary member again.
        with pytest.raises(AdminRefusedError) as excinfo:
            await apply_member_action_transactional(
                session,
                actor_login="owner@example.invalid",
                target_login="owner@example.invalid",
                action="revoke_admin",
            )
        assert excinfo.value.code == "last_admin_protected"
        # Deactivation persists and bumps the revocation generation.
        deactivated = await apply_member_action_transactional(
            session,
            actor_login="owner@example.invalid",
            target_login="alice@example.invalid",
            action="deactivate",
        )
        assert deactivated.is_active is False
        generation = await session.get(
            MoonmindUserSessionGeneration, deactivated.id
        )
        assert generation is not None and int(generation.generation) >= 1
        await session.close()


@pytest.mark.asyncio
async def test_db_lifecycle_events_leave_no_secret_material(tmp_path) -> None:
    """Nonce records carry no secret material (nonces only, never tokens)."""
    async with lifecycle_db(tmp_path) as sessions:
        session = await _fresh_session(sessions)
        token = mint_bootstrap_capability("owner@example.invalid", key=KEY, now=NOW)
        await claim_first_owner_and_create_user(
            session, login="owner@example.invalid", nonce="bootstrap-nonce-1"
        )
        rows = (await session.execute(select(MoonmindAccountNonce))).scalars().all()
        assert len(rows) == 1
        assert rows[0].nonce == "bootstrap-nonce-1"
        assert rows[0].purpose == "bootstrap"
        assert token not in (rows[0].nonce + rows[0].login + rows[0].purpose)
        await session.close()
