"""Measure mounted operator admission against retained PostgreSQL history."""

import asyncio
import importlib
import json
import sys
from contextlib import asynccontextmanager
from contextvars import ContextVar
from time import perf_counter
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import event, insert, literal, select, text, update
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import (
    Preset,
    PresetFavorite,
    PresetRecent,
    SettingsAuditEvent,
    SettingsOverride,
    TemporalArtifact,
)
from api_service.services import single_user_conversion as conversion
from tests.helpers.account_source import create_source_schema
from tests.integration import test_single_user_conversion_postgres_4346 as postgres_fixtures

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]
conversion_postgres_url = postgres_fixtures.conversion_postgres_url


@pytest_asyncio.fixture
async def admission_database(conversion_postgres_url, monkeypatch, tmp_path):
    schema = "admission_scale_" + uuid4().hex
    engine = create_async_engine(
        conversion_postgres_url,
        connect_args={"server_settings": {
            "search_path": schema, "statement_timeout": "15000",
        }},
    )
    async with engine.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        await connection.run_sync(create_source_schema)
    monkeypatch.setenv("AUTH_PROVIDER", "disabled")
    monkeypatch.setenv("MOONMIND_PUBLIC_BASE_URL", "")
    monkeypatch.delenv("DEFAULT_USER_ID", raising=False)
    # Match the existing deployment-surface admission fixture. Only asset
    # availability is controlled; the mounted auth/conversion path stays real.
    # This test measures HTML admission, not JavaScript behavior.
    dist = tmp_path / "dist"
    (dist / ".vite").mkdir(parents=True)
    (dist / "assets").mkdir()
    (dist / "assets" / "dashboard.js").write_text("export {};")
    manifest = dist / ".vite" / "manifest.json"
    manifest.write_text(json.dumps({"entrypoints/dashboard.tsx": {
        "file": "assets/dashboard.js", "isEntry": True,
    }}))
    monkeypatch.setenv("VITE_MANIFEST_PATH", str(manifest))
    monkeypatch.delenv("MOONMIND_UI_DEV_SERVER_URL", raising=False)
    try:
        yield sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    finally:
        async with engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await engine.dispose()


async def seed_history(sessions, count, revision):
    async with sessions() as session:
        for offset in range(0, count, 1000):
            await session.execute(insert(conversion.SingleUserConversionRun), [
                {"preflight_digest": f"historical-{number}", "status": "complete",
                 "result_json": {"schema_revision": revision,
                                 "published": True,
                                 "production_transforms_completed": False}}
                for number in range(offset, min(offset + 1000, count))
            ])
        await session.commit()
        # The real conversion owner writes the current certificate. Historical
        # generic/custom completions must never stand in for that evidence.
        result = await conversion.run_guarded_upgrade(session, operator_authorized=True)
        assert result.published, result.to_sanitized_dict()

        for offset in range(0, count, 1000):
            numbers = range(offset, min(offset + 1000, count))
            presets = [{"id": uuid4(), "slug": f"retained-{number}",
                        "title": f"Retained preset {number}",
                        "description": "Synthetic instance-authored fixture",
                        "inputs_schema": [],
                        "steps": [{"instructions": "Use controlled fixture inputs"}]}
                       for number in numbers]
            await session.execute(insert(Preset), presets)
            await session.execute(insert(PresetFavorite), [
                {"template_id": preset["id"]} for preset in presets
            ])
            await session.execute(insert(PresetRecent), [
                {"template_id": preset["id"]} for preset in presets
            ])
            await session.execute(insert(TemporalArtifact), [
                {"artifact_id": f"retained-{number}", "storage_key": f"fixture/{number}",
                 "created_by_principal": "operator", "metadata_json": {"retained": True}}
                for number in numbers
            ])
            await session.execute(insert(SettingsAuditEvent), [
                {"event_type": "configuration_changed", "key": "workflow.context_budget_tokens",
                 "old_value_json": 2048, "new_value_json": 4096}
                for _ in numbers
            ])
        await session.commit()
        for table in ("single_user_conversion_runs", "presets", "preset_favorites",
                      "preset_recents", "temporal_artifacts", "settings_audit_events"):
            await session.execute(text("ANALYZE " + table))
        await session.commit()


async def generic_plan(connection, statement, parameters):
    name = "admission_probe_" + uuid4().hex
    await connection.exec_driver_sql("SET LOCAL plan_cache_mode = force_generic_plan")
    await connection.exec_driver_sql("PREPARE " + name + " AS " + statement)
    try:
        arguments = ", ".join(str(literal(value).compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True},
        )) for value in parameters)
        return (await connection.exec_driver_sql(
            "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) EXECUTE "
            + name + "(" + arguments + ")"
        )).scalar_one()[0]
    finally:
        await connection.exec_driver_sql("DEALLOCATE " + name)


