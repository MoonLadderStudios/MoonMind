"""Fresh serialized workflows join product admission without changing retries."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from api_service.api.routers.executions import _get_service, router
from api_service.db.base import get_async_session
from api_service.db.models import (
    ManagedAgentProviderProfile,
    TemporalExecutionCanonicalRecord,
)
from api_service.services.omnigent_agent_bootstrap_service import (
    reconcile_bootstrap_agent_profile,
)
from api_service.services.omnigent_agent_profile_service import (
    synchronize_omnigent_harness_catalog,
)
from moonmind.config.settings import settings
from moonmind.omnigent.harness_platform.stores import DbExecutionPlanStore
from moonmind.omnigent.settings import resolved_server_url
from moonmind.workflows.adapters.omnigent_client import OmnigentHttpClient
from moonmind.workflows.temporal.service import TemporalExecutionService
from tests.integration.omnigent import (
    test_generic_native_durable_journey as native_fixture,
)
from tests.integration.omnigent.test_generic_native_durable_journey import (
    _DURABLE_SKILL_NAME,
    PROFILE,
)
from tests.integration.omnigent.test_native_default_admission import (
    _execute_admitted_generic_plan,
)
from tests.unit.api.routers.test_execution_saved_profile_boundaries import agent_requests
from tests.unit.api.routers.test_executions import _override_user_dependencies

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]
native_journey = native_fixture.native_journey
session_factory = native_fixture.session_factory


@pytest.mark.parametrize(
    "native_journey", ["codex-native", "claude-native", "opencode-native"],
    indirect=True,
)
async def test_fresh_serialized_native_workflow_uses_shared_plan_and_keeps_retry(
    native_journey, monkeypatch,
):
    journey = native_journey
    monkeypatch.setattr(settings.workflow, "default_runtime", "omnigent")
    monkeypatch.setattr(settings.workflow, "github_repository", "")
    inventory = await OmnigentHttpClient(base_url=resolved_server_url()).list_agents()
    async with journey.session_factory() as session:
        provider = await session.get(ManagedAgentProviderProfile, PROFILE)
        await reconcile_bootstrap_agent_profile(session, inventory=inventory)
        await synchronize_omnigent_harness_catalog(session)
        app = FastAPI()
        app.include_router(router)
        service = TemporalExecutionService(session)
        service._client_adapter.start_workflow = AsyncMock(
            return_value=SimpleNamespace(run_id="raw-shared-run")
        )
        app.dependency_overrides[_get_service] = lambda: service
        app.dependency_overrides[get_async_session] = lambda: session
        _override_user_dependencies(app, is_superuser=True)
        # OpenCode is a harness identity, rather than a direct backend alias.
        backend = provider.runtime_id if journey.harness != "opencode-native" else "omnigent"
        body = {
            "workflowType": "MoonMind.UserWorkflow",
            "title": "Keep the operator title",
            "failurePolicy": "continue_and_report",
            "idempotencyKey": "raw-shared-native",
            "initialParameters": {
                "workflow": {
                    "instructions": "Generate a report.",
                    "skill": {"id": _DURABLE_SKILL_NAME},
                    "runtime": {
                        "mode": backend,
                        "providerProfileRef": PROFILE,
                        "model": journey.server.model,
                    },
                    "publish": {"mode": "none"},
                },
                "workspaceSpec": {"workspaceSource": {"kind": "scratch"}},
            },
        }
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post("/api/executions", json=body)
            assert response.status_code == 201, response.text
            record = await session.get(
                TemporalExecutionCanonicalRecord, response.json()["workflowId"]
            )
            assert response.json()["title"] == body["title"]
            assert record.parameters["failurePolicy"] == "continue_and_report"
            assert record.parameters["targetRuntime"] == "omnigent"
            binding = record.parameters["omnigentExecutionPlan"]
            plan = await DbExecutionPlanStore(journey.session_factory).load(binding["planRef"])
            assert plan is not None
            assert plan.payload.executionRealizerRef == "generic-omnigent-host@1"
            assert plan.payload.harnessId == journey.harness
            assert plan.payload.modelConfig.qualifiedId == journey.server.model
            assert record.parameters["profileId"] == PROFILE
            requests = agent_requests(record.parameters)
            assert requests and all(r.agent_id == "omnigent" for r in requests.values())

            # An accepted identity is returned before loading today's account
            # defaults or compiling another plan. No recorded binding is changed.
            provider.enabled = False
            await session.commit()
            retry = await client.post("/api/executions", json=body)
            assert retry.status_code == 201, retry.text
            assert retry.json()["workflowId"] == record.workflow_id
            await session.refresh(record)
            assert record.parameters["omnigentExecutionPlan"] == binding
            assert service._client_adapter.start_workflow.await_count == 1
            provider.enabled = True
            await session.commit()
        await _execute_admitted_generic_plan(journey, plan, record)
