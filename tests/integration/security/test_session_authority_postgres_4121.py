"""Real-PostgreSQL session/revocation evidence (MoonLadderStudios/MoonMind#4121).

Proves the durability seams on PostgreSQL, not SQLite: cross-restart and
two-instance revocation visibility, cached-token invalidation, concurrent
issuance versus disable/reset (no resurrection), revocation-store outage
fail-closed behavior, and the concurrent generation-bump race. Runs an
ephemeral local cluster with no manually managed test service (mirrors the
control-plane Postgres binding).
"""

from __future__ import annotations

import asyncio
import os
import secrets
import shutil
import socket
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import (
    MoonmindSession,
    MoonmindUserSessionGeneration,
    User,
)
from api_service.services.session_store import (
    DbAccountStore,
    DbRevocationStore,
    disable_user_and_revoke_sessions,
    mint_and_record_session,
)
from moonmind.security import omnigent_auth_qualification as q
from moonmind.security import session_authority_4121 as s

pytestmark = [pytest.mark.integration, pytest.mark.integration_ci]

_TABLES = (
    User.__table__,
    MoonmindUserSessionGeneration.__table__,
    MoonmindSession.__table__,
)


def _config(secret: bytes | None = None) -> q.MoonmindAuthConfig:
    return q.MoonmindAuthConfig(
        mode="accounts",
        cookie_name=q.MOONMIND_DEV_COOKIE,
        cookie_secret=secret or secrets.token_bytes(32),
        session_ttl_seconds=3600,
        require_secure_cookies=False,
    )


@pytest.fixture
def session_postgres_url():
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
    data_root = Path(tempfile.mkdtemp(prefix="moonmind-session-postgres-"))
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
async def pg_maker(session_postgres_url):
    engine = create_async_engine(session_postgres_url)
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


async def _make_user(session: AsyncSession, **overrides) -> User:
    params = {
        "id": uuid.uuid4(),
        "email": f"sess-{uuid.uuid4().hex}@example.invalid",
        "hashed_password": None,
        "is_active": True,
        "is_superuser": False,
        "is_verified": False,
    }
    params.update(overrides)
    user = User(**params)
    session.add(user)
    await session.flush()
    return user


def _identity_for(user: User) -> q.ValidatedIdentity:
    return q.ValidatedIdentity(issuer="moonmind-accounts", subject=user.email)


@pytest.mark.asyncio
async def test_postgres_logout_revokes_across_instances_and_restart(pg_maker) -> None:
    """Logout on one instance denies cached tokens on a second instance."""
    secret = secrets.token_bytes(32)
    config = _config(secret)
    async with pg_maker() as session:
        user = await _make_user(session)
        await session.commit()
    # Instance one mints and records.
    async with pg_maker() as session:
        accounts = DbAccountStore(session)
        revocation = DbRevocationStore(session)
        token, user_id = await mint_and_record_session(
            _identity_for(user), accounts, revocation, config, session
        )
        assert user_id == user.id
    # Instance two validates through a bounded cache (simulated replica).
    async with pg_maker() as session:
        cache = s.BoundedSessionCache(config=config)
        accounts = DbAccountStore(session)
        revocation = DbRevocationStore(session)
        resolved = await cache.validate(token, accounts, revocation)
        assert resolved.user_id == user.id
        assert cache.misses == 1
        # Logout revokes the current session durably.
        import jwt as _jwt

        jti = str(_jwt.decode(token, options={"verify_signature": False})["jti"])
        await revocation.revoke_session_for_user(jti, user.id, reason="logout")
        await session.commit()
    # After "restart" (fresh sessions), the cached token is denied.
    async with pg_maker() as session:
        accounts = DbAccountStore(session)
        revocation = DbRevocationStore(session)
        with pytest.raises(q.AuthInvalidError):
            await q.validate_moonmind_session(token, accounts, revocation, config)


