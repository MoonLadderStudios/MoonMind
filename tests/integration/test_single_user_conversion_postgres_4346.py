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
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.auth_providers import instance_operator_principal
from api_service.db.models import (
    Base,
    ManagedSecret,
    Preset,
    PresetScopeType,
    PresetFavorite,
    PresetRecent,
    RecurringWorkflowDefinition,
    RecurringWorkflowRun,
    SecretStatus,
    SettingsOverride,
    SettingsAuditEvent,
    TemporalArtifact,
    TemporalArtifactLink,
    TemporalArtifactPin,
    TemporalArtifactUseClaim,
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
    TemporalArtifactLink.__table__,
    TemporalArtifactPin.__table__,
    TemporalArtifactUseClaim.__table__,
    TemporalArtifactDeletionIntent.__table__,
    SettingsOverride.__table__,
    SettingsAuditEvent.__table__,
    Preset.__table__,
    PresetFavorite.__table__,
    PresetRecent.__table__,
    RecurringWorkflowDefinition.__table__,
    RecurringWorkflowRun.__table__,
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


@asynccontextmanager
async def _served_product(app):
    """Serve the existing application on real sockets in the isolated test job."""
    import asyncio
    import uvicorn

    # Serve loopback and the workload address on one port, so the backend probe
    # can send the operator's exact Host, without listening on every interface.
    hosts = list(dict.fromkeys(("127.0.0.1", socket.gethostbyname(socket.gethostname()))))
    for _ in range(20):
        listeners = [socket.socket() for _ in hosts]
        try:
            listeners[0].bind((hosts[0], 0))
            port = listeners[0].getsockname()[1]
            for listener, host in zip(listeners[1:], hosts[1:]):
                listener.bind((host, port))
            break
        except OSError:
            for listener in listeners:
                listener.close()
    else:
        pytest.fail("No port is free on both the loopback and workload addresses")
    for listener in listeners:
        listener.listen(128)
    server = uvicorn.Server(uvicorn.Config(app, lifespan="off", log_level="error",
                                          access_log=False, proxy_headers=False))
    task = asyncio.create_task(server.serve(sockets=listeners))
    try:
        for _ in range(100):
            if server.started:
                break
            if task.done():
                pytest.fail(f"Product HTTP server exited before readiness: {task.exception()!r}")
            await asyncio.sleep(0.05)
        assert server.started, "Product HTTP server did not become ready"
        yield f"http://127.0.0.1:{port}", f"http://{hosts[-1]}:{port}"
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 10)
        for listener in listeners:
            listener.close()


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
@pytest.mark.parametrize("preset_scope", [PresetScopeType.GLOBAL, PresetScopeType.PERSONAL])
async def test_postgres_populated_instance_resources_publish_and_remain_usable(
    pg_maker, tmp_path, preset_scope,
) -> None:
    """Already instance-scoped resources need preservation, not no-op plugins."""
    from api_service.services import single_user_conversion as suc
    from api_service.services.presets.catalog import PresetCatalogService
    from api_service.services.recurring_workflows_service import RecurringWorkflowsService
    from api_service.services.secrets import SecretsService
    from api_service.services.settings_catalog import SettingsCatalogService
    from moonmind.statuses.workflow import MoonMindWorkflowState
    from moonmind.workflows.temporal.artifacts import (
        LocalTemporalArtifactStore, TemporalArtifactRepository, TemporalArtifactService,
    )
    from moonmind.workflows.temporal.service import TemporalExecutionService

    store = LocalTemporalArtifactStore(tmp_path / "retained-artifacts")
    async with pg_maker() as source:
        owner = await _mk_user(source)
        owner_id = owner.id
        source.add(ManagedSecret(slug="retained-binding", ciphertext="synthetic-credential",
                                 status=SecretStatus.ACTIVE))
        source.add(SettingsOverride(scope="workspace", key="integrations.github.token_ref",
                                    value_json="db://retained-binding"))
        preset = Preset(slug="retained-custom", scope_type=preset_scope,
            scope_ref=str(owner_id) if preset_scope == PresetScopeType.PERSONAL else None,
            title="Retained custom preset", description="Keep explicit inputs",
            created_by=owner_id,
            inputs_schema=[{"name": "audience", "label": "Audience", "type": "text",
                            "required": True, "default": "default audience"}],
            steps=[{"instructions": "Write for {{ inputs.audience }}."}])
        schedule = RecurringWorkflowDefinition(name="retained-paused", cron="17 * * * *",
            timezone="UTC", owner_user_id=owner_id, enabled=False,
            target={"kind": "workflow", "publishMode": "none"})
        source.add_all([preset, schedule])
        source.add(TemporalExecutionRecord(workflow_id="retained-accepted", run_id="original-run",
            entry="e", workflow_type=TemporalWorkflowType.USER_WORKFLOW,
            owner_id=str(owner_id), owner_type=TemporalExecutionOwnerType.USER,
            state=MoonMindWorkflowState.EXECUTING))
        source.add(TemporalExecutionCanonicalRecord(workflow_id="retained-accepted",
            run_id="original-run", entry="e", workflow_type=TemporalWorkflowType.USER_WORKFLOW,
            owner_id=str(owner_id), owner_type=TemporalExecutionOwnerType.USER,
            state=MoonMindWorkflowState.EXECUTING))
        await source.commit()
        schedule_id = schedule.id
        catalog = PresetCatalogService(source)
        before_preset = {
            "title": preset.title, "description": preset.description,
            "inputs": preset.inputs_schema, "steps": preset.steps,
        }
        preset_id = preset.id
        from api_service.services.presets.catalog import PresetNotFoundError
        with pytest.raises(PresetNotFoundError):
            await catalog.get_template(slug=preset.slug)
        artifacts = TemporalArtifactService(TemporalArtifactRepository(source), store=store)
        artifact, _ = await artifacts.create(principal=str(owner_id), content_type="text/plain")
        await artifacts.write_complete(artifact_id=artifact.artifact_id, principal=str(owner_id),
                                       payload=b"retained output", content_type="text/plain")
        artifact_id = artifact.artifact_id

    async with pg_maker() as converter:
        result = await suc.run_guarded_upgrade(converter, operator_authorized=True)
        assert result.published, result.to_sanitized_dict()
        assert result.decision.disposition == "eligible_conversion"
        assert set(result.decision.present_subsystems) >= {
            "settings", "presets", "schedules", "temporal", "artifacts"}
        assert "synthetic-credential" not in str(result.to_sanitized_dict())

    # New connections exercise the actual persistence consumers after cutover.
    # No test-only conversion, authority object, dispatcher or service is used.
    async with pg_maker() as restarted:
        value = await SettingsCatalogService(session=restarted).effective_value_async(
            "integrations.github.token_ref", scope="workspace")
        assert value.value == "db://retained-binding"
        assert await SecretsService.get_secret(restarted, "retained-binding") == "synthetic-credential"
        catalog = PresetCatalogService(restarted)
        found = await catalog.get_template(slug="retained-custom")
        assert {key: found[key] for key in before_preset} == before_preset
        retained_preset = await restarted.get(Preset, preset_id)
        assert retained_preset.created_by == owner_id
        assert retained_preset.scope_type is None and retained_preset.scope_ref is None
        expanded = await catalog.expand_template(slug="retained-custom",
            inputs={"audience": "explicit operator input"})
        assert expanded["steps"][0]["instructions"] == "Write for explicit operator input."
        schedules = await RecurringWorkflowsService(restarted).list_definitions()
        retained = next(row for row in schedules if row.id == schedule_id)
        assert retained.cron == "17 * * * *" and not retained.enabled
        assert retained.owner_user_id == owner_id
        assert retained.target == {"kind": "workflow", "publishMode": "none"}
        execution = (await TemporalExecutionService(restarted).list_executions(page_size=10)).items[0]
        assert execution.workflow_id == "retained-accepted"
        assert execution.run_id == "original-run"
        assert execution.state == MoonMindWorkflowState.EXECUTING
        artifacts = TemporalArtifactService(TemporalArtifactRepository(restarted), store=store)
        _, payload = await artifacts.read(artifact_id=artifact_id, principal="operator")
        assert payload == b"retained output"
        replay = await suc.run_guarded_upgrade(restarted, operator_authorized=True)
        assert replay.published and replay.digest == result.digest


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy_surface", ["personal_setting", "context_setting"])
async def test_postgres_default_still_refuses_unsupported_personal_data(
    pg_maker, legacy_surface,
) -> None:
    from api_service.services import single_user_conversion as suc

    async with pg_maker() as source:
        owner = await _mk_user(source)
        source.add(SettingsOverride(scope="user" if legacy_surface == "personal_setting" else "workspace",
            workspace_id=uuid.uuid4() if legacy_surface == "context_setting" else uuid.UUID(suc.DEPLOYMENT_SUBJECT_ID),
            user_id=owner.id if legacy_surface == "personal_setting" else uuid.UUID(suc.DEPLOYMENT_SUBJECT_ID),
            key="integrations.github.token_ref", value_json="db://retained-personal"))
        await source.commit()
        tables = (User.__table__, SettingsOverride.__table__, Preset.__table__)
        before = [(await source.execute(select(table))).all() for table in tables]
        result = await suc.run_guarded_upgrade(source, operator_authorized=True)
        assert not result.published
        assert result.decision.reason_code == "missing_transform_coverage"
        assert [(await source.execute(select(table))).all() for table in tables] == before
        assert (await source.execute(select(SingleUserConversionRun))).scalars().all() == []


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


