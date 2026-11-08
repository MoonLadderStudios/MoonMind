from copy import deepcopy
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from api_service.services.assessment_engagements import (
    build_assessment_proposal,
    validate_reviewed_proposal,
)
from moonmind.security.assessment_authorizations import AssessmentAuthorizations
from tests.unit.security.test_assessment_authorizations import (
    current_saved_authorization,
)


def record():
    return AssessmentAuthorizations.model_validate(
        current_saved_authorization()
    ).authorizations[0]


def payload():
    return {
        "workflow": {
            "instructions": "Assess only the admitted target",
            "steps": [{"id": "scan", "skill": {"name": "security-review"}}],
        }
    }


def test_ui_and_direct_api_plan_aliases_compile_identically():
    direct = payload()
    ui = {
        "task": deepcopy(direct["workflow"]),
        "assessmentAuthorizationRef": "client-review",
    }
    auth = record()
    first = build_assessment_proposal(direct, auth)
    second = build_assessment_proposal(ui, auth)
    assert first == second
    labels = {section["label"] for section in first["sections"]}
    assert {
        "Targets",
        "Exclusions",
        "Actions",
        "Identities",
        "Network reach",
        "Rate and resource limits",
        "Validity",
        "Evidence handling",
        "Stop conditions",
        "Authority",
    } <= labels
    assert first["scope"]["engagement"]["authorizationGeneration"] == auth.generation
    assert "publish" in " ".join(first["limitations"]).lower()


def test_exact_review_accepts_and_any_plan_or_scope_edit_requires_review_again():
    auth = record()
    original = payload()
    review = build_assessment_proposal(original, auth)
    admitted = {
        **original,
        "assessmentAuthorizationRef": auth.ref,
        "assessmentReview": review,
    }
    assert validate_reviewed_proposal(admitted, auth) == review
    changed = deepcopy(admitted)
    changed["workflow"]["instructions"] = "Also assess a different target"
    with pytest.raises(HTTPException) as error:
        validate_reviewed_proposal(changed, auth)
    assert error.value.status_code == 409
    changed = deepcopy(admitted)
    changed["assessmentReview"]["scope"]["maxJobs"] += 1
    with pytest.raises(HTTPException):
        validate_reviewed_proposal(changed, auth)
    changed = deepcopy(admitted)
    changed["assessmentReview"]["sections"][0]["items"] = [
        "Different displayed permission"
    ]
    with pytest.raises(HTTPException):
        validate_reviewed_proposal(changed, auth)


def test_artifact_reference_is_bound_and_preview_never_contains_secret_values():
    auth = record()
    original = {"workflow": {"inputArtifactRef": "art_plan", "instructions": ""}}
    review = build_assessment_proposal(original, auth)
    changed = {
        "workflow": {"inputArtifactRef": "art_other", "instructions": ""},
        "assessmentReview": review,
    }
    with pytest.raises(HTTPException):
        validate_reviewed_proposal(changed, auth)
    assert review["scope"]["grants"] == []


def test_review_metadata_cannot_rebind_authority():
    auth = record()
    review = build_assessment_proposal(payload(), auth)
    newer = auth.model_copy(update={"generation": "new-generation"})
    with pytest.raises(HTTPException):
        validate_reviewed_proposal({**payload(), "assessmentReview": review}, newer)


def test_direct_execution_envelope_binds_external_artifact_inputs():
    auth = record()
    direct = {
        "workflowType": "MoonMind.UserWorkflow",
        "initialParameters": payload(),
        "inputArtifactRef": "art_reviewed",
    }
    proposal = build_assessment_proposal(direct, auth)
    changed = deepcopy(direct)
    changed["inputArtifactRef"] = "art_changed"
    assert (
        build_assessment_proposal(changed, auth)["proposalDigest"]
        != proposal["proposalDigest"]
    )
    flat = {**payload(), "inputArtifactRef": "art_reviewed"}
    assert build_assessment_proposal(flat, auth) == proposal


@pytest.mark.asyncio
async def test_non_operator_cannot_admit_reviewed_scope():
    from api_service.services.assessment_engagements import admit_assessment_parameters

    with pytest.raises(HTTPException) as error:
        await admit_assessment_parameters(
            {"assessmentAuthorizationRef": "client-review"},
            session=None,
            user=None,
            access=None,
        )
    assert error.value.status_code == 403


@pytest.mark.asyncio
async def test_current_record_is_rechecked_and_expired_window_is_not_renewed(
    monkeypatch,
):
    from unittest.mock import AsyncMock
    from api_service.services import assessment_authorizations
    from api_service.services.assessment_engagements import admit_assessment_parameters
    from moonmind.security.operator_admission import OperatorAdmission
    from moonmind.workflows.temporal.artifacts import ArtifactAccessContext

    auth = record()
    old = auth.model_copy(
        update={
            "valid_from": datetime(2020, 1, 1, tzinfo=timezone.utc),
            "valid_until": datetime(2020, 1, 2, tzinfo=timezone.utc),
        }
    )
    original = payload()
    original["assessmentAuthorizationRef"] = old.ref
    original["assessmentReview"] = build_assessment_proposal(original, old)
    monkeypatch.setattr(
        assessment_authorizations,
        "load_assessment_authorization",
        AsyncMock(return_value=old),
    )
    operator = OperatorAdmission(via="loopback")
    with pytest.raises(HTTPException) as error:
        await admit_assessment_parameters(
            original,
            session=None,
            user=operator,
            access=ArtifactAccessContext.for_operator(operator),
        )
    assert error.value.status_code == 403
    assert "assessmentScope" not in original