def nodes(plan):
    yield plan
    for child in plan.get("Plans", []):
        yield from nodes(child)


@asynccontextmanager
async def mounted_operator(sessions, monkeypatch):
    import api_service.main as main
    from api_service.db import base

    async def database():
        async with sessions() as session:
            yield session

    previous = dict(main.app.dependency_overrides)
    main.app.dependency_overrides[base.get_async_session] = database
    monkeypatch.setattr(base, "async_session_maker", sessions)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=main.app, client=("127.0.0.1", 32000)),
            base_url="http://localhost:7000",
        ) as client:
            yield client
    finally:
        main.app.dependency_overrides.clear()
        main.app.dependency_overrides.update(previous)


async def assert_admission(client, expected):
    responses = await asyncio.gather(*(
        client.get(path) for path in ("/api/v1/settings/catalog", "/workflows")
    ))
    assert [response.status_code for response in responses] == [expected, expected], [
        response.text[:1000] for response in responses
    ]
    if expected == 503:
        assert all(response.json()["detail"] == "conversion_required" for response in responses)


async def test_warm_requests_observe_grant_revocation_and_owner_changes(
    admission_database, monkeypatch,
):
    await seed_history(admission_database, 0, "older-conversion")
    original_owner = str(uuid4())
    async with admission_database() as session:
        session.add(TemporalArtifact(
            artifact_id="restored", storage_key="original/retained-key",
            created_by_principal=original_owner, operator_recovery_granted=True,
            metadata_json={"original": "preserved"},
        ))
        await session.commit()
    async with mounted_operator(admission_database, monkeypatch) as client:
        await assert_admission(client, 200)
        async with admission_database() as writer:
            await writer.execute(update(TemporalArtifact).where(
                TemporalArtifact.artifact_id == "restored",
            ).values(operator_recovery_granted=False))
            await writer.commit()
        await assert_admission(client, 503)

        # A current machine-owned row needs no human recovery grant. Changing
        # that same row back to a retained human is immediately visible too.
        async with admission_database() as writer:
            await writer.execute(update(TemporalArtifact).where(
                TemporalArtifact.artifact_id == "restored",
            ).values(created_by_principal="operator"))
            await writer.commit()
        await assert_admission(client, 200)
        async with admission_database() as writer:
            await writer.execute(update(TemporalArtifact).where(
                TemporalArtifact.artifact_id == "restored",
            ).values(created_by_principal=original_owner))
            await writer.commit()
        await assert_admission(client, 503)
    async with admission_database() as session:
        row = await session.get(TemporalArtifact, "restored")
        assert row.created_by_principal == original_owner
        assert row.storage_key == "original/retained-key"
        assert row.metadata_json == {"original": "preserved"}
        assert row.operator_recovery_granted is False
        assert len((await session.execute(select(
            conversion.SingleUserConversionRun,
        ))).scalars().all()) == 1


@pytest.mark.parametrize("resource", ["preset", "settings"])
async def test_warm_requests_observe_late_instance_conversion_work(
    admission_database, monkeypatch, resource,
):
    from api_service.db.models import PresetScopeType

    await seed_history(admission_database, 1, "older-conversion")
    owner = uuid4()
    async with mounted_operator(admission_database, monkeypatch) as client:
        await assert_admission(client, 200)
        async with admission_database() as writer:
            if resource == "preset":
                await writer.execute(update(Preset).where(
                    Preset.slug == "retained-0",
                ).values(scope_type=PresetScopeType.PERSONAL, scope_ref=str(owner)))
            else:
                writer.add(SettingsOverride(
                    scope="user", user_id=owner,
                    key="workflow.context_budget_tokens", value_json=4096,
                ))
            await writer.commit()
        await assert_admission(client, 503)
        async with admission_database() as session:
            if resource == "preset":
                row = (await session.execute(select(Preset).where(
                    Preset.slug == "retained-0",
                ))).scalar_one()
                assert row.scope_ref == str(owner)
                assert row.steps == [{"instructions": "Use controlled fixture inputs"}]
            else:
                row = (await session.execute(select(SettingsOverride))).scalar_one()
                assert row.user_id == owner and row.value_json == 4096


