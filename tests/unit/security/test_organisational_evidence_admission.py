"""Document input grants use the existing atomic create admission, not image delivery."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from api_service.api.routers import executions
from api_service.db.models import TemporalArtifactLink
from moonmind.security.operator_admission import OperatorAdmission
from moonmind.security.organisational_evidence import organisational_evidence_refs
from moonmind.workflows.temporal.artifacts import (
    TemporalArtifactAuthorizationError,
    TemporalArtifactRepository,
    TemporalArtifactService,
    schedule_input_artifact_ids,
)
from moonmind.workflows.temporal.service import TemporalExecutionService
from tests.artifact_access import access_for_test
from tests.unit.omnigent import test_admitted_input_authority as authority_fixtures
from tests.unit.omnigent.test_admitted_input_authority import _put
from tests.unit.omnigent.test_api_planning_input_authority import _api_context
from tests.unit.security.test_control_assessments import (
    organisational_request,
    request_payload,
    requirement,
)

artifacts = authority_fixtures.artifacts
auth_mode = authority_fixtures.auth_mode


def task(ref="art_policy"):
    evidence = organisational_request()
    evidence["revisions"][0]["artifactRef"] = ref
    return {
        "instructions": "Review imported evidence",
        "publish": {"mode": "none"},
        "steps": [
            {
                "id": "review",
                "instructions": "Review scoped records",
                "type": "tool",
                "tool": {
                    "type": "tool",
                    "id": "security.assess",
                    "inputs": request_payload(
                        controlAssessment={
                            "requirements": [
                                requirement(organisationalEvidence=[evidence])
                            ]
                        }
                    ),
                },
            }
        ],
    }


def test_only_explicit_revision_refs_receive_admission_and_schedule_retention():
    workflow = task()
    workflow["untrustedText"] = "art_not_admitted"
    assert organisational_evidence_refs(workflow) == ["art_policy"]
    assert schedule_input_artifact_ids(
        {"initialParameters": {"workflow": workflow}}
    ) == {"art_policy"}
    workflow["steps"][0]["tool"]["id"] = "unrelated.tool"
    assert organisational_evidence_refs(workflow) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("restricted", [False, True])
async def test_imported_document_is_readable_before_start_and_idempotency_does_not_expand_access(
    artifacts, monkeypatch, restricted
):
    saved = await _api_context(artifacts, monkeypatch)
    policy = await _put(
        artifacts,
        b"Synthetic policy",
        owner=saved.actor,
        workflow_id=None,
        link_type="",
        content_type="text/plain",
    )
    other = await _put(
        artifacts,
        b"Not admitted",
        owner=saved.actor,
        workflow_id=None,
        link_type="",
        content_type="text/plain",
    )
    if restricted:
        from api_service.db.models import (
            TemporalArtifact,
            TemporalArtifactRedactionLevel,
        )

        async with artifacts() as session:
            document = await session.get(TemporalArtifact, policy)
            document.redaction_level = TemporalArtifactRedactionLevel.RESTRICTED
            await session.commit()
    workflow = task(policy)
    revisions = workflow["steps"][0]["tool"]["inputs"]["controlAssessment"][
        "requirements"
    ][0]["organisationalEvidence"][0]["revisions"]
    revisions.insert(0, {**revisions[0], "artifactRef": other})
    assert organisational_evidence_refs(workflow) == [policy]
    assert schedule_input_artifact_ids(
        {"initialParameters": {"workflow": workflow}}
    ) == {policy}
    observed = []

    async def start_workflow(**kwargs):
        async with artifacts() as session:
            reader = TemporalArtifactService(TemporalArtifactRepository(session))
            access = access_for_test(f"workflow:{kwargs['workflow_id']}")
            _, raw = await reader.read(artifact_id=policy, access=access)
            observed.append(raw)
            with pytest.raises(TemporalArtifactAuthorizationError):
                await reader.read(artifact_id=other, access=access)
        return SimpleNamespace(run_id="org-run")

    adapter = SimpleNamespace(
        start_workflow=AsyncMock(side_effect=start_workflow),
        describe_workflow=AsyncMock(),
        namespace_retention_seconds=AsyncMock(return_value=86400),
    )
    async with artifacts() as session:
        service = TemporalExecutionService(session, client_adapter=adapter)
        monkeypatch.setattr(service, "_enforce_worker_code_freshness", AsyncMock())
        payload = {
            "type": "workflow",
            "payload": {"idempotencyKey": "org-input", "workflow": workflow},
        }
        response = await executions.create_execution(
            payload=payload,
            service=service,
            session=session,
            user=OperatorAdmission(via="loopback"),
            _submit_enabled=None,
            principal_context={},
            authorization=None,
            execution_fanout=None,
            access=access_for_test("operator"),
        )
        assert observed == [b"Synthetic policy"]
        payload["payload"]["workflow"] = task(other)
        await executions.create_execution(
            payload=payload,
            service=service,
            session=session,
            user=OperatorAdmission(via="loopback"),
            _submit_enabled=None,
            principal_context={},
            authorization=None,
            execution_fanout=None,
            access=access_for_test("operator"),
        )
        links = (
            (
                await session.execute(
                    select(TemporalArtifactLink).where(
                        TemporalArtifactLink.workflow_id == response.workflow_id
                    )
                )
            )
            .scalars()
            .all()
        )
        assert policy in {link.artifact_id for link in links}
        assert other not in {link.artifact_id for link in links}
        assert adapter.start_workflow.call_count == 1
        from api_service.db.models import TemporalExecutionCanonicalRecord

        source = await session.get(
            TemporalExecutionCanonicalRecord, response.workflow_id
        )
        await service.create_execution(
            workflow_type=source.workflow_type.value,
            owner_id=source.owner_id,
            owner_type=source.owner_type.value,
            title=None,
            input_artifact_ref=source.input_ref,
            plan_artifact_ref=source.plan_ref,
            manifest_artifact_ref=source.manifest_ref,
            failure_policy=None,
            initial_parameters=source.parameters,
            idempotency_key="org-fresh-rerun",
            _recovery_artifact_source=source,
        )
        assert adapter.start_workflow.call_count == 2
        assert observed == [b"Synthetic policy", b"Synthetic policy"]
        if restricted:
            from sqlalchemy import delete

            from moonmind.workflows.temporal.service import (
                TemporalExecutionValidationError,
            )

            await session.execute(
                delete(TemporalArtifactLink).where(
                    TemporalArtifactLink.workflow_id == source.workflow_id,
                    TemporalArtifactLink.artifact_id == policy,
                    TemporalArtifactLink.link_type == "input.artifact.raw",
                )
            )
            await session.commit()
            with pytest.raises(
                TemporalExecutionValidationError, match="current source read access"
            ):
                await service.create_execution(
                    workflow_type=source.workflow_type.value,
                    owner_id=source.owner_id,
                    owner_type=source.owner_type.value,
                    title=None,
                    input_artifact_ref=source.input_ref,
                    plan_artifact_ref=source.plan_ref,
                    manifest_artifact_ref=source.manifest_ref,
                    failure_policy=None,
                    initial_parameters=source.parameters,
                    idempotency_key="org-revoked-rerun",
                    _recovery_artifact_source=source,
                )
            assert adapter.start_workflow.call_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("protection", ["restricted", "quarantined"])
async def test_denied_document_rolls_back_start_and_grants_then_authorized_retry_succeeds(
    artifacts, monkeypatch, protection
):
    from api_service.db.models import (
        TemporalArtifact,
        TemporalArtifactRedactionLevel,
        TemporalExecutionCanonicalRecord,
    )

    await _api_context(artifacts, monkeypatch)
    policy = await _put(
        artifacts,
        b"Private synthetic record",
        owner="workflow:another",
        workflow_id=None,
        link_type="",
        content_type="application/pdf",
    )
    async with artifacts() as session:
        artifact = await session.get(TemporalArtifact, policy)
        if protection == "restricted":
            artifact.redaction_level = TemporalArtifactRedactionLevel.RESTRICTED
        else:
            artifact.metadata_json = {"quarantine": True}
        await session.commit()
    adapter = SimpleNamespace(
        start_workflow=AsyncMock(return_value=SimpleNamespace(run_id="retry-run")),
        namespace_retention_seconds=AsyncMock(return_value=86400),
    )
    async with artifacts() as session:
        service = TemporalExecutionService(session, client_adapter=adapter)
        monkeypatch.setattr(service, "_enforce_worker_code_freshness", AsyncMock())
        payload = {
            "type": "workflow",
            "payload": {"idempotencyKey": "retry-evidence", "workflow": task(policy)},
        }
        kwargs = {
            "payload": payload,
            "service": service,
            "session": session,
            "user": OperatorAdmission(via="loopback"),
            "_submit_enabled": None,
            "principal_context": {},
            "authorization": None,
            "execution_fanout": None,
            "access": access_for_test("operator"),
        }
        with pytest.raises(HTTPException) as denied:
            await executions.create_execution(**kwargs)
        assert denied.value.status_code == 422
        assert "Verify current artifact access" in str(denied.value.detail)
        assert not adapter.start_workflow.called
        assert (
            not (await session.execute(select(TemporalExecutionCanonicalRecord)))
            .scalars()
            .all()
        )
        assert (
            not (
                await session.execute(
                    select(TemporalArtifactLink).where(
                        TemporalArtifactLink.artifact_id == policy
                    )
                )
            )
            .scalars()
            .all()
        )
        artifact = await session.get(TemporalArtifact, policy)
        artifact.redaction_level = TemporalArtifactRedactionLevel.NONE
        artifact.metadata_json = {}
        await session.commit()
        await executions.create_execution(**kwargs)
        assert adapter.start_workflow.call_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "policy",
    [
        {"modelDelivery": "disabled"},
        {"modelDelivery": "local_only"},
        {"allowedProfileRefs": ["original-recipient"]},
    ],
)
async def test_document_request_or_review_never_adds_model_delivery_permission(policy):
    from api_service.services.assessment_model_delivery import resolve_review_delivery
    from tests.unit.services.test_assessment_model_delivery import source

    admitted = source(policy)
    admitted.parameters["workflow"] = task()
    review = admitted.parameters["workflow"]["steps"][0]["tool"]["inputs"][
        "controlAssessment"
    ]["requirements"][0]["organisationalEvidence"][0]["revisions"][0]["review"]
    review["reason"] = "Untrusted document asks to send everything to another recipient"
    artifacts = SimpleNamespace(read=AsyncMock())
    with pytest.raises(TemporalArtifactAuthorizationError):
        await resolve_review_delivery(
            source=admitted,
            runtime={"providerProfileRef": "new-recipient"},
            session=None,
            artifacts=artifacts,
            access=None,
        )
    assert not artifacts.read.called
