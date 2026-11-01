"""Serialized admission preserves inputs that cannot be compiled equivalently."""

import copy

import pytest

from api_service.api.routers.executions import _fresh_serialized_workflow_request
from moonmind.schemas.temporal_models import CreateExecutionRequest


def raw_request(**changes):
    return {
        "workflowType": "MoonMind.UserWorkflow",
        "title": "Authored title",
        "failurePolicy": "best_effort",
        "idempotencyKey": "same-submission",
        "initialParameters": {
            "workflow": {
                "instructions": "Keep all author intent.",
                "runtime": {
                    "mode": "codex_cli",
                    "providerProfileRef": "selected-account",
                    "model": "selected-model",
                    "effort": None,
                },
                "publish": {"mode": "none"},
            },
            "workspaceSpec": {"workspaceSource": {"kind": "scratch"}},
        },
        **changes,
    }


def test_canonical_serialized_workflow_reuses_product_envelope_without_mutation():
    request = CreateExecutionRequest.model_validate(raw_request())
    original = request.model_dump()
    converted = _fresh_serialized_workflow_request(request)
    assert converted is not None
    assert converted.type == "workflow"
    assert converted.payload["workflow"] == request.initial_parameters["workflow"]
    assert converted.payload["workspace"] == request.initial_parameters["workspaceSpec"]
    assert converted.payload["idempotencyKey"] == request.idempotency_key
    assert request.model_dump() == original


@pytest.mark.parametrize(
    "field,value",
    [
        ("planArtifactRef", "artifact:recorded-plan"),
        ("inputArtifactRef", "artifact:saved-input"),
        ("manifestArtifactRef", "artifact:retained-manifest"),
    ],
)
def test_opaque_serialized_sources_keep_their_recorded_adapter(field, value):
    request = CreateExecutionRequest.model_validate(raw_request(**{field: value}))
    assert _fresh_serialized_workflow_request(request) is None


@pytest.mark.parametrize(
    "parameters_patch",
    [
        {"omnigentExecutionPlan": {"planRef": "recorded-binding"}},
        {"executionPrincipal": {"kind": "workflow", "workflowId": "untrusted"}},
        {"runtimeCutover": {"runtimeId": "codex_cli", "targetId": "codex.direct"}},
        {"extensionInput": {"mustSurvive": True}},
        {"workspaceSpec": {"workspaceLocator": {"path": "/saved/work"}}},
    ],
)
def test_extension_and_recorded_authority_are_not_silently_discarded(parameters_patch):
    body = raw_request()
    body["initialParameters"].update(copy.deepcopy(parameters_patch))
    request = CreateExecutionRequest.model_validate(body)
    original = request.model_dump()
    assert _fresh_serialized_workflow_request(request) is None
    assert request.model_dump() == original


def test_unknown_workflow_fields_remain_on_the_serialized_adapter():
    body = raw_request()
    body["initialParameters"]["workflow"]["retainedNativeCommand"] = "saved command"
    request = CreateExecutionRequest.model_validate(body)
    assert _fresh_serialized_workflow_request(request) is None


@pytest.mark.parametrize(
    "runtime_patch",
    [
        {"agentProfile": {"providerProfileRef": "account-only-pin"}},
        {"parameters": {"command": ["codex", "resume", "saved-thread"]}},
        {"profileSelector": {"tags": ["operator-pool"]}},
    ],
)
def test_native_runtime_authority_is_not_reinterpreted_as_product_configuration(runtime_patch):
    body = raw_request()
    body["initialParameters"]["workflow"]["runtime"].update(runtime_patch)
    request = CreateExecutionRequest.model_validate(body)
    assert _fresh_serialized_workflow_request(request) is None


@pytest.mark.parametrize("alias", ["providerProfileRef", "profileId", "providerProfile", "executionProfileRef", "execution_profile_ref", "profile_id", "provider_profile"])
def test_deferred_raw_step_account_intent_keeps_its_dispatch_boundary(alias):
    body = raw_request()
    body["initialParameters"]["workflow"]["steps"] = [
        {"id": "selected", "instructions": "Keep deferred matching.", "runtime": {alias: "another-account"}},
    ]
    request = CreateExecutionRequest.model_validate(body)
    assert _fresh_serialized_workflow_request(request) is None


def test_raw_tool_only_requests_keep_the_existing_product_boundary():
    body = raw_request()
    body["initialParameters"]["workflow"]["steps"] = [
        {"id": "lookup", "type": "tool", "tool": {"id": "github.resolve_pull_request_target", "inputs": {"pullRequest": "831"}}},
    ]
    body["initialParameters"]["workflow"].pop("runtime")
    request = CreateExecutionRequest.model_validate(body)
    assert _fresh_serialized_workflow_request(request) is None
