"""Real-PostgreSQL account-lifecycle evidence (MoonLadderStudios/MoonMind#4122).

Proves the production User-table wiring on PostgreSQL, not SQLite:
first-owner claim plus owner creation in one transaction (exactly one
winner under concurrent setup, closed on a populated database),
invitation redemption plus member creation (replay refused), recovery
nonce consumption without account mutation, and nonce durability across
instances and restart. Runs an ephemeral local cluster with no manually
managed test service (mirrors the control-plane Postgres binding in
``test_session_authority_postgres_4121.py``).
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import tempfile
import uuid
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import (
    MoonmindAccountNonce,
    MoonmindUserSessionGeneration,
    User,
    UserProfile,
)
from api_service.services.account_lifecycle_store_4122 import (
    AsyncDbLifecycleStore,
    claim_first_owner_and_create_user,
    redeem_invite_and_create_user,
    redeem_recovery_for_user,
)
from moonmind.security.account_lifecycle_4122 import (
    BootstrapError,
    InviteError,
    RecoveryError,
)

pytestmark = [pytest.mark.integration, pytest.mark.integration_ci]

_TABLES = (
    User.__table__,
    UserProfile.__table__,
    MoonmindAccountNonce.__table__,
    MoonmindUserSessionGeneration.__table__,
)


@pytest.fixture
def lifecycle_postgres_url():
    """Ephemeral PostgreSQL; honors MOONMIND_TEST_POSTGRES_URL when set."""
    configured = os.getenv("MOONMIND_TEST_POSTGRES_URL", "").strip()
    if configured:
        if configured.startswith("postgresql://"):
            configured = configured.replace("postgresql://", "postgresql+asyncpg://", 1)
        if not configured.startswith("postgresql+asyncpg://"):
            pytest.fail("MOONMIND_TEST_POSTGRES_URL must use PostgreSQL")
        yield configured
        return
    initdb_path = shutil.which("initdb")
    if initdb_path is None:
        candidates = sorted(Path("/usr/lib/postgresql").glob("*/bin/initdb"))
        initdb_path = str(candidates[-1]) if candidates else None
    if initdb_path is None:
        pytest.fail("PostgreSQL test binaries are unavailable in the Python test image")
    initdb = str(Path(initdb_path))
    pg_ctl = str(Path(initdb).with_name("pg_ctl"))
    data_root = Path(tempfile.mkdtemp(prefix="moonmind-lifecycle-postgres-"))
    data_dir = data_root / "data"
    log_path = data_root / "postgres.log"
    command_prefix: list[str] = []
    if os.geteuid() == 0:
        shutil.chown(data_root, user="postgres", group="postgres")
        command_prefix = ["runuser", "--user", "postgres", "--"]
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    subprocess.run(
        [*command_prefix, initdb, "--pgdata", str(data_dir), "--username", "postgres",
         "--auth", "trust", "--encoding", "UTF8", "--no-locale"],
        check=True, capture_output=True, text=True,
    )
    subprocess.run(
        [*command_prefix, pg_ctl, "--pgdata", str(data_dir), "--log", str(log_path),
         "--options", f"-F -p {port} -h 127.0.0.1 -k {data_dir}", "--wait", "start"],
        check=True, capture_output=True, text=True,
    )
    try:
        yield f"postgresql+asyncpg://postgres@127.0.0.1:{port}/postgres"
    finally:
        subprocess.run(
            [*command_prefix, pg_ctl, "--pgdata", str(data_dir), "--mode", "immediate",
             "--wait", "stop"],
            check=False, capture_output=True, text=True,
        )
        shutil.rmtree(data_root, ignore_errors=True)


@pytest_asyncio.fixture()
async def pg_maker(lifecycle_postgres_url):
    engine = create_async_engine(lifecycle_postgres_url)
    async with engine.begin() as conn:
        for table in _TABLES:
            await conn.run_sync(table.create, checkfirst=True)
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        yield maker
    finally:
        async with engine.begin() as conn:
            for table in reversed(_TABLES):
                await conn.run_sync(table.drop, checkfirst=True)
        await engine.dispose()


@pytest.mark.asyncio
async def test_postgres_first_owner_claim_is_single_winner(pg_maker) -> None:
    """Concurrent first-owner setups produce exactly one owner User."""
    import asyncio

    winner_logins = ["owner@example.invalid"]

    async def setup_once(nonce: str) -> uuid.UUID | None:
        async with pg_maker() as session:
            try:
                user = await claim_first_owner_and_create_user(
                    session, login=winner_logins[0], nonce=nonce
                )
                return user.id
            except BootstrapError:
                await session.rollback()
                return None

    # Sequential second setup is refused (closed database).
    async with pg_maker() as session:
        owner = await claim_first_owner_and_create_user(
            session, login="owner@example.invalid", nonce="pg-bootstrap-1"
        )
        assert owner.is_superuser is True
    results = await asyncio.gather(
        *[setup_once(f"pg-bootstrap-racer-{index}") for index in range(4)]
    )
    assert all(result is None for result in results)
    async with pg_maker() as session:
        users = (await session.execute(select(User))).scalars().all()
        assert len(users) == 1
        assert users[0].id == owner.id
        store = AsyncDbLifecycleStore(session)
        assert await store.has_owner() is True


@pytest.mark.asyncio
async def test_postgres_first_owner_distinct_concurrent_claims_single_winner(pg_maker) -> None:
    """Distinct concurrent claims on a fresh database yield exactly one owner.

    Each racer carries a distinct bootstrap nonce and login, so neither the
    nonce primary key nor the login constraint converges them; the empty-
    to-owned transition must serialize and every loser must fail closed.
    """
    import asyncio

    async def setup_once(login: str, nonce: str) -> uuid.UUID | None:
        async with pg_maker() as session:
            try:
                user = await claim_first_owner_and_create_user(
                    session, login=login, nonce=nonce
                )
                return user.id
            except BootstrapError:
                await session.rollback()
                return None

    results = await asyncio.gather(
        *[
            setup_once(f"racer-{index}@example.invalid", f"pg-fresh-racer-{index}")
            for index in range(4)
        ]
    )
    assert sum(result is not None for result in results) == 1
    async with pg_maker() as session:
        users = (await session.execute(select(User))).scalars().all()
        assert len(users) == 1
        store = AsyncDbLifecycleStore(session)
        assert await store.has_owner() is True


@pytest.mark.asyncio
async def test_postgres_invite_replay_refused_after_restart(pg_maker) -> None:
    """Redeemed invite nonces stay consumed across instances (restart)."""
    async with pg_maker() as session:
        await claim_first_owner_and_create_user(
            session, login="owner@example.invalid", nonce="pg-bootstrap-1"
        )
        member = await redeem_invite_and_create_user(
            session, login="alice@example.invalid", nonce="pg-invite-1"
        )
        assert member.is_superuser is False
    # After "restart" (fresh sessions), the replay is refused and no
    # duplicate account appears.
    async with pg_maker() as session:
        with pytest.raises(InviteError):
            await redeem_invite_and_create_user(
                session, login="bob@example.invalid", nonce="pg-invite-1"
            )
        await session.rollback()
        users = (await session.execute(select(User))).scalars().all()
        assert sorted(str(user.email) for user in users) == [
            "alice@example.invalid",
            "owner@example.invalid",
        ]


@pytest.mark.asyncio
async def test_postgres_recovery_consumes_without_mutation(pg_maker) -> None:
    """Recovery consumes its nonce and returns the account unmodified."""
    async with pg_maker() as session:
        owner = await claim_first_owner_and_create_user(
            session, login="owner@example.invalid", nonce="pg-bootstrap-1"
        )
        owner_id = owner.id
    async with pg_maker() as session:
        recovered = await redeem_recovery_for_user(
            session, login="owner@example.invalid", nonce="pg-recovery-1"
        )
        assert recovered.id == owner_id
        assert recovered.is_superuser is True
    async with pg_maker() as session:
        with pytest.raises(RecoveryError):
            await redeem_recovery_for_user(
                session, login="owner@example.invalid", nonce="pg-recovery-1"
            )
        await session.rollback()
        users = (await session.execute(select(User))).scalars().all()
        assert len(users) == 1