async def test_reserved_deployment_scope_is_rechecked_on_each_request(
    admission_database, monkeypatch,
):
    await seed_history(admission_database, 0, "older-conversion")
    async with admission_database() as writer:
        writer.add(TemporalArtifact(
            artifact_id="reserved-scope", storage_key="retained/reserved",
            created_by_principal=conversion.DEPLOYMENT_SUBJECT_ID,
        ))
        await writer.commit()
    async with mounted_operator(admission_database, monkeypatch) as client:
        await assert_admission(client, 200)
        monkeypatch.setenv("DEFAULT_USER_ID", str(uuid4()))
        await assert_admission(client, 503)
        monkeypatch.delenv("DEFAULT_USER_ID")
        await assert_admission(client, 200)


@pytest.mark.parametrize("state", ["missing", "incomplete", "refused"])
async def test_nonproduction_completion_cannot_admit_protected_routes(
    admission_database, monkeypatch, state,
):
    if state != "missing":
        await seed_history(admission_database, 0, "older-conversion")
        async with admission_database() as writer:
            row = (await writer.execute(select(conversion.SingleUserConversionRun))).scalar_one()
            if state == "incomplete":
                row.result_json = dict(row.result_json, production_transforms_completed=False)
            else:
                row.status = "refused"
            await writer.commit()
    async with mounted_operator(admission_database, monkeypatch) as client:
        await assert_admission(client, 503)


async def _fresh_process_probe(url, schema):
    """Child process entry: actual mounted routes, fresh app and DB pool."""
    import api_service.main as main
    from api_service.db import base

    engine = create_async_engine(url, connect_args={
        "server_settings": {"search_path": schema, "statement_timeout": "15000"},
    })
    sessions = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def database():
        async with sessions() as session:
            yield session

    main.app.dependency_overrides[base.get_async_session] = database
    base.async_session_maker = sessions
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=main.app, client=("127.0.0.1", 32000)),
            base_url="http://localhost:7000",
        ) as client:
            statuses = [(await client.get(path)).status_code for path in (
                "/api/v1/settings/catalog", "/workflows",
            )]
        print("FRESH_ADMISSION " + json.dumps(statuses))
    finally:
        await engine.dispose()


async def test_new_api_process_observes_shared_database_revocation(admission_database):
    await seed_history(admission_database, 0, "older-conversion")
    async with admission_database() as session:
        schema = (await session.execute(text("SELECT current_schema()"))).scalar_one()
        session.add(TemporalArtifact(
            artifact_id="cross-process", storage_key="retained/original",
            created_by_principal=str(uuid4()), operator_recovery_granted=True,
        ))
        await session.commit()

    async def probe():
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-c",
            "import asyncio,sys; from tests.integration.test_protected_admission_scale "
            "import _fresh_process_probe; asyncio.run(_fresh_process_probe(*sys.argv[1:]))",
            admission_database.kw["bind"].url.render_as_string(hide_password=False), schema,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=45)
        except TimeoutError:
            process.kill()
            await process.wait()
            raise
        assert process.returncode == 0, stderr.decode()[-3000:]
        result = [line.removeprefix("FRESH_ADMISSION ") for line in stdout.decode().splitlines()
                  if line.startswith("FRESH_ADMISSION ")]
        assert len(result) == 1, stdout.decode()[-3000:]
        return json.loads(result[0])

    assert await probe() == [200, 200]
    async with admission_database() as writer:
        await writer.execute(update(TemporalArtifact).where(
            TemporalArtifact.artifact_id == "cross-process",
        ).values(operator_recovery_granted=False))
        await writer.commit()
    assert await probe() == [503, 503]


