"""Public HTTP manual requests retain identity across Temporal observation."""

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from api_service.api.routers import recurring_workflows as routes
from api_service.services.recurring_workflows_service import RecurringWorkflowsService
from moonmind.workflows.temporal.client import TemporalClientAdapter
from tests.integration.reliability.test_resolver_verification_capability_journey import (
    resolver_test_client,
)
from tests.unit.services.test_recurring_workflows_service import recurring_db

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.integration,
    pytest.mark.reliability_journey,
]


async def test_run_now_http_retries_observe_one_execution_and_skip_active_overlap(
    tmp_path,
):
    client = await resolver_test_client()
    adapter = TemporalClientAdapter(client=client)
    queue = "manual-api-" + uuid4().hex
    adapter._get_task_queue = lambda *_args, **_kwargs: queue
    user = SimpleNamespace(id=uuid4(), is_superuser=True)
    async with recurring_db(tmp_path) as sessions:
        async with sessions() as session:
            definition = await RecurringWorkflowsService(
                session, temporal_client_adapter=adapter
            ).create_definition(
                name="Manual recovery",
                description="",
                enabled=True,
                schedule_type="cron",
                cron="0 6 * * *",
                timezone="UTC",
                scope_type="personal",
                scope_ref=None,
                owner_user_id=user.id,
                policy={},
                target={
                    "workflowType": "MoonMind.UserWorkflow",
                    "initialParameters": {
                        "task": {"instructions": "Test accepted work"}
                    },
                },
            )
        app = FastAPI()
        app.include_router(routes.router)

        async def service():
            async with sessions() as session:
                yield RecurringWorkflowsService(
                    session, temporal_client_adapter=adapter
                )

        app.dependency_overrides[routes._get_service] = service
        for route in routes.router.routes:
            for dependency in getattr(
                getattr(route, "dependant", None), "dependencies", ()
            ):
                if dependency.name == "user":
                    app.dependency_overrides[dependency.call] = lambda: user
        endpoint = f"/api/recurring-workflows/{definition.id}"
        schedule = client.get_schedule_handle(f"mm-schedule:{definition.id}")
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as http:
                request_id = str(uuid4())
                first = await http.post(
                    endpoint + "/run", headers={"Idempotency-Key": request_id}
                )
                assert first.status_code == 201, first.text
                assert first.json()["id"] == request_id
                repeated = await http.post(
                    endpoint + "/run", headers={"Idempotency-Key": request_id}
                )
                assert repeated.json()["id"] == request_id
                for _ in range(50):
                    if (await schedule.describe()).info.recent_actions:
                        break
                    await asyncio.sleep(0.1)
                # A fresh API session reconciles a potentially lost RPC response.
                async with sessions() as session:
                    await RecurringWorkflowsService(
                        session, temporal_client_adapter=adapter
                    ).reconcile_manual_runs()
                observed = await http.post(
                    endpoint + "/run", headers={"Idempotency-Key": request_id}
                )
                assert observed.json()["outcome"] == "enqueued"
                assert observed.json()["temporalWorkflowId"]
                assert observed.json()["temporalRunId"]
                skipped = await http.post(
                    endpoint + "/run", headers={"Idempotency-Key": str(uuid4())}
                )
                assert skipped.status_code == 201, skipped.text
                assert skipped.json()["outcome"] == "skipped"
                assert (
                    skipped.json()["temporalWorkflowId"]
                    == observed.json()["temporalWorkflowId"]
                )
                assert len((await schedule.describe()).info.recent_actions) == 1
        finally:
            for action in (await schedule.describe()).info.running_actions:
                await client.get_workflow_handle(action.workflow_id).terminate()
            await schedule.delete()
