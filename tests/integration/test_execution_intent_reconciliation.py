"""The real operational workflow resumes committed PostgreSQL admission."""

import asyncio
import json
from contextlib import AsyncExitStack, asynccontextmanager
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from importlib import import_module
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import HTTPException, Response
from sqlalchemy import event, insert, select, text
from temporalio import activity, workflow
from temporalio.client import WorkflowFailureError
from temporalio.common import SearchAttributeKey
from temporalio.exceptions import CancelledError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

from api_service.core.sync import sync_execution_projection
from api_service.db.models import (
    MoonMindWorkflowState,
    TemporalArtifactLink,
    TemporalExecutionCanonicalRecord,
    TemporalExecutionCloseStatus,
    TemporalWorkflowType,
)
from api_service.api.routers import executions
from moonmind.config.settings import settings
from moonmind.security.operator_admission import OperatorAdmission
from moonmind.workflows.temporal.activity_catalog import (
    TemporalActivityCatalog,
    build_default_activity_catalog,
    publication_task_queue,
)
from moonmind.workflows.temporal.activity_runtime import build_activity_bindings
from moonmind.workflows.temporal.artifacts import (
    TemporalArtifactActivities,
    TemporalArtifactRepository,
    TemporalArtifactService,
)
from moonmind.workflows.temporal.client import TemporalClientAdapter
from moonmind.workflows.temporal.service import (
    TemporalExecutionService,
    reconcile_pending_execution_intents,
)
from moonmind.workflows.temporal.workflows.managed_session_reconcile import (
    MoonMindManagedSessionReconcileWorkflow,
)
from tests.integration import test_execution_start_admission as admission
from tests.unit.workflows.temporal.test_execution_intent_reconciliation import (
    _api_context,
    RequestDisappeared,
    admission_request,
    admit_then_disappear,
    operational_tick,
)

conversion_postgres_url = admission.conversion_postgres_url
sessions = admission.sessions
pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]

_BACKGROUND_QUEUE = "fixture.execution-intent-background"
_INPUT_QUEUE = settings.temporal.activity_agent_runtime_task_queue


@workflow.defn(name="MoonMind.UserWorkflow")
class AdmittedInputConsumer:
    def __init__(self):
        self.finished = False

    @workflow.signal
    def finish(self):
        self.finished = True

    @workflow.run
    async def run(self, payload: dict) -> dict:
        result = await workflow.execute_activity(
            "fixture.read_admitted_execution_input",
            {
                "workflowId": workflow.info().workflow_id,
                "parameters": payload["initial_parameters"],
            },
            task_queue=_INPUT_QUEUE,
            start_to_close_timeout=timedelta(seconds=15),
        )
        await workflow.wait_condition(lambda: self.finished)
        workflow.upsert_search_attributes({"mm_updated_at": [workflow.now()]})
        return result


