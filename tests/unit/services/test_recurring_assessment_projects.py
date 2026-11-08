"""Saved recurring targets use execution assessment admission, then stay frozen."""

import copy
import json

import pytest
from fastapi import HTTPException

from api_service.services.recurring_workflows_service import RecurringWorkflowsService
from moonmind.security.assessment_projects import resolve_assessment_project
from moonmind.security.assessments import AssessmentRequest
from moonmind.security.operator_admission import OperatorAdmission
from tests.unit.security.test_assessment_projects import project_document
from tests.unit.services.test_recurring_workflows_service import (
    mock_temporal_adapter as _mock_temporal_adapter,
)
from tests.unit.services.test_recurring_workflows_service import (
    recurring_db,
)

pytestmark = pytest.mark.asyncio


@pytest.fixture
def mock_temporal_adapter():
    return _mock_temporal_adapter.__wrapped__()


def assessment_target(*, context=True, **overrides):
    snapshot = resolve_assessment_project(
        project_document(),
        project_id="shop",
        environment_id="staging",
        version=7,
        overrides={"artifactRefs": [], "credentialRefs": [], **overrides},
    ).model_dump(mode="json", by_alias=True)
    parameters = {
        "targetRuntime": "codex_cli",
        "workflow": {
            "instructions": "Compare retained evidence",
            "steps": [
                {
                    "id": "review",
                    "type": "tool",
                    "tool": {
                        "id": "security.assess",
                        "inputs": {"reviewRef": "art_review"},
                    },
                }
            ],
        },
    }
    if context:
        parameters["assessmentProject"] = snapshot
    return {
        "workflowType": "MoonMind.UserWorkflow",
        "initialParameters": parameters,
    }, snapshot


async def create(service, target):
    return await service.create_definition(
        name="Assessment",
        description=None,
        enabled=True,
        schedule_type="cron",
        cron="0 6 * * *",
        timezone="UTC",
        scope_type="personal",
        scope_ref=None,
        owner_user_id=None,
        target=target,
        policy=None,
        actor=OperatorAdmission(via="loopback"),
    )


@pytest.mark.parametrize("fault", ["unbound", "invalid", "unavailable"])
async def test_recurring_assessment_create_rejects_unadmitted_context(
    tmp_path, mock_temporal_adapter, fault
):
    target, snapshot = assessment_target(
        credentialRefs=["db://missing"] if fault == "unavailable" else []
    )
    if fault == "invalid":
        target["initialParameters"]["assessmentProject"] = {"projectId": "invalid"}
    if fault == "unbound":
        target["initialParameters"].pop("assessmentProject")
        target["initialParameters"]["workflow"]["steps"][0]["tool"]["inputs"][
            "assessment_project"
        ] = snapshot
    async with recurring_db(tmp_path) as maker, maker() as session:
        service = RecurringWorkflowsService(
            session, temporal_client_adapter=mock_temporal_adapter
        )
        with pytest.raises(HTTPException) as error:
            await create(service, target)
        assert error.value.status_code == (409 if fault == "unavailable" else 422)
        mock_temporal_adapter.create_schedule.assert_not_awaited()


async def test_recurring_assessment_empty_workflow_cannot_hide_legacy_task_context(
    tmp_path, mock_temporal_adapter
):
    target, snapshot = assessment_target(context=False)
    parameters = target["initialParameters"]
    parameters["task"] = parameters.pop("workflow")
    parameters["workflow"] = {}
    parameters["task"]["steps"][0]["tool"]["inputs"]["assessment_project"] = snapshot
    async with recurring_db(tmp_path) as maker, maker() as session:
        service = RecurringWorkflowsService(
            session, temporal_client_adapter=mock_temporal_adapter
        )
        with pytest.raises(HTTPException) as error:
            await create(service, target)
        assert error.value.status_code == 422
        mock_temporal_adapter.create_schedule.assert_not_awaited()


async def test_recurring_assessment_freezes_defaults_and_rejects_edits_atomically(
    tmp_path, mock_temporal_adapter
):
    target, snapshot = assessment_target()
    async with recurring_db(tmp_path) as maker, maker() as session:
        service = RecurringWorkflowsService(
            session, temporal_client_adapter=mock_temporal_adapter
        )
        row = await create(service, target)
        stored = copy.deepcopy(row.target)
        inputs = stored["initialParameters"]["workflow"]["steps"][0]["tool"]["inputs"]
        parsed = AssessmentRequest.model_validate(inputs)
        assert (
            parsed.assessment_project.model_dump(mode="json", by_alias=True) == snapshot
        )
        assert parsed.scope.assets == ["shop-staging"]
        assert parsed.credential_refs == parsed.network_refs == []
        assert (
            mock_temporal_adapter.create_schedule.await_args.kwargs["workflow_input"][
                "initial_parameters"
            ]["workflow"]
            == stored["initialParameters"]["workflow"]
        )
        edited = copy.deepcopy(stored)
        edited["initialParameters"]["assessmentProject"] = {"projectId": "invalid"}
        version = row.version
        with pytest.raises(HTTPException):
            await service.update_definition(
                row,
                target=edited,
                expected_version=version,
                actor=OperatorAdmission(via="loopback"),
            )
        assert row.target == stored
        assert row.version == version
        mock_temporal_adapter.update_schedule.assert_not_awaited()
        await service.update_definition(
            row,
            cron="0 7 * * *",
            expected_version=version,
            actor=OperatorAdmission(via="loopback"),
        )
        assert row.target == stored
        assert (
            "workflow_input"
            not in mock_temporal_adapter.update_schedule.await_args.kwargs
        )
        workflow_input = service._workflow_bundle_for_target(
            definition_id=row.id,
            name=row.name,
            owner_user_id=row.owner_user_id,
            target_payload=row.target,
        )[1]
        assert workflow_input["initial_parameters"]["assessmentProject"] == snapshot
        assert (
            workflow_input["initial_parameters"]["workflow"]
            == stored["initialParameters"]["workflow"]
        )
        cleared = copy.deepcopy(stored)
        cleared["initialParameters"]["assessmentProject"] = None
        cleared["initialParameters"]["workflow"]["steps"][0]["tool"]["inputs"][
            "assessment_project"
        ] = snapshot
        await service.update_definition(
            row,
            target=cleared,
            expected_version=row.version,
            actor=OperatorAdmission(via="loopback"),
        )
        cleared_inputs = row.target["initialParameters"]["workflow"]["steps"][0][
            "tool"
        ]["inputs"]
        assert (
            "assessmentProject" not in cleared_inputs
            and "assessment_project" not in cleared_inputs
        )
        assert (
            AssessmentRequest.model_validate(cleared_inputs).assessment_project is None
        )