@pytest.mark.asyncio
async def test_postgres_product_routes_enforce_remote_operator_ingress(pg_maker, monkeypatch):
    """Real app/dependencies/persistence; only ingress transport is controlled."""
    import httpx

    from api_service.db import base
    from api_service.services.single_user_conversion import run_guarded_upgrade
    from moonmind.config.settings import settings
    from moonmind.security import auth_modes_4120

    monkeypatch.setattr(auth_modes_4120, "_ACTIVE_PRODUCTION_MODE", "disabled")
    monkeypatch.setenv("MOONMIND_PUBLIC_BASE_URL", "https://operator.example")
    monkeypatch.setenv("MOONMIND_TRUSTED_INGRESS", "1")
    monkeypatch.setenv("MOONMIND_TRUSTED_PROXIES", "192.0.2.10")
    monkeypatch.setattr(settings.feature_flags, "preset_catalog", True)
    import api_service.main as main

    async def database():
        async with pg_maker() as db:
            yield db

    previous = dict(main.app.dependency_overrides)
    main.app.dependency_overrides[base.get_async_session] = database
    monkeypatch.setattr(base, "async_session_maker", pg_maker)
    try:
        async with pg_maker() as db:
            assert (await run_guarded_upgrade(db, operator_authorized=True)).published
            assert (await db.execute(select(User))).scalars().all() == []
        headers = {"X-Moonmind-User": "trusted-ingress-assertion"}
        body = {"title": "Preserved custom input", "slug": "served-instance-preset",
                "description": "Actual product route write",
                "inputs": [{"name": "audience", "label": "Audience", "type": "text",
                            "required": True, "default": "default audience"}],
                "steps": [{"instructions": "Write for {{ inputs.audience }}."}]}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(
            app=main.app, client=("192.0.2.10", 32000)),
            base_url="https://operator.example") as client:
            assert (await client.get("/api/v1/settings/catalog", headers=headers)).status_code == 200
            assert (await client.post("/api/presets", headers=headers, json=body)).status_code == 201
            foreign_host = await client.get("/api/v1/settings/catalog",
                headers={**headers, "Host": "foreign.example"})
            assert foreign_host.status_code == 403
            foreign_origin = await client.post("/api/presets",
                headers={**headers, "Origin": "https://foreign.example"}, json={**body, "slug": "denied-origin"})
            assert foreign_origin.status_code == 403
            assert (await client.get("/api/v1/settings/catalog")).status_code == 401
        async with httpx.AsyncClient(transport=httpx.ASGITransport(
            app=main.app, client=("192.0.2.99", 32000)),
            base_url="https://operator.example") as direct:
            assert (await direct.get("/api/v1/settings/catalog", headers=headers)).status_code == 401
            assert (await direct.get("/api/v1/settings/catalog", headers={
                "Authorization": "Bearer synthetic-machine-material",
                "X-Moonmind-Execution-Fanout": "v1"})).status_code == 401
        monkeypatch.delenv("MOONMIND_PUBLIC_BASE_URL")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(
            app=main.app, client=("127.0.0.1", 32000)),
            base_url="http://localhost:7000") as local:
            assert (await local.get("/api/v1/settings/catalog")).status_code == 200
            assert (await local.post("/api/presets", headers={"Origin": "http://foreign.example"},
                json={**body, "slug": "denied-local-origin"})).status_code == 403
        async with pg_maker() as db:
            assert (await db.execute(select(User))).scalars().all() == []
            assert (await db.execute(select(Preset.slug))).scalars().all() == [body["slug"]]
    finally:
        main.app.dependency_overrides.clear()
        main.app.dependency_overrides.update(previous)