@pytest.mark.parametrize("loss", ["before", "lost-ack", "confirmation", "concurrent"])
async def test_background_owner_recovers_one_admitted_start(sessions, monkeypatch, loss):
    saved = await _api_context(sessions, monkeypatch)
    monkeypatch.setattr(settings.temporal, "namespace", "default")
    monkeypatch.setattr(settings.temporal_dashboard, "temporal_workflow_editing_enabled", True)
    observed = []

    @activity.defn(name="fixture.read_admitted_execution_input")
    async def read_admitted_input(request):
        async with sessions() as session:
            reader = TemporalArtifactActivities(
                TemporalArtifactService(TemporalArtifactRepository(session))
            )
            body = await reader.artifact_read(
                {
                    "principal": f"workflow:{request['workflowId']}",
                    "artifact_ref": request["parameters"]["resolvedSkillsetRef"],
                }
            )
        assert json.loads(body)
        observed.append((activity.info().workflow_run_id, deepcopy(request)))
        return {"status": "success"}

    async with await WorkflowEnvironment.start_local(
        search_attributes=[
            SearchAttributeKey.for_keyword("SessionStatus"),
            SearchAttributeKey.for_bool("IsDegraded"),
        ],
    ) as env:
        await admission._register_search_attributes(env)
        adapter = TemporalClientAdapter(client=env.client)
        actual_start = adapter.start_workflow
        actual_describe = adapter.describe_workflow
        lose_ack = loss == "lost-ack"

        async def start(**kwargs):
            result = await actual_start(**kwargs)
            if lose_ack:
                raise ConnectionError("fixture lost acknowledgement after acceptance")
            return result

        async def describe(*args, **kwargs):
            if lose_ack:
                raise ConnectionError("fixture observation unavailable")
            return await actual_describe(*args, **kwargs)

        adapter.start_workflow = AsyncMock(side_effect=start)
        adapter.describe_workflow = AsyncMock(side_effect=describe)
        default_catalog = build_default_activity_catalog()
        route = default_catalog.resolve_activity("agent_runtime.reconcile_managed_sessions")
        catalog = TemporalActivityCatalog(
            activities=(route,), fleets=default_catalog.fleets
        )
        binding = build_activity_bindings(
            catalog, agent_runtime_activities=operational_tick(adapter)
        )[0]
        async with AsyncExitStack() as stack:
            for queue, workflows, activities in (
                (
                    publication_task_queue(settings.temporal.user_workflow_v2_task_queue),
                    [AdmittedInputConsumer],
                    [],
                ),
                (_BACKGROUND_QUEUE, [MoonMindManagedSessionReconcileWorkflow], []),
                (route.task_queue, [], [binding.handler, read_admitted_input]),
            ):
                await stack.enter_async_context(
                    Worker(
                        env.client,
                        task_queue=queue,
                        workflows=workflows,
                        activities=activities,
                        workflow_runner=UnsandboxedWorkflowRunner(),
                    )
                )
            if loss in {"before", "concurrent"}:
                workflow_id, _provisional, parameters = await admit_then_disappear(
                    sessions, adapter
                )
                adapter.start_workflow.assert_not_awaited()
            else:
                with pytest.raises(HTTPException) as error:
                    await admission._submit(
                        sessions,
                        adapter,
                        admission_request(),
                        fail_confirmation=loss == "confirmation",
                    )
                assert error.value.status_code == 503
                async with sessions() as session:
                    record = (
                        await session.execute(select(TemporalExecutionCanonicalRecord))
                    ).scalar_one()
                    workflow_id = record.workflow_id
                    parameters = deepcopy(record.parameters)
                    assert record.memo["temporalStart"]["state"] == "pending"
            lose_ack = False

            async def background():
                return await env.client.execute_workflow(
                    MoonMindManagedSessionReconcileWorkflow.run,
                    {},
                    id=f"fixture-intent-reconcile-{uuid4()}",
                    task_queue=_BACKGROUND_QUEUE,
                    execution_timeout=timedelta(seconds=60),
                )

            if loss == "concurrent":
                await asyncio.gather(
                    background(),
                    background(),
                    admission._submit(sessions, adapter, admission_request()),
                )
            else:
                # The initiating request has gone away. Only the ordinary
                # scheduled workflow/Activity owner resumes its saved intent.
                await background()
            handle = env.client.get_workflow_handle(workflow_id)
            await handle.signal(AdmittedInputConsumer.finish)
            result = await asyncio.wait_for(handle.result(), 20)
            assert result == {"status": "success"}
            assert len(observed) == 1
            run_id, read_request = observed[0]
            assert read_request["workflowId"] == workflow_id
            assert read_request["parameters"] == parameters
            assert len(saved.compiled) == 1
            async with sessions() as session:
                records = (
                    await session.execute(select(TemporalExecutionCanonicalRecord))
                ).scalars().all()
                assert len(records) == 1
                record = records[0]
                assert record.run_id == run_id
                assert record.parameters == parameters
                assert record.memo["temporalStart"]["state"] == "confirmed"
                links = (
                    await session.execute(
                        select(TemporalArtifactLink).where(
                            TemporalArtifactLink.workflow_id == workflow_id
                        )
                    )
                ).scalars().all()
                assert links and all(link.run_id == run_id for link in links)
                # An ordinary poll reads the immutable start-time Temporal
                # memo. It must not turn confirmed DB delivery back to pending.
                await sync_execution_projection(
                    session, await env.client.get_workflow_handle(workflow_id).describe()
                )
                await session.commit()
                await session.refresh(record)
                assert record.memo["temporalStart"]["state"] == "confirmed"


@workflow.defn(name="MoonMind.UserWorkflow")
class CancellableAdmittedConsumer:
    @workflow.run
    async def run(self, payload: dict) -> None:
        await workflow.wait_condition(lambda: False)


