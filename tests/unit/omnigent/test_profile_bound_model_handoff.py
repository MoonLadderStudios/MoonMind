"""Keep admitted model intent intact through the native session boundary."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from moonmind.omnigent.harness_platform.failures import HarnessPlatformError
from moonmind.omnigent.host_services.github_credentials import (
    OmnigentGithubCredentialService,
)
from moonmind.omnigent.realizers.generic_host import GenericOmnigentHostRealizer
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from moonmind.workflows.adapters.omnigent_agent_adapter import (
    OmnigentAdapterError,
    build_omnigent_selection,
)
from tests.unit.omnigent.test_generic_platform_production_services import (
    _plan as _generic_plan,
)


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
    request_input_binder = AsyncMock(
        side_effect=AssertionError("model drift reached input binding")
    )
    realizer = GenericOmnigentHostRealizer(
        request_input_binder=request_input_binder,
        runtime_binding_store=runtime_bindings,
        provider_lease_coordinator=object(),
        credential_provisioning_service=object(),
        host_lease_repository=object(),
        host_runtime=SimpleNamespace(
            validate_repository_intent=(
                OmnigentGithubCredentialService(None).validate_repository_intent
            )
        ),
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
    request_input_binder.assert_not_awaited()
