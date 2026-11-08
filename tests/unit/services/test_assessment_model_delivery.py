"""Assessment processing uses canonical source intent, not retrieved claims."""

from types import SimpleNamespace

import pytest

from moonmind.security.assessment_projects import resolve_assessment_project


def source(policy=None):
    environment = {"id": "production", "name": "Production"}
    if policy is not None:
        environment["evidenceProcessing"] = policy
    snapshot = resolve_assessment_project(
        {
            "projects": [
                {
                    "id": "review",
                    "name": "Review",
                    "purpose": "Assess",
                    "boundaries": "Selected evidence",
                    "environments": [environment],
                }
            ]
        },
        project_id="review",
        environment_id="production",
        version=1,
    )
    return SimpleNamespace(
        parameters={
            "assessmentProject": snapshot.model_dump(mode="json", by_alias=True)
        },
        workflow_id="source",
        run_id="source-run",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["disabled", "local_only"], ids=["disabled", "local"])
async def test_processing_constraint_blocks_before_reading_or_selecting(mode):
    from api_service.services.assessment_model_delivery import resolve_review_delivery
    from moonmind.workflows.temporal.artifacts import TemporalArtifactAuthorizationError

    with pytest.raises(TemporalArtifactAuthorizationError):
        await resolve_review_delivery(
            source=source({"modelDelivery": mode}),
            runtime={"providerProfileRef": "selected"},
            session=None,
            artifacts=None,
            access=None,
        )


@pytest.mark.asyncio
async def test_selected_profile_must_fit_source_constraint():
    from api_service.services.assessment_model_delivery import resolve_review_delivery
    from moonmind.workflows.temporal.artifacts import TemporalArtifactAuthorizationError

    with pytest.raises(TemporalArtifactAuthorizationError):
        await resolve_review_delivery(
            source=source({"allowedProfileRefs": ["selected"]}),
            runtime={"providerProfileRef": "other"},
            session=None,
            artifacts=None,
            access=None,
        )


@pytest.mark.asyncio
async def test_native_assessment_does_not_choose_ambient_recipient():
    from api_service.services.assessment_model_delivery import resolve_review_delivery
    from moonmind.workflows.temporal.artifacts import TemporalArtifactStateError

    with pytest.raises(TemporalArtifactStateError, match="explicit"):
        await resolve_review_delivery(
            source=source(), runtime={}, session=None, artifacts=None, access=None
        )


@pytest.mark.asyncio
async def test_explicit_alternate_is_not_frozen_to_previous_provider():
    from api_service.services.assessment_model_delivery import resolve_review_delivery

    selected = source({"modelDelivery": "allowed"})
    selected.parameters["profileId"] = "previous"
    delivery = await resolve_review_delivery(
        source=selected,
        runtime={"providerProfileRef": "alternate", "model": "selected-model"},
        session=None,
        artifacts=None,
        access=None,
    )
    assert delivery["runtime"]["providerProfileRef"] == "alternate"
    assert delivery["receipt"]["recipient"]["providerProfileRef"] == "alternate"
    assert delivery["receipt"]["status"] == "pending_admission"


def recorded_model_source():
    import json
    from pathlib import Path
    from unittest.mock import AsyncMock

    from moonmind.omnigent.harness_platform.execution_plan import (
        OmnigentExecutionPlanEnvelope,
    )
    from moonmind.schemas.agent_runtime_models import AgentExecutionRequest

    fixture = json.loads(
        (
            Path(__file__).parents[2]
            / "fixtures/omnigent/retained_profile_bound_plans/codex-on-demand-16.json"
        ).read_text()
    )
    request = AgentExecutionRequest.model_validate(fixture["request"])
    binding = request.omnigent_execution_plan
    bodies = fixture["artifacts"]
    plan = OmnigentExecutionPlanEnvelope.model_validate_json(
        bodies[binding.plan_artifact_ref]
    )
    # Retained launch fixtures omit the old task body; give this test a complete
    # genuinely digest-bound task closure instead of mocking integrity checks.
    from api_service.services.omnigent_execution_plan_service import (
        json_artifact_digest,
    )
    from moonmind.omnigent.harness_platform.execution_plan import (
        create_execution_plan_envelope,
    )

    task_body = json.dumps(
        {"target": {"initialParameters": {"workflow": {"instructions": "Review"}}}},
        sort_keys=True,
        separators=(",", ":"),
    )
    task_digest = json_artifact_digest(json.loads(task_body))
    plan = create_execution_plan_envelope(
        plan.payload.model_copy(
            update={
                "authority": plan.payload.authority.model_copy(
                    update={"taskInputSnapshotDigest": task_digest}
                )
            }
        )
    )
    binding = binding.model_copy(
        update={
            "plan_ref": plan.planRef,
            "plan_digest": "sha256:" + plan.planRef.rsplit(":", 1)[-1],
            "task_input_snapshot_digest": task_digest,
        }
    )
    bodies[binding.plan_artifact_ref] = plan.model_dump_json(by_alias=True)
    bodies[binding.task_input_snapshot_ref] = task_body
    snapshot = json.loads(
        bodies[plan.payload.agentProfileSnapshotRef.removeprefix("artifact:")]
    )
    selected = source()
    selected.parameters.pop("assessmentProject")
    selected.parameters.update(
        omnigentExecutionPlan=binding.model_dump(mode="json", by_alias=True),
        agentProfileSnapshot=snapshot,
    )
    row = SimpleNamespace(
        schema_version=plan.schemaVersion,
        plan_ref=plan.planRef,
        payload_json=plan.payload.model_dump(mode="json", by_alias=True),
    )
    session = SimpleNamespace(get=AsyncMock(return_value=row))

    async def read(*, artifact_id, access):
        return SimpleNamespace(), bodies[artifact_id].encode()

    artifacts = SimpleNamespace(read=AsyncMock(side_effect=read))
    return selected, session, artifacts, plan


