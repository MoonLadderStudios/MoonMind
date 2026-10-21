"""Retry a failed run from a step through the API against a real Temporal server."""

import asyncio
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from temporalio import activity, workflow
from temporalio.client import WorkflowExecutionStatus, WorkflowFailureError
from temporalio.common import (
    SearchAttributeKey,
    SearchAttributePair,
    TypedSearchAttributes,
)
from temporalio.exceptions import ActivityError, ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, UnsandboxedWorkflowRunner, Worker

from api_service.api.routers import executions
from api_service.auth_providers import get_current_user, get_current_user_optional
from api_service.db.base import get_async_session
from api_service.db.models import (
    Base,
    TemporalExecutionCanonicalRecord,
    TemporalExecutionRecord,
)
from moonmind.workflows.temporal import retry_from_step
from moonmind.workflows.temporal.client import TemporalClientAdapter
from moonmind.workflows.temporal.data_converter import MOONMIND_TEMPORAL_DATA_CONVERTER
from moonmind.workflows.temporal.retry_from_step import STEP_ORDER_MEMO_KEY
from moonmind.workflows.temporal.service import TemporalExecutionService
from tests.helpers.temporal_visibility import register_deployment_search_attributes

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]

TASK_QUEUE = "retry-from-step-test"
STEPS = ("step-1", "step-2", "step-3", "step-4")
STEP_CALLS: list[str] = []
FAILING_STEP: dict[str, str | None] = {"step": "step-3"}


@activity.defn(name="retry_probe.run_step")
async def run_step(step_id: str) -> str:
    STEP_CALLS.append(step_id)
    if step_id == FAILING_STEP["step"]:
        raise ApplicationError(f"{step_id} failed", non_retryable=True)
    return f"{step_id} ok"


@workflow.defn(name="MoonMind.UserWorkflow")
class StepWorkflow:
    """Follows the run workflow's step-start memo contract."""

    def __init__(self) -> None:
        self._rows: list[dict] = []

    @workflow.query(name="get_step_ledger")
    def get_step_ledger(self) -> dict:
        info = workflow.info()
        return {"workflowId": info.workflow_id, "runId": info.run_id, "steps": self._rows}

    @workflow.run
    async def run(self) -> list[str]:
        now = workflow.now().isoformat()
        self._rows = [
            {"logicalStepId": step, "order": order, "title": f"Step {order}", "status": "pending", "updatedAt": now}
            for order, step in enumerate(STEPS, start=1)
        ]
        results = []
        for row in self._rows:
            row.update(status="running", startedAt=workflow.now().isoformat())
            workflow.upsert_memo({STEP_ORDER_MEMO_KEY: row["order"], "summary": f"Executing {row['title']}"})
            try:
                results.append(
                    await workflow.execute_activity(
                        run_step,
                        row["logicalStepId"],
                        start_to_close_timeout=timedelta(seconds=30),
                    )
                )
            except ActivityError as exc:
                row["status"] = "failed"
                raise ApplicationError(f"{row['logicalStepId']} failed") from exc
            row["status"] = "completed"
        return results


@workflow.defn(name="MoonMind.UserWorkflow")
class ChangedWorkflow(StepWorkflow):
    """Installed code whose first command differs from the recorded history."""

    @workflow.run
    async def run(self) -> list[str]:
        await workflow.sleep(timedelta(seconds=1))
        return await super().run()


def _app(service, session, env, user) -> FastAPI:
    app = FastAPI()
    app.include_router(executions.router)

    async def session_dependency():
        yield session

    app.dependency_overrides[get_async_session] = session_dependency
    app.dependency_overrides[executions._get_service] = lambda: service
    app.dependency_overrides[executions.get_temporal_client] = lambda: env.client
    app.dependency_overrides[get_current_user()] = lambda: user
    app.dependency_overrides[get_current_user_optional()] = lambda: user
    routes = list(app.routes)
    while routes:
        route = routes.pop()
        routes.extend(getattr(getattr(route, "original_router", route), "routes", ()))
        for dependency in getattr(getattr(route, "dependant", None), "dependencies", ()):
            if getattr(dependency.call, "__name__", "") in {
                "_current_user_fallback",
                "_strict_current_user",
                "_optional_current_user",
                "get_instance_access",
                "get_instance_access_optional",
            }:
                app.dependency_overrides[dependency.call] = lambda: user
    return app


