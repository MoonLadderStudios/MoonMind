"""Shared Omnigent control-plane integration fixtures.

Source: MoonLadderStudios/MoonMind#3703 / #3709.

Provisions an isolated PostgreSQL cluster (no manually managed test service) and
binds an :class:`OmnigentControlPlaneStore` over the seven control-plane tables.
Both the decisive-invariant coverage in ``test_control_plane_postgres.py`` and the
fault-injection replay binding in ``test_control_plane_faultlab.py`` consume these
fixtures so the ephemeral-cluster provisioning lives in exactly one place.
"""

from __future__ import annotations

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
    ManagedAgentProviderProfile,
    OmnigentChatBindingAlias,
    OmnigentCleanupAuthority,
    OmnigentCommand,
    OmnigentCredentialRuntimeRecord,
    OmnigentExecutionPlanRecord,
    OmnigentHostBindingRecordV2,
    OmnigentHostLeaseRecordV2,
    OmnigentObservation,
    OmnigentReconciliationDecision,
    OmnigentRuntimeBindingRecord,
    OmnigentSession,
    OmnigentTurnAttempt,
)
from moonmind.omnigent.control_plane import OmnigentControlPlaneStore

_CONTROL_PLANE_TABLES = [
    # MoonLadderStudios/MoonMind#3701: the canonical session now has foreign
    # keys to the immutable admission authority, so PostgreSQL integration
    # fixtures must exercise those real handoff tables too.
    ManagedAgentProviderProfile.__table__,
    OmnigentExecutionPlanRecord.__table__,
    OmnigentRuntimeBindingRecord.__table__,
    OmnigentHostBindingRecordV2.__table__,
    OmnigentHostLeaseRecordV2.__table__,
    OmnigentCredentialRuntimeRecord.__table__,
    OmnigentSession.__table__,
    OmnigentTurnAttempt.__table__,
    OmnigentObservation.__table__,
    OmnigentCommand.__table__,
    OmnigentReconciliationDecision.__table__,
    OmnigentChatBindingAlias.__table__,
    OmnigentCleanupAuthority.__table__,
]


def _create_tables(sync_conn) -> None:
    for table in _CONTROL_PLANE_TABLES:
        table.create(sync_conn, checkfirst=True)


def _drop_tables(sync_conn) -> None:
    for table in reversed(_CONTROL_PLANE_TABLES):
        table.drop(sync_conn, checkfirst=True)


@pytest.fixture
def control_plane_postgres_url():
    """Provide isolated PostgreSQL without a manually managed test service.

    Honors ``MOONMIND_TEST_POSTGRES_URL`` when set; otherwise runs a throwaway
    local cluster on a random port via ``initdb``/``pg_ctl``. Fails closed with an
    actionable message when PostgreSQL binaries are unavailable so a missing
    dependency reads as an environment blocker, not a silent skip.
    """

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
    """Bind an :class:`OmnigentControlPlaneStore` over an ephemeral PostgreSQL."""

    engine = create_async_engine(control_plane_postgres_url)
    async with engine.begin() as conn:
        await conn.run_sync(_create_tables)
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        yield OmnigentControlPlaneStore(maker)
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(_drop_tables)
        await engine.dispose()