@pytest.mark.asyncio
@pytest.mark.parametrize("base_url", ["", "http://localhost:7000"])
async def test_postgres_product_routes_enforce_local_operator_transport(
    pg_maker, monkeypatch, base_url,
):
    """Exercise real product routes, not demonstration admission endpoints."""
    import httpx
    from api_service.db import base
    from api_service.services.single_user_conversion import run_guarded_upgrade
    from moonmind.security import auth_modes_4120
    import api_service.main as main

    monkeypatch.setattr(auth_modes_4120, "_ACTIVE_PRODUCTION_MODE", "disabled")
    monkeypatch.setenv("MOONMIND_PUBLIC_BASE_URL", base_url)
    monkeypatch.delenv("MOONMIND_TRUSTED_INGRESS", raising=False)
    monkeypatch.delenv("MOONMIND_TRUSTED_PROXIES", raising=False)

    async def database():
        async with pg_maker() as db:
            yield db

    previous = dict(main.app.dependency_overrides)
    main.app.dependency_overrides[base.get_async_session] = database
    monkeypatch.setattr(base, "async_session_maker", pg_maker)
    try:
        async with pg_maker() as db:
            assert (await run_guarded_upgrade(db, operator_authorized=True)).published
        async with httpx.AsyncClient(transport=httpx.ASGITransport(
            app=main.app, client=("127.0.0.1", 32000)),
            base_url="http://localhost:7000") as operator:
            assert (await operator.get("/api/v1/settings/catalog")).status_code == 200
            hostile = await operator.get("/api/v1/settings/catalog",
                headers={"Host": "foreign.example"})
            assert hostile.status_code in {401, 403}, hostile.text
            hostile = await operator.patch("/api/v1/settings/workspace",
                headers={"Origin": "http://foreign.example"}, json={"changes": {}})
            assert hostile.status_code == 403, hostile.text
        # The workload peer claims both a loopback Host and forwarded identity.
        # Neither those claims nor its machine material can grant operator access.
        async with httpx.AsyncClient(transport=httpx.ASGITransport(
            app=main.app, client=("192.0.2.99", 32000)),
            base_url="http://localhost:7000") as internal:
            forged = {"X-Forwarded-For": "127.0.0.1", "X-Moonmind-User": "operator"}
            machine = {"Authorization": "Bearer synthetic-machine-material",
                       "X-Moonmind-Execution-Fanout": "v1"}
            for headers in ({}, forged, machine):
                denied = await internal.get("/api/v1/settings/catalog", headers=headers)
                assert denied.status_code == 401, denied.text
                denied = await internal.patch("/api/v1/settings/workspace",
                    headers=headers, json={"changes": {}})
                assert denied.status_code == 401, denied.text
    finally:
        main.app.dependency_overrides.clear()
        main.app.dependency_overrides.update(previous)


