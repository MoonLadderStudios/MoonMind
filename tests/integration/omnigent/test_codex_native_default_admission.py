"""A Codex Profile's native model-default choice survives product admission."""

from copy import deepcopy
import json
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
from api_service.services.omnigent_execution_plan_service import (
    compile_frozen_execution_plan,
    changed_admitted_intent,
)
from moonmind.config.settings import settings
from moonmind.omnigent.harness_platform.stores import DbExecutionPlanStore
from moonmind.omnigent.settings import resolved_server_url
from moonmind.omnigent.realizers.generic_host import GenericOmnigentHostRealizer
from moonmind.schemas.agent_runtime_models import OmnigentExecutionPlanBinding
from moonmind.workflows.adapters.omnigent_agent_adapter import (
    OmnigentResolvedTarget,
    build_omnigent_selection,
    build_omnigent_session_create_payload,
)
from moonmind.workflows.adapters.omnigent_client import OmnigentHttpClient
from moonmind.workflows.temporal.service import TemporalExecutionService
from moonmind.workflows.temporal.artifacts import (
    ArtifactAccessContext,
    ArtifactTransactionOwnership,
    TemporalArtifactRepository,
    TemporalArtifactService,
)
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
    "native_journey",
    [
        {
            "harness": harness,
            "static": static,
            "codexNativeDefault": True,
            **(
                {"privateProfileConfiguration": True}
                if harness.endswith("api-key")
                else {}
            ),
        }
        for static in (False, True)
        for harness in ("codex-native", "codex-native-api-key")
        if not (static and harness.endswith("api-key"))
    ]
    + [
        {
            "harness": "codex-native",
            "codexNativeDefault": True,
            "hiddenNativeDefault": True,
        }
    ],
    indirect=True,
    ids=[
        "oauth-on-demand",
        "key-preferences-on-demand",
        "oauth-static",
        "oauth-hidden-default",
    ],
)
async def test_frozen_native_default_runs_and_saves_through_the_shared_lifecycle(
    native_journey,
):
    journey = native_journey
    async with journey.session_factory() as session:
        provider = await session.get(
            ManagedAgentProviderProfile, native_fixture.PROFILE
        )
        provider.command_behavior = {"suppress_default_model_flag": False}
        provider.default_model = "later-profile-default"
        await session.commit()
    if journey.static_mode:
        from tests.integration.omnigent.test_generic_static_host_journey import (
            assert_static_native_turn_saved_and_retained,
        )

        await assert_static_native_turn_saved_and_retained(journey)
    else:
        await native_fixture.assert_native_turn_saved_and_cleaned(journey)
    assert (
        journey.plan.payload.modelConfig.normalizedOptions.get("codexNativeDefault")
        is True
    )


