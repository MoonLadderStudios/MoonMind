"""A specialized step receives its own Profile, harness and credential plan."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from api_service.api.routers.executions import _get_service, router
from api_service.db.base import get_async_session
from api_service.db.models import (
    ManagedAgentProviderProfile, ProviderCredentialSource, RuntimeMaterializationMode,
    TemporalExecutionCanonicalRecord,
)
from api_service.services.omnigent_agent_bootstrap_service import reconcile_bootstrap_agent_profile
from api_service.services.omnigent_agent_profile_service import synchronize_omnigent_harness_catalog
from api_service.services.omnigent_execution_plan_service import readmit_frozen_execution_plan
from moonmind.config.settings import settings
from moonmind.omnigent.harness_platform.stores import DbExecutionPlanStore
from moonmind.omnigent.settings import resolved_server_url
from moonmind.workflows.adapters.omnigent_client import OmnigentHttpClient
from moonmind.workflows.temporal.service import TemporalExecutionService
from tests.integration.omnigent import test_generic_native_durable_journey as native_fixture
from tests.integration.omnigent.test_generic_native_durable_journey import _DURABLE_SKILL_NAME, PROFILE
from tests.unit.api.routers.test_execution_saved_profile_boundaries import agent_requests
from tests.unit.api.routers.test_executions import _override_user_dependencies

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]
native_journey = native_fixture.native_journey
session_factory = native_fixture.session_factory


@pytest.mark.parametrize("native_journey,step_harness", [
    ("codex-native", "codex-native"), ("claude-native", "claude-native"),
    ("opencode-native", "opencode-native"), ("codex-native", "claude-native"),
    ("claude-native", "codex-native"), ("opencode-native", "codex-native"),
], indirect=["native_journey"])
async def test_independent_profile_and_harness_get_their_own_shared_plan(native_journey, step_harness, monkeypatch):
    journey = native_journey
    monkeypatch.setattr(settings.workflow, "default_runtime", "omnigent")
    monkeypatch.setattr(settings.workflow, "github_repository", "")
    runtime_id = {"codex-native": "codex_cli", "claude-native": "claude_code", "opencode-native": "opencode"}[step_harness]
    provider_id = "anthropic" if step_harness == "claude-native" else "openai"
    capabilities = native_fixture.provider_profile_creation_capabilities(runtime_id=runtime_id, provider_id=provider_id)
    oauth = step_harness != "opencode-native"
    method = next(m for m in capabilities["authentication_methods"] if m["id"] == ("oauth" if oauth else "api_key"))
    fields = {key: field["value"] for key, field in method["fields"].items()}
    fields["credential_source"] = ProviderCredentialSource(fields["credential_source"])
    fields["runtime_materialization_mode"] = RuntimeMaterializationMode(fields["runtime_materialization_mode"])
    selected_profile = PROFILE + "-independent"
    journey.server.registered_harnesses = sorted({journey.harness, step_harness})
    inventory = await OmnigentHttpClient(base_url=resolved_server_url()).list_agents()
    async with journey.session_factory() as session:
        root_provider = await session.get(ManagedAgentProviderProfile, PROFILE)
        session.add(ManagedAgentProviderProfile(
            profile_id=selected_profile, runtime_id=runtime_id, provider_id=provider_id,
            **fields, volume_ref="journey_step_oauth" if oauth else None,
            volume_mount_path=method["imported_volume"]["mount_path"] if oauth else None,
            secret_refs={role["role"]: "env://NATIVE_JOURNEY_KEY" for role in method["secret_roles"]},
            credential_generation=5, max_parallel_runs=1, enabled=True,
            auth_state="connected", last_auth_method="oauth_volume" if oauth else "secret_ref",
        ))
        await session.commit()
        await reconcile_bootstrap_agent_profile(session, inventory=inventory)
        await synchronize_omnigent_harness_catalog(session)
        app = FastAPI()
        app.include_router(router)
        service = TemporalExecutionService(session)
        service._client_adapter.start_workflow = AsyncMock(return_value=SimpleNamespace(run_id="step-profiles-run"))
        app.dependency_overrides[_get_service] = lambda: service
        app.dependency_overrides[get_async_session] = lambda: session
        _override_user_dependencies(app, is_superuser=True)
        root_mode = root_provider.runtime_id if journey.harness != "opencode-native" else "omnigent"
        step_mode = runtime_id if step_harness != "opencode-native" else "omnigent"
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/executions", json={"type": "workflow", "payload": {
                "workflow": {"instructions": "Use the selected accounts.",
                    "runtime": {"mode": root_mode, "providerProfileRef": PROFILE, "model": journey.server.model},
                    "steps": [
                        {"id": "root", "instructions": "Use the root account.", "type": "skill", "skill": {"id": _DURABLE_SKILL_NAME}},
                        {"id": "selected", "instructions": "Use the step account.", "type": "skill", "skill": {"id": _DURABLE_SKILL_NAME}, "runtime": {"mode": step_mode, "providerProfileRef": selected_profile, "model": "example/step-model"}},
                    ]},
                "publishMode": "none", "workspace": {"workspaceSource": {"kind": "scratch"}},
            }})
        assert response.status_code == 201, response.text
        record = await session.get(TemporalExecutionCanonicalRecord, response.json()["workflowId"])
        assert record.parameters["targetRuntime"] == "omnigent"
        requests = agent_requests(record.parameters)
        assert requests["root"].agent_id == requests["selected"].agent_id == "omnigent"
        assert requests["root"].execution_profile_ref == PROFILE
        assert requests["selected"].execution_profile_ref == selected_profile
        plans = DbExecutionPlanStore(journey.session_factory)
        root = await plans.load(requests["root"].omnigent_execution_plan.plan_ref)
        selected = await plans.load(requests["selected"].omnigent_execution_plan.plan_ref)
        assert selected.planRef != root.planRef
        assert root.payload.harnessId == journey.harness
        assert selected.payload.harnessId == step_harness
        assert root.payload.credentialBindings["primary-model"].providerProfileRef == PROFILE
        assert selected.payload.credentialBindings["primary-model"].providerProfileRef == selected_profile
        assert selected.payload.modelConfig.qualifiedId == "example/step-model"
        assert record.parameters["profileId"] == PROFILE
        assert service._client_adapter.start_workflow.await_count == 1
        saved = record.parameters["omnigentStepExecutionPlans"]["selected"]
        assert saved["agentProfileSnapshot"]["providerProfileRef"] == selected_profile
        assert selected.payload.launchPolicyRef == saved["agentProfileSnapshot"]["launchPolicyRef"]
        assert record.parameters["agentProfileSnapshot"]["providerProfileRef"] == PROFILE
        child = await session.get(ManagedAgentProviderProfile, selected_profile)
        child.execution_configuration = {"profileId": "missing-current-config", "version": 99, "digest": "sha256:" + "a" * 64}
        await session.commit()
        successor = await readmit_frozen_execution_plan(
            db_session=session, artifact_service=None, workflow_id=record.workflow_id,
            initial_parameters=record.parameters, subject="Profile step test",
            preserve_recorded_intent=True,
        )
        assert successor["disposition"] == "admitted", successor
        continued = successor["omnigentStepExecutionPlans"]["selected"]
        assert continued["agentProfileSnapshot"] == saved["agentProfileSnapshot"]
        continued_plan = await plans.load(continued["omnigentExecutionPlan"]["planRef"])
        assert continued_plan.payload.harnessId == selected.payload.harnessId
        assert continued_plan.payload.modelConfig == selected.payload.modelConfig
        assert continued_plan.payload.credentialBindings == selected.payload.credentialBindings
        if journey.harness == step_harness == "codex-native":
            from copy import deepcopy
            from api_service.db.models import MoonMindWorkflowState
            from moonmind.workflows.temporal.service import TemporalExecutionRerunPlanError
            from moonmind.workflows.temporal.artifacts import ArtifactAccessContext, TemporalArtifactRepository, TemporalArtifactService

            source_id = record.workflow_id
            altered = deepcopy(record.parameters)
            altered["omnigentStepExecutionPlans"]["selected"]["profileId"] = PROFILE
            with pytest.raises(TemporalExecutionRerunPlanError):
                await service.validate_exact_rerun_execution_plan(parameters=altered)
            record.state = MoonMindWorkflowState.COMPLETED
            await session.commit()
            async def start_with_grants(**kwargs):
                async with journey.session_factory() as reader:
                    artifacts = TemporalArtifactService(TemporalArtifactRepository(reader))
                    for ref in (requests["root"].omnigent_execution_plan.plan_artifact_ref, requests["selected"].omnigent_execution_plan.plan_artifact_ref, requests["selected"].resolved_skillset_ref):
                        await artifacts.read(artifact_id=ref, access=ArtifactAccessContext.for_execution(kwargs["workflow_id"]))
                return SimpleNamespace(run_id="step-plans-rerun")
            service._client_adapter.start_workflow = AsyncMock(side_effect=start_with_grants)
            rerun = await service.create_fresh_rerun_execution(workflow_id=source_id, idempotency_key="exact-step-plan-rerun")
            destination = await session.get(TemporalExecutionCanonicalRecord, rerun["workflow_id"])
            assert destination.parameters["omnigentStepExecutionPlans"] == record.parameters["omnigentStepExecutionPlans"]
            assert destination.parameters["omnigentExecutionPlan"] == record.parameters["omnigentExecutionPlan"]
            assert destination.workflow_id != source_id