@pytest.mark.asyncio
async def test_postgres_instance_routes_need_no_virtual_person(pg_maker, monkeypatch):
    """Actual admission, routes, writes, expansion and subsequent DB sessions."""
    import httpx
    from api_service import auth_providers
    from api_service.db import base
    from api_service.services.single_user_conversion import run_guarded_upgrade
    from moonmind.config.settings import settings
    from moonmind.security import auth_modes_4120
    import api_service.main as main

    monkeypatch.setattr(auth_modes_4120, "_ACTIVE_PRODUCTION_MODE", "disabled")
    monkeypatch.setenv("MOONMIND_PUBLIC_BASE_URL", "")
    monkeypatch.setattr(settings.feature_flags, "preset_catalog", True)
    monkeypatch.setattr(settings.workflow, "test_mode", False)

    def no_virtual_person():
        pytest.fail("Instance consumers must use permission admission, not a virtual User")

    monkeypatch.setattr(auth_providers, "_transient_disabled_operator", no_virtual_person)

    async def database():
        async with pg_maker() as db:
            yield db

    previous = dict(main.app.dependency_overrides)
    main.app.dependency_overrides[base.get_async_session] = database
    monkeypatch.setattr(base, "async_session_maker", pg_maker)
    try:
        async with pg_maker() as db:
            assert (await run_guarded_upgrade(db, operator_authorized=True)).published
            db.add(ManagedSecret(slug="instance-binding", ciphertext="synthetic-credential",
                                 status=SecretStatus.ACTIVE))
            await db.commit()
        transport = httpx.ASGITransport(app=main.app, client=("127.0.0.1", 32000))
        async with httpx.AsyncClient(transport=transport, base_url="http://localhost:7000") as client:
            assert (await client.get("/api/v1/settings/catalog")).status_code == 200
            ui = await client.get("/api/ui/info")
            assert ui.status_code == 200, ui.text
            assert ui.json()["features"]["workflowList"] is True
            changed = await client.patch("/api/v1/settings/workspace", json={
                "changes": {"integrations.github.token_ref": "db://instance-binding"},
                "expected_versions": {"integrations.github.token_ref": 1}})
            assert changed.status_code == 200, changed.text
            assert changed.json()["change_events"][0]["actor_user_id"] is None
            created = await client.post("/api/presets", json={
                "slug": "instance-authored", "title": "Custom instance preset",
                "description": "Retain exact custom content and inputs",
                "inputs": [{"name": "audience", "label": "Audience", "type": "text",
                            "required": True, "default": "default audience"}],
                "steps": [{"instructions": "Write for {{ inputs.audience }}."}]})
            assert created.status_code == 201, created.text
            saved = created.json()
            assert "scope" not in saved and "scopeRef" not in saved
            from_workflow = await client.post("/api/presets/save-from-workflow", json={
                "title": "Saved instance draft", "description": "Dashboard draft",
                "steps": [{"instructions": "Keep this custom step."}]})
            assert from_workflow.status_code == 201, from_workflow.text
            assert "scope" not in from_workflow.json()
            favorite = await client.post("/api/presets/instance-authored:favorite",
                                         json={})
            assert favorite.status_code == 204, favorite.text
        # Replay the production conversion owner and read via new API/DB sessions.
        async with pg_maker() as db:
            assert (await run_guarded_upgrade(db, operator_authorized=True)).published
        async with httpx.AsyncClient(transport=transport, base_url="http://localhost:7000") as restarted:
            value = await restarted.get("/api/v1/settings/effective/integrations.github.token_ref")
            assert value.status_code == 200, value.text
            assert value.json()["value"] == "db://instance-binding"
            catalog = await restarted.get("/api/presets")
            assert catalog.status_code == 200, catalog.text
            assert {p["slug"] for p in catalog.json()["items"]} == {
                "instance-authored", from_workflow.json()["slug"]}
            assert all(p["isFavorite"] for p in catalog.json()["items"])
            found = await restarted.get("/api/presets/instance-authored")
            assert found.status_code == 200, found.text
            assert {key: value for key, value in found.json().items() if key != "isFavorite"} == {
                key: value for key, value in saved.items() if key != "isFavorite"}
            expanded = await restarted.post("/api/presets/instance-authored:expand",
                json={"inputs": {"audience": "explicit operator input"}})
            assert expanded.status_code == 200, expanded.text
            assert expanded.json()["steps"][0]["instructions"] == "Write for explicit operator input."
            recent = await restarted.get("/api/presets/instance-authored")
            assert recent.json()["recentAppliedAt"]
            denied = await restarted.patch("/api/v1/settings/user", json={
                "changes": {"integrations.github.token_ref": "db://instance-binding"}})
            assert denied.status_code == 403
            denied = await restarted.get("/api/presets?scope=personal&scopeRef=someone")
            assert denied.status_code == 422
        async with pg_maker() as db:
            assert (await db.execute(select(User))).scalars().all() == []
            assert all(p.created_by is None for p in (await db.execute(select(Preset))).scalars())
            favorites = (await db.execute(select(PresetFavorite))).scalars().all()
            recents = (await db.execute(select(PresetRecent))).scalars().all()
            assert len(favorites) == 2 and all(row.user_id is None for row in favorites)
            assert len(recents) == 2 and all(row.user_id is None for row in recents)
            assert all(e.actor_user_id is None for e in
                       (await db.execute(select(SettingsAuditEvent))).scalars())
    finally:
        main.app.dependency_overrides.clear()
        main.app.dependency_overrides.update(previous)


