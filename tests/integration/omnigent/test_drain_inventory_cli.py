"""MoonLadderStudios/MoonMind#5343: ``moonmind drain-inventory`` on real services.

Runs the CLI command against real PostgreSQL and the Temporal test server and
compares every table, every seeded workflow and every process launch before
and after, so the inventory is shown to be read-only.
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from temporalio.testing import WorkflowEnvironment
from typer.testing import CliRunner

from api_service.db.models import (
    Base,
    OmnigentExecutionPlanRecord,
    OmnigentExecutionPlanUsageRecord,
    RecurringWorkflowDefinition,
    TemporalExecutionCanonicalRecord,
    TemporalWorkflowType,
)
from moonmind import cli
from moonmind.config.settings import settings
from moonmind.omnigent import retired_path_inventory
from moonmind.omnigent.retired_path_inventory import (
    RECORDED_PATH_CATEGORIES,
    RETIRED_PATH_CATEGORIES,
    RETIRED_PATH_INFORMATIONAL,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]

USER_QUEUE = "mm.workflow.user.v2"
GATING = [c for c in RETIRED_PATH_CATEGORIES if c not in RETIRED_PATH_INFORMATIONAL]


def _plan_ref(char: str) -> str:
    return "omnigent-execution-plan:sha256:" + char * 64


@pytest_asyncio.fixture
async def database(control_plane_postgres_url):
    engine = create_async_engine(control_plane_postgres_url)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield (
            control_plane_postgres_url,
            engine,
            sessionmaker(engine, class_=AsyncSession, expire_on_commit=False),
        )
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def temporal():
    async with await WorkflowEnvironment.start_local() as env:
        yield env


def _unused_address() -> str:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return f"127.0.0.1:{probe.getsockname()[1]}"


async def _row_state(engine) -> dict[str, str]:
    """Digest every row of every MoonMind table."""

    state = {}
    async with engine.connect() as connection:
        for table in Base.metadata.sorted_tables:
            state[table.name] = (
                await connection.execute(
                    text(
                        "SELECT count(*)::text || ':' || coalesce(md5("
                        "string_agg(t::text, '|' ORDER BY t::text)), '') "
                        f'FROM "{table.name}" t'
                    )
                )
            ).scalar_one()
    return state


async def _workflow_state(client, workflow_ids) -> dict[str, tuple]:
    state = {}
    for workflow_id in workflow_ids:
        description = await client.get_workflow_handle(workflow_id).describe()
        state[workflow_id] = (
            description.run_id,
            description.status,
            description.history_length,
        )
    return state


async def _listed_ids(client) -> list[str]:
    return sorted([execution.id async for execution in client.list_workflows()])


async def _wait_until_visible(client, workflow_ids) -> None:
    for _ in range(100):
        if set(workflow_ids) <= set(await _listed_ids(client)):
            return
        await asyncio.sleep(0.1)
    raise AssertionError(f"workflows never became visible: {workflow_ids}")


async def _run_cli(monkeypatch, tmp_path, *, database_url, temporal_address):
    """Run the command in its own thread and record any process launch."""

    launches = []
    shim = tmp_path / "bin"
    shim.mkdir()
    docker_log = tmp_path / "docker-calls.log"
    (shim / "docker").write_text(f'#!/bin/sh\necho "$@" >> "{docker_log}"\n')
    (shim / "docker").chmod(0o755)
    monkeypatch.setenv("PATH", f"{shim}{os.pathsep}{os.environ.get('PATH', '')}")

    def refuse_popen(*args, **kwargs):
        launches.append(args)
        raise AssertionError("drain-inventory must not launch processes")

    async def refuse_exec(*args, **kwargs):
        launches.append(args)
        raise AssertionError("drain-inventory must not launch processes")

    with monkeypatch.context() as patch:
        patch.setattr(subprocess, "Popen", refuse_popen)
        patch.setattr(asyncio, "create_subprocess_exec", refuse_exec)
        patch.setattr(asyncio, "create_subprocess_shell", refuse_exec)
        result = await asyncio.to_thread(
            CliRunner().invoke,
            cli.app,
            [
                "drain-inventory",
                "--database-url",
                database_url,
                "--temporal-address",
                temporal_address,
                "--temporal-namespace",
                "default",
            ],
        )
    assert launches == []
    assert not docker_log.exists()
    return result


async def _seed_plan(sessions, plan_ref, *, realizer="codex-profile-bound@1"):
    async with sessions() as session:
        session.add(
            OmnigentExecutionPlanRecord(
                plan_ref=plan_ref,
                schema_version="omnigent.execution_plan.v1",
                payload_json={"planRef": plan_ref},
                harness_id="codex-native",
                harness_implementation_ref="codex-native@1",
                host_class_ref="omnigent-codex@1",
                launch_policy_ref="omnigent-default@1",
                execution_realizer_ref=realizer,
            )
        )
        await session.commit()


async def _seed_schedule(sessions, plan_ref, *, enabled) -> str:
    definition = RecurringWorkflowDefinition(
        name="nightly",
        enabled=enabled,
        cron="0 3 * * *",
        timezone="UTC",
        target={"initialParameters": {"omnigentExecutionPlan": {"planRef": plan_ref}}},
    )
    async with sessions() as session:
        session.add(definition)
        await session.commit()
    return str(definition.id)


async def _seed_execution(sessions, workflow_id, plan_ref, *, run_id, start, close):
    async with sessions() as session:
        session.add(
            TemporalExecutionCanonicalRecord(
                workflow_id=workflow_id,
                run_id=run_id,
                namespace="default",
                workflow_type=TemporalWorkflowType.USER_WORKFLOW,
                owner_type="user",
                state="completed" if close else "executing",
                close_status="completed" if close else None,
                entry="api",
                search_attributes={},
                memo={"temporalStart": {"state": start}},
                parameters={"omnigentExecutionPlan": {"planRef": plan_ref}},
                artifact_refs=[],
            )
        )
        await session.flush()
        session.add(
            OmnigentExecutionPlanUsageRecord(
                usage_id=f"usage-{workflow_id}",
                workflow_id=workflow_id,
                step_execution_id=workflow_id,
                idempotency_key=f"key-{workflow_id}",
                plan_ref=plan_ref,
                request_digest="sha256:" + "0" * 64,
            )
        )
        await session.commit()


async def test_empty_deployment_reports_clean_and_changes_nothing(
    database, temporal, monkeypatch, tmp_path
):
    url, engine, _ = database
    client = temporal.client
    rows_before = await _row_state(engine)
    workflows_before = await _listed_ids(client)

    result = await _run_cli(
        monkeypatch,
        tmp_path,
        database_url=url,
        temporal_address=client.service_client.config.target_host,
    )

    assert result.exit_code == 0, result.output
    report = json.loads(result.output)
    assert report["deletable"] is True
    assert report["blockers"] == []
    assert report["states"] == {"active": 0, "blocked": 0, "clean": 0, "unknown": 0}
    assert {
        category: report["items"][category] for category in RETIRED_PATH_CATEGORIES
    } == {category: [] for category in RETIRED_PATH_CATEGORIES}
    assert await _row_state(engine) == rows_before
    assert await _listed_ids(client) == workflows_before == []


async def test_seeded_open_profile_bound_plan_reports_active_and_changes_nothing(
    database, temporal, monkeypatch, tmp_path
):
    url, engine, sessions = database
    client = temporal.client
    suffix = uuid4().hex[:8]
    running_id = f"mm:running-{suffix}"
    queued_id = f"mm:queued-{suffix}"
    closed_id = f"mm:closed-{suffix}"
    session_id = f"agent-session-{suffix}"
    legacy_id = f"legacy-{suffix}"

    running = await client.start_workflow(
        "MoonMind.UserWorkflow", {}, id=running_id, task_queue=USER_QUEUE
    )
    await client.start_workflow(
        "MoonMind.AgentSession", {}, id=session_id, task_queue=USER_QUEUE
    )
    await client.start_workflow(
        "MoonMind.UserWorkflow",
        {},
        id=legacy_id,
        task_queue=settings.temporal.workflow_task_queue,
    )
    for plan_ref in (_plan_ref(char) for char in "abcde"):
        await _seed_plan(sessions, plan_ref)
    # Enabled schedules hold their recorded plan without any open execution.
    schedule_id = await _seed_schedule(sessions, _plan_ref("d"), enabled=True)
    await _seed_schedule(sessions, _plan_ref("e"), enabled=False)
    await _seed_execution(
        sessions,
        running_id,
        _plan_ref("a"),
        run_id=running.first_execution_run_id,
        start="confirmed",
        close=False,
    )
    await _seed_execution(
        sessions, queued_id, _plan_ref("b"), run_id="", start="pending", close=False
    )
    await _seed_execution(
        sessions,
        closed_id,
        _plan_ref("c"),
        run_id="closed",
        start="confirmed",
        close=True,
    )
    seeded_workflows = [running_id, session_id, legacy_id]
    await _wait_until_visible(client, seeded_workflows)
    rows_before = await _row_state(engine)
    workflows_before = await _workflow_state(client, seeded_workflows)
    listed_before = await _listed_ids(client)

    result = await _run_cli(
        monkeypatch,
        tmp_path,
        database_url=url,
        temporal_address=client.service_client.config.target_host,
    )

    assert result.exit_code == 1, result.output
    report = json.loads(result.output)
    items = report["items"]
    assert report["deletable"] is False
    assert {item["id"]: item["state"] for item in items["profile_bound_plans"]} == {
        _plan_ref("a"): "active",
        _plan_ref("b"): "active",
        _plan_ref("c"): "clean",
        _plan_ref("d"): "active",
        _plan_ref("e"): "clean",
    }
    scheduled = next(
        item for item in items["profile_bound_plans"] if item["id"] == _plan_ref("d")
    )
    assert scheduled["scheduleIds"] == [schedule_id]
    assert items["profile_bound_retries"] == [
        {"id": running_id, "state": "active", "planRefs": [_plan_ref("a")]}
    ]
    assert items["profile_bound_queued_starts"] == [
        {"id": queued_id, "state": "active", "planRefs": [_plan_ref("b")]}
    ]
    assert [item["id"] for item in items["legacy_task_queue_workflows"]] == [legacy_id]
    assert [item["id"] for item in items["agent_session_children"]] == [session_id]
    assert report["informationalStates"]["active"] == 1

    assert await _row_state(engine) == rows_before
    assert await _workflow_state(client, seeded_workflows) == workflows_before
    assert await _listed_ids(client) == listed_before


@pytest.mark.parametrize("unreachable", ["postgres", "temporal"])
async def test_unreachable_source_reports_unknown_never_clean(
    database, temporal, monkeypatch, tmp_path, unreachable
):
    url, engine, _ = database
    client = temporal.client
    rows_before = await _row_state(engine)

    result = await _run_cli(
        monkeypatch,
        tmp_path,
        database_url=(
            f"postgresql+asyncpg://moonmind:unused@{_unused_address()}/moonmind"
            if unreachable == "postgres"
            else url
        ),
        temporal_address=(
            _unused_address()
            if unreachable == "temporal"
            else client.service_client.config.target_host
        ),
    )

    assert result.exit_code == 1, result.output
    report = json.loads(result.output)
    assert report["deletable"] is False
    affected = (
        RECORDED_PATH_CATEGORIES
        if unreachable == "postgres"
        else tuple(set(RETIRED_PATH_CATEGORIES) - set(RECORDED_PATH_CATEGORIES))
    )
    for category in affected:
        [item] = report["items"][category]
        assert item["state"] == "unknown"
        assert item["error"]
    for category in set(GATING) - set(affected):
        assert report["items"][category] == []
    assert "unused@" not in result.output
    assert await _row_state(engine) == rows_before


async def test_stalled_database_read_reports_unknown_and_still_reads_temporal(
    database, temporal, monkeypatch, tmp_path
):
    url, engine, _ = database
    client = temporal.client
    legacy_id = f"legacy-{uuid4().hex[:8]}"
    await client.start_workflow(
        "MoonMind.UserWorkflow",
        {},
        id=legacy_id,
        task_queue=settings.temporal.workflow_task_queue,
    )
    await _wait_until_visible(client, [legacy_id])
    monkeypatch.setattr(retired_path_inventory, "_READ_TIMEOUT_SECONDS", 1.0)

    # A migration holding an exclusive lock blocks every read of the table.
    async with engine.connect() as migration:
        await migration.execute(
            text("LOCK TABLE omnigent_execution_plans IN ACCESS EXCLUSIVE MODE")
        )
        result = await asyncio.wait_for(
            _run_cli(
                monkeypatch,
                tmp_path,
                database_url=url,
                temporal_address=client.service_client.config.target_host,
            ),
            timeout=60,
        )
        await migration.rollback()

    assert result.exit_code == 1, result.output
    report = json.loads(result.output)
    assert report["deletable"] is False
    for category in RECORDED_PATH_CATEGORIES:
        [item] = report["items"][category]
        assert item["state"] == "unknown"
        assert item["error"]
    assert [item["id"] for item in report["items"]["legacy_task_queue_workflows"]] == [
        legacy_id
    ]