@pytest.mark.asyncio
async def test_omitted_runtime_inherits_verified_recipient_without_ambient_defaults():
    from api_service.services.assessment_model_delivery import resolve_review_delivery
    from tests.artifact_access import access_for_test

    selected, session, artifacts, plan = recorded_model_source()
    delivery = await resolve_review_delivery(
        source=selected,
        runtime={},
        session=session,
        artifacts=artifacts,
        access=access_for_test("operator"),
    )
    profile = next(iter(plan.payload.credentialBindings.values())).providerProfileRef
    assert delivery["runtime"]["providerProfileRef"] == profile
    assert delivery["runtime"]["model"] == plan.payload.modelConfig.qualifiedId
    assert delivery["omnigent"]["launchPolicyRef"] == plan.payload.launchPolicyRef
    assert (
        delivery["agentProfile"]["overrides"]["capture"] == plan.payload.capturePolicy
    )
    assert delivery["receipt"]["sourcePlanRef"] == plan.planRef
    assert "repository" not in delivery
    assert "credentialBindings" not in delivery


@pytest.mark.asyncio
async def test_source_plan_artifact_mismatch_cannot_choose_another_recipient():
    from api_service.services.assessment_model_delivery import resolve_review_delivery
    from moonmind.workflows.temporal.artifacts import TemporalArtifactStateError

    selected, session, artifacts, _ = recorded_model_source()
    artifacts.read.side_effect = None
    artifacts.read.return_value = (SimpleNamespace(), b"{}")
    with pytest.raises(TemporalArtifactStateError, match="no alternate"):
        await resolve_review_delivery(
            source=selected,
            runtime={},
            session=session,
            artifacts=artifacts,
            access=None,
        )


@pytest.mark.asyncio
async def test_scheduled_review_reads_admitted_start_not_projection_claims(monkeypatch):
    from unittest.mock import AsyncMock

    from api_service.services.assessment_model_delivery import (
        assessment_processing_for_execution,
    )
    from moonmind.workflows.temporal.service import TemporalExecutionService

    projection = SimpleNamespace(
        run_id="source-run", parameters={"assessmentProject": None}
    )
    session = SimpleNamespace(get=AsyncMock(side_effect=[None, projection]))
    reader = AsyncMock(return_value=source({"modelDelivery": "local_only"}))
    monkeypatch.setattr(
        TemporalExecutionService, "read_scheduled_execution_source", reader
    )
    policy = await assessment_processing_for_execution(session, "source", "source-run")
    assert policy["modelDelivery"] == "local_only"
    reader.assert_awaited_once_with(projection)


