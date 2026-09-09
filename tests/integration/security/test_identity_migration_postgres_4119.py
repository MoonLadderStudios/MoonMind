"""Real-PostgreSQL K3 identity migration evidence (MoonLadderStudios/MoonMind#4119).

Proves the concurrency and preservation seams on PostgreSQL, not SQLite:
unique-index race convergence for concurrent first logins, UUID/foreign-key
preservation across fresh / Keycloak-associated / local-default / built-in /
mixed datasets, and idempotent reruns. Runs an ephemeral local cluster with
no manually managed test service (mirrors the control-plane Postgres binding).
"""

from __future__ import annotations

import asyncio
import os
import shutil
import socket
import subprocess
import tempfile
import uuid
from pathlib import Path
from uuid import UUID

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import (
    IdentityMigrationRun,
    RecurringWorkflowDefinition,
    User,
    UserExternalIdentity,
    UserProfile,
)
from api_service.services.identity_migration import apply_migration, preflight
from api_service.services.identity_service import (
    get_or_create_user_for_identity,
    ownership_snapshot,
    resolve_user_id_for_identity,
)

pytestmark = [pytest.mark.integration, pytest.mark.integration_ci]

ISSUER = "https://idp.example.invalid/realms/moonmind"

_TABLES = (
    User.__table__,
    UserExternalIdentity.__table__,
    UserProfile.__table__,
    IdentityMigrationRun.__table__,
    RecurringWorkflowDefinition.__table__,
)


@pytest.fixture
def identity_postgres_url():
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
    data_root = Path(tempfile.mkdtemp(prefix="moonmind-identity-postgres-"))
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
async def pg_maker(identity_postgres_url):
    engine = create_async_engine(identity_postgres_url)
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
        "email": f"pg-{uuid.uuid4().hex}@example.invalid",
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


@pytest.mark.asyncio
async def test_postgres_concurrent_first_login_yields_one_mapping(pg_maker) -> None:
    """The decisive race: N concurrent first logins collapse to one mapping."""
    barrier = asyncio.Barrier(8)

    async def first_login(n: int) -> UUID:
        for attempt in range(10):
            async with pg_maker() as session:
                try:
                    if attempt == 0:
                        # Collide all racers on the first attempt only;
                        # retries proceed independently so an early winner
                        # cannot strand the barrier and hang the test.
                        await barrier.wait()
                    user, _ = await get_or_create_user_for_identity(
                        session, ISSUER, "pg-race-sub", email=f"pg-race-{n}@example.invalid"
                    )
                    uid = user.id
                    await session.commit()
                    return uid
                except Exception:
                    await session.rollback()
                    await asyncio.sleep(0.01 * (attempt + 1))
        async with pg_maker() as session:
            winner = await resolve_user_id_for_identity(session, ISSUER, "pg-race-sub")
            assert winner is not None
            return winner

    winners = await asyncio.gather(*[first_login(n) for n in range(8)])
    assert len(set(winners)) == 1
    async with pg_maker() as session:
        rows = (await session.execute(select(UserExternalIdentity))).scalars().all()
        assert len(rows) == 1
        assert rows[0].user_id == winners[0]
        profiles = (
            (await session.execute(
                select(UserProfile).where(UserProfile.user_id == winners[0])
            )).scalars().all()
        )
        assert len(profiles) == 1
        winner = await session.get(User, winners[0])
        assert winner is not None and winner.is_superuser is False


@pytest.mark.asyncio
async def test_postgres_mixed_dataset_preserves_uuids_and_owners(pg_maker) -> None:
    """Fresh/Keycloak/local-default/built-in rows keep UUIDs and FK owners."""
    async with pg_maker() as session:
        fresh = await _make_user(session, email="pg-fresh@example.invalid")
        legacy = await _make_user(
            session, email="pg-legacy@example.invalid",
            oidc_provider="keycloak", oidc_subject="pg-legacy-sub",
        )
        default = await _make_user(
            session, id=UUID("00000000-0000-0000-0000-000000000000"),
            email="pg-default@example.invalid",
        )
        admin = await _make_user(
            session, email="pg-admin@example.invalid", is_superuser=True
        )
        for user in (fresh, legacy, default, admin):
            session.add(UserProfile(user_id=user.id))
        session.add(RecurringWorkflowDefinition(
            name="pg-schedule", cron="0 * * * *", timezone="UTC",
            target={}, policy={}, owner_user_id=legacy.id,
        ))
        session.add(RecurringWorkflowDefinition(
            name="pg-admin-schedule", cron="0 * * * *", timezone="UTC",
            target={}, policy={}, owner_user_id=admin.id,
        ))
        await session.commit()
        before = await ownership_snapshot(session)
        enrollment = {
            str(legacy.id): {"issuer": ISSUER, "subject": "pg-legacy-sub",
                             "source_provider": "keycloak"},
            str(fresh.id): {"issuer": ISSUER, "subject": "pg-fresh-sub",
                            "source_provider": "keycloak"},
        }
        checked = await preflight(
            session, provider_to_issuer={"keycloak": ISSUER},
            enrollment_evidence=enrollment,
        )
        outcome = await apply_migration(
            session, checked, operator_authorized=True,
            provider_to_issuer={"keycloak": ISSUER}, enrollment_evidence=enrollment,
        )
        assert outcome.blocked == []
        assert sorted(outcome.migrated) == sorted([str(legacy.id), str(fresh.id)])

        rerun_check = await preflight(
            session, provider_to_issuer={"keycloak": ISSUER},
            enrollment_evidence=enrollment,
        )
        rerun = await apply_migration(
            session, rerun_check, operator_authorized=True,
            provider_to_issuer={"keycloak": ISSUER}, enrollment_evidence=enrollment,
        )
        assert rerun.migrated == [] and len(rerun.skipped) == 2

        for uid in (fresh.id, legacy.id, default.id, admin.id):
            assert await session.get(User, uid) is not None
        owners = {
            s.name: s.owner_user_id
            for s in (await session.execute(select(RecurringWorkflowDefinition))).scalars().all()
        }
        assert owners == {"pg-schedule": legacy.id, "pg-admin-schedule": admin.id}
        assert (await session.get(User, admin.id)).is_superuser is True
        assert (await session.get(User, legacy.id)).oidc_provider == "keycloak"
        after = await ownership_snapshot(session)
        assert after["user_ids"] == before["user_ids"]
        assert after["fk_owner_counts"] == before["fk_owner_counts"]