def test_edit_proposal_binds_retained_inputs_and_complete_effective_plan():
    from types import SimpleNamespace
    from api_service.services.assessment_engagements import assessment_edit_material

    auth = record()
    retained = SimpleNamespace(
        workflow_id="mm:source",
        parameters={
            "workflow": {"instructions": "Original"},
            "repository": "org/original",
        },
        input_ref="art_input",
        plan_ref="art_plan",
    )
    patch = {"workflow": {"instructions": "Reviewed"}, "workspace": None}
    material = assessment_edit_material(retained, patch)
    assert material["repository"] == "org/original"
    assert material["inputArtifactRef"] == "art_input"
    assert material["planArtifactRef"] == "art_plan"
    proposal = build_assessment_proposal(material, auth)
    retained.plan_ref = "art_replaced"
    assert (
        build_assessment_proposal(assessment_edit_material(retained, patch), auth)[
            "proposalDigest"
        ]
        != proposal["proposalDigest"]
    )
    retained.plan_ref = "art_plan"
    retained.parameters["repository"] = "org/other"
    assert (
        build_assessment_proposal(assessment_edit_material(retained, patch), auth)[
            "proposalDigest"
        ]
        != proposal["proposalDigest"]
    )


def test_edit_embedded_artifact_ref_cannot_replace_transport_authority():
    from types import SimpleNamespace
    from api_service.services.assessment_engagements import assessment_edit_material

    source = SimpleNamespace(
        workflow_id="mm:source",
        parameters={},
        input_ref="art_input",
        plan_ref="art_plan",
        manifest_ref="art_manifest",
    )
    patch = {
        "workflow": {"instructions": "Review"},
        "inputArtifactRef": "art_injected",
        "planArtifactRef": "art_injected",
        "manifestArtifactRef": "art_injected",
    }
    material = assessment_edit_material(source, patch)
    assert material["inputArtifactRef"] == "art_input"
    assert material["planArtifactRef"] == "art_plan"
    assert material["manifestArtifactRef"] == "art_manifest"
    proposed = assessment_edit_material(source, patch, input_artifact_ref="art_new")
    assert proposed["inputArtifactRef"] == "art_new"


def test_edit_material_preserves_executed_legacy_task_fallback():
    from types import SimpleNamespace
    from api_service.services.assessment_engagements import assessment_edit_material

    source = SimpleNamespace(
        workflow_id="mm:source",
        parameters={"workflow": {}, "task": {"instructions": "Retained legacy work"}},
        input_ref=None,
        plan_ref=None,
    )
    material = assessment_edit_material(source, {})
    assert material["workflow"]["instructions"] == "Retained legacy work"
    assert "task" not in material


@pytest.mark.parametrize(
    "extra",
    [
        {"title": "Review lab"},
        {"failurePolicy": "continue"},
        {"schedule": {"mode": "once", "scheduledFor": "2030-01-01T12:00:00Z"}},
    ],
)
def test_direct_envelope_and_admission_keep_same_authored_metadata(extra):
    from moonmind.schemas.temporal_models import CreateExecutionRequest

    auth = record()
    direct = {
        "workflowType": "MoonMind.UserWorkflow",
        "initialParameters": payload(),
        **extra,
    }
    request = CreateExecutionRequest.model_validate(direct)
    material = {
        **request.initial_parameters,
        **request.model_dump(
            mode="json",
            by_alias=True,
            exclude_none=True,
            exclude_unset=True,
            exclude={"initial_parameters"},
        ),
    }
    assert build_assessment_proposal(direct, auth) == build_assessment_proposal(
        material, auth
    )


@pytest.mark.asyncio
async def test_direct_preview_checks_project_references_in_initial_parameters(
    monkeypatch,
):
    from unittest.mock import AsyncMock

    from api_service.services import assessment_authorizations, assessment_projects
    from api_service.services.assessment_engagements import compile_assessment_proposal
    from moonmind.security.operator_admission import OperatorAdmission

    auth = record()
    monkeypatch.setattr(
        assessment_authorizations,
        "load_assessment_authorization",
        AsyncMock(return_value=auth),
    )
    validate = AsyncMock(return_value=None)
    monkeypatch.setattr(
        assessment_projects, "validate_assessment_project_parameter", validate
    )
    parameters = {**payload(), "assessmentProject": {"projectId": "retained-project"}}
    await compile_assessment_proposal(
        {"workflowType": "MoonMind.UserWorkflow", "initialParameters": parameters},
        auth.ref,
        session=None,
        user=OperatorAdmission(via="loopback"),
        access=None,
    )
    assert (
        validate.await_args.args[0]["assessmentProject"]
        == parameters["assessmentProject"]
    )
