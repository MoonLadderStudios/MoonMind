"""Ordinary saved-Profile admission uses the catalog-backed shared plan owner.

Reuses the composed native journey's controlled transports and real storage.
This joins API admission and bootstrap inventory to the immutable plan actually
used by the shared durable lifecycle. Temporal scheduling/grants remain controlled;
this does not qualify an installed image or execute a paid provider turn.
No generic Codex opt-in is supplied. An explicit-false control proves that
disabling the shared owner fails closed rather than reopening a replaced owner.
"""

import hashlib
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from api_service.api.routers.executions import _get_service, router
from api_service.db.base import get_async_session
from api_service.db.models import (
    ManagedAgentProviderProfile,
    ProviderProfileSlotLease,
    TemporalExecutionCanonicalRecord,
)
from api_service.services.omnigent_agent_bootstrap_service import (
    reconcile_bootstrap_agent_profile,
)
from api_service.services.omnigent_agent_profile_service import (
    synchronize_omnigent_harness_catalog,
)
from api_service.services.provider_profile_readiness import (
    provider_profile_launch_ready,
)
from api_service.services.settings_catalog import SettingsCatalogService
from moonmind.config.settings import settings
from moonmind.omnigent.harness_platform.stores import DbExecutionPlanStore
from moonmind.omnigent.settings import resolved_server_url
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from moonmind.workflows.adapters.omnigent_client import OmnigentHttpClient
from moonmind.workflows.temporal.service import TemporalExecutionService
from tests.integration.omnigent import (
    test_generic_native_durable_journey as native_fixture,
)
from tests.integration.omnigent.test_generic_native_durable_journey import (
    _DURABLE_SKILL_NAME,
    PROFILE,
    assert_native_turn_saved_and_cleaned,
)
from tests.unit.api.routers.test_executions import _override_user_dependencies
from tests.unit.api.routers.test_execution_saved_profile_boundaries import (
    agent_requests,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]

native_journey = native_fixture.native_journey
session_factory = native_fixture.session_factory


@pytest.mark.parametrize(
    "native_journey,shared_default,saved_profile",
    [
        ("codex-native", True, True),
        ("codex-native", False, True),
        ("claude-native", True, True),
        ("opencode-native", True, True),
        ("codex-native", True, False),
        ("claude-native", True, False),
    ],
    indirect=["native_journey"],
)
async def test_saved_profile_default_and_explicit_backend_admit_same_plan_owner(
    native_journey,
    shared_default,
    saved_profile,
    monkeypatch,
):
    journey = native_journey
    if shared_default and journey.harness == "codex-native":
        monkeypatch.setenv("MOONMIND_CODEX_DIRECT_RETIRED_AT", "2020-01-01T00:00:00Z")
    if shared_default:
        assert "MOONMIND_OMNIGENT_GENERIC_CODEX_QUALIFIED" not in os.environ
    else:
        monkeypatch.setenv("MOONMIND_OMNIGENT_GENERIC_CODEX_QUALIFIED", "false")
    expected_realizer = "generic-omnigent-host@1"
    monkeypatch.setattr(settings.workflow, "default_runtime", "omnigent")
    monkeypatch.setattr(settings.workflow, "github_repository", "")
    # Discovery's existing client adapter derives session.start from the stock
    # list endpoint contract. Never add capabilities to the provider response.
    inventory = await OmnigentHttpClient(base_url=resolved_server_url()).list_agents()
    async with journey.session_factory() as session:
        provider = await session.get(ManagedAgentProviderProfile, PROFILE)
        assert provider_profile_launch_ready(provider)
        if not saved_profile:
            monkeypatch.setattr(
                settings.workflow, "default_runtime", provider.runtime_id
            )
        await reconcile_bootstrap_agent_profile(session, inventory=inventory)
        await synchronize_omnigent_harness_catalog(session)
        app = FastAPI()
        app.include_router(router)
        service = TemporalExecutionService(session)
        service._client_adapter.start_workflow = AsyncMock(
            return_value=SimpleNamespace(run_id="admitted-native-run")
        )
        app.dependency_overrides[_get_service] = lambda: service
        app.dependency_overrides[get_async_session] = lambda: session
        user = _override_user_dependencies(app, is_superuser=True)
        if saved_profile:
            await SettingsCatalogService(session=session).apply_overrides(
                changes={"workflow.default_provider_profile_ref": PROFILE},
            )
        backends = [(None, None), ("omnigent", None)]
        if shared_default and provider.runtime_id in {"codex_cli", "claude_code"}:
            backends.extend(
                [(provider.runtime_id, None), ("omnigent", provider.runtime_id)]
            )
        admitted = []
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            for explicit_backend, step_backend in backends:
                payload = {
                    "workflow": {
                        "instructions": "Generate a report.",
                        "skill": {"id": _DURABLE_SKILL_NAME},
                        "runtime": {"model": journey.server.model},
                    },
                    "publishMode": "none",
                    "workspace": {"workspaceSource": {"kind": "scratch"}},
                }
                if explicit_backend:
                    payload["targetRuntime"] = explicit_backend
                if step_backend:
                    payload["workflow"]["steps"] = [
                        {
                            "id": "implement",
                            "type": "skill",
                            "skill": {"id": _DURABLE_SKILL_NAME},
                            "instructions": "Generate a report.",
                            "runtime": {"mode": step_backend},
                        },
                        {
                            "id": "verify",
                            "type": "skill",
                            "skill": {"id": _DURABLE_SKILL_NAME},
                            "instructions": "Verify the report.",
                        },
                    ]
                response = await client.post(
                    "/api/executions", json={"type": "workflow", "payload": payload}
                )
                if not shared_default:
                    assert response.status_code in {409, 422}, response.text
                    assert "rollout_disabled" in response.text
                    continue
                assert response.status_code == 201, response.text
                record = await session.get(
                    TemporalExecutionCanonicalRecord, response.json()["workflowId"]
                )
                parameters = record.parameters
                assert parameters["targetRuntime"] == "omnigent"
                assert parameters["profileId"] == PROFILE
                assert parameters["runtimeCutover"]["selectionSource"] == (
                    "authored"
                    if explicit_backend
                    else "profile" if saved_profile else "configured_default"
                )
                if step_backend:
                    assert (
                        parameters["workflow"]["steps"][0]["runtime"]["mode"]
                        == "omnigent"
                    )
                binding = parameters["omnigentExecutionPlan"]
                plan = await DbExecutionPlanStore(journey.session_factory).load(
                    binding["planRef"]
                )
                assert plan is not None
                assert plan.payload.executionRealizerRef == expected_realizer
                assert plan.payload.harnessId == journey.harness
                assert plan.payload.modelConfig.qualifiedId == journey.server.model
                requests = agent_requests(parameters)
                assert requests
                if step_backend:
                    assert set(requests) == {"implement", "verify"}
                assert all(
                    request.agent_id == "omnigent" for request in requests.values()
                )
                admitted.append((plan, record))
        if not shared_default:
            service._client_adapter.start_workflow.assert_not_awaited()
            assert admitted == []
            return
        assert service._client_adapter.start_workflow.await_count == len(backends)
        for plan, _record in admitted[1:]:
            assert admitted[0][0].payload.harnessId == plan.payload.harnessId
            assert (
                admitted[0][0].payload.credentialBindings
                == plan.payload.credentialBindings
            )
    if shared_default:
        await _execute_admitted_generic_plan(journey, *admitted[0])