async def test_migrated_certificate_lookup_is_bounded_and_preserves_evidence(
    admission_database,
):
    migration = importlib.import_module(
        "api_service.migrations.versions.403_conversion_completion_lookup",
    )
    await seed_history(admission_database, 12000, conversion.SCHEMA_REVISION)
    engine = admission_database.kw["bind"]

    async def migrate(session, direction):
        connection = await session.connection()

        def apply(sync_connection):
            with Operations.context(MigrationContext.configure(sync_connection)):
                getattr(migration, direction)()

        await connection.run_sync(apply)
        await session.commit()

    async def evidence(session):
        return (await session.execute(text(
            "SELECT count(*), sum(hashtextextended(row_to_json(r)::text, 0)::numeric) "
            "FROM single_user_conversion_runs r"
        ))).one()

    statements = []

    def capture(_connection, _cursor, statement, parameters, *_args):
        if statement.startswith("SELECT single_user_conversion_runs."):
            statements.append((statement, parameters))

    async with admission_database() as session:
        original = await evidence(session)
        await migrate(session, "downgrade")
        assert await evidence(session) == original
        await migrate(session, "upgrade")
        assert await evidence(session) == original

        event.listen(engine.sync_engine, "before_cursor_execute", capture)
        try:
            assert await conversion.completed_guarded_upgrade(session) is not None
        finally:
            event.remove(engine.sync_engine, "before_cursor_execute", capture)
        assert len(statements) == 1
        statement, parameters = statements[0]
        connection = await session.connection()
        positive = await generic_plan(connection, statement, parameters)
        assert any(node.get("Index Name") == "ix_single_user_conversion_published_schema"
                   for node in nodes(positive["Plan"]))
        assert positive["Plan"]["Shared Hit Blocks"] + positive["Plan"]["Shared Read Blocks"] < 32

        # Restoring history without its certified completion cannot become ready,
        # and the absent-result path must not walk the custom completion ledger.
        await session.execute(text(
            "DELETE FROM single_user_conversion_runs "
            "WHERE CAST(result_json -> 'production_transforms_completed' AS TEXT) = 'true'"
        ))
        await session.commit()
        assert await conversion.completed_guarded_upgrade(session) is None
        connection = await session.connection()
        negative = await generic_plan(connection, statement, parameters)
        assert negative["Plan"]["Actual Rows"] == 0
        assert any(node.get("Index Name") == "ix_single_user_conversion_published_schema"
                   for node in nodes(negative["Plan"]))
        assert negative["Plan"]["Shared Hit Blocks"] + negative["Plan"]["Shared Read Blocks"] < 32

        restored = await evidence(session)
        await migrate(session, "downgrade")
        assert await evidence(session) == restored
        await migrate(session, "upgrade")
        assert await evidence(session) == restored


@pytest.mark.parametrize(("history", "revision"), [
    (0, "older-conversion"), (12000, "older-conversion"),
    (12000, conversion.SCHEMA_REVISION),
], ids=["empty", "previous-schema", "current-custom"])
async def test_mounted_protected_admission_cost(
    admission_database, monkeypatch, tmp_path, history, revision,
):
    import api_service.main as main
    from api_service.db import base

    await seed_history(admission_database, history, revision)
    engine = admission_database.kw["bind"]
    tag = ContextVar("request", default=None)
    queries = []
    timings = []

    def capture(_connection, _cursor, statement, parameters, *_args):
        if tag.get() is not None:
            queries.append((tag.get(), statement, parameters))

    async def database():
        async with admission_database() as session:
            yield session

    previous = dict(main.app.dependency_overrides)
    main.app.dependency_overrides[base.get_async_session] = database
    monkeypatch.setattr(base, "async_session_maker", admission_database)
    event.listen(engine.sync_engine, "before_cursor_execute", capture)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=main.app, client=("127.0.0.1", 32000)),
            base_url="http://localhost:7000",
        ) as client:
            async def request(number, path):
                token = tag.set(number)
                started = perf_counter()
                try:
                    response = await client.get(path)
                    assert response.status_code == 200, response.text[:1000]
                    timings.append({"request": number, "path": path,
                                    "milliseconds": (perf_counter() - started) * 1000})
                finally:
                    tag.reset(token)

            await request(0, "/api/v1/settings/catalog")
            await request(1, "/workflows")
            for number in range(2, 6):
                await request(number, "/api/v1/settings/catalog")
            await asyncio.gather(*(request(number, "/api/v1/settings/catalog")
                                   for number in range(6, 10)))
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", capture)
        main.app.dependency_overrides.clear()
        main.app.dependency_overrides.update(previous)

    plans = []
    async with admission_database() as session:
        connection = await session.connection()
        for number, statement, parameters in queries:
            if number != 0 or not statement.startswith("SELECT") or "LIMIT" not in statement:
                continue
            plan = await generic_plan(connection, statement, parameters)
            scans = [node for node in nodes(plan["Plan"]) if node.get("Relation Name")]
            plans.append({"relations": [{key: node.get(key) for key in (
                "Relation Name", "Node Type", "Actual Rows", "Actual Loops",
                "Rows Removed by Filter", "Shared Hit Blocks", "Shared Read Blocks",
            )} for node in scans], "milliseconds": plan["Execution Time"]})
    report = {"history_per_kind": history, "history_revision": revision, "requests": timings,
              "query_counts": [sum(number == item[0] for item in queries) for number in range(10)],
              "plans": plans}
    (tmp_path / "admission-measurement.json").write_text(json.dumps(report, indent=2))
    print("ADMISSION_MEASUREMENT " + json.dumps(report))
    assert plans
    assert len(timings) == 10
    for plan in plans:
        for scan in plan["relations"]:
            visited = (scan["Actual Rows"] + (scan["Rows Removed by Filter"] or 0)) * scan["Actual Loops"]
            assert visited <= 64, (history, revision, scan)
            assert (scan["Shared Hit Blocks"] or 0) + (scan["Shared Read Blocks"] or 0) <= 64, scan
