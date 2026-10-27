"""Real PostgreSQL plans and reversible DDL for admission/journal lookups."""

import importlib
import json
import time
from datetime import UTC, datetime
from uuid import uuid4

import pytest
import pytest_asyncio
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import event, insert, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from api_service.db.models import OmnigentBridgeSessionEvent, TemporalArtifact
from api_service.services import single_user_conversion as conversion
from tests.artifact_access import access_for_test

pytestmark = [pytest.mark.integration, pytest.mark.integration_ci, pytest.mark.asyncio]

MIGRATION = importlib.import_module(
    "api_service.migrations.versions.401_artifact_recovery_lookup_indexes"
)
RECOVERY_INDEX = "ix_temporal_artifacts_recovery_owner"
JOURNAL_INDEX = "ix_omnigent_bridge_session_events_artifact_ref"


@pytest_asyncio.fixture
async def lookup_database(control_plane_postgres_url):
    schema = f"recovery_lookup_{uuid4().hex}"
    engine = create_async_engine(
        control_plane_postgres_url,
        connect_args={
            "server_settings": {"search_path": schema, "statement_timeout": "15000"}
        },
    )
    async with engine.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        await connection.run_sync(TemporalArtifact.__table__.create)
        await connection.run_sync(OmnigentBridgeSessionEvent.__table__.create)
    try:
        yield engine
    finally:
        async with engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await engine.dispose()


async def migrate(connection, direction):
    def apply(sync_connection):
        with Operations.context(MigrationContext.configure(sync_connection)):
            getattr(MIGRATION, direction)()

    await connection.run_sync(apply)


async def recovery_statement(session, engine):
    captured = []

    def capture(_connection, _cursor, statement, parameters, *_args):
        if statement.startswith("SELECT temporal_artifacts.artifact_id"):
            captured.append((statement, parameters))

    event.listen(engine.sync_engine, "before_cursor_execute", capture)
    try:
        pending = await conversion._has_pending_artifact_recovery(session)
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", capture)
    return pending, captured.pop()


async def generic_plan(connection, statement, parameters):
    # Force the plan used after asyncpg/PostgreSQL start reusing a prepared
    # statement; a custom plan alone can conceal parameterized index constants.
    assert all(isinstance(value, int) for value in parameters)
    await connection.exec_driver_sql("SET LOCAL plan_cache_mode = force_generic_plan")
    await connection.exec_driver_sql("PREPARE recovery_lookup AS " + statement)
    try:
        arguments = ", ".join(str(value) for value in parameters)
        result = await connection.exec_driver_sql(
            "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) EXECUTE recovery_lookup("
            + arguments
            + ")"
        )
        return result.scalar_one()[0]
    finally:
        await connection.exec_driver_sql("DEALLOCATE recovery_lookup")


def plan_nodes(plan):
    yield plan
    for child in plan.get("Plans", []):
        yield from plan_nodes(child)


def assert_indexed(plan, name):
    nodes = list(plan_nodes(plan["Plan"]))
    assert any(node.get("Index Name") == name for node in nodes), nodes
    assert not any(node["Node Type"] == "Seq Scan" for node in nodes), nodes


