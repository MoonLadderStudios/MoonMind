"""PostgreSQL coverage for the decisive Omnigent control-plane invariants.

Source: MoonLadderStudios/MoonMind#3703 ([Omnigent control plane 2/11]).

The uniqueness and concurrency cases that decide whether a second canonical
chat authority can be created must be proven on PostgreSQL, not only SQLite,
because the "fail closed rather than pick the newest row" guarantee depends on
the real unique-index concurrency behaviour. Runs an ephemeral local cluster
with no manually managed test service (mirrors the checkpoint-branch migration
integration test).
"""

from __future__ import annotations

import asyncio
import os
import shutil
import socket
import subprocess
import tempfile
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import (
    OmnigentChatBindingAlias,
    OmnigentCommand,
    OmnigentObservation,
    OmnigentReconciliationDecision,
    OmnigentSession,
    OmnigentTurnAttempt,
)
from moonmind.omnigent.control_plane import (
    ConflictingSessionAuthorityError,
    OmnigentControlPlaneStore,
)

pytestmark = [pytest.mark.integration, pytest.mark.integration_ci]

_CONTROL_PLANE_TABLES = [
    OmnigentSession.__table__,
    OmnigentTurnAttempt.__table__,
    OmnigentObservation.__table__,
    OmnigentCommand.__table__,
    OmnigentReconciliationDecision.__table__,
    OmnigentChatBindingAlias.__table__,
]


@pytest.fixture
def control_plane_postgres_url():
    """Provide isolated PostgreSQL without a manually managed test service."""

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


@pytest_asyncio.fixture()
async def pg_store(control_plane_postgres_url):
    engine = create_async_engine(control_plane_postgres_url)
    async with engine.begin() as conn:
        await conn.run_sync(
            lambda sync_conn: _create_tables(sync_conn)
        )
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        yield OmnigentControlPlaneStore(maker)
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(lambda sync_conn: _drop_tables(sync_conn))
        await engine.dispose()


def _create_tables(sync_conn) -> None:
    for table in _CONTROL_PLANE_TABLES:
        table.create(sync_conn, checkfirst=True)


def _drop_tables(sync_conn) -> None:
    for table in reversed(_CONTROL_PLANE_TABLES):
        table.drop(sync_conn, checkfirst=True)


@pytest.mark.asyncio
async def test_postgres_scope_uniqueness_fails_closed(pg_store) -> None:
    async with pg_store.transaction() as repos:
        await repos.sessions.create(
            session_id="s1",
            moonmind_workflow_id="wf-1",
            provider="codex",
            provider_session_ref="psess-1",
        )
    with pytest.raises(ConflictingSessionAuthorityError):
        async with pg_store.transaction() as repos:
            await repos.sessions.create(
                session_id="s2",
                moonmind_workflow_id="wf-1",
                provider="codex",
                provider_session_ref="psess-1",
            )


@pytest.mark.asyncio
async def test_postgres_chat_binding_uniqueness_fails_closed(pg_store) -> None:
    async with pg_store.transaction() as repos:
        await repos.sessions.create(
            session_id="s1",
            moonmind_workflow_id="wf-1",
            provider="codex",
            provider_session_ref="psess-1",
            chat_binding_id="cb-shared",
        )
    with pytest.raises(ConflictingSessionAuthorityError):
        async with pg_store.transaction() as repos:
            await repos.sessions.create(
                session_id="s2",
                moonmind_workflow_id="wf-2",
                provider="codex",
                provider_session_ref="psess-2",
                chat_binding_id="cb-shared",
            )


@pytest.mark.asyncio
async def test_postgres_concurrent_scope_creation_admits_one_authority(pg_store) -> None:
    async def _create(session_id: str):
        async with pg_store.transaction() as repos:
            return await repos.sessions.create(
                session_id=session_id,
                moonmind_workflow_id="wf-1",
                provider="codex",
                provider_session_ref="psess-1",
            )

    results = await asyncio.gather(
        _create("s-a"), _create("s-b"), return_exceptions=True
    )
    successes = [r for r in results if not isinstance(r, Exception)]
    conflicts = [r for r in results if isinstance(r, ConflictingSessionAuthorityError)]
    # Exactly one canonical authority is admitted; the loser fails closed rather
    # than superseding the winner.
    assert len(successes) == 1
    assert len(conflicts) == 1

    async with pg_store.transaction() as repos:
        winner = successes[0]
        stored = await repos.sessions.get_by_scope("wf-1", "psess-1")
    assert stored is not None
    assert stored.session_id == winner.session_id


@pytest.mark.asyncio
async def test_postgres_null_provider_session_scopes_are_distinct(pg_store) -> None:
    # Two unattached canonical rows in one workflow do not collide (NULLs are
    # distinct under the unique index), so pre-attachment rows stay independent.
    async with pg_store.transaction() as repos:
        await repos.sessions.create(
            session_id="s1", moonmind_workflow_id="wf-1", provider="codex"
        )
        await repos.sessions.create(
            session_id="s2", moonmind_workflow_id="wf-1", provider="codex"
        )
        listed = await repos.turn_attempts.list_for_session("s1")
    assert listed == []
