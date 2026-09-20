"""Exercise logical workflow listing against real Temporal continuation history."""

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from temporalio import workflow
from temporalio.client import WorkflowExecutionStatus
from temporalio.common import (
    SearchAttributeKey,
    SearchAttributePair,
    TypedSearchAttributes,
)
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

from api_service.api.routers import executions
from api_service.auth_providers import get_current_user, get_current_user_optional
from api_service.db.base import get_async_session
from api_service.db.models import Base
from moonmind.workflows.temporal.client import TemporalClientAdapter
from moonmind.workflows.temporal.service import TemporalExecutionService
from tests.helpers.temporal_visibility import register_deployment_search_attributes

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]


@workflow.defn(name="MoonMind.UserWorkflow")
class ContinuingWorkflow:
    @workflow.run
    async def run(self, remaining: int) -> None:
        if remaining:
            workflow.continue_as_new(remaining - 1)
        await workflow.wait_condition(lambda: False)


async def test_list_filters_and_counts_follow_current_run_after_continuation(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/executions.db")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with (
            async_sessionmaker(engine, expire_on_commit=False)() as session,
            await WorkflowEnvironment.start_local() as env,
        ):
            await register_deployment_search_attributes(env)
            owner = str(uuid4())
            workflow_id = f"mm:{uuid4()}"
            user = SimpleNamespace(id=owner, is_superuser=False)
            service = TemporalExecutionService(
                session, client_adapter=TemporalClientAdapter(env.client)
            )
            app = FastAPI()
            app.include_router(executions.router)

            async def session_dependency():
                yield session

            app.dependency_overrides[get_async_session] = session_dependency
            app.dependency_overrides[executions._get_service] = lambda: service
            app.dependency_overrides[executions.get_temporal_client] = (
                lambda: env.client
            )
            app.dependency_overrides[get_current_user()] = lambda: user
            app.dependency_overrides[get_current_user_optional()] = lambda: user
            routes = list(app.routes)
            while routes:
                route = routes.pop()
                routes.extend(
                    getattr(getattr(route, "original_router", route), "routes", ())
                )
                for dependency in getattr(
                    getattr(route, "dependant", None), "dependencies", ()
                ):
                    if getattr(dependency.call, "__name__", "") in {
                        "_current_user_fallback",
                        "_strict_current_user",
                        "_optional_current_user",
                    }:
                        app.dependency_overrides[dependency.call] = lambda: user

            async with Worker(
                env.client,
                task_queue="continuation-list-test",
                workflows=[ContinuingWorkflow],
                workflow_runner=UnsandboxedWorkflowRunner(),
            ):
                await env.client.start_workflow(
                    ContinuingWorkflow.run,
                    2,
                    id=workflow_id,
                    task_queue="continuation-list-test",
                    search_attributes=TypedSearchAttributes(
                        [
                            SearchAttributePair(
                                SearchAttributeKey.for_keyword("mm_owner_id"), owner
                            ),
                            SearchAttributePair(
                                SearchAttributeKey.for_keyword("mm_owner_type"), "user"
                            ),
                            SearchAttributePair(
                                SearchAttributeKey.for_keyword("mm_entry"),
                                "user_workflow",
                            ),
                            SearchAttributePair(
                                SearchAttributeKey.for_keyword("mm_state"), "executing"
                            ),
                            SearchAttributePair(
                                SearchAttributeKey.for_keyword("mm_repo"),
                                "example/repo",
                            ),
                        ]
                    ),
                )
                handle = env.client.get_workflow_handle(workflow_id)

                async def wait_for_visibility(status):
                    async with asyncio.timeout(20):
                        while True:
                            rows = [
                                row
                                async for row in env.client.list_workflows(
                                    query=f'WorkflowId="{workflow_id}"'
                                )
                            ]
                            if (
                                len(rows) == 3
                                and sum(row.status == status for row in rows) == 1
                            ):
                                return next(row for row in rows if row.status == status)
                            await asyncio.sleep(0.1)

                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app), base_url="http://test"
                ) as client:
                    for temporal_status, state, closed in (
                        (WorkflowExecutionStatus.RUNNING, "executing", False),
                        (WorkflowExecutionStatus.TERMINATED, "failed", True),
                    ):
                        if closed:
                            await handle.terminate("Test terminal successor")
                        current = await wait_for_visibility(temporal_status)
                        for filters in (
                            {},
                            {"stateIn": state},
                            {"stateNotIn": "completed"},
                        ):
                            params = {"source": "temporal", "pageSize": 1, **filters}
                            items = []
                            for _ in range(5):
                                response = await client.get(
                                    "/api/executions", params=params
                                )
                                assert response.status_code == 200, response.text
                                body = response.json()
                                items.extend(body["items"])
                                if not body["nextPageToken"]:
                                    break
                                params["nextPageToken"] = body["nextPageToken"]
                            else:
                                pytest.fail(
                                    "Workflow list pagination did not terminate"
                                )
                            assert [(row["runId"], row["state"]) for row in items] == [
                                (current.run_id, state)
                            ]

                        # SQLite Visibility cannot sort the terminal sample.
                        # Active-only metrics still expose leaked predecessors
                        # in active counts, including after termination.
                        response = await client.get(
                            "/api/executions/metrics",
                            params={
                                "source": "temporal",
                                "stateNotIn": "completed,failed,canceled,no_commit",
                            },
                        )
                        assert response.status_code == 200, response.text
                        assert response.json()["totalRuns"] == (0 if closed else 1)
                        assert response.json()["failedRuns"] == 0

                        response = await client.get(
                            "/api/executions/facets",
                            params={
                                "source": "temporal",
                                "facet": "repository",
                            },
                        )
                        assert response.status_code == 200, response.text
                        assert [
                            (item["value"], item["count"])
                            for item in response.json()["items"]
                        ] == [("example/repo", 1)]

                        if closed:
                            response = await client.get(
                                f"/api/executions/{workflow_id}",
                                params={"source": "temporal"},
                            )
                            assert response.status_code == 200, response.text
                            assert response.json()["runId"] == current.run_id
                            assert response.json()["state"] == state
    finally:
        await engine.dispose()