@asynccontextmanager
async def cancel_runtime():
    async with await WorkflowEnvironment.start_local(
        search_attributes=[
            SearchAttributeKey.for_keyword("SessionStatus"),
            SearchAttributeKey.for_bool("IsDegraded"),
        ],
    ) as env:
        await admission._register_search_attributes(env)
        adapter = TemporalClientAdapter(client=env.client)
        catalog = build_default_activity_catalog()
        route = catalog.resolve_activity("agent_runtime.reconcile_managed_sessions")
        binding = build_activity_bindings(
            TemporalActivityCatalog(activities=(route,), fleets=catalog.fleets),
            agent_runtime_activities=operational_tick(adapter),
        )[0]
        async with AsyncExitStack() as stack:
            for queue, workflows, activities in (
                (publication_task_queue(settings.temporal.user_workflow_v2_task_queue),
                 [CancellableAdmittedConsumer], []),
                (_BACKGROUND_QUEUE, [MoonMindManagedSessionReconcileWorkflow], []),
                (route.task_queue, [], [binding.handler]),
            ):
                await stack.enter_async_context(Worker(
                    env.client, task_queue=queue, workflows=workflows,
                    activities=activities, workflow_runner=UnsandboxedWorkflowRunner(),
                ))

            async def background():
                return await env.client.execute_workflow(
                    MoonMindManagedSessionReconcileWorkflow.run, {},
                    id=f"fixture-cancel-reconcile-{uuid4()}",
                    task_queue=_BACKGROUND_QUEUE,
                    execution_timeout=timedelta(seconds=60),
                )

            yield env, adapter, background


@pytest.mark.parametrize("loss", ["before", "ambiguous", "confirmed", "observation", "closed-projection"])
async def test_background_honors_cancel_without_starting_a_successor(
    sessions, monkeypatch, tmp_path, loss,
):
    await _api_context(sessions, monkeypatch)
    monkeypatch.setattr(settings.temporal, "namespace", "default")
    monkeypatch.setattr(settings.temporal_dashboard, "temporal_workflow_editing_enabled", True)
    monkeypatch.setenv("MOONMIND_AGENT_RUNTIME_STORE", str(tmp_path / "managed-runtime"))
    async with cancel_runtime() as (env, adapter, background):
        actual_start, actual_describe = adapter.start_workflow, adapter.describe_workflow

        async def lost_start(**kwargs):
            await actual_start(**kwargs)
            raise ConnectionError("fixture lost start acknowledgement")

        adapter.start_workflow = AsyncMock(
            side_effect=lost_start if loss == "ambiguous" else actual_start
        )
        if loss == "ambiguous":
            adapter.describe_workflow = AsyncMock(side_effect=ConnectionError("fixture outage"))
            with pytest.raises(HTTPException) as pending:
                await admission._submit(sessions, adapter, admission_request())
            assert pending.value.status_code == 503
            async with sessions() as session:
                record = (await session.execute(select(TemporalExecutionCanonicalRecord))).scalar_one()
                workflow_id = record.workflow_id
        else:
            workflow_id, _, _ = await admit_then_disappear(
                sessions, adapter, disappear=loss == "before",
            )
        initial_starts = adapter.start_workflow.await_count
        actual_cancel = adapter.cancel_workflow

        async def interrupted_cancel(*args, **kwargs):
            if loss in {"ambiguous", "observation", "closed-projection"}:
                await actual_cancel(*args, **kwargs)
            raise RequestDisappeared()

        adapter.cancel_workflow = AsyncMock(side_effect=interrupted_cancel)
        async with sessions() as session:
            service = TemporalExecutionService(session, client_adapter=adapter)
            with pytest.raises(RequestDisappeared):
                await executions.cancel_execution(
                    workflow_id=workflow_id, response=Response(),
                    payload=executions.CancelExecutionRequest(graceful=True),
                    service=service, user=OperatorAdmission(via="loopback"),
                    _actions_enabled=None, authorization=None, execution_fanout=None,
                )
        async with sessions() as session:
            record = await session.get(TemporalExecutionCanonicalRecord, workflow_id)
            assert record.memo["temporalCancel"]["state"] == "pending"
            assert record.close_status is None
        adapter.cancel_workflow = actual_cancel
        adapter.describe_workflow = actual_describe
        if loss == "closed-projection":
            with pytest.raises(WorkflowFailureError):
                await asyncio.wait_for(env.client.get_workflow_handle(workflow_id).result(), 10)
            async with sessions() as session:
                await sync_execution_projection(
                    session, await env.client.get_workflow_handle(workflow_id).describe()
                )
                await session.commit()
        if loss == "observation":
            adapter.describe_workflow = AsyncMock(side_effect=ConnectionError("fixture outage"))
            await background()
            async with sessions() as session:
                record = await session.get(TemporalExecutionCanonicalRecord, workflow_id)
                assert record.memo["temporalCancel"]["state"] == "pending"
                assert record.close_status is None
            adapter.describe_workflow = actual_describe
        await background()
        if loss != "before":
            with pytest.raises(WorkflowFailureError) as closed:
                await asyncio.wait_for(env.client.get_workflow_handle(workflow_id).result(), 10)
            assert isinstance(closed.value.cause, CancelledError)
            # One further ordinary tick observes the authoritative close.
            await background()
        else:
            await background()
        assert adapter.start_workflow.await_count == initial_starts
        async with sessions() as session:
            record = await session.get(TemporalExecutionCanonicalRecord, workflow_id)
            if loss == "before":
                assert record.memo["temporalCancel"]["state"] == "pending"
                assert record.close_status is None
            else:
                assert record.memo["temporalCancel"]["state"] == "confirmed"
                assert record.close_status.value == "canceled"


