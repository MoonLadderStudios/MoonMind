"""Shared model selection preserves the native Profile's existing aliases."""

from types import SimpleNamespace

import pytest

from moonmind.omnigent.harness_platform.planning_service import (
    OmnigentExecutionPlanningService,
)
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from moonmind.workflows.temporal.runtime.strategies import get_strategy


@pytest.mark.parametrize(
    "runtime,provider_id", [("codex_cli", "openai"), ("claude_code", "anthropic")]
)
@pytest.mark.parametrize("authored", [False, True])
@pytest.mark.parametrize("requested", ["operator-alias", "unmapped-model"])
def test_direct_and_shared_selection_apply_the_same_account_alias_once(
    runtime, provider_id, authored, requested
):
    provider = SimpleNamespace(
        runtime_id=runtime,
        provider_id=provider_id,
        enabled=True,
        auth_state="connected",
        default_model="default-model",
        default_effort=None,
        model_tiers=None,
        default_model_tier=1,
        model_overrides={
            "operator-alias": "selected-model",
            "selected-model": "do-not-recurse",
        },
    )
    parameters = {"model": requested}
    if authored:
        parameters["runtime"] = {"model": requested, "modelTier": None}
    request = AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        correlationId="parent",
        idempotencyKey="step",
        parameters=parameters,
    )
    direct = get_strategy(runtime).get_model(provider, request)
    assert direct == ("selected-model" if requested == "operator-alias" else requested)
    service = object.__new__(OmnigentExecutionPlanningService)
    service._deployment_default_model = ""
    shared, _, route = service._resolve_model(
        request, SimpleNamespace(model={}), provider
    )
    assert shared == f"{provider_id}/{direct}"
    assert route == provider_id


def test_omitted_model_keeps_the_existing_profile_default_semantics():
    provider = SimpleNamespace(
        runtime_id="codex_cli",
        provider_id="openai",
        enabled=True,
        auth_state="connected",
        default_model="operator-alias",
        default_effort=None,
        model_tiers=None,
        default_model_tier=1,
        model_overrides={"operator-alias": "selected-model"},
    )
    request = AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        correlationId="parent",
        idempotencyKey="step",
    )
    assert get_strategy("codex_cli").get_model(provider, request) == "operator-alias"
    service = object.__new__(OmnigentExecutionPlanningService)
    service._deployment_default_model = ""
    assert (
        service._resolve_model(request, SimpleNamespace(model={}), provider)[0]
        == "openai/operator-alias"
    )
