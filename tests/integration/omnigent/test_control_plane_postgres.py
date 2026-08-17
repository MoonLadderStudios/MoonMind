"""PostgreSQL uniqueness and concurrency coverage for the control plane.

Issue MoonLadderStudios/MoonMind#3703 (PostgreSQL tests). The decisive
uniqueness and concurrency cases for the canonical session authority run on
PostgreSQL, not only SQLite: the ``356_omnigent_control_plane`` migration is
applied on PostgreSQL, and concurrent inserts of the same authority scope must
resolve to exactly one canonical row.
"""

from __future__ import annotations

import asyncio
import importlib.util
import os
import shutil
import socket
import subprocess
import tempfile
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import OmnigentSession

pytestmark = [pytest.mark.integration, pytest.mark.integration_ci]


@pytest.fixture
def control_plane_postgres_url():
    """Provide isolated PostgreSQL without a manually managed test service."""

    configured = os.getenv("MOONMIND_TEST_POSTGRES_URL", "").strip()
    if configured:
        if configured.startswith("postgresql://"):
            configured = configured.replace(
                "postgresql://", "postgresql+asyncpg://", 1
            )
        if not configured.startswith("postgresql+asyncpg://"):
            pytest.fail("MOONMIND_TEST_POSTGRES_URL must use PostgreSQL")
        yield configured
        return

    initdb_path = shutil.which("initdb")
    if initdb_path is None:
        candidates = sorted(Path("/usr/lib/postgresql").glob("*/bin/initdb"))
        initdb_path = str(candidates[-1]) if candidates else None
    if initdb_path is None:
        pytest.fail(
            "PostgreSQL test binaries are unavailable in the Python test image"
        )
    initdb = str(Path(initdb_path))
    pg_ctl = str(Path(initdb).with_name("pg_ctl"))
    data_root = Path(tempfile.mkdtemp(prefix="moonmind-control-plane-postgres-"))
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
        [
            *command_prefix,
            initdb,
            "--pgdata",
            str(data_dir),
            "--username",
            "postgres",
            "--auth",
            "trust",
            "--encoding",
            "UTF8",
            "--no-locale",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        [
            *command_prefix,
            pg_ctl,
            "--pgdata",
            str(data_dir),
            "--log",
            str(log_path),
            "--options",
            f"-F -p {port} -h 127.0.0.1 -k {data_dir}",
            "--wait",
            "start",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    try:
        yield f"postgresql+asyncpg://postgres@127.0.0.1:{port}/postgres"
    finally:
        subprocess.run(
            [
                *command_prefix,
                pg_ctl,
                "--pgdata",
                str(data_dir),
                "--mode",
                "immediate",
                "--wait",
                "stop",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        shutil.rmtree(data_root, ignore_errors=True)


def _load_migration():
    migration_path = (
        Path(__file__).resolve().parents[3]
        / "api_service"
        / "migrations"
        / "versions"
        / "356_omnigent_control_plane.py"
    )
    spec = importlib.util.spec_from_file_location("m356_pg", migration_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _apply_migration(sync_conn) -> None:
    migration = _load_migration()
    migration.__dict__["op"] = Operations(MigrationContext.configure(sync_conn))
    migration.upgrade()


def _session(session_id: str, scope: str, chat_binding: str | None = None) -> OmnigentSession:
    return OmnigentSession(
        session_id=session_id,
        moonmind_workflow_id="wf-pg",
        provider="omnigent",
        compatibility_profile="omnigent.server.v1",
        authority_scope=scope,
        chat_binding_id=chat_binding,
    )


@pytest.mark.asyncio
async def test_migration_and_uniqueness_on_postgres(control_plane_postgres_url) -> None:
    engine = create_async_engine(control_plane_postgres_url)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(_apply_migration)
            inspector = await conn.run_sync(lambda c: sa.inspect(c).get_table_names())
        assert {
            "omnigent_sessions",
            "omnigent_turn_attempts",
            "omnigent_observations",
            "omnigent_commands",
            "omnigent_reconciliation_decisions",
            "omnigent_chat_binding_aliases",
        }.issubset(set(inspector))

        maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with maker() as db:
            db.add(_session("s1", "scope-a", chat_binding="chatb_a"))
            await db.commit()

        # Duplicate authority scope fails closed on PostgreSQL.
        async with maker() as db:
            db.add(_session("s2", "scope-a"))
            with pytest.raises(IntegrityError):
                await db.commit()

        # Duplicate chat authority fails closed on PostgreSQL.
        async with maker() as db:
            db.add(_session("s3", "scope-b", chat_binding="chatb_a"))
            with pytest.raises(IntegrityError):
                await db.commit()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_canonical_authority_resolves_to_one(
    control_plane_postgres_url,
) -> None:
    engine = create_async_engine(control_plane_postgres_url)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(_apply_migration)
        maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        async def _insert() -> bool:
            async with maker() as db:
                db.add(_session(f"s_{uuid4().hex}", "contended-scope"))
                try:
                    await db.commit()
                    return True
                except IntegrityError:
                    await db.rollback()
                    return False

        results = await asyncio.gather(*[_insert() for _ in range(5)])
        # Exactly one concurrent writer wins the canonical authority.
        assert sum(1 for ok in results if ok) == 1

        async with maker() as db:
            rows = (
                await db.execute(
                    sa.select(OmnigentSession).where(
                        OmnigentSession.authority_scope == "contended-scope"
                    )
                )
            ).scalars().all()
        assert len(rows) == 1
    finally:
        await engine.dispose()