async def test_pending_selection_is_bounded_with_large_completed_history(
    sessions, monkeypatch,
):
    now = datetime.now(UTC)
    common = {
        "namespace": "default", "workflow_type": TemporalWorkflowType.USER_WORKFLOW,
        "entry": "run", "run_id": "fixture-run", "updated_at": now,
    }
    async with sessions() as session:
        for offset in range(0, 40000, 1000):
            await session.execute(insert(TemporalExecutionCanonicalRecord), [
                {**common, "workflow_id": f"mm:completed-{number}",
                 "state": MoonMindWorkflowState.COMPLETED,
                 "close_status": TemporalExecutionCloseStatus.COMPLETED,
                 # Old start-time memos are intentionally still pending.
                 "memo": {"temporalStart": {"state": "pending"}}}
                for number in range(offset, offset + 1000)
            ])
        await session.execute(insert(TemporalExecutionCanonicalRecord), [
            {**common, "workflow_id": f"mm:legacy-{number}", "memo": {}}
            for number in range(1000)
        ] + [
            {**common, "workflow_id": f"mm:pending-{number:02}",
             "memo": {"temporalStart": {"state": "pending"}}}
            for number in range(15)
        ])
        await session.commit()
        migration = import_module(
            "api_service.migrations.versions.402_pending_execution_intents"
        )

        def install_index(connection):
            with Operations.context(MigrationContext.configure(connection)):
                migration.downgrade()
                migration.upgrade()

        connection = await session.connection()
        await connection.run_sync(install_index)
        await session.commit()
        await session.execute(text("ANALYZE temporal_execution_sources"))
        await session.commit()

    captured = []
    engine = sessions.kw["bind"]

    def capture(_connection, _cursor, statement, parameters, _context, _many):
        if "temporal_execution_sources" in statement and "ORDER BY" in statement:
            captured.append((statement, parameters))

    owner = AsyncMock(side_effect=lambda record: record)
    monkeypatch.setattr(TemporalExecutionService, "ensure_create_started", owner)
    event.listen(engine.sync_engine, "before_cursor_execute", capture)
    try:
        result = await reconcile_pending_execution_intents(
            session_factory=sessions, client_adapter=AsyncMock(), namespace="default",
        )
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", capture)
    assert result["selected"] == result["observed"] == 10
    assert owner.await_count == 10
    assert {item["workflowId"] for item in result["items"]} == {
        f"mm:pending-{number:02}" for number in range(10)
    }
    assert len(captured) == 1
    statement, parameters = captured[0]
    async with sessions() as session:
        await session.execute(text("SET LOCAL plan_cache_mode = force_generic_plan"))
        connection = await session.connection()
        plan = (await connection.exec_driver_sql(
            "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + statement, parameters,
        )).scalar_one()[0]["Plan"]

    def scans(node):
        yield node
        for child in node.get("Plans", []):
            yield from scans(child)

    source_scans = [node for node in scans(plan)
                    if node.get("Relation Name") == "temporal_execution_sources"]
    assert source_scans
    assert all("Index" in node["Node Type"] for node in source_scans)
    assert sum((node["Actual Rows"] + node.get("Rows Removed by Filter", 0))
               * node["Actual Loops"] for node in source_scans) <= 20
    assert plan.get("Shared Hit Blocks", 0) + plan.get("Shared Read Blocks", 0) < 100