@pytest.mark.parametrize("native_journey", ["codex-native"], indirect=True)
@pytest.mark.parametrize("model_choice", ["omitted", "same-default", "different-model"])
async def test_native_default_profile_uses_shared_admission_without_losing_explicit_models(
    native_journey, monkeypatch, model_choice
):
    journey = native_journey
    profile_default = "gpt-profile-default"
    monkeypatch.setattr(settings.workflow, "default_runtime", "codex_cli")
    monkeypatch.setattr(settings.workflow, "github_repository", "")
    inventory = await OmnigentHttpClient(base_url=resolved_server_url()).list_agents()
    async with journey.session_factory() as session:
        provider = await session.get(
            ManagedAgentProviderProfile, native_fixture.PROFILE
        )
        provider.default_model = profile_default
        provider.model_tiers = []
        provider.default_model_tier = 1
        provider.command_behavior = {"suppress_default_model_flag": True}
        await session.commit()
        await reconcile_bootstrap_agent_profile(session, inventory=inventory)
        service = TemporalExecutionService(session)
        service._client_adapter.start_workflow = AsyncMock(
            return_value=SimpleNamespace(run_id="admitted-run")
        )
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[_get_service] = lambda: service
        app.dependency_overrides[get_async_session] = lambda: session
        _override_user_dependencies(app, is_superuser=True)
        runtime = {"mode": "codex_cli", "providerProfileRef": native_fixture.PROFILE}
        if model_choice != "omitted":
            runtime["model"] = (
                profile_default
                if model_choice == "same-default"
                else journey.server.model
            )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/executions",
                json={
                    "type": "workflow",
                    "payload": {
                        "publishMode": "none",
                        "workflow": {
                            "instructions": "Keep my Codex native model-default choice.",
                            "runtime": runtime,
                            "steps": [
                                {
                                    "id": "work",
                                    "type": "skill",
                                    "instructions": "Perform the accepted task.",
                                    "skill": {"id": native_fixture._DURABLE_SKILL_NAME},
                                }
                            ],
                        },
                    },
                },
            )
        assert response.status_code == 201, response.text
        record = await session.get(
            TemporalExecutionCanonicalRecord, response.json()["workflowId"]
        )
        request = agent_requests(record.parameters)["work"]
        assert record.parameters["targetRuntime"] == "omnigent"
        assert request.agent_kind == "external" and request.agent_id == "omnigent"
        assert request.execution_profile_ref == native_fixture.PROFILE
        plan = await DbExecutionPlanStore(journey.session_factory).load(
            request.omnigent_execution_plan.plan_ref
        )
        assert plan.payload.harnessId == "codex-native"
        assert (
            plan.payload.credentialBindings["primary-model"].providerProfileRef
            == native_fixture.PROFILE
        )
        if model_choice == "different-model":
            assert plan.payload.modelConfig.qualifiedId == journey.server.model
        await session.refresh(provider)
        assert provider.command_behavior == {"suppress_default_model_flag": True}
        before = deepcopy(request.parameters)
        bound = GenericOmnigentHostRealizer._bind_exact_host(
            SimpleNamespace(ref="generic-omnigent-host@1"),
            request,
            plan,
            {"omnigentHostId": "selected-host", "workspacePath": "/workspaces/run"},
            SimpleNamespace(
                bindingId="selected-binding",
                hostBindingRef="host-binding",
                hostLeaseRef="host-lease",
                providerLeases={},
            ),
        )
        payload = build_omnigent_session_create_payload(
            request=bound,
            selection=build_omnigent_selection(bound),
            target=OmnigentResolvedTarget(
                agent_id="codex-native-ui", source="agent_id"
            ),
        )
        delivered = []

        async def native_create(incoming):
            delivered.append(json.loads(incoming.content))
            return httpx.Response(201, json={"id": "selected-session"})

        native = OmnigentHttpClient(
            base_url="https://omnigent.test",
            transport=httpx.MockTransport(native_create),
        )
        await native.create_session(payload)
        expected = journey.server.model if model_choice == "different-model" else None
        assert delivered[0].get("model_override") == expected
        assert delivered[0]["host_id"] == "selected-host"
        assert request.parameters == before

        root_binding = OmnigentExecutionPlanBinding.model_validate(
            record.parameters["omnigentExecutionPlan"]
        )
        root = await DbExecutionPlanStore(journey.session_factory).load(
            root_binding.plan_ref
        )
        access = ArtifactAccessContext.for_execution(
            record.workflow_id, principal="service:execution-plan"
        )
        artifacts = TemporalArtifactService(
            TemporalArtifactRepository(session),
            transaction_ownership=ArtifactTransactionOwnership.CALLER,
        )
        row, body = await artifacts.read(
            artifact_id=root.payload.agentProfileSnapshotRef.removeprefix("artifact:"),
            access=access,
        )
        provider.default_model = "later-profile-default"
        provider.command_behavior = {"suppress_default_model_flag": False}
        await session.flush()
        frozen = await compile_frozen_execution_plan(
            db_session=session,
            artifact_service=artifacts,
            workflow_id=record.workflow_id,
            principal=row.created_by_principal,
            snapshot=json.loads(body),
            provider_profile=provider,
            initial_parameters=record.parameters,
            binding=root_binding,
            access=access,
        )
        assert changed_admitted_intent(root.payload, frozen.envelope.payload) == []