@pytest.mark.parametrize(
    "fault",
    [
        "unbound",
        "hidden_task",
        "mismatched",
        "clear",
        "matched",
        "matched_legacy",
        "matched_clear",
    ],
)
async def test_recurring_assessment_input_artifact_checks_original_parser(
    tmp_path, mock_temporal_adapter, fault
):
    from api_service.auth_providers import instance_operator_principal
    from api_service.services.omnigent_execution_plan_service import (
        persist_json_artifact,
    )
    from moonmind.security.assessment_projects import (
        AssessmentProjectSnapshot,
        apply_assessment_defaults,
    )
    from moonmind.workflows.temporal.artifacts import (
        ArtifactAccessContext,
        LocalTemporalArtifactStore,
        TemporalArtifactRepository,
        TemporalArtifactService,
    )

    target, snapshot = assessment_target()
    artifact_parameters = copy.deepcopy(target["initialParameters"])
    inputs = artifact_parameters["workflow"]["steps"][0]["tool"]["inputs"]
    inputs["assessment_project"] = snapshot
    if fault in ("unbound", "hidden_task"):
        target["initialParameters"].pop("assessmentProject")
        if fault == "hidden_task":
            artifact_parameters.pop("assessmentProject")
            artifact_parameters["task"] = artifact_parameters.pop("workflow")
            artifact_parameters["workflow"] = {}
    elif fault == "mismatched":
        _, other = assessment_target(assets=["other"])
        artifact_parameters["assessmentProject"] = other
    elif fault in ("clear", "matched_clear"):
        target["initialParameters"]["assessmentProject"] = None
    if fault == "matched_clear":
        apply_assessment_defaults(
            artifact_parameters["workflow"],
            AssessmentProjectSnapshot.model_validate(snapshot),
        )
        apply_assessment_defaults(artifact_parameters["workflow"], None)
        artifact_parameters["assessmentProject"] = None
    # Artifact-only steps exercise effective-input freezing, not just inline defaults.
    target["initialParameters"].pop("workflow")
    if fault == "matched_legacy":
        artifact_parameters["task"] = artifact_parameters.pop("workflow")
        artifact_parameters["workflow"] = {}
    source = {"draft": artifact_parameters}
    original = copy.deepcopy(source)
    async with recurring_db(tmp_path) as maker, maker() as session:
        artifacts = TemporalArtifactService(
            TemporalArtifactRepository(session),
            store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
        )
        ref, _ = await persist_json_artifact(
            artifact_service=artifacts,
            principal=instance_operator_principal(),
            artifact_class="workflow.input",
            payload=source,
        )
        target["inputArtifactRef"] = ref
        service = RecurringWorkflowsService(
            session,
            temporal_client_adapter=mock_temporal_adapter,
            artifact_service=artifacts,
        )
        if fault in ("matched", "matched_legacy", "matched_clear"):
            row = await create(service, target)
            stored = copy.deepcopy(row.target)
            values = row.target["initialParameters"]["workflow"]["steps"][0]["tool"][
                "inputs"
            ]
            parsed = AssessmentRequest.model_validate(values)
            if fault in ("matched", "matched_legacy"):
                assert (
                    parsed.assessment_project.model_dump(mode="json", by_alias=True)
                    == snapshot
                )
                changed = copy.deepcopy(stored)
                _, replacement = assessment_target(assets=["changed"])
                changed["initialParameters"]["assessmentProject"] = replacement
                version = row.version
                with pytest.raises(HTTPException) as error:
                    await service.update_definition(
                        row,
                        target=changed,
                        expected_version=version,
                        actor=OperatorAdmission(via="loopback"),
                    )
                assert "Rebuild the input artifact" in error.value.detail["message"]
                assert row.target == stored and row.version == version
                mock_temporal_adapter.update_schedule.assert_not_awaited()
            else:
                assert parsed.assessment_project is None
                assert "assessment_project" not in values
        else:
            with pytest.raises(HTTPException) as error:
                await create(service, target)
            assert error.value.status_code == 422
            assert "Rebuild the input artifact" in error.value.detail["message"]
            mock_temporal_adapter.create_schedule.assert_not_awaited()
        _, retained_body = await artifacts.read(
            artifact_id=ref,
            access=ArtifactAccessContext.for_operator(
                OperatorAdmission(via="loopback")
            ),
        )
        assert json.loads(retained_body) == original