@pytest_asyncio.fixture
async def account_free_product(pg_maker, monkeypatch, tmp_path, request):
    """Real main app and persistence; no identity or business-service overrides."""
    import httpx
    from api_service import auth_providers
    from api_service.db import base
    from api_service.services.single_user_conversion import run_guarded_upgrade
    from moonmind.config.settings import settings
    from moonmind.security import auth_modes_4120
    from moonmind.workflows.temporal.artifacts import (
        LocalTemporalArtifactStore,
        TemporalArtifactRepository,
        TemporalArtifactService,
    )
    import api_service.main as main

    monkeypatch.setattr(auth_modes_4120, "_ACTIVE_PRODUCTION_MODE", "disabled")
    monkeypatch.setenv("MOONMIND_PUBLIC_BASE_URL", "")
    monkeypatch.setattr(settings.temporal_dashboard, "submit_enabled", True)
    monkeypatch.setattr(settings.feature_flags, "preset_catalog", True)
    monkeypatch.setattr(settings.workflow, "test_mode", False)
    monkeypatch.setattr(
        TemporalArtifactService,
        "_build_store_from_settings",
        staticmethod(lambda: LocalTemporalArtifactStore(tmp_path / "artifacts")),
    )

    def no_virtual_person():
        pytest.fail("Journey consumers must use admission, not a transient User")

    monkeypatch.setattr(
        auth_providers, "_transient_disabled_operator", no_virtual_person
    )

    async def database():
        async with pg_maker() as db:
            yield db

    previous = dict(main.app.dependency_overrides)
    main.app.dependency_overrides[base.get_async_session] = database
    monkeypatch.setattr(base, "async_session_maker", pg_maker)
    retained_id = None
    retained_owner = None
    try:
        async with pg_maker() as db:
            if getattr(request, "param", "fresh") == "retained":
                # A real account-era source is conversion input, never a
                # request identity override or a shortcut to operator access.
                from api_service.db.models import TemporalArtifactRedactionLevel

                retained_owner = str((await _mk_user(db)).id)
                await db.commit()
                artifacts = TemporalArtifactService(TemporalArtifactRepository(db))
                retained, _ = await artifacts.create(
                    principal=retained_owner,
                    content_type="text/plain",
                    redaction_level=TemporalArtifactRedactionLevel.RESTRICTED,
                )
                await artifacts.write_complete(
                    artifact_id=retained.artifact_id,
                    principal=retained_owner,
                    payload=b"retained protected output",
                    content_type="text/plain",
                )
                retained_id = retained.artifact_id
            assert (await run_guarded_upgrade(db, operator_authorized=True)).published
        async with _served_product(main.app) as (operator_url, _backend_url):
            async with httpx.AsyncClient(
                base_url=operator_url, trust_env=False
            ) as client:
                if retained_id is not None:
                    recovered = await client.get(
                        f"/api/artifacts/{retained_id}/download"
                    )
                    assert recovered.status_code == 200, recovered.text
                    assert recovered.content == b"retained protected output"
                yield client
        async with pg_maker() as db:
            people = (await db.execute(select(User.id))).scalars().all()
            assert [str(person) for person in people] == (
                [retained_owner] if retained_owner else []
            )
            if retained_id is not None:
                retained = await db.get(TemporalArtifact, retained_id)
                assert retained.created_by_principal == retained_owner
                assert retained.operator_recovery_granted
    finally:
        main.app.dependency_overrides.clear()
        main.app.dependency_overrides.update(previous)