async def test_failed_run_restarts_from_the_failed_step_and_keeps_earlier_steps(
    tmp_path, monkeypatch
):
    STEP_CALLS.clear()
    FAILING_STEP["step"] = "step-3"
    # The service replays the kept history on the installed workflow classes.
    monkeypatch.setattr(retry_from_step, "_replay_workflows", lambda: [StepWorkflow])
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/executions.db")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with (
            async_sessionmaker(engine, expire_on_commit=False)() as session,
            await WorkflowEnvironment.start_local(
                data_converter=MOONMIND_TEMPORAL_DATA_CONVERTER
            ) as env,
        ):
            await register_deployment_search_attributes(env)
            owner = str(uuid4())
            workflow_id = f"mm:{uuid4()}"
            user = SimpleNamespace(id=owner, is_superuser=False)
            service = TemporalExecutionService(
                session, client_adapter=TemporalClientAdapter(env.client)
            )
            app = _app(service, session, env, user)
            async with Worker(
                env.client,
                task_queue=TASK_QUEUE,
                workflows=[StepWorkflow],
                activities=[run_step],
                workflow_runner=UnsandboxedWorkflowRunner(),
            ):
                handle = await env.client.start_workflow(
                    StepWorkflow.run,
                    id=workflow_id,
                    task_queue=TASK_QUEUE,
                    search_attributes=TypedSearchAttributes(
                        [
                            SearchAttributePair(SearchAttributeKey.for_keyword("mm_owner_id"), owner),
                            SearchAttributePair(SearchAttributeKey.for_keyword("mm_owner_type"), "user"),
                            SearchAttributePair(SearchAttributeKey.for_keyword("mm_entry"), "user_workflow"),
                        ]
                    ),
                )
                with pytest.raises(WorkflowFailureError):
                    await handle.result()
                failed_run_id = (await handle.describe()).run_id
                assert STEP_CALLS == ["step-1", "step-2", "step-3"]

                FAILING_STEP["step"] = None
                STEP_CALLS.clear()
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app), base_url="http://test"
                ) as client:
                    unstarted = await client.post(
                        f"/api/executions/{workflow_id}/retry-from-step",
                        json={"idempotencyKey": "retry-unstarted", "stepId": "step-4"},
                    )
                    assert unstarted.status_code == 409
                    assert unstarted.json()["detail"]["reason"] == "step_not_retryable"

                    response = await client.post(
                        f"/api/executions/{workflow_id}/retry-from-step",
                        json={"idempotencyKey": "retry-1", "sourceRunId": failed_run_id},
                    )
                    assert response.status_code == 202, response.text
                    body = response.json()
                    assert body["stepId"] == "step-3"
                    assert body["sourceRunId"] == failed_run_id
                    new_run_id = body["runId"]
                    assert new_run_id != failed_run_id

                    retried = env.client.get_workflow_handle(workflow_id, run_id=new_run_id)
                    async with asyncio.timeout(30):
                        assert await retried.result() == [
                            "step-1 ok",
                            "step-2 ok",
                            "step-3 ok",
                            "step-4 ok",
                        ]
                    # Earlier steps came from history; only the failed step and
                    # its successors ran again.
                    assert STEP_CALLS == ["step-3", "step-4"]
                    assert (await retried.describe()).status == WorkflowExecutionStatus.COMPLETED

                    repeated = await client.post(
                        f"/api/executions/{workflow_id}/retry-from-step",
                        json={"idempotencyKey": "retry-1", "sourceRunId": failed_run_id},
                    )
                    assert repeated.status_code == 202
                    assert repeated.json()["runId"] == new_run_id

                    # Temporal accepted the reset but MoonMind lost its response
                    # (crash or failed commit), with the row either still on the
                    # failed run or already synced to the successor.
                    for row_run_id in (failed_run_id, new_run_id):
                        for model in (TemporalExecutionCanonicalRecord, TemporalExecutionRecord):
                            row = await session.get(model, workflow_id)
                            if row is not None:
                                row.run_id = row_run_id
                                row.last_update_idempotency_key = None
                                row.last_update_response = None
                        await session.commit()
                        recovered = await client.post(
                            f"/api/executions/{workflow_id}/retry-from-step",
                            json={"idempotencyKey": "retry-1", "sourceRunId": failed_run_id},
                        )
                        assert recovered.status_code == 202, recovered.text
                        assert recovered.json()["runId"] == new_run_id
                        assert recovered.json()["stepId"] == "step-3"

                    other = await client.post(
                        f"/api/executions/{workflow_id}/retry-from-step",
                        json={"idempotencyKey": "retry-2", "sourceRunId": failed_run_id},
                    )
                    assert other.status_code == 409
                    assert other.json()["detail"]["reason"] == "run_changed"

                    detail = await client.get(f"/api/executions/{workflow_id}")
                    assert detail.status_code == 200
                    assert detail.json()["runId"] == new_run_id

                replay = await Replayer(
                    workflows=[StepWorkflow],
                    data_converter=MOONMIND_TEMPORAL_DATA_CONVERTER,
                    workflow_runner=UnsandboxedWorkflowRunner(),
                ).replay_workflow(await retried.fetch_history(), raise_on_replay_failure=False)
                assert replay.replay_failure is None
    finally:
        await engine.dispose()


