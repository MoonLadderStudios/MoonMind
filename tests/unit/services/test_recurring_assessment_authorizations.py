"""Recurring authoring reviews raw intent before server defaults are applied."""

from copy import deepcopy
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from api_service.api.routers import executions, recurring_workflows
from api_service.services import assessment_authorizations
from api_service.services.assessment_engagements import build_assessment_proposal
from api_service.services.recurring_workflows_service import (
    RecurringWorkflowsService,
    RecurringWorkflowValidationError,
)
from moonmind.security.operator_admission import OperatorAdmission
from tests.unit.api.routers.test_recurring_workflows import _definition
from tests.unit.services.test_assessment_engagements import record

pytestmark = pytest.mark.asyncio
OPERATOR = OperatorAdmission(via="loopback")


@pytest.fixture
def authority(monkeypatch):
    value = record()
    monkeypatch.setattr(
        assessment_authorizations,
        "load_assessment_authorization",
        AsyncMock(return_value=value),
    )
    return value


def reviewed_target(authority, **metadata):
    target = {
        "workflowType": "MoonMind.UserWorkflow",
        "initialParameters": {
            "workflow": {"instructions": "Inspect the admitted targets"},
            "assessmentAuthorizationRef": authority.ref,
        },
        **metadata,
    }
    target["initialParameters"]["assessmentReview"] = build_assessment_proposal(
        target, authority
    )
    return target


def service():
    result = RecurringWorkflowsService(None, temporal_client_adapter=AsyncMock())
    # Artifact read permissions/model selection have their own boundary tests.
    result._validate_model_selection_submission = AsyncMock(return_value={})
    return result


@pytest.mark.parametrize(
    "key,value,replacement",
    [
        ("title", "Reviewed assessment", "Other assessment"),
        ("failurePolicy", "continue", "fail_fast"),
        ("inputArtifactRef", "art_input", "art_other"),
        ("planArtifactRef", "art_plan", "art_other"),
        ("manifestArtifactRef", "art_manifest", "art_other"),
    ],
)
async def test_direct_target_binds_complete_reviewed_metadata(
    authority, key, value, replacement
):
    owner = service()
    target = reviewed_target(authority, **{key: value})
    admitted = await owner._admit_target_inputs(target, actor=OPERATOR)
    assert admitted[key] == value
    assert (
        admitted["initialParameters"]["assessmentScope"]["engagement"][
            "authorizationGeneration"
        ]
        == authority.generation
    )

    changed = deepcopy(target)
    changed[key] = replacement
    with pytest.raises(RecurringWorkflowValidationError, match="changed"):
        await owner._admit_target_inputs(changed, actor=OPERATOR)


async def test_retained_target_keeps_admission_but_changed_target_needs_review(
    authority,
):
    owner = service()
    saved = await owner._admit_target_inputs(
        reviewed_target(authority, title="Original"), actor=OPERATOR
    )
    retained = await owner._admit_target_inputs(
        deepcopy(saved), actor=OPERATOR, saved_target=saved
    )
    assert retained == saved

    changed = deepcopy(saved)
    changed["initialParameters"]["workflow"]["instructions"] = "Changed work"
    with pytest.raises(RecurringWorkflowValidationError, match="changed"):
        await owner._admit_target_inputs(changed, actor=OPERATOR, saved_target=saved)
    changed["initialParameters"]["assessmentReview"] = build_assessment_proposal(
        changed, authority
    )
    admitted = await owner._admit_target_inputs(
        changed, actor=OPERATOR, saved_target=saved
    )
    assert admitted["initialParameters"]["workflow"]["instructions"] == "Changed work"


@pytest.mark.parametrize("fault", ["missing_review", "legacy_scope", "not_operator"])
async def test_direct_target_cannot_bypass_new_admission(authority, fault):
    target = reviewed_target(authority, assessment_already_admitted=True)
    parameters = target["initialParameters"]
    actor = OPERATOR
    if fault == "missing_review":
        parameters.pop("assessmentReview")
    elif fault == "legacy_scope":
        parameters.clear()
        parameters.update(
            workflow={"instructions": "Inspect"},
            assessmentScope=authority.scope.model_dump(mode="json", by_alias=True),
        )
    else:
        actor = None
    with pytest.raises(RecurringWorkflowValidationError):
        await service()._admit_target_inputs(target, actor=actor)


async def test_route_admits_raw_target_before_runtime_defaults(authority, monkeypatch):
    owner = service()
    target = reviewed_target(
        authority, title="Reviewed schedule", failurePolicy="continue"
    )
    original_review = deepcopy(target["initialParameters"]["assessmentReview"])

    async def resolve_runtime(proposed, **_kwargs):
        assert proposed["initialParameters"].get("assessmentScope") is not None
        return (
            {
                "targetRuntime": "codex_cli",
                "runtimeCutover": {"selectionSource": "default"},
            },
            {},
            None,
            None,
            None,
        )

    resolver = AsyncMock(side_effect=resolve_runtime)
    monkeypatch.setattr(executions, "_resolve_recurring_runtime_selection", resolver)

    async def create_definition(**kwargs):
        admitted = await owner._admit_target_inputs(
            kwargs["target"],
            actor=kwargs["actor"],
            assessment_already_admitted=kwargs.get(
                "assessment_already_admitted", False
            ),
        )
        return _definition(target=admitted)

    owner.create_definition = AsyncMock(side_effect=create_definition)
    response = await recurring_workflows.create_recurring_workflow(
        payload=recurring_workflows.CreateRecurringWorkflowRequest(
            name="Assessment",
            cron="0 6 * * *",
            target=target,
        ),
        service=owner,
        session=None,
        user=OPERATOR,
    )
    parameters = response.target["initialParameters"]
    assert parameters["targetRuntime"] == "codex_cli"
    assert parameters["runtimeCutover"] == {"selectionSource": "default"}
    assert (
        parameters["assessmentReview"]["proposalDigest"]
        == original_review["proposalDigest"]
    )
    assert (
        owner.create_definition.await_args.kwargs["assessment_already_admitted"] is True
    )
    assert "assessmentScope" not in target["initialParameters"]


async def test_route_denies_unreviewed_scope_before_resolving_defaults(
    authority, monkeypatch
):
    target = reviewed_target(authority)
    target["initialParameters"].pop("assessmentReview")
    resolver = AsyncMock(return_value=None)
    monkeypatch.setattr(executions, "_resolve_recurring_runtime_selection", resolver)
    owner = service()
    owner.create_definition = AsyncMock(return_value=_definition())
    with pytest.raises(HTTPException) as error:
        await recurring_workflows.create_recurring_workflow(
            payload=recurring_workflows.CreateRecurringWorkflowRequest(
                name="Assessment",
                cron="0 6 * * *",
                target=target,
            ),
            service=owner,
            session=None,
            user=OPERATOR,
        )
    assert error.value.status_code == 422
    resolver.assert_not_awaited()
    owner.create_definition.assert_not_awaited()
