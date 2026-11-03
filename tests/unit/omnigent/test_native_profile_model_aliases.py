"""Shared model selection preserves the native Profile's existing aliases."""

from types import SimpleNamespace

import pytest

from moonmind.omnigent.harness_platform.planning_service import (
    OmnigentExecutionPlanningService,
)
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from moonmind.workflows.temporal.runtime.strategies import get_strategy
from tests.artifact_access import access_for_test
from tests.unit.services.test_omnigent_execution_plan_service import (
    _ArtifactService,
    _compiler_dependency_owner,  # noqa: F401 - pytest fixture import
    _configure_ready_host_image_pair,
    _PlanStore,
    _policy_snapshot,
    _protected_support_evidence,
    _snapshot,
)


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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "runtime,provider_id,harness",
    [("codex_cli", "openai", "codex-native"), ("claude_code", "anthropic", "claude-native")],
)
@pytest.mark.parametrize(
    "source,expected",
    [
        ("task_override", "selected-model"),
        ("provider_profile_default", "operator-alias"),
        ("runtime_default", "operator-alias"),
        (None, "operator-alias"),
    ],
)
async def test_api_compiler_preserves_resolved_model_defaults(
    monkeypatch, runtime, provider_id, harness, source, expected
):
    from api_service.services import omnigent_execution_plan_service as service

    monkeypatch.setenv(
        "OMNIGENT_SHARED_HOST_IMAGE_REF",
        "ghcr.io/example/omnigent-host@sha256:" + "f" * 64,
    )
    _configure_ready_host_image_pair(monkeypatch)
    monkeypatch.setattr(service, "DbExecutionPlanStore", _PlanStore)
    monkeypatch.setattr(
        service,
        "resolve_execution_evidence",
        lambda payload, **_kwargs: (_protected_support_evidence(payload), "supported"),
    )
    policy = f"{harness.removesuffix('-native')}-on-demand@1"

    async def resolve_policy(**_kwargs):
        return _policy_snapshot(harness=harness, policy=policy)

    monkeypatch.setattr(service, "_resolve_runtime_policy_snapshot", resolve_policy)
    result = await service.compile_and_persist_execution_plan(
        session_factory=object(),
        artifact_service=_ArtifactService(),
        principal="user-1",
        access=access_for_test("user-1"),
        workflow_id="mm:resolved-model-default",
        agent_profile_snapshot=_snapshot(
            harness=harness, policy=policy, provider_id="selected-provider"
        ),
        provider_profile=SimpleNamespace(
            profile_id="selected-provider",
            runtime_id=runtime,
            provider_id=provider_id,
            credential_source="oauth_volume",
            runtime_materialization_mode="oauth_home",
            model_overrides={
                "operator-alias": "selected-model",
                "selected-model": "do-not-recurse",
            },
        ),
        initial_parameters={
            "model": "operator-alias",
            **({"modelSource": source} if source else {}),
            "targetRuntime": "omnigent",
            "publishMode": "none",
            "workflow": {"instructions": "Preserve the admitted model selection."},
        },
        authored_request_ref="art_request_1",
        authored_request_digest="sha256:" + "1" * 64,
        task_input_snapshot_ref="art_request_1",
        task_input_snapshot_digest="sha256:" + "1" * 64,
    )
    assert result.envelope.payload.modelConfig.qualifiedId == expected