async def test_retry_is_refused_when_installed_code_cannot_replay_the_kept_history(
    tmp_path, monkeypatch
):
    STEP_CALLS.clear()
    FAILING_STEP["step"] = "step-2"


    monkeypatch.setattr(retry_from_step, "_replay_workflows", lambda: [ChangedWorkflow])
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/executions.db")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with (
            async_sessionmaker(engine, expire_on_commit=False)() as session,
            await WorkflowEnvironment.start_local(
                data_converter=MOONMIND_TEMPORAL_DATA_CONVERTER
            ) as env,
        ):
            await register_deployment_search_attributes(env)
            owner = str(uuid4())
            workflow_id = f"mm:{uuid4()}"
            user = SimpleNamespace(id=owner, is_superuser=False)
            service = TemporalExecutionService(
                session, client_adapter=TemporalClientAdapter(env.client)
            )
            app = _app(service, session, env, user)
            async with Worker(
                env.client,
                task_queue=TASK_QUEUE,
                workflows=[StepWorkflow],
                activities=[run_step],
                workflow_runner=UnsandboxedWorkflowRunner(),
            ):
                handle = await env.client.start_workflow(
                    StepWorkflow.run,
                    id=workflow_id,
                    task_queue=TASK_QUEUE,
                    search_attributes=TypedSearchAttributes(
                        [
                            SearchAttributePair(SearchAttributeKey.for_keyword("mm_owner_id"), owner),
                            SearchAttributePair(SearchAttributeKey.for_keyword("mm_owner_type"), "user"),
                            SearchAttributePair(SearchAttributeKey.for_keyword("mm_entry"), "user_workflow"),
                        ]
                    ),
                )
                with pytest.raises(WorkflowFailureError):
                    await handle.result()
                failed_run_id = (await handle.describe()).run_id
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app), base_url="http://test"
                ) as client:
                    response = await client.post(
                        f"/api/executions/{workflow_id}/retry-from-step",
                        json={"idempotencyKey": "retry-changed"},
                    )
                assert response.status_code == 409
                assert response.json()["detail"]["reason"] == "history_not_replayable"
                # Nothing was reset: the failed run is still the latest run.
                description = await env.client.get_workflow_handle(workflow_id).describe()
                assert description.run_id == failed_run_id
                assert description.status == WorkflowExecutionStatus.FAILED
    finally:
        await engine.dispose()