@pytest.mark.asyncio
async def test_postgres_recurrence_journey_needs_no_virtual_person(
    account_free_product,
    pg_maker,
):
    """Retained definition and accepted run read/retry through their real owner."""
    from api_service.db.models import RecurringWorkflowRunOutcome
    from datetime import UTC, datetime
    import asyncio
    from tools import single_user_journey_checks as journey

    async with pg_maker() as db:
        definition = RecurringWorkflowDefinition(
            name="Retained custom schedule",
            cron="17 * * * *",
            timezone="UTC",
            enabled=False,
            target={"workflowType": "MoonMind.UserWorkflow"},
        )
        db.add(definition)
        await db.flush()
        run = RecurringWorkflowRun(
            definition_id=definition.id,
            scheduled_for=datetime.now(UTC),
            outcome=RecurringWorkflowRunOutcome.PENDING_DISPATCH,
        )
        db.add(run)
        await db.commit()
        definition_id, run_id = definition.id, run.id

    client = account_free_product
    state = {}
    await asyncio.to_thread(
        journey.operator_access, journey.Api(str(client.base_url).rstrip("/")), state
    )
    assert state["operatorAccess"]["allowed"]
    listing = await client.get("/api/recurring-workflows")
    assert listing.status_code == 200, listing.text
    assert listing.json()["items"][0]["name"] == "Retained custom schedule"
    detail = await client.get(f"/api/recurring-workflows/{definition_id}")
    assert detail.status_code == 200, detail.text
    repeated = await client.post(
        f"/api/recurring-workflows/{definition_id}/run",
        headers={"Idempotency-Key": str(run_id)},
    )
    assert repeated.status_code == 201, repeated.text
    assert repeated.json()["id"] == str(run_id)
    runs = await client.get(f"/api/recurring-workflows/{definition_id}/runs")
    assert runs.status_code == 200, runs.text
    assert [item["id"] for item in runs.json()["items"]] == [str(run_id)]
    denied = await client.get(
        "/api/recurring-workflows", headers={"Host": "foreign.example"}
    )
    assert denied.status_code == 401, denied.text
    import httpx
    import api_service.main as main

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=main.app, client=("192.0.2.99", 32000)),
        base_url="http://localhost:7000",
    ) as machine:
        denied = await machine.get(
            "/api/recurring-workflows",
            headers={
                "Authorization": "Bearer synthetic-machine-material",
                "X-Moonmind-Execution-Fanout": "v1",
            },
        )
        assert denied.status_code == 401, denied.text
    async with pg_maker() as db:
        assert (
            await db.get(RecurringWorkflowDefinition, definition_id)
        ).owner_user_id is None
        assert (
            len((await db.execute(select(RecurringWorkflowRun))).scalars().all()) == 1
        )