async def _execute_admitted_generic_plan(journey, plan, record):
    # Temporal scheduling and the workflow grant remain controlled transports.
    # Bind their existing fixture handoff to the actual API-authored plan and
    # workflow; do not recompile or substitute a plan for execution.
    parameters = record.parameters
    workflow_id = record.workflow_id
    step_id = f"{workflow_id}:{record.run_id}:implement:execution:1"
    data = journey.request.model_dump(by_alias=True, mode="json")
    data.update(
        correlationId=workflow_id,
        resolvedSkillsetRef=parameters["resolvedSkillsetRef"],
        omnigentExecutionPlan=parameters["omnigentExecutionPlan"],
    )
    data["stepExecution"].update(
        workflowId=workflow_id, runId=record.run_id, stepExecutionId=step_id
    )
    data["workspaceSpec"]["workspaceLocator"]["workspaceId"] = hashlib.sha256(
        f"{workflow_id}:{step_id}".encode()
    ).hexdigest()[:24]
    data["admittedProviderCapacity"].update(
        executionPlanRef=plan.planRef, stepExecutionId=step_id
    )
    data["parameters"]["omnigent"]["prompt"]["text"] = parameters["workflow"][
        "instructions"
    ]
    request = AgentExecutionRequest.model_validate(data)
    async with journey.session_factory() as session:
        lease = await session.get(ProviderProfileSlotLease, journey.provider_lease_id)
        lease.execution_plan_ref = plan.planRef
        lease.step_execution_id = step_id
        await session.commit()
    journey.plan, journey.request = plan, request
    journey.manager.plan, journey.manager.request = plan, request
    await journey.gateway.admit_execution_plan_inputs(request=request, plan=plan)
    journey.docker.host_class, _ = await journey.services.planned_host_resolver(
        plan, request=request
    )
    from tests.integration.omnigent.test_generic_bound_native_chat import (
        _during_active_session,
        assert_bound_native_round_trip,
    )

    await _during_active_session(
        journey,
        lambda: assert_bound_native_round_trip(journey),
        execute=lambda: assert_native_turn_saved_and_cleaned(
            journey, expected_messages=2
        ),
    )
