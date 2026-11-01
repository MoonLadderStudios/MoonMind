"""Independent step model choices receive their own immutable shared plan."""

from types import SimpleNamespace
from copy import deepcopy
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from api_service.api.routers.executions import _get_service, router
from api_service.db.base import get_async_session
from api_service.db.models import ManagedAgentProviderProfile, TemporalExecutionCanonicalRecord
from api_service.services.omnigent_agent_bootstrap_service import reconcile_bootstrap_agent_profile
from api_service.services.omnigent_agent_profile_service import synchronize_omnigent_harness_catalog
from api_service.services.omnigent_execution_plan_service import readmit_frozen_execution_plan
from moonmind.config.settings import settings
from moonmind.omnigent.harness_platform.stores import DbExecutionPlanStore
from moonmind.omnigent.harness_platform.execution_plan import bind_omnigent_model_selection
from moonmind.omnigent.settings import resolved_server_url
from moonmind.workflows.adapters.omnigent_client import OmnigentHttpClient
from moonmind.workflows.temporal.service import TemporalExecutionService
from moonmind.workflows.temporal.artifacts import (
    ArtifactAccessContext, TemporalArtifactAuthorizationError, TemporalArtifactRepository,
    TemporalArtifactService,
)
from tests.integration.omnigent import test_generic_native_durable_journey as native_fixture
from tests.integration.omnigent.test_generic_native_durable_journey import _DURABLE_SKILL_NAME, PROFILE
from tests.unit.api.routers.test_execution_saved_profile_boundaries import agent_requests
from tests.unit.api.routers.test_executions import _override_user_dependencies

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]
native_journey = native_fixture.native_journey
session_factory = native_fixture.session_factory


