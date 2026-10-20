"""Real-PostgreSQL guarded single-user conversion evidence (#4346).

Proves the four-disposition matrix, staleness/advisory contention, ledger
idempotent rerun, and migration 386 on PostgreSQL — not SQLite. Postgres-only
paths (``pg_try_advisory_xact_lock``, ledger concurrency, migration 386 DDL on
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
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import (
    Base,
    ManagedSecret,
    Preset,
    PresetScopeType,
    RecurringWorkflowDefinition,
    SecretStatus,
    SettingsOverride,
    TemporalArtifact,
    TemporalArtifactLink, TemporalArtifactPin, TemporalArtifactUseClaim,
    TemporalArtifactDeletionIntent,
    TemporalExecutionCanonicalRecord,
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
    TemporalExecutionCanonicalRecord.__table__,
    TemporalArtifact.__table__,
    TemporalArtifactLink.__table__, TemporalArtifactPin.__table__,
    TemporalArtifactUseClaim.__table__, TemporalArtifactDeletionIntent.__table__,
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
        await conn.run_sync(Base.metadata.create_all, tables=list(_TABLES), checkfirst=True)
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        yield maker
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all, tables=list(_TABLES), checkfirst=True)
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
            session, alias_groups=[{u1_id, u2_id}], operator_authorized=True
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
async def test_postgres_staleness_ledger_rerun_and_contention(
    pg_maker, conversion_postgres_url
) -> None:
    """Stale attribution, same-digest rerun, in-progress contention, advisory."""
    from api_service.services import single_user_conversion as suc

    async with pg_maker() as session:
        u = await _mk_user(session)
        session.add(UserProfile(user_id=u.id))
        await session.commit()
        pre = await suc.preflight(session)
        assert pre.decision.eligible is True

        # Conversion-wide claim works on real PostgreSQL (sqlite fallback).
        assert await suc._try_conversion_claim(session) is True
        await suc._release_conversion_claim(session)

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

        # A stale in_progress row (no live writer) is reclaimed while the
        # apply holds the conversion-wide claim; a live writer holding the
        # claim elsewhere fails closed and the row is never stolen. Use the
        # real preflight digest so the staleness gate passes and the ledger
        # path is what decides. Clear the completed row first to simulate a
        # crash-interrupted attempt holding the digest.
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
        holder_engine = create_async_engine(conversion_postgres_url)
        try:
            async with holder_engine.connect() as held:
                await held.execute(
                    text("SELECT pg_advisory_lock(:key)"),
                    {"key": suc._conversion_advisory_key()},
                )
                with pytest.raises(suc.ConcurrentConversionError):
                    await suc.apply_conversion(
                        session, pre3, operator_authorized=True,
                        transforms={"t": _t},
                    )
                stale = (
                    await session.execute(
                        select(SingleUserConversionRun).where(
                            SingleUserConversionRun.preflight_digest
                            == pre3.digest
                        )
                    )
                ).scalars().one()
                assert stale.status == "in_progress"
                # Explicit unlock while still holding the connection: pool
                # checkout alone does not release a session-level lock.
                await held.execute(
                    text("SELECT pg_advisory_unlock(:key)"),
                    {"key": suc._conversion_advisory_key()},
                )
        finally:
            await holder_engine.dispose()
        # Lock released (no live writer): the retry reclaims and publishes.
        recovered = await suc.apply_conversion(
            session, pre3, operator_authorized=True, transforms={"t": _t}
        )
        assert recovered.published is True
        assert recovered.digest == pre3.digest


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


@pytest.mark.asyncio
async def test_postgres_unavailable_inventory_refuses(
    pg_maker, conversion_postgres_url
) -> None:
    """A failed required read is unknown evidence, never empty/eligible."""
    from api_service.services import single_user_conversion as suc

    # Fresh ephemeral cluster per test: drift the existing settings table
    # (drop a column the SELECT needs) instead of dropping the table, so
    # the failure is a genuinely unreadable surface, not an empty schema.
    ddl = create_async_engine(conversion_postgres_url)
    try:
        async with ddl.begin() as conn:
            await conn.execute(
                text("ALTER TABLE settings_overrides DROP COLUMN value_json")
            )
        async with pg_maker() as session:
            inv = await suc.collect_inventory(session)
            assert inv.inventory_errors, "failed read must be recorded"
            decision = await suc.evaluate_disposition(session)
            assert decision.disposition == "unresolved_refusal"
            assert decision.reason_code == "inventory_unavailable"
            assert decision.eligible is False
    finally:
        await ddl.dispose()


@pytest.mark.asyncio
async def test_postgres_same_count_value_change_is_stale(pg_maker) -> None:
    """Owner sets/counts alone do not bind the source; values do too."""
    from api_service.services import single_user_conversion as suc

    async with pg_maker() as session:
        u = await _mk_user(session)
        row = SettingsOverride(
            scope="user", user_id=u.id, key="theme", value_json={"v": 1}
        )
        session.add(row)
        await session.commit()
        pre = await suc.preflight(session)
        row.value_json = {"v": 2}
        await session.commit()
        with pytest.raises(suc.StaleAttributionError):
            await suc.apply_conversion(
                session, pre, operator_authorized=True, transforms={}
            )


@pytest.mark.asyncio
async def test_postgres_same_count_artifact_change_is_stale(pg_maker) -> None:
    """Retained artifact values bind preflight without loading rows in Python."""
    from api_service.services import single_user_conversion as suc

    async with pg_maker() as session:
        u = await _mk_user(session)
        artifact = TemporalArtifact(
            artifact_id="pg-bound", storage_key="before",
            created_by_principal=str(u.id),
        )
        session.add(artifact)
        await session.commit()
        pre = await suc.preflight(session)
        assert (await suc.preflight(session)).fingerprint == pre.fingerprint
        artifact.storage_key = "after"
        await session.commit()
        with pytest.raises(suc.StaleAttributionError):
            await suc.apply_conversion(
                session, pre, operator_authorized=True, transforms={}
            )


@pytest.mark.asyncio
async def test_postgres_concurrent_proposals_serialize(pg_maker) -> None:
    """Two different conversion proposals for one database serialize."""
    import asyncio

    from api_service.services import single_user_conversion as suc

    async with pg_maker() as session_a, pg_maker() as session_b:
        pre_a = await suc.preflight(session_a)
        pre_b = await suc.preflight(
            session_a,
            deployment_owned_tables={"presets"},
            operator_authorized=True,
        )
        assert pre_a.digest != pre_b.digest
        started = asyncio.Event()
        release = asyncio.Event()

        async def _slow(s, eligible):
            started.set()
            await release.wait()
            return {"outcome": "slow"}

        task_a = asyncio.create_task(
            suc.apply_conversion(
                session_a, pre_a, operator_authorized=True,
                transforms={"t": _slow},
            )
        )
        await asyncio.wait_for(started.wait(), timeout=30)
        # A holds the conversion-wide claim: B fails closed, never publishes.
        with pytest.raises(suc.ConcurrentConversionError):
            await suc.apply_conversion(
                session_b, pre_b, operator_authorized=True, transforms={}
            )
        release.set()
        result_a = await asyncio.wait_for(task_a, timeout=60)
        assert result_a.published is True


@pytest.mark.asyncio
async def test_postgres_interruption_rolls_back_and_retry_publishes(
    pg_maker,
) -> None:
    """Interruption before commit leaves nothing; the retry converges."""
    from api_service.services import single_user_conversion as suc

    async with pg_maker() as session:
        u = await _mk_user(session)
        session.add(UserProfile(user_id=u.id))
        await session.commit()
        pre = await suc.preflight(session)
        calls = {"n": 0}

        async def _flaky(s, eligible):
            calls["n"] += 1
            if calls["n"] == 1:
                s.add(
                    ManagedSecret(
                        slug="pg-partial",
                        ciphertext="x",
                        status=SecretStatus.ACTIVE,
                    )
                )
                await s.flush()
                raise RuntimeError("simulated crash after progress")
            return {"outcome": "recovered"}

        with pytest.raises(RuntimeError, match="simulated crash"):
            await suc.apply_conversion(
                session, pre, operator_authorized=True,
                transforms={"t": _flaky},
            )
        assert (
            await session.execute(select(SingleUserConversionRun))
        ).scalars().all() == []
        assert (await session.execute(select(ManagedSecret))).scalars().all() == []
        result = await suc.apply_conversion(
            session, pre, operator_authorized=True, transforms={"t": _flaky}
        )
        assert result.published is True
        assert calls["n"] == 2


@pytest.mark.asyncio
async def test_postgres_rerun_replays_through_cutover(pg_maker) -> None:
    """Eligible rerun through the shared entrypoint replays one completion."""
    from api_service.services import single_user_conversion as suc

    async with pg_maker() as session:
        u = await _mk_user(session)
        session.add(UserProfile(user_id=u.id, openai_api_key_encrypted="pg-cut"))
        await session.commit()
        first = await suc.run_guarded_upgrade(session, operator_authorized=True)
        assert first.published is True
        second = await suc.run_guarded_upgrade(session, operator_authorized=True)
        assert second.published is True
        assert second.digest == first.digest
        secrets = (await session.execute(select(ManagedSecret))).scalars().all()
        assert len(secrets) == 1
        blob = str(second.to_sanitized_dict())
        assert "pg-cut" not in blob


@pytest.mark.asyncio
async def test_postgres_artifact_conversion_blocks_authority_writers(
    pg_maker, tmp_path, monkeypatch,
) -> None:
    """Default conversion retains locks across missing-table inspection savepoints.

    Pause only the final commit, after the real artifact transform. An ordinary
    writer on each authority table must remain blocked until that commit. The
    fixture intentionally omits unrelated tables to exercise absent-table reads.
    """
    import asyncio
    from datetime import datetime, timedelta, timezone
    from api_service.services import single_user_conversion as suc
    from moonmind.config.settings import settings
    from moonmind.security import auth_modes_4120
    from moonmind.workflows.temporal.artifacts import (
        LocalTemporalArtifactStore, TemporalArtifactAuthorizationError,
        TemporalArtifactRepository, TemporalArtifactService,
    )
    from api_service.db.models import (
        TemporalArtifactLink, TemporalArtifactPin, TemporalArtifactUseClaim,
        TemporalArtifactRedactionLevel,
    )
    monkeypatch.setattr(auth_modes_4120, "_ACTIVE_PRODUCTION_MODE", "oidc")
    monkeypatch.setattr(settings.oidc, "DEFAULT_USER_ID", None)
    store = LocalTemporalArtifactStore(tmp_path / "artifact-bytes")
    async with pg_maker() as session:
        owner = str((await _mk_user(session)).id)
        await session.commit()
        artifacts = TemporalArtifactService(TemporalArtifactRepository(session), store=store)
        artifact, _ = await artifacts.create(
            principal=owner, redaction_level=TemporalArtifactRedactionLevel.RESTRICTED,
            content_type="application/octet-stream",
            link={"namespace": "default", "workflow_id": "historical", "run_id": "old", "link_type": "input"},
        )
        artifact_id = artifact.artifact_id
        await artifacts.write_complete(artifact_id=artifact_id, principal=owner,
            payload=b"retained postgres bytes", content_type="application/octet-stream")
        # Restricted completion creates a separate preview under the same
        # creator. Conversion must preserve and grant exactly this pair.
        preview_id = artifact.metadata_json["preview_artifact_id"]
        assert preview_id != artifact_id
        retained = (await session.execute(select(TemporalArtifact))).scalars().all()
        assert {row.artifact_id for row in retained} == {artifact_id, preview_id}
        preview = await session.get(TemporalArtifact, preview_id)
        assert preview.redaction_level is TemporalArtifactRedactionLevel.PREVIEW_ONLY
        assert preview.metadata_json == {"preview_of": artifact_id, "policy": "auto-generated"}
        assert all(row.created_by_principal == owner and not row.operator_recovery_granted
                   for row in retained)
        session.add(TemporalArtifactPin(artifact_id=artifact_id, pinned_by_principal=owner))
        session.add(TemporalArtifactUseClaim(artifact_id=artifact_id,
            owner_principal=owner, request_id="retained", operation_kind="restore",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1)))
        await session.commit()

    entered, release = asyncio.Event(), asyncio.Event()
    async with pg_maker() as converter:
        real_commit = converter.commit

        async def pause_commit():
            entered.set()
            await release.wait()
            await real_commit()

        monkeypatch.setattr(converter, "commit", pause_commit)
        converting = asyncio.create_task(suc.run_guarded_upgrade(converter, operator_authorized=True))
        await asyncio.wait_for(entered.wait(), 10)

        async def authority_writer(table):
            # Fail fast at PostgreSQL's real lock boundary. A leaked outer
            # rollback during partial-schema inspection would let this write.
            from sqlalchemy.exc import DBAPIError
            async with pg_maker() as writer:
                await writer.execute(text("SET LOCAL lock_timeout = '150ms'"))
                with pytest.raises(DBAPIError, match="lock timeout"):
                    if table == "user":
                        writer.add(User(id=uuid.uuid4(), email="concurrent-person@example.invalid"))
                        await writer.flush()
                    else:
                        await writer.execute(text(f'UPDATE "{table}" SET artifact_id = artifact_id'))
                await writer.rollback()

        try:
            for table in ("user", "temporal_artifacts", "temporal_artifact_links",
                          "temporal_artifact_pins", "temporal_artifact_use_claims",
                          "temporal_artifact_deletion_intents"):
                await authority_writer(table)
        finally:
            release.set()
        result = await asyncio.wait_for(converting, 10)
        assert result.published
        assert result.transforms["artifacts"]["recovery_grants"] == 2

    async with pg_maker() as restarted:
        retained = (await restarted.execute(select(TemporalArtifact))).scalars().all()
        assert {row.artifact_id for row in retained if row.operator_recovery_granted} == {
            artifact_id, preview_id}
        assert {row.artifact_id for row in retained} == {artifact_id, preview_id}
        assert all(row.created_by_principal == owner for row in retained)
        assert (await restarted.get(TemporalArtifact, artifact_id)).metadata_json[
            "preview_artifact_id"] == preview_id
        assert (await restarted.get(TemporalArtifact, preview_id)).metadata_json == {
            "preview_of": artifact_id, "policy": "auto-generated"}
        artifacts = TemporalArtifactService(TemporalArtifactRepository(restarted), store=store)
        row, payload = await artifacts.read(artifact_id=artifact_id, principal="operator")
        assert payload == b"retained postgres bytes"
        assert row.created_by_principal == owner
        with pytest.raises(TemporalArtifactAuthorizationError):
            await artifacts.read(artifact_id=artifact_id, principal=str(uuid.uuid4()))
        assert not await artifacts.release_saved_work_use(
            artifact_id=artifact_id, principal="operator", request_id="retained")
        assert (await restarted.execute(select(TemporalArtifactLink))).scalars().one().run_id == "old"
        again = await suc.run_guarded_upgrade(restarted, operator_authorized=True)
        assert again.published

    # Real account-free consumption and creation preserve the reserved local
    # scope without inventing a User, before another OIDC -> disabled cycle.
    zero = suc.DEPLOYMENT_SUBJECT_ID
    monkeypatch.setattr(auth_modes_4120, "_ACTIVE_PRODUCTION_MODE", "disabled")
    async with pg_maker() as local:
        artifacts = TemporalArtifactService(TemporalArtifactRepository(local), store=store)
        await artifacts.presign_download(artifact_id=artifact_id, principal=zero,
                                        recovery_principal="operator")
        row, _ = await artifacts.create(principal=zero,
            redaction_level=TemporalArtifactRedactionLevel.RESTRICTED,
            content_type="application/octet-stream")
        row = await artifacts.write_complete(artifact_id=row.artifact_id, principal=zero,
            payload=b"account-free postgres bytes", content_type="application/octet-stream")
        zero_ids = {row.artifact_id, row.metadata_json["preview_artifact_id"]}
        assert await local.get(User, uuid.UUID(zero)) is None
        zero_rows = (await local.execute(select(TemporalArtifact.__table__).where(
            TemporalArtifact.created_by_principal == zero))).all()
        zero_claims = (await local.execute(select(TemporalArtifactUseClaim.__table__).where(
            TemporalArtifactUseClaim.owner_principal == zero))).all()
        assert len(zero_rows) == 2 and zero_claims
        zero_quota = await TemporalArtifactRepository(local).quota_usage_for_scope(zero)

    monkeypatch.setattr(auth_modes_4120, "_ACTIVE_PRODUCTION_MODE", "oidc")
    # The prerequisite can first run while OIDC still admits ordinary writes.
    # Later historical human resources need current guarded proof at cutover.
    later = {}
    later_human_id = None
    async with pg_maker() as writer:
        artifacts = TemporalArtifactService(TemporalArtifactRepository(writer), store=store)
        for principal in (owner, "service:producer"):
            row, _ = await artifacts.create(
                principal=principal, redaction_level=TemporalArtifactRedactionLevel.RESTRICTED,
                content_type="application/octet-stream",
            )
            row = await artifacts.write_complete(
                artifact_id=row.artifact_id, principal=principal,
                payload=b"post-completion postgres bytes", content_type="application/octet-stream",
            )
            later[principal] = {row.artifact_id, row.metadata_json["preview_artifact_id"]}
            if principal == owner:
                later_human_id = row.artifact_id
            assert row.operator_recovery_granted is False

    monkeypatch.setattr(auth_modes_4120, "_ACTIVE_PRODUCTION_MODE", "disabled")
    async with pg_maker() as restarted:
        converted = await suc.run_guarded_upgrade(restarted, operator_authorized=True)
        assert converted.published
        assert converted.digest != result.digest
        retained = (await restarted.execute(select(TemporalArtifact))).scalars().all()
        assert {row.artifact_id for row in retained if row.operator_recovery_granted} == {
            artifact_id, preview_id, *later[owner]}
        assert {row.artifact_id for row in retained if not row.operator_recovery_granted} == later["service:producer"] | zero_ids
        assert (await restarted.execute(select(TemporalArtifact.__table__).where(
            TemporalArtifact.created_by_principal == zero))).all() == zero_rows
        assert (await restarted.execute(select(TemporalArtifactUseClaim.__table__).where(
            TemporalArtifactUseClaim.owner_principal == zero))).all() == zero_claims
        assert await TemporalArtifactRepository(restarted).quota_usage_for_scope(zero) == zero_quota
        assert await restarted.get(User, uuid.UUID(zero)) is None
        artifacts = TemporalArtifactService(TemporalArtifactRepository(restarted), store=store)
        row, payload = await artifacts.read(artifact_id=later_human_id, principal="operator")
        assert payload == b"post-completion postgres bytes"
        assert row.created_by_principal == owner

    async with pg_maker() as restarted:
        async def must_not_inventory(*_args, **_kwargs):
            raise AssertionError("unchanged machine-only pending rows must not reload history")

        with monkeypatch.context() as unchanged:
            unchanged.setattr(suc, "collect_inventory", must_not_inventory)
            assert (await suc.run_guarded_upgrade(restarted, operator_authorized=True)).published
            assert (await suc.startup_guard_decision(restarted)).eligible

    for malformed in ("service:\t", "SERVICE:agent"):
        # The SQL probe must not mistake empty suffixes or case variants for
        # established machines on PostgreSQL. Current attribution refuses.
        async with pg_maker() as writer:
            writer.add(TemporalArtifact(artifact_id="ambiguous-later", storage_key="ambiguous",
                created_by_principal=malformed))
            await writer.commit()
        async with pg_maker() as restarted:
            refused = await suc.run_guarded_upgrade(restarted, operator_authorized=True)
            assert not refused.published
            assert refused.decision.reason_code == "inventory_unavailable"
            ambiguous = await restarted.get(TemporalArtifact, "ambiguous-later")
            assert ambiguous.created_by_principal == malformed
            assert ambiguous.operator_recovery_granted is False
            # Isolate the second malformed fixture without modifying any of
            # the retained human or machine rows checked above.
            await restarted.delete(ambiguous)
            await restarted.commit()

    # A real retained account at the reserved UUID is still a person. It
    # cannot be hidden by the preserved-scope exception during a new apply.
    async with pg_maker() as writer:
        writer.add(User(id=uuid.UUID(zero), email="real-zero@example.invalid"))
        writer.add(TemporalArtifact(artifact_id="after-zero-user", storage_key="later-owner",
            created_by_principal=owner))
        await writer.commit()
    async with pg_maker() as restarted:
        refused = await suc.run_guarded_upgrade(restarted, operator_authorized=True)
        assert not refused.published
        assert refused.decision.reason_code == "multi_person"
        assert not (await restarted.get(TemporalArtifact, "after-zero-user")).operator_recovery_granted
        assert (await restarted.execute(select(TemporalArtifact.__table__).where(
            TemporalArtifact.created_by_principal == zero))).all() == zero_rows


@pytest.mark.asyncio
async def test_postgres_artifact_conversion_rollback_and_old_ledger(pg_maker, monkeypatch):
    from api_service.services import single_user_conversion as suc
    from api_service.auth_providers import _disabled_account_free_allowed

    async with pg_maker() as session:
        owner = str((await _mk_user(session)).id)
        session.add(TemporalArtifact(artifact_id="retained", storage_key="retained",
            created_by_principal=owner))
        session.add(SingleUserConversionRun(preflight_digest="historical", status="complete",
            result_json={"published": True, "decision": {"disposition": "eligible_conversion"}}))
        await session.commit()
        assert await _disabled_account_free_allowed(session)
        assert not (await session.get(TemporalArtifact, "retained")).operator_recovery_granted
        real_commit = session.commit

        async def interrupted_commit():
            # Real default transforms have run, but neither grant nor current
            # completion can become durable if publication is interrupted.
            await session.flush()
            raise RuntimeError("publication interrupted")

        monkeypatch.setattr(session, "commit", interrupted_commit)
        with pytest.raises(RuntimeError, match="publication interrupted"):
            await suc.run_guarded_upgrade(session, operator_authorized=True)
        monkeypatch.setattr(session, "commit", real_commit)
    async with pg_maker() as restarted:
        assert not (await restarted.get(TemporalArtifact, "retained")).operator_recovery_granted
        assert await _disabled_account_free_allowed(restarted)
        assert (await suc.run_guarded_upgrade(restarted, operator_authorized=True)).published
    async with pg_maker() as restarted:
        assert (await restarted.get(TemporalArtifact, "retained")).operator_recovery_granted
        assert await _disabled_account_free_allowed(restarted)


@pytest.mark.asyncio
async def test_postgres_artifact_recovery_migration_grants_nothing(pg_maker):
    """Actual additive DDL preserves retained rows and defaults to no grant."""
    import importlib
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    migration = importlib.import_module("api_service.migrations.versions.396_artifact_operator_recovery")
    async with pg_maker() as session:
        owner = str((await _mk_user(session)).id)
        session.add(TemporalArtifact(artifact_id="pre-schema", storage_key="original-key",
            created_by_principal=owner))
        await session.commit()
        await session.execute(text("ALTER TABLE temporal_artifacts DROP COLUMN operator_recovery_granted"))
        connection = await session.connection()

        def upgrade(sync_connection):
            with Operations.context(MigrationContext.configure(sync_connection)):
                migration.upgrade()

        await connection.run_sync(upgrade)
        await session.commit()
    async with pg_maker() as restarted:
        row = await restarted.get(TemporalArtifact, "pre-schema")
        assert row.created_by_principal == owner
        assert row.storage_key == "original-key"
        assert not row.operator_recovery_granted


@pytest.mark.asyncio
async def test_postgres_conversion_claim_ends_with_transaction(
    pg_maker, conversion_postgres_url,
):
    """A different physical connection can claim after publish, replay, refusal."""
    from api_service.services import single_user_conversion as suc
    other_engine = create_async_engine(conversion_postgres_url)
    try:
        async with pg_maker() as session:
            # Explicit production transforms exercise authoritative apply and
            # digest replay, rather than the scan-free default startup replay.
            for _ in range(2):
                assert (await suc.run_guarded_upgrade(session, operator_authorized=True,
                    transforms=suc.DEFAULT_SUBSYSTEM_TRANSFORMS)).published
                async with other_engine.connect() as other:
                    assert (await other.execute(text("SELECT pg_try_advisory_xact_lock(:key)"),
                        {"key": suc._conversion_advisory_key()})).scalar() is True
                    await other.rollback()
            await _mk_user(session)
            await _mk_user(session)
            await session.commit()
            assert not (await suc.run_guarded_upgrade(session, operator_authorized=True,
                transforms=suc.DEFAULT_SUBSYSTEM_TRANSFORMS)).published
            async with other_engine.connect() as other:
                assert (await other.execute(text("SELECT pg_try_advisory_xact_lock(:key)"),
                    {"key": suc._conversion_advisory_key()})).scalar() is True
    finally:
        await other_engine.dispose()


@pytest.mark.asyncio
async def test_postgres_converted_recovery_refuses_schema_downgrade(pg_maker):
    import importlib
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from api_service.services import single_user_conversion as suc
    migration = importlib.import_module("api_service.migrations.versions.396_artifact_operator_recovery")
    async with pg_maker() as session:
        owner = str((await _mk_user(session)).id)
        session.add(TemporalArtifact(artifact_id="granted", storage_key="retained",
            created_by_principal=owner))
        await session.commit()
        assert (await suc.run_guarded_upgrade(session, operator_authorized=True)).published
        connection = await session.connection()

        def downgrade(sync_connection):
            with Operations.context(MigrationContext.configure(sync_connection)):
                migration.downgrade()

        with pytest.raises(RuntimeError, match="recovery grants"):
            await connection.run_sync(downgrade)
        await session.rollback()
    async with pg_maker() as restarted:
        assert (await restarted.get(TemporalArtifact, "granted")).operator_recovery_granted