@pytest.mark.asyncio
async def test_actual_admitted_recipient_is_recorded_in_artifact_payload():
    import json
    from unittest.mock import AsyncMock

    from api_service.db.models import TemporalExecutionCanonicalRecord
    from api_service.services.assessment_model_delivery import (
        persist_review_delivery_receipt,
    )
    from tests.artifact_access import access_for_test

    selected, session, artifacts, plan = recorded_model_source()
    selected.namespace = "moonmind"
    plan_row = session.get.return_value
    session.get.side_effect = lambda kind, identity: (
        selected if kind is TemporalExecutionCanonicalRecord else plan_row
    )
    artifacts.put_content_addressed_payload_complete = AsyncMock(
        return_value=(SimpleNamespace(artifact_id="art_receipt"), True)
    )
    ref = await persist_review_delivery_receipt(
        execution=selected,
        receipt={
            "artifacts": [],
            "transformation": {"version": "evidence-redaction/v1"},
        },
        session=session,
        artifacts=artifacts,
        access=access_for_test("operator"),
        principal="operator",
    )
    assert ref == "art_receipt"
    saved = artifacts.put_content_addressed_payload_complete.await_args.kwargs
    payload = json.loads(saved["payload"])
    assert payload["status"] == "admitted"
    assert payload["recipient"]["model"] == plan.payload.modelConfig.qualifiedId
    assert payload["recipient"]["provider"] == plan.payload.modelConfig.routeRef
    assert payload["executionPlanRef"] == plan.planRef
    assert saved["metadata_json"] == {"artifact_type": "assessment_evidence_delivery"}
    assert saved["link"]["workflow_id"] == selected.workflow_id


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutation",
    ["binding_digest", "task_bytes", "source_policy"],
    ids=["binding", "body", "policy"],
)
async def test_source_binding_and_frozen_input_cannot_be_replaced(mutation):
    from api_service.services.assessment_model_delivery import resolve_review_delivery
    from moonmind.workflows.temporal.artifacts import TemporalArtifactStateError

    selected, session, artifacts, _ = recorded_model_source()
    if mutation == "binding_digest":
        selected.parameters["omnigentExecutionPlan"]["planDigest"] = (
            "sha256:" + "0" * 64
        )
    elif mutation == "source_policy":
        selected.parameters["assessmentProject"] = source(
            {"modelDelivery": "allowed"}
        ).parameters["assessmentProject"]
    else:
        original_read = artifacts.read.side_effect

        async def replaced(*, artifact_id, access):
            if (
                artifact_id
                == selected.parameters["omnigentExecutionPlan"]["taskInputSnapshotRef"]
            ):
                return SimpleNamespace(), b"{}"
            return await original_read(artifact_id=artifact_id, access=access)

        artifacts.read.side_effect = replaced
    with pytest.raises(TemporalArtifactStateError, match="no alternate"):
        await resolve_review_delivery(
            source=selected,
            runtime={},
            session=session,
            artifacts=artifacts,
            access=None,
        )


@pytest.mark.asyncio
async def test_inherited_profile_selection_comes_from_plan_artifact():
    from api_service.services.assessment_model_delivery import resolve_review_delivery

    selected, session, artifacts, _ = recorded_model_source()
    expected = selected.parameters["agentProfileSnapshot"]["profileId"]
    selected.parameters["agentProfileSnapshot"] = {
        "profileId": "unrelated-profile",
        "version": 99,
    }
    delivery = await resolve_review_delivery(
        source=selected, runtime={}, session=session, artifacts=artifacts, access=None
    )
    assert delivery["agentProfile"]["profileId"] == expected


@pytest.mark.asyncio
async def test_specialized_step_keeps_parent_processing_constraint(monkeypatch):
    from unittest.mock import AsyncMock

    from api_service.services import omnigent_step_plan_service as steps
    from moonmind.workflows.temporal.artifacts import TemporalArtifactAuthorizationError

    parameters = source({"allowedProfileRefs": ["root-profile"]}).parameters
    parameters.update(
        targetRuntime="omnigent",
        workflow={
            "steps": [
                {
                    "id": "review",
                    "runtime": {
                        "providerProfileRef": "other-profile",
                        "model": "other/model",
                    },
                }
            ]
        },
    )
    provider = SimpleNamespace(profile_id="other-profile", runtime_id="opencode")
    monkeypatch.setattr(
        "api_service.services.provider_profile_runtime.load_provider_profile_for_runtime",
        AsyncMock(return_value=provider),
    )
    monkeypatch.setattr(
        "api_service.services.omnigent_agent_profile_selection.resolve_default_agent_profile_snapshot",
        AsyncMock(return_value={"providerProfileRef": "other-profile"}),
    )
    monkeypatch.setattr(
        "api_service.services.omnigent_agent_profile_selection.compile_agent_profile_snapshot_parameters",
        lambda parameters, **_: parameters,
    )
    selection = SimpleNamespace(
        tier_parameters={},
        model="other/model",
        effort="high",
        model_source="explicit",
        as_metadata=dict,
    )
    monkeypatch.setattr(steps, "resolve_model_effort", lambda **_: selection)
    artifacts = AsyncMock()
    with pytest.raises(TemporalArtifactAuthorizationError, match="outside"):
        await steps.compile_step_execution_plans(
            root_plan=SimpleNamespace(
                payload=SimpleNamespace(
                    modelConfig=SimpleNamespace(qualifiedId="root/model", effort="high")
                )
            ),
            provider_profile=SimpleNamespace(profile_id="root-profile"),
            initial_parameters=parameters,
            agent_profile_snapshot={},
            session_factory=object(),
            artifact_service=artifacts,
            principal="operator",
            workflow_id="source",
            authored_request_ref="art-request",
            authored_request_digest="sha256:" + "a" * 64,
            task_input_snapshot_ref="art-input",
            task_input_snapshot_digest="sha256:" + "b" * 64,
            access=None,
        )
    assert parameters["assessmentProject"]["configuration"]["evidenceProcessing"][
        "allowedProfileRefs"
    ] == ["root-profile"]
    assert artifacts.mock_calls == []