@pytest.mark.asyncio
async def test_postgres_submission_retry_needs_no_virtual_person(
    account_free_product,
    pg_maker,
):
    """The production idempotency owner preserves work admitted before migration."""
    request_id = uuid.uuid4().hex
    workflow_id = f"mm:{uuid.uuid4()}"
    async with pg_maker() as db:
        db.add(
            TemporalExecutionCanonicalRecord(
                workflow_id=workflow_id,
                run_id=str(uuid.uuid4()),
                entry="user-workflow",
                workflow_type=TemporalWorkflowType.USER_WORKFLOW,
                owner_type=TemporalExecutionOwnerType.SYSTEM,
                owner_id="system",
                create_idempotency_key=request_id,
                state="executing",
                parameters={"retainedIntent": "unchanged"},
            )
        )
        await db.commit()
    body = {
        "type": "workflow",
        "payload": {
            "workflow": {
                "instructions": "Retain the already admitted request.",
                "idempotencyKey": request_id,
            }
        },
    }
    for _ in range(2):
        result = await account_free_product.post("/api/executions", json=body)
        assert result.status_code == 201, result.text
        assert result.json()["workflowId"] == workflow_id
    async with pg_maker() as db:
        records = (
            (await db.execute(select(TemporalExecutionCanonicalRecord))).scalars().all()
        )
        assert len(records) == 1
        assert records[0].parameters == {"retainedIntent": "unchanged"}
        assert records[0].state.value == "executing"


@pytest.mark.asyncio
@pytest.mark.parametrize("account_free_product", ["fresh", "retained"], indirect=True)
async def test_postgres_artifact_journey_needs_no_virtual_person(
    account_free_product,
    pg_maker,
):
    """Actual upload, finalization and download; restricted machine bytes stay gated."""
    from api_service.db.models import TemporalArtifactRedactionLevel
    from moonmind.workflows.temporal.artifacts import (
        TemporalArtifactRepository,
        TemporalArtifactService,
    )

    client = account_free_product
    payload = b"Generated output retained in the instance artifact store.\n"
    created = await client.post("/api/artifacts", json={"content_type": "text/plain"})
    assert created.status_code == 201, created.text
    artifact_id = created.json()["artifact_ref"]["artifact_id"]
    uploaded = await client.put(
        f"/api/artifacts/{artifact_id}/content",
        content=payload,
        headers={"Content-Type": "text/plain"},
    )
    assert uploaded.status_code == 200, uploaded.text
    download = await client.get(f"/api/artifacts/{artifact_id}/download")
    assert download.status_code == 200, download.text
    assert download.content == payload
    async with pg_maker() as db:
        artifact = await db.get(TemporalArtifact, artifact_id)
        # Account-free writes keep the retained local-operator scope.
        assert artifact.created_by_principal == instance_operator_principal()
        service = TemporalArtifactService(TemporalArtifactRepository(db))
        machine_owner = f"workflow:{uuid.uuid4()}"
        restricted, _ = await service.create(
            principal=machine_owner,
            content_type="text/plain",
            redaction_level=TemporalArtifactRedactionLevel.RESTRICTED,
        )
        await service.write_complete(
            artifact_id=restricted.artifact_id,
            principal=machine_owner,
            payload=b"restricted output",
            content_type="text/plain",
        )
        restricted_id = restricted.artifact_id
    denied = await client.get(f"/api/artifacts/{restricted_id}/download")
    assert denied.status_code == 403, denied.text
    denied = await client.get(
        f"/api/artifacts/{artifact_id}/download",
        headers={
            "Authorization": "Bearer synthetic-machine-material",
            "X-Moonmind-Execution-Fanout": "v1",
        },
    )
    assert denied.status_code == 401, denied.text


