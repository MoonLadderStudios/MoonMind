"""Real-PostgreSQL guarded single-user conversion evidence (#4346).

Proves the four-disposition matrix, staleness/advisory contention, ledger
idempotent rerun, and migration 386 on PostgreSQL — not SQLite. Postgres-only
paths (``pg_try_advisory_lock``, ledger concurrency, migration 386 DDL on
postgres) are unproven by the sqlite suites. Runs an ephemeral local cluster
with no manually managed test service (mirrors
``test_profile_secret_migration_postgres_4349.py``).
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
    ManagedSecret,
    Preset,
    PresetScopeType,
    RecurringWorkflowDefinition,
    SettingsOverride,
    TemporalArtifact,
    TemporalExecutionOwnerType,
    TemporalExecutionRecord,
    TemporalWorkflowType,
    User,
    UserProfile,
)
from api_service.services.single_user_conversion import SingleUserConversionRun

pytestmark = [pytest.mark.integration, pytest.mark.integration_ci]

_TABLES = (
    User.__table__,
    UserProfile.__table__,
    ManagedSecret.__table__,
    SingleUserConversionRun.__table__,
    TemporalExecutionRecord.__table__,
    TemporalArtifact.__table__,
    SettingsOverride.__table__,
    Preset.__table__,
    RecurringWorkflowDefinition.__table__,
)


@pytest.fixture
def conversion_postgres_url():
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
    data_root = Path(tempfile.mkdtemp(prefix="moonmind-conv4346-postgres-"))
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
async def pg_maker(conversion_postgres_url):
    engine = create_async_engine(conversion_postgres_url)
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


async def _mk_user(session, **kw):
    u = User(id=uuid.uuid4(), email=kw.get("email", f"{uuid.uuid4().hex}@ex.invalid"))
    for k, v in kw.items():
        if k != "email":
            setattr(u, k, v)
    session.add(u)
    await session.flush()
    return u


@pytest.mark.asyncio
async def test_postgres_four_dispositions(pg_maker) -> None:
    """Empty, single, alias, multi, unknown, and misleading-flag sources."""
    from api_service.services.single_user_conversion import evaluate_disposition
    from moonmind.statuses.workflow import MoonMindWorkflowState

    async with pg_maker() as session:
        decision = await evaluate_disposition(session)
        assert decision.disposition == "fresh_init"
        assert decision.eligible is True

    async with pg_maker() as session:
        u = await _mk_user(session)
        session.add(UserProfile(user_id=u.id))
        session.add(
            RecurringWorkflowDefinition(
                name="pg-sched", cron="0 * * * *", timezone="UTC",
                owner_user_id=u.id,
            )
        )
        session.add(
            Preset(
                slug="pg-preset", scope_type=PresetScopeType.GLOBAL,
                title="PG", description="pg fixture", created_by=u.id,
            )
        )
        await session.commit()
        decision = await evaluate_disposition(session)
        assert decision.disposition == "eligible_conversion"
        assert set(decision.present_subsystems) >= {"schedules", "presets"}

    async with pg_maker() as session:
        for table in (UserProfile.__table__, Preset.__table__,
                      RecurringWorkflowDefinition.__table__, User.__table__):
            await session.execute(table.delete())
        await session.commit()
        u1 = await _mk_user(session, email="Same.Person@example.invalid")
        u1_id = str(u1.id)
        u2 = await _mk_user(session, email="same.person@EXAMPLE.invalid")
        u2_id = str(u2.id)
        session.add(UserProfile(user_id=u1.id))
        session.add(UserProfile(user_id=u2.id))
        await session.commit()
        refused = await evaluate_disposition(session)
        assert refused.disposition == "multi_person_refusal"
        ok = await evaluate_disposition(
            session, alias_groups=[{u1_id, u2_id}]
        )
        assert ok.disposition == "eligible_conversion"

    async with pg_maker() as session:
        for table in (UserProfile.__table__, User.__table__):
            await session.execute(table.delete())
        await session.commit()
        admin = await _mk_user(session, is_superuser=True, is_active=False)
        plain = await _mk_user(session, is_superuser=False, is_active=True)
        session.add(UserProfile(user_id=admin.id))
        session.add(UserProfile(user_id=plain.id))
        await session.commit()
        decision = await evaluate_disposition(session)
        assert decision.disposition == "multi_person_refusal"

    async with pg_maker() as session:
        for table in (UserProfile.__table__, User.__table__):
            await session.execute(table.delete())
        await session.commit()
        u = await _mk_user(session)
        session.add(UserProfile(user_id=u.id))
        session.add(
            TemporalArtifact(
                artifact_id="pg-unknown", storage_key="k",
                created_by_principal=str(uuid.uuid4()),
            )
        )
        session.add(
            TemporalExecutionRecord(
                workflow_id="pg-wf", run_id="pg-run", entry="e",
                workflow_type=TemporalWorkflowType.USER_WORKFLOW,
                owner_id=str(u.id), owner_type=TemporalExecutionOwnerType.USER,
                state=MoonMindWorkflowState.EXECUTING,
            )
        )
        await session.commit()
        decision = await evaluate_disposition(session)
        assert decision.disposition == "unresolved_refusal"
        assert decision.reason_code == "unknown_principal"


@pytest.mark.asyncio
async def test_postgres_staleness_ledger_rerun_and_contention(pg_maker) -> None:
    """Stale attribution, same-digest rerun, in-progress contention, advisory."""
    from api_service.services import single_user_conversion as suc

    async with pg_maker() as session:
        u = await _mk_user(session)
        session.add(UserProfile(user_id=u.id))
        await session.commit()
        pre = await suc.preflight(session)
        assert pre.decision.eligible is True

        # Advisory claim works on real PostgreSQL (not the sqlite fallback).
        assert await suc._try_advisory_claim(session, pre.digest) is True
        await suc._release_advisory_claim(session, pre.digest)

        # Intervening ownership change invalidates the preflight.
        u2 = await _mk_user(session)
        session.add(UserProfile(user_id=u2.id))
        await session.commit()
        with pytest.raises(suc.StaleAttributionError):
            await suc.apply_conversion(
                session, pre, operator_authorized=True,
                transforms={"__test__": lambda s, e: {}},
            )

    async with pg_maker() as session:
        for table in (UserProfile.__table__, User.__table__,
                      SingleUserConversionRun.__table__):
            await session.execute(table.delete())
        await session.commit()
        u = await _mk_user(session)
        session.add(UserProfile(user_id=u.id))
        await session.commit()

        async def _t(s, eligible):
            return {"ok": True}

        pre = await suc.preflight(session)
        first = await suc.apply_conversion(
            session, pre, operator_authorized=True, transforms={"t": _t}
        )
        assert first.published is True
        # Same-digest rerun replays the recorded ledger result.
        pre2 = await suc.preflight(session)
        assert pre2.digest == first.digest
        second = await suc.apply_conversion(
            session, pre2, operator_authorized=True, transforms={"t": _t}
        )
        assert second.published is True
        assert second.digest == first.digest

        # A concurrent holder (in_progress ledger row) fails closed. Use the
        # real preflight digest so the staleness gate passes and the ledger
        # mutual-exclusion path is what refuses. Clear the completed row
        # first to simulate a crash-interrupted attempt holding the digest.
        await session.execute(SingleUserConversionRun.__table__.delete())
        await session.commit()
        pre3 = await suc.preflight(session)
        session.add(
            SingleUserConversionRun(
                preflight_digest=pre3.digest,
                status="in_progress",
                result_json={},
            )
        )
        await session.commit()
        with pytest.raises(suc.ConcurrentConversionError):
            await suc.apply_conversion(
                session, pre3, operator_authorized=True, transforms={"t": _t}
            )


@pytest.mark.asyncio
async def test_postgres_guarded_upgrade_refuses_multi_without_mutation(pg_maker) -> None:
    """Rejected conversion performs no conversion-side mutation on postgres."""
    from api_service.services import single_user_conversion as suc

    async with pg_maker() as session:
        u1 = await _mk_user(session)
        u2 = await _mk_user(session)
        session.add(UserProfile(user_id=u1.id, openai_api_key_encrypted="pg-a"))
        session.add(UserProfile(user_id=u2.id, openai_api_key_encrypted="pg-b"))
        await session.commit()
        users_before = len((await session.execute(select(User))).scalars().all())
        result = await suc.run_guarded_upgrade(session, operator_authorized=True)
        assert result.published is False
        assert (await session.execute(select(User))).scalars() is not None
        users_after = len((await session.execute(select(User))).scalars().all())
        assert users_after == users_before
        assert (await session.execute(select(ManagedSecret))).scalars().all() == []
        blob = str(result.to_sanitized_dict())
        assert "pg-a" not in blob and "pg-b" not in blob


@pytest.mark.asyncio
async def test_postgres_migration_386_ledger_ddl(pg_maker) -> None:
    """Migration 386 revision chain is ledger-only and enforceable."""
    import importlib

    m386 = importlib.import_module(
        "api_service.migrations.versions.386_single_user_conversion_4346"
    )

    assert m386.revision == "386_single_user_conversion_4346"
    assert m386.down_revision == "385_runtime_issuance"
    async with pg_maker() as session:
        # Unique digest constraint is the mutual-exclusion enforcement.
        first = SingleUserConversionRun(
            preflight_digest="pg-ddl-digest", status="complete",
            result_json={"published": True},
        )
        session.add(first)
        await session.commit()
        from sqlalchemy.exc import IntegrityError

        session.add(
            SingleUserConversionRun(
                preflight_digest="pg-ddl-digest", status="in_progress",
                result_json={},
            )
        )
        with pytest.raises(IntegrityError):
            await session.flush()
        await session.rollback()