@pytest.mark.parametrize("auth_provider", ["disabled", "oidc"])
async def test_negative_recovery_probe_uses_migrated_index_with_generic_plans(
    lookup_database, monkeypatch, auth_provider
):
    monkeypatch.setenv("AUTH_PROVIDER", auth_provider)
    monkeypatch.delenv("DEFAULT_USER_ID", raising=False)
    async with lookup_database.begin() as connection:
        await migrate(connection, "downgrade")
        await connection.execute(
            insert(TemporalArtifact),
            [
                {
                    "artifact_id": f"history-{number}",
                    "storage_key": f"history-{number}",
                    "created_by_principal": (
                        conversion.DEPLOYMENT_SUBJECT_ID if number % 2 else "operator"
                    ),
                    "operator_recovery_granted": auth_provider != "disabled"
                    and bool(number % 2),
                    "metadata_json": {"retained": "x" * 128},
                }
                for number in range(12000)
            ],
        )
        await connection.exec_driver_sql("ANALYZE temporal_artifacts")
    async with AsyncSession(lookup_database) as session:
        pending, (statement, parameters) = await recovery_statement(
            session, lookup_database
        )
        assert not pending
        connection = await session.connection()
        before = await generic_plan(connection, statement, parameters)
        await migrate(connection, "upgrade")
        await connection.exec_driver_sql("ANALYZE temporal_artifacts")
        after = await generic_plan(connection, statement, parameters)
        assert_indexed(after, RECOVERY_INDEX)
        assert after["Plan"]["Actual Rows"] == 0
        assert (
            after["Plan"]["Shared Hit Blocks"] + after["Plan"]["Shared Read Blocks"]
            < 32
        )
        print(
            json.dumps(
                {
                    "probe": "artifact_recovery",
                    "auth": auth_provider,
                    "rows": 12000,
                    "before_ms": before["Execution Time"],
                    "after_ms": after["Execution Time"],
                    "after_blocks": after["Plan"]["Shared Hit Blocks"]
                    + after["Plan"]["Shared Read Blocks"],
                }
            )
        )
        # Committed new work cannot hide behind cached process readiness.
        session.add(
            TemporalArtifact(
                artifact_id="late-owner",
                storage_key="late-owner",
                created_by_principal=str(uuid4()),
            )
        )
        await session.commit()
    async with AsyncSession(lookup_database) as restarted:
        assert await conversion._has_pending_artifact_recovery(restarted)
        await restarted.execute(
            update(TemporalArtifact)
            .where(TemporalArtifact.artifact_id == "late-owner")
            .values(operator_recovery_granted=True)
        )
        await restarted.commit()
        assert not await conversion._has_pending_artifact_recovery(restarted)


async def test_journal_reference_lookup_uses_migrated_index(lookup_database):
    async with lookup_database.begin() as connection:
        await migrate(connection, "downgrade")
        await connection.execute(
            insert(OmnigentBridgeSessionEvent),
            [
                {
                    "event_id": f"event-{number}",
                    "bridge_session_id": "retained-session",
                    "sequence": number,
                    "deduplication_key": str(number),
                    "timestamp": datetime.now(UTC),
                    "direction": "host_to_moonmind",
                    "event_type": "message",
                    "artifact_ref": f"artifact:journal-{number}",
                }
                for number in range(12000)
            ],
        )
        await connection.exec_driver_sql("ANALYZE omnigent_bridge_session_events")
        query = "SELECT event_id FROM omnigent_bridge_session_events WHERE artifact_ref IN ('missing', 'artifact:missing', 'missing-alias') LIMIT 1"
        before = (
            await connection.exec_driver_sql(
                "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + query
            )
        ).scalar_one()[0]
        await migrate(connection, "upgrade")
        after = (
            await connection.exec_driver_sql(
                "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + query
            )
        ).scalar_one()[0]
        assert_indexed(after, JOURNAL_INDEX)
        print(
            json.dumps(
                {
                    "probe": "journal_reference",
                    "rows": 12000,
                    "before_ms": before["Execution Time"],
                    "after_ms": after["Execution Time"],
                }
            )
        )
        found = await connection.scalar(
            select(OmnigentBridgeSessionEvent.event_id)
            .where(
                OmnigentBridgeSessionEvent.artifact_ref.in_(
                    ("missing", "artifact:journal-11999")
                )
            )
            .limit(1)
        )
        assert found == "event-11999"


