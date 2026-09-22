"""Real-PostgreSQL projection race/failure evidence (#3946 REQ-04).

Proves concurrent absent-row creation and failure recovery on the supported
PostgreSQL backend through the shared mutator owner
(``api_service.core.sync.mutate_execution_projection``): one row per workflow,
row locking orders concurrent writers, per-item savepoints isolate batch
failures, and uncertain work reconciles by execution identity without
restarting the workflow. SQLite suites cannot prove PG locking or
insert-conflict behavior, so this module runs an ephemeral local cluster with
no manually managed test service (mirrors
``test_single_user_conversion_postgres_4346.py``).
"""

from __future__ import annotations

import asyncio
import os
import shutil
import socket
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.core import sync as sync_module
from api_service.core.sync import (
    _locked_get,
    mutate_execution_projection,
)
from api_service.db.models import (
    Base,
    MoonMindWorkflowState,
    TemporalExecutionCanonicalRecord,
    TemporalExecutionOwnerType,
    TemporalExecutionProjectionSyncState,
    TemporalExecutionRecord,
    TemporalWorkflowType,
)

pytestmark = [pytest.mark.integration, pytest.mark.integration_ci]


@pytest.fixture
def projection_postgres_url():
    """Ephemeral PostgreSQL; honors MOONMIND_TEST_POSTGRES_URL when set."""
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
    data_root = Path(tempfile.mkdtemp(prefix="moonmind-proj3946-postgres-"))
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
async def pg_maker(projection_postgres_url):
    engine = create_async_engine(projection_postgres_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        yield maker
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await engine.dispose()


def _payload(workflow_id: str, updated_at: datetime) -> dict:
    return {
        "workflow_id": workflow_id,
        "run_id": "run-1",
        "namespace": "moonmind",
        "workflow_type": TemporalWorkflowType.USER_WORKFLOW,
        "owner_id": "owner-1",
        "owner_type": TemporalExecutionOwnerType.USER,
        "state": MoonMindWorkflowState.EXECUTING,
        "close_status": None,
        "entry": "run",
        "search_attributes": {},
        "memo": {"title": "Task"},
        "artifact_refs": [],
        "parameters": {"targetRuntime": "codex_cli"},
        "updated_at": updated_at,
    }


@pytest.mark.asyncio
async def test_postgres_concurrent_absent_row_creation_converges(pg_maker) -> None:
    """REQ-04: two independent transactions racing absent-row creation converge
    on one projection row — no duplicates, no second execution identity, no
    poisoned transaction for the loser."""
    stamped = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)

    async def _race_once() -> str:
        async with pg_maker() as session:
            refreshed = await mutate_execution_projection(
                session,
                workflow_id="mm:pg-race",
                payload=_payload("mm:pg-race", stamped),
                owner="temporal",
            )
            await session.commit()
            return refreshed.workflow_id

    winners = await asyncio.gather(_race_once(), _race_once())
    assert winners == ["mm:pg-race", "mm:pg-race"]

    async with pg_maker() as session:
        count = (
            await session.execute(
                select(func.count()).select_from(TemporalExecutionRecord).where(
                    TemporalExecutionRecord.workflow_id == "mm:pg-race"
                )
            )
        ).scalar_one()
        assert count == 1
        row = await session.get(TemporalExecutionRecord, "mm:pg-race")
        assert row.run_id == "run-1"
        assert row.owner_id == "owner-1"
        assert row.parameters == {"targetRuntime": "codex_cli"}
        assert row.sync_state is TemporalExecutionProjectionSyncState.FRESH
        assert row.projection_version >= 1


@pytest.mark.asyncio
async def test_postgres_row_lock_orders_concurrent_writers(pg_maker) -> None:
    """REQ-04: the locked read is a real PG row lock — a second transaction's
    locked read blocks until the lock holder commits, then observes the
    committed state (SQLite ignores FOR UPDATE, so only PG proves this)."""
    stamped = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
    async with pg_maker() as holder:
        await mutate_execution_projection(
            holder,
            workflow_id="mm:pg-lock",
            payload=_payload("mm:pg-lock", stamped),
            owner="temporal",
        )
        await holder.commit()
        # Re-acquire under lock and hold the transaction open.
        held = await _locked_get(holder, TemporalExecutionRecord, "mm:pg-lock")
        assert held is not None
        held.state = MoonMindWorkflowState.AWAITING_EXTERNAL

        async with pg_maker() as waiter:
            pending = asyncio.create_task(
                _locked_get(waiter, TemporalExecutionRecord, "mm:pg-lock")
            )
            await asyncio.sleep(2)
            # Still blocked on the holder's row lock.
            assert not pending.done()
            await holder.commit()
            observed = await asyncio.wait_for(pending, timeout=15)
            assert observed is not None
            assert observed.state is MoonMindWorkflowState.AWAITING_EXTERNAL


@pytest.mark.asyncio
async def test_postgres_batch_item_failure_does_not_poison_sibling(
    pg_maker, monkeypatch
) -> None:
    """REQ-04: on PostgreSQL, one item's fetch/database error rolls back only
    its own savepoint — the sibling repair still commits and the shared
    session stays usable for the independent recovery path."""
    stamped = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
    async with pg_maker() as session:
        await mutate_execution_projection(
            session,
            workflow_id="mm:pg-batch-good",
            payload=_payload("mm:pg-batch-good", stamped),
            owner="temporal",
        )
        await session.commit()

        newer = datetime(2026, 9, 1, 12, 5, tzinfo=UTC)

        async def _fake_fetch(sess, workflow_id, client):
            if workflow_id == "mm:pg-batch-bad":
                raise RuntimeError("temporal unavailable")
            payload = _payload("mm:pg-batch-good", newer)
            return await mutate_execution_projection(
                sess, workflow_id="mm:pg-batch-good",
                payload=payload, owner="temporal",
            )

        monkeypatch.setattr(
            sync_module, "fetch_and_sync_execution", _fake_fetch,
        )
        items = [
            SimpleNamespace(workflow_id="mm:pg-batch-bad"),
            SimpleNamespace(workflow_id="mm:pg-batch-good"),
        ]
        results = await sync_module.sync_temporal_executions_safely(
            session, items, object()
        )

        assert len(results) == 2
        assert results[0].workflow_id == "mm:pg-batch-bad"
        await session.refresh(results[1])
        assert results[1].sync_state is TemporalExecutionProjectionSyncState.FRESH

        # The session is usable afterwards: uncertain work reconciles by
        # execution identity instead of restarting the workflow.
        repaired = await mutate_execution_projection(
            session,
            workflow_id="mm:pg-batch-good",
            payload=_payload("mm:pg-batch-good", newer),
            owner="temporal",
        )
        await session.commit()
        assert repaired.run_id == "run-1"
        assert repaired.sync_state is TemporalExecutionProjectionSyncState.FRESH