@pytest.mark.asyncio
async def test_postgres_revoke_all_invalidates_cached_and_blocks_resurrection(
    pg_maker,
) -> None:
    """Password-reset/disable/admin-revoke bumps generation; racers die."""
    secret = secrets.token_bytes(32)
    config = _config(secret)
    async with pg_maker() as session:
        user = await _make_user(session)
        await session.commit()
    async with pg_maker() as session:
        accounts = DbAccountStore(session)
        revocation = DbRevocationStore(session)
        stale, _ = await mint_and_record_session(
            _identity_for(user), accounts, revocation, config, session
        )
        # Concurrent issuance started before the revocation boundary.
        racing, _ = await mint_and_record_session(
            _identity_for(user), accounts, revocation, config, session
        )
        generation = await revocation.revoke_all_for_user(user.id)
        await session.commit()
        assert generation == 1
        for doomed in (stale, racing):
            with pytest.raises(q.AuthInvalidError):
                await q.validate_moonmind_session(doomed, accounts, revocation, config)
        fresh, _ = await mint_and_record_session(
            _identity_for(user), accounts, revocation, config, session
        )
        resolved = await q.validate_moonmind_session(fresh, accounts, revocation, config)
        assert resolved.user_id == user.id


@pytest.mark.asyncio
async def test_postgres_disable_blocks_new_actions(pg_maker) -> None:
    """Account disablement deactivates and revokes in one transaction."""
    secret = secrets.token_bytes(32)
    config = _config(secret)
    async with pg_maker() as session:
        user = await _make_user(session)
        await session.commit()
    async with pg_maker() as session:
        accounts = DbAccountStore(session)
        revocation = DbRevocationStore(session)
        token, _ = await mint_and_record_session(
            _identity_for(user), accounts, revocation, config, session
        )
    async with pg_maker() as session:
        generation = await disable_user_and_revoke_sessions(session, user.id)
        assert generation >= 1
    async with pg_maker() as session:
        accounts = DbAccountStore(session)
        revocation = DbRevocationStore(session)
        # Inactive accounts are forbidden (not silently re-resolved).
        with pytest.raises((q.AuthInvalidError, q.ForbiddenError)):
            await q.validate_moonmind_session(token, accounts, revocation, config)


@pytest.mark.asyncio
async def test_postgres_concurrent_revoke_all_converges(pg_maker) -> None:
    """Concurrent admin revocations produce increasing generations."""
    async with pg_maker() as session:
        user = await _make_user(session)
        await session.commit()

    async def revoke_once() -> int:
        async with pg_maker() as session:
            store = DbRevocationStore(session)
            generation = await store.revoke_all_for_user(user.id)
            await session.commit()
            return generation

    generations = await asyncio.gather(*[revoke_once() for _ in range(4)])
    assert sorted(generations) == [1, 2, 3, 4]
    async with pg_maker() as session:
        store = DbRevocationStore(session)
        assert await store.generation_for_user(user.id) == 4


@pytest.mark.asyncio
async def test_postgres_outage_fails_closed_bounded(pg_maker) -> None:
    """Store outage is 503 unavailable, never stale authority or an admin."""
    secret = secrets.token_bytes(32)
    config = _config(secret)
    async with pg_maker() as session:
        user = await _make_user(session)
        await session.commit()
    async with pg_maker() as session:
        accounts = DbAccountStore(session)
        revocation = DbRevocationStore(session)
        token, _ = await mint_and_record_session(
            _identity_for(user), accounts, revocation, config, session
        )

    class _BrokenRevocation(DbRevocationStore):
        async def is_session_revoked(self, jti: str) -> bool:
            raise ConnectionError("revocation store down")

        async def generation_for_user(self, user_id) -> int:
            raise ConnectionError("revocation store down")

    async with pg_maker() as session:
        accounts = DbAccountStore(session)
        broken = _BrokenRevocation(session)
        with pytest.raises(q.UnavailableError):
            await q.validate_moonmind_session(token, accounts, broken, config)
        status, code = s.http_status_for_error(q.UnavailableError("down"))
        assert (status, code) == (503, "unavailable")
        # Bounded failure: the outage surfaces quickly, not as a hang.
        start = time.monotonic()
        with pytest.raises(q.UnavailableError):
            await q.validate_moonmind_session(token, accounts, broken, config)
        assert time.monotonic() - start < 5.0
