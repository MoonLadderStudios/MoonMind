"""Profile options retain their selected meaning across shared admission."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from api_service.api.routers.executions import router, _get_service
from api_service.db.base import get_async_session
from api_service.db.models import (
    ManagedAgentProviderProfile,
    TemporalExecutionCanonicalRecord,
)
from api_service.services.omnigent_agent_bootstrap_service import (
    reconcile_bootstrap_agent_profile,
)
from moonmind.config.settings import settings
from moonmind.omnigent.settings import resolved_server_url
from moonmind.omnigent.harness_platform.stores import DbExecutionPlanStore
from moonmind.workflows.adapters.omnigent_client import OmnigentHttpClient
from moonmind.workflows.temporal.service import TemporalExecutionService
from tests.integration.omnigent import (
    test_generic_native_durable_journey as native_fixture,
)
from tests.unit.api.routers.test_execution_saved_profile_boundaries import (
    agent_requests,
)
from tests.unit.api.routers.test_executions import _override_user_dependencies

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]
native_journey = native_fixture.native_journey
session_factory = native_fixture.session_factory


@pytest.mark.parametrize(
    "native_journey", ["codex-native", "claude-native"], indirect=True
)
@pytest.mark.parametrize(
    "option",
    [
        "env_template",
        "file_templates",
        "home_path_overrides",
        "model_overrides",
        "step_model_overrides",
    ],
)
async def test_product_creation_preserves_the_exact_oauth_profile_options(
    native_journey, monkeypatch, option
):
    journey = native_journey
    runtime = "codex_cli" if journey.harness == "codex-native" else "claude_code"
    monkeypatch.setattr(settings.workflow, "default_runtime", runtime)
    monkeypatch.setattr(settings.workflow, "github_repository", "")
    values = {
        "env_template": {"RUNTIME_NONSECRET_TUNING": "accepted-setting"},
        "file_templates": [
            {
                "path": "{{runtime_support_dir}}/accepted-settings.txt",
                "format": "text",
                "content_template": "accepted-setting",
            }
        ],
        "home_path_overrides": {"HOME": "{{runtime_support_dir}}/accepted-home"},
        "model_overrides": {"selected-model-alias": journey.server.model},
        "step_model_overrides": {"selected-model-alias": journey.server.model},
    }
    inventory = await OmnigentHttpClient(base_url=resolved_server_url()).list_agents()
    async with journey.session_factory() as session:
        profile = await session.get(ManagedAgentProviderProfile, native_fixture.PROFILE)
        assert (
            str(getattr(profile.credential_source, "value", profile.credential_source))
            == "oauth_volume"
        )
        field = "model_overrides" if option == "step_model_overrides" else option
        setattr(profile, field, values[option])
        await session.commit()
        await reconcile_bootstrap_agent_profile(session, inventory=inventory)
        service = TemporalExecutionService(session)
        service._client_adapter.start_workflow = AsyncMock(
            return_value=SimpleNamespace(run_id="accepted-run")
        )
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[_get_service] = lambda: service
        app.dependency_overrides[get_async_session] = lambda: session
        _override_user_dependencies(app, is_superuser=True)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/executions",
                json={
                    "type": "workflow",
                    "payload": {
                        "workflow": {
                            "instructions": "Preserve my selected Profile settings.",
                            "runtime": {
                                "mode": runtime,
                                "providerProfileRef": native_fixture.PROFILE,
                                **(
                                    {"model": "selected-model-alias"}
                                    if option == "model_overrides"
                                    else {}
                                ),
                            },
                            "steps": [
                                {
                                    "id": "work",
                                    "instructions": "Perform the accepted task.",
                                    "type": "skill",
                                    "skill": {"id": native_fixture._DURABLE_SKILL_NAME},
                                    **(
                                        {"runtime": {"model": "selected-model-alias"}}
                                        if option == "step_model_overrides"
                                        else {}
                                    ),
                                }
                            ],
                        },
                        "publishMode": "none",
                    },
                },
            )
        assert response.status_code == 201, response.text
        record = await session.get(
            TemporalExecutionCanonicalRecord, response.json()["workflowId"]
        )
        request = agent_requests(record.parameters)["work"]
        if field == "model_overrides":
            assert record.parameters["targetRuntime"] == "omnigent"
            assert request.agent_kind == "external" and request.agent_id == "omnigent"
            plan = await DbExecutionPlanStore(journey.session_factory).load(
                request.omnigent_execution_plan.plan_ref
            )
            assert plan.payload.modelConfig.qualifiedId == journey.server.model
        else:
            assert record.parameters["targetRuntime"] == runtime
            assert not record.parameters.get("omnigentExecutionPlan")
            assert request.agent_kind == "managed" and request.agent_id == runtime
        assert request.execution_profile_ref == native_fixture.PROFILE
        await session.refresh(profile)
        assert getattr(profile, field) == values[option]