@pytest.mark.parametrize("native_journey", ["codex-native", "claude-native", "opencode-native"], indirect=True)
@pytest.mark.parametrize("specialization", ["model", "effort", "clear_effort", "tier"])
async def test_independent_step_model_uses_its_own_shared_plan(native_journey, monkeypatch, specialization):
    journey = native_journey
    monkeypatch.setattr(settings.workflow, "default_runtime", "omnigent")
    monkeypatch.setattr(settings.workflow, "github_repository", "")
    inventory = await OmnigentHttpClient(base_url=resolved_server_url()).list_agents()
    async with journey.session_factory() as session:
        provider = await session.get(ManagedAgentProviderProfile, PROFILE)
        provider.model_tiers = [
            {"model": journey.server.model, "effort": "medium"},
            {"model": "example/independent-model", "effort": "high"},
        ]
        await session.commit()
        root_runtime = {"mode": provider.runtime_id, "providerProfileRef": PROFILE, "modelTier": 1}
        step_runtime, expected_model, expected_effort = {
            "model": ({"model": "example/independent-model", "effort": "medium"}, "example/independent-model", "medium"),
            "effort": ({"model": journey.server.model, "effort": "high"}, journey.server.model, "high"),
            "clear_effort": ({"model": journey.server.model, "effort": None}, journey.server.model, "high" if journey.harness == "codex-native" else None),
            "tier": ({"modelTier": 2}, "example/independent-model", "high"),
        }[specialization]
        await reconcile_bootstrap_agent_profile(session, inventory=inventory)
        await synchronize_omnigent_harness_catalog(session)
        app = FastAPI()
        app.include_router(router)
        service = TemporalExecutionService(session)
        service._client_adapter.start_workflow = AsyncMock(return_value=SimpleNamespace(run_id="step-plans-run"))
        app.dependency_overrides[_get_service] = lambda: service
        app.dependency_overrides[get_async_session] = lambda: session
        _override_user_dependencies(app, is_superuser=True)
        backend = provider.runtime_id if journey.harness != "opencode-native" else "omnigent"
        root_runtime["mode"] = backend
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/executions", json={
                "type": "workflow",
                "payload": {
                    "workflow": {
                        "instructions": "Generate and check a report.",
                        "runtime": root_runtime,
                        "steps": [
                            {"id": "generate", "instructions": "Generate a report.", "type": "skill", "skill": {"id": _DURABLE_SKILL_NAME}},
                            {"id": "verify", "instructions": "Check a report.", "type": "skill", "skill": {"id": _DURABLE_SKILL_NAME}, "runtime": step_runtime},
                        ],
                    },
                    "publishMode": "none",
                    "workspace": {"workspaceSource": {"kind": "scratch"}},
                },
            })
        assert response.status_code == 201, response.text
        record = await session.get(TemporalExecutionCanonicalRecord, response.json()["workflowId"])
        assert record.parameters["targetRuntime"] == "omnigent"
        requests = agent_requests(record.parameters)
        assert set(requests) == {"generate", "verify"}
        assert all(request.agent_id == "omnigent" for request in requests.values())
        root = requests["generate"].omnigent_execution_plan
        specialized = requests["verify"].omnigent_execution_plan
        assert root is not None and specialized is not None
        assert root.plan_ref != specialized.plan_ref
        plans = DbExecutionPlanStore(journey.session_factory)
        root_plan, step_plan = await plans.load(root.plan_ref), await plans.load(specialized.plan_ref)
        assert root_plan.payload.modelConfig.qualifiedId == journey.server.model
        assert step_plan.payload.modelConfig.qualifiedId == expected_model
        assert step_plan.payload.modelConfig.effort == expected_effort
        assert root_plan.payload.harnessId == step_plan.payload.harnessId == journey.harness
        assert root_plan.payload.credentialBindings == step_plan.payload.credentialBindings
        bound = bind_omnigent_model_selection(requests["verify"], model=expected_model, effort=expected_effort)
        assert bound.parameters["model"] == expected_model
        assert bound.parameters["effort"] == expected_effort
        assert service._client_adapter.start_workflow.await_count == 1
        artifacts = TemporalArtifactService(TemporalArtifactRepository(session))
        for ref in (specialized.plan_artifact_ref, requests["verify"].resolved_skillset_ref):
            await artifacts.read(artifact_id=ref, access=ArtifactAccessContext.for_execution(record.workflow_id))
            with pytest.raises(TemporalArtifactAuthorizationError):
                await artifacts.read(artifact_id=ref, access=ArtifactAccessContext.for_execution("unrelated-workflow"))
        original_binding = dict(record.parameters["omnigentExecutionPlan"])
        original_step_plans = dict(record.parameters["omnigentStepExecutionPlans"])
        successor = await readmit_frozen_execution_plan(
            db_session=session, artifact_service=None, workflow_id=record.workflow_id,
            initial_parameters=record.parameters, subject="Step plan test",
            preserve_recorded_intent=True,
        )
        assert successor["disposition"] == "admitted", successor
        successor_binding = successor["omnigentStepExecutionPlans"]["verify"]["omnigentExecutionPlan"]
        successor_plan = await plans.load(successor_binding["planRef"])
        assert successor_plan.payload.modelConfig == step_plan.payload.modelConfig
        # The same transaction/grant owner used by scheduled occurrences and
        # interrupted attempts must authorize every new step artifact.
        from api_service.db import base
        from moonmind.workflows.temporal.activities.omnigent_activities import _link_execution_plan_inputs

        monkeypatch.setattr(base, "async_session_maker", journey.session_factory)
        await _link_execution_plan_inputs(
            successor["omnigentExecutionPlan"],
            execution=SimpleNamespace(workflow_id=record.workflow_id, workflow_run_id=record.run_id, namespace="default"),
            admitted_step_plans=successor["omnigentStepExecutionPlans"],
        )
        successor_skillset = successor["omnigentStepExecutionPlans"]["verify"]["resolvedSkillsetRef"]
        await artifacts.read(artifact_id=successor_skillset, access=ArtifactAccessContext.for_execution(record.workflow_id))
        with pytest.raises(TemporalArtifactAuthorizationError):
            await artifacts.read(artifact_id=successor_skillset, access=ArtifactAccessContext.for_execution("unrelated-workflow"))
        assert record.parameters["omnigentExecutionPlan"] == original_binding
        assert record.parameters["omnigentStepExecutionPlans"] == original_step_plans
        if specialization == "model":
            from tests.integration.omnigent.test_native_default_admission import _execute_admitted_generic_plan

            journey.server.additional_models = [expected_model]
            await _execute_admitted_generic_plan(journey, step_plan, SimpleNamespace(
                workflow_id=record.workflow_id, run_id=record.run_id,
                parameters={**record.parameters, **record.parameters["omnigentStepExecutionPlans"]["verify"]},
            ))
        if specialization == "model" and journey.harness == "codex-native":
            from temporalio import activity
            from moonmind.workflows.temporal.activity_runtime import TemporalActivityRuntimeError, TemporalPlanActivities

            workflow_id = record.workflow_id
            run_id = record.run_id
            monkeypatch.setattr(activity, "in_activity", lambda: True)
            monkeypatch.setattr(activity, "info", lambda: SimpleNamespace(workflow_id=workflow_id, workflow_run_id=run_id, namespace="default"))
            planner = TemporalPlanActivities(artifact_service=artifacts)
            admitted_parameters = deepcopy(record.parameters)
            for tamper in ("replace", "remove"):
                parameters = deepcopy(admitted_parameters)
                if tamper == "replace":
                    parameters["omnigentStepExecutionPlans"]["verify"]["omnigentExecutionPlan"] = parameters["omnigentExecutionPlan"]
                else:
                    parameters.pop("omnigentStepExecutionPlans")
                refused = await readmit_frozen_execution_plan(
                    db_session=session, artifact_service=None, workflow_id=workflow_id,
                    initial_parameters=parameters, subject="Step handoff tamper test",
                    preserve_recorded_intent=True,
                )
                assert refused["disposition"] == "recorded", refused
                assert "step" in refused["reason"] and "authority" in refused["reason"]
                with pytest.raises(TemporalActivityRuntimeError, match="step.*authority"):
                    await planner.plan_validate(
                        plan_ref="unused-agent-plan", principal=f"workflow:{workflow_id}",
                        omnigent_execution_plan=admitted_parameters["omnigentExecutionPlan"],
                        execution_parameters=parameters,
                    )
        if specialization == "clear_effort" and journey.harness == "codex-native":
            monkeypatch.setattr(settings.workflow, "codex_effort", "low")
            refused = await readmit_frozen_execution_plan(
                db_session=session, artifact_service=None, workflow_id=record.workflow_id,
                initial_parameters=record.parameters, subject="Step plan drift test",
                preserve_recorded_intent=True,
            )
            assert refused["disposition"] == "recorded"
            assert "step verify" in refused["reason"] and "model" in refused["reason"]