async def test_index_migration_roundtrip_preserves_grants_and_references(
    lookup_database,
):
    async with lookup_database.begin() as connection:
        await migrate(connection, "downgrade")
        await connection.execute(
            insert(TemporalArtifact),
            [
                {
                    "artifact_id": "retained",
                    "storage_key": "immutable-blob",
                    "created_by_principal": str(uuid4()),
                    "operator_recovery_granted": True,
                    "metadata_json": {"quarantine": True},
                },
                {
                    "artifact_id": "unconverted",
                    "storage_key": "unconverted",
                    "created_by_principal": str(uuid4()),
                    "operator_recovery_granted": False,
                    "metadata_json": {},
                },
            ],
        )
        await connection.execute(
            insert(OmnigentBridgeSessionEvent).values(
                event_id="retained-event",
                bridge_session_id="retained-session",
                sequence=1,
                deduplication_key="1",
                timestamp=datetime.now(UTC),
                direction="host_to_moonmind",
                event_type="message",
                artifact_ref="artifact:retained",
                metadata_={"evidence": True},
            )
        )
        before_artifacts = (
            await connection.execute(
                select(TemporalArtifact.__table__).order_by(
                    TemporalArtifact.artifact_id
                )
            )
        ).all()
        before_events = (
            await connection.execute(select(OmnigentBridgeSessionEvent.__table__))
        ).all()
        for direction in ("upgrade", "downgrade", "upgrade"):
            await migrate(connection, direction)
            assert (
                await connection.execute(
                    select(TemporalArtifact.__table__).order_by(
                        TemporalArtifact.artifact_id
                    )
                )
            ).all() == before_artifacts
            assert (
                await connection.execute(select(OmnigentBridgeSessionEvent.__table__))
            ).all() == before_events
            installed = set(
                (
                    await connection.execute(
                        text(
                            "SELECT indexname FROM pg_indexes WHERE schemaname = current_schema()"
                        )
                    )
                ).scalars()
            )
            assert (RECOVERY_INDEX in installed) is (direction == "upgrade")
            assert (JOURNAL_INDEX in installed) is (direction == "upgrade")


