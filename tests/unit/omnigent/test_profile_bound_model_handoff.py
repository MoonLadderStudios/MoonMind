"""Keep admitted model intent intact through Codex's native session boundary."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from moonmind.omnigent.codex_execution_decisions import bind_exact_host
from moonmind.omnigent.harness_platform.execution_plan import (
    bind_runtime_request_authority,
)
from moonmind.omnigent.harness_platform.failures import HarnessPlatformError
from moonmind.omnigent.realizers.generic_host import GenericOmnigentHostRealizer
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from moonmind.workflows.adapters.omnigent_agent_adapter import (
    OmnigentAdapterError,
    OmnigentResolvedTarget,
    build_omnigent_selection,
    build_omnigent_session_create_payload,
)
from moonmind.workflows.adapters.omnigent_client import OmnigentHttpClient
from tests.unit.omnigent.test_generic_platform_production_services import (
    _plan as _generic_plan,
)
from tests.unit.omnigent.test_profile_bound_plan_authority import (
    admitted_codex_plan as _admitted_codex_plan,
)

admitted_codex_plan = _admitted_codex_plan


class _RequestCaptured(Exception):
    pass


def _select_model(admitted):
    coordinator, request, plan, _snapshot, _launch = admitted
    plan = bind_runtime_request_authority(
        plan,
        resolved_skillset_ref=plan.payload.resolvedSkills.get("resolvedSkillSetRef"),
        model="gpt-6.1-sol",
        effort="max",
    )
    coordinator._execution_plan = plan
    request = request.model_copy(
        update={
            "omnigent_execution_plan": request.omnigent_execution_plan.model_copy(
                update={
                    "plan_ref": plan.planRef,
                    "plan_digest": "sha256:" + plan.planRef.rsplit(":", 1)[-1],
                }
            )
        }
    )
    return coordinator, request, plan


@pytest.mark.asyncio
@pytest.mark.parametrize("authored_location", ["omitted", "root", "session"])
async def test_codex_dispatch_preserves_admitted_model_on_native_wire(
    admitted_codex_plan, authored_location
):
    coordinator, request, _plan = _select_model(admitted_codex_plan)
    if authored_location == "root":
        request.parameters.update({"model": "gpt-6.1-sol", "effort": "max"})
    elif authored_location == "session":
        request.parameters["omnigent"]["session"] = {
            "modelOverride": "gpt-6.1-sol",
            "reasoningEffort": "max",
        }
    captured = []

    async def capture_request(**kwargs):
        captured.append(kwargs["request"])
        raise _RequestCaptured

    # Observe the actual request that the coordinator carries into its existing
    # lifecycle. No provider capacity, host, or session effects can precede it.
    coordinator._run_store = SimpleNamespace(get_or_create=capture_request)
    with pytest.raises(_RequestCaptured):
        await coordinator.execute(request)

    bound = bind_exact_host(
        captured[0],
        host_id="host-1",
        workspace_path="/workspaces/run",
        profile_authorization={},
        harness="codex-native",
        agent_name="codex-native-ui",
    )
    payload = build_omnigent_session_create_payload(
        request=bound,
        selection=build_omnigent_selection(bound),
        target=OmnigentResolvedTarget(agent_id="codex-agent", source="agent_id"),
    )
    native_requests = []

    async def native_create(incoming):
        native_requests.append(json.loads(incoming.content))
        assert incoming.method == "POST"
        assert incoming.url.path == "/v1/sessions"
        return httpx.Response(201, json={"id": "session-1"})

    client = OmnigentHttpClient(
        base_url="https://omnigent.test",
        transport=httpx.MockTransport(native_create),
    )
    await client.create_session(payload)
    assert native_requests[0].get("model_override") == "gpt-6.1-sol"
    assert native_requests[0].get("reasoning_effort") == "max"
    assert native_requests[0]["host_id"] == "host-1"
    assert native_requests[0]["workspace"] == "/workspaces/run"
    # Immutable plan projection never rewrites the caller's authored inputs.
    if authored_location == "omitted":
        assert "model" not in request.parameters
        assert "session" not in request.parameters["omnigent"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("location", "field", "value"),
    [
        ("root", "model", "gpt-5.6-sol"),
        ("root", "effort", "high"),
        ("session", "modelOverride", "gpt-5.6-sol"),
        ("session", "reasoningEffort", "high"),
    ],
)
async def test_codex_rejects_model_drift_before_lifecycle_effects(
    admitted_codex_plan, location, field, value
):
    coordinator, request, _plan = _select_model(admitted_codex_plan)
    parameters = request.parameters
    if location == "session":
        parameters = parameters["omnigent"].setdefault("session", {})
    parameters[field] = value
    coordinator._run_store = SimpleNamespace(
        get_or_create=AsyncMock(
            side_effect=AssertionError("model drift reached the lifecycle boundary")
        )
    )
    with pytest.raises(HarnessPlatformError) as rejected:
        await coordinator.execute(request)
    assert rejected.value.code == "OMNIGENT_EXECUTION_PLAN_CONFLICT"
    coordinator._run_store.get_or_create.assert_not_awaited()


def test_unplanned_native_selection_preserves_canonical_model_parameters():
    request = AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        correlationId="unplanned-1",
        idempotencyKey="unplanned-1",
        parameters={
            "model": "gpt-6.1-sol",
            "effort": "max",
            "omnigent": {"session": {"allowEmptyWorkspace": True}},
        },
    )
    selection = build_omnigent_selection(request)
    assert selection.session.model_override == "gpt-6.1-sol"
    assert selection.session.reasoning_effort == "max"
    request.parameters["omnigent"]["session"]["modelOverride"] = "gpt-5.6-sol"
    with pytest.raises(OmnigentAdapterError, match="model"):
        build_omnigent_selection(request)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value"), [("model", "other/model"), ("effort", "high")]
)
async def test_generic_model_drift_precedes_binding_readiness_and_command_effects(
    field, value
):
    runtime_bindings = SimpleNamespace(
        get=AsyncMock(
            side_effect=AssertionError("model drift reached runtime binding lookup")
        )
    )
    deployment_validator = AsyncMock()
    realizer = GenericOmnigentHostRealizer(
        runtime_binding_store=runtime_bindings,
        provider_lease_coordinator=object(),
        credential_provisioning_service=object(),
        host_lease_repository=object(),
        host_runtime=object(),
        planned_host_resolver=object(),
        session_driver=object(),
        session_cleanup_service=object(),
        workspace_publisher=object(),
        deployment_validator=deployment_validator,
    )
    request = AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        correlationId="generic-1",
        idempotencyKey="generic-1",
        parameters={field: value},
    )
    with pytest.raises(HarnessPlatformError) as rejected:
        await realizer.execute(request, _generic_plan("opencode/test"))
    assert rejected.value.code == "OMNIGENT_EXECUTION_PLAN_CONFLICT"
    runtime_bindings.get.assert_not_awaited()
    deployment_validator.assert_not_awaited()