@pytest.mark.asyncio
@pytest.mark.parametrize("explicit_loopback", [False, True])
async def test_postgres_existing_journey_checks_served_access(
    pg_maker, monkeypatch, explicit_loopback,
):
    """Existing HTTP journey checks consume the production app over real TCP."""
    import asyncio
    from urllib.parse import urlsplit
    from api_service.db import base
    from api_service.services.single_user_conversion import run_guarded_upgrade
    from moonmind.config.settings import settings
    from moonmind.security import auth_modes_4120
    from tools import single_user_journey_checks as journey
    import api_service.main as main

    monkeypatch.setattr(auth_modes_4120, "_ACTIVE_PRODUCTION_MODE", "disabled")
    monkeypatch.setattr(settings.feature_flags, "preset_catalog", True)

    async def database():
        async with pg_maker() as db:
            yield db

    previous = dict(main.app.dependency_overrides)
    main.app.dependency_overrides[base.get_async_session] = database
    monkeypatch.setattr(base, "async_session_maker", pg_maker)
    try:
        async with pg_maker() as db:
            assert (await run_guarded_upgrade(db, operator_authorized=True)).published
        async with _served_product(main.app) as (operator_url, backend_url):
            monkeypatch.setenv("MOONMIND_PUBLIC_BASE_URL", operator_url if explicit_loopback else "")
            state = {}
            await asyncio.to_thread(journey.operator_access, journey.Api(operator_url), state)
            assert state["operatorAccess"]["allowed"]
            denied_state = {}
            await asyncio.to_thread(journey.direct_backend_access, journey.Api(backend_url),
                                    denied_state, operator_host=urlsplit(operator_url).netloc)
            assert denied_state["directBackendAccess"]["denied"]
        async with pg_maker() as db:
            assert (await db.execute(select(User))).scalars().all() == []
            assert (await db.execute(select(Preset))).scalars().all() == []
    finally:
        main.app.dependency_overrides.clear()
        main.app.dependency_overrides.update(previous)


@pytest.mark.asyncio
async def test_postgres_compose_host_transport_uses_kernel_gateway(pg_maker, monkeypatch):
    """Qualify the adapter with real container routing and production consumers.

    ASGI controls the peer for the host-NAT case; the full Compose journey owns
    actual host port publication. No invented gateway or admission override is used.
    """
    import httpx
    from api_service.db import base
    from api_service.operator_admission import _local_compose_host_gateway
    from api_service.services.single_user_conversion import run_guarded_upgrade
    from moonmind.security import auth_modes_4120
    import api_service.main as main

    if not Path("/.dockerenv").exists():
        pytest.skip("Compose gateway qualification requires the Docker test image")
    monkeypatch.setattr(auth_modes_4120, "_ACTIVE_PRODUCTION_MODE", "disabled")
    monkeypatch.setenv("MOONMIND_PUBLIC_BASE_URL", "")
    monkeypatch.setenv("MOONMIND_DEPLOYMENT_PROJECT_NAME", "moonmind-test-operator-access")
    monkeypatch.setenv("MOONMIND_API_PUBLISH_HOST", "127.0.0.1")
    gateway = _local_compose_host_gateway()
    kernel_defaults = [line for line in Path("/proc/net/route").read_text().splitlines()[1:]
                       if len(line.split()) >= 2 and line.split()[1] == "00000000"]
    if not kernel_defaults:
        assert gateway is None
        pytest.skip("Managed job has no kernel default route; positive host publication is exercised by Compose CI")
    assert gateway, "The test container must expose one kernel default gateway"

    async def database():
        async with pg_maker() as db:
            yield db

    previous = dict(main.app.dependency_overrides)
    main.app.dependency_overrides[base.get_async_session] = database
    try:
        async with pg_maker() as db:
            assert (await run_guarded_upgrade(db, operator_authorized=True)).published
        async with httpx.AsyncClient(transport=httpx.ASGITransport(
            app=main.app, client=(gateway, 32000)), base_url="http://localhost:7000") as host:
            assert (await host.get("/api/v1/settings/catalog")).status_code == 200
            assert (await host.get("/api/v1/settings/catalog", headers={"Host": "foreign.invalid"})).status_code == 401
            monkeypatch.setenv("MOONMIND_API_PUBLISH_HOST", "0.0.0.0")
            assert (await host.get("/api/v1/settings/catalog")).status_code == 401
            monkeypatch.setenv("MOONMIND_API_PUBLISH_HOST", "127.0.0.1")
            monkeypatch.delenv("MOONMIND_DEPLOYMENT_PROJECT_NAME")
            assert (await host.get("/api/v1/settings/catalog")).status_code == 401
    finally:
        main.app.dependency_overrides.clear()
        main.app.dependency_overrides.update(previous)