async def test_retained_history_startup_admission_and_retention(
    lookup_database, monkeypatch, tmp_path, record_property
):
    """Bounded real startup/request/schedule/sweep composition for #4991.

    Historical rows are retained tombstones. The admitted inputs are produced
    and read by the artifact service; only the Temporal schedule RPC is a double.
    No completion ledger or artifact availability result is fabricated.
    """
    from api_service.db import models
    from moonmind.config.settings import settings
    from tests.integration.reliability.test_schedule_input_retention_postgres import (
        adapter,
        write_schedule,
    )
    from tests.unit.workflows.temporal.test_saved_work_retention_4017 import _service
    from tests.unit.workflows.temporal.test_schedule_input_retention import (
        FUTURE,
        expired_input,
    )

    monkeypatch.setenv("AUTH_PROVIDER", "disabled")
    monkeypatch.delenv("DEFAULT_USER_ID", raising=False)
    monkeypatch.setattr(settings.workflow, "default_runtime", "codex_cli")
    async with lookup_database.begin() as connection:
        await connection.run_sync(models.Base.metadata.create_all)
    async with AsyncSession(lookup_database, expire_on_commit=False) as session:
        completion = await conversion.run_guarded_upgrade(
            session, operator_authorized=True
        )
        assert completion.published

    size = 12000
    tombstoned = datetime(2020, 1, 1, tzinfo=UTC)
    async with lookup_database.begin() as connection:
        await connection.execute(
            insert(TemporalArtifact),
            [
                {
                    "artifact_id": f"history-{n}",
                    "storage_key": f"history-{n}",
                    "created_by_principal": "service:retained-producer",
                    "status": models.TemporalArtifactStatus.DELETED,
                    "deleted_at": tombstoned,
                    "hard_deleted_at": tombstoned,
                    "tombstoned_at": tombstoned,
                    "metadata_json": {"retained": "x" * 128},
                }
                for n in range(size)
            ],
        )
        await connection.exec_driver_sql("ANALYZE temporal_artifacts")

    captured = []

    def capture(_connection, _cursor, statement, parameters, *_args):
        if statement.startswith("SELECT "):
            captured.append((statement, parameters))

    event.listen(lookup_database.sync_engine, "before_cursor_execute", capture)
    try:
        started = time.monotonic()
        async with AsyncSession(lookup_database) as restarted:
            replay = await conversion.run_guarded_upgrade(
                restarted, operator_authorized=True
            )
            assert replay.published and replay.digest == completion.digest
            assert not restarted.in_transaction()
        startup_seconds = time.monotonic() - started
        startup_queries = len(captured)
        assert startup_queries <= 20, captured
        probe = next(
            item
            for item in captured
            if item[0].startswith("SELECT temporal_artifacts.artifact_id")
        )
        async with lookup_database.begin() as connection:
            startup_plan = await generic_plan(connection, *probe)
        assert_indexed(startup_plan, RECOVERY_INDEX)
        assert startup_plan["Plan"]["Actual Rows"] == 0
        assert (
            startup_plan["Plan"]["Shared Hit Blocks"]
            + startup_plan["Plan"]["Shared Read Blocks"]
            < 32
        )
        # The ordinary request admission guard reads the same certified state.
        async with AsyncSession(lookup_database) as request:
            assert (await conversion.startup_guard_decision(request)).eligible

        async with AsyncSession(lookup_database, expire_on_commit=False) as session:
            service = _service(session, tmp_path)
            saved, first, second = [await expired_input(service) for _ in range(3)]
            temporal = adapter()
            await write_schedule(session, saved.artifact_id, temporal, tmp_path)
            temporal.create_schedule.assert_awaited_once()
            ids = [row.artifact_id for row in (saved, first, second)]
            commit = service._repository.commit
            unlocked_checkpoints = []

            async def observe_commit():
                await commit()
                assert not session.in_transaction()
                # Independent connection can lock every input at each page
                # boundary. A transaction retained across candidates fails NOWAIT.
                async with AsyncSession(lookup_database) as observer:
                    rows = (
                        await observer.scalars(
                            select(TemporalArtifact)
                            .where(TemporalArtifact.artifact_id.in_(ids))
                            .with_for_update(nowait=True)
                        )
                    ).all()
                    unlocked_checkpoints.append(
                        sum(
                            row.status == models.TemporalArtifactStatus.DELETED
                            for row in rows
                        )
                    )

            monkeypatch.setattr(service._repository, "commit", observe_commit)
            captured.clear()
            started = time.monotonic()
            for _ in range(2):
                result = await service.sweep_lifecycle(
                    principal="service:sweeper", now=FUTURE, limit=1
                )
                assert result.soft_deleted_count == 1
            again = await service.sweep_lifecycle(
                principal="service:sweeper", now=FUTURE, limit=1
            )
            assert again.soft_deleted_count == 0
            assert (
                await service.read(
                    artifact_id=saved.artifact_id, access=access_for_test("operator")
                )
            )[1] == b'{"instructions":"retained"}'
            sweep_seconds = time.monotonic() - started
            assert 1 in unlocked_checkpoints and 2 in unlocked_checkpoints
            expiry_query = next(
                item
                for item in captured
                if "ORDER BY temporal_artifacts.expires_at" in item[0]
            )
            # End the read transaction before plan observation on another connection.
            await session.rollback()
        async with lookup_database.begin() as connection:
            expiry_plan = (
                await connection.exec_driver_sql(
                    "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + expiry_query[0],
                    expiry_query[1],
                )
            ).scalar_one()[0]
        assert any(
            node.get("Index Name") == "ix_temporal_artifacts_expires_at"
            for node in plan_nodes(expiry_plan["Plan"])
        )
        assert (
            expiry_plan["Plan"]["Shared Hit Blocks"]
            + expiry_plan["Plan"]["Shared Read Blocks"]
            < 256
        )
        evidence = {
            "fixture_rows": size,
            "history_kind": "artifact_tombstones",
            "produced_inputs": 3,
            "page_limit": 1,
            "startup_queries": startup_queries,
            "startup_seconds": startup_seconds,
            "sweep_seconds": sweep_seconds,
            "unlocked_checkpoints": unlocked_checkpoints,
            "startup_plan": startup_plan,
            "expiry_plan": expiry_plan,
        }
        record_property("retained_scale", json.dumps(evidence))
        print(json.dumps(evidence))
    finally:
        event.remove(lookup_database.sync_engine, "before_cursor_execute", capture)
