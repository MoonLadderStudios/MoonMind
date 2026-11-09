"""Selected security evidence crosses the existing remediation boundary intact."""
import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from moonmind.security.assessments import Comparison
from moonmind.workflows.temporal import remediation_context
from moonmind.workflows.temporal.artifacts import ArtifactAccessContext, ArtifactDependency
from tests.unit.security.test_assessment_comparison import compare, report


def comparison_service():
    comparison = Comparison.model_validate({**compare(report()), "scanRef": "art_scan"})
    body = comparison.model_dump_json(by_alias=True).encode()
    artifact = SimpleNamespace(artifact_id="art_comparison", sha256=hashlib.sha256(body).hexdigest(), size_bytes=len(body))
    link = SimpleNamespace(workflow_id="mm:source", run_id="run-source", link_type="security.comparison")
    service = SimpleNamespace(
        get_metadata=AsyncMock(return_value=(artifact, [link], False, None)),
        read=AsyncMock(return_value=(artifact, body)),
        verified_dependencies=AsyncMock(return_value=SimpleNamespace(dependencies=(ArtifactDependency("art_evidence", "a" * 64), ArtifactDependency("art_scan", "b" * 64)))),
    )
    return service, comparison


async def resolve(service):
    return await remediation_context.read_security_remediation_findings(
        service=service, workflow_id="mm:source", run_id="run-source",
        comparison_artifact_ref="art_comparison",
        access=ArtifactAccessContext.for_execution("mm:source", principal="service:test"),
    )


@pytest.mark.asyncio
async def test_selected_evidence_preserves_original_finding_and_comparison_state():
    service, comparison = comparison_service()
    result = await resolve(service)
    selected = result["findings"][0]
    assert selected["findingId"] == comparison.findings[0].finding_id
    assert selected["finding"]["state"] == comparison.findings[0].state
    assert selected["finding"]["finding"]["evidenceRefs"] == ["art_evidence"]
    assert selected["scope"] == comparison.observation.scope.model_dump(by_alias=True)
    assert selected["comparisonDigest"] == service.get_metadata.return_value[0].sha256
    assert selected["decisions"] == []
    service.verified_dependencies.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("workflow,run", [("mm:other", "run-source"), ("mm:source", "run-other")])
async def test_cannot_select_another_workflow_or_run_comparison(workflow, run):
    service, _ = comparison_service()
    service.get_metadata.return_value[1][0].workflow_id = workflow
    service.get_metadata.return_value[1][0].run_id = run
    with pytest.raises(remediation_context.RemediationContextError, match="source workflow/run"):
        await resolve(service)
    service.read.assert_not_awaited()


@pytest.mark.asyncio
async def test_unsealed_comparison_cannot_become_verified_remediation_evidence():
    service, _ = comparison_service()
    service.verified_dependencies.return_value = None
    with pytest.raises(remediation_context.RemediationContextError, match="sealed"):
        await resolve(service)


@pytest.mark.asyncio
async def test_read_bytes_must_match_comparison_digest():
    service, _ = comparison_service()
    artifact, body = service.read.return_value
    service.read.return_value = (artifact, body + b" ")
    with pytest.raises(remediation_context.RemediationContextError, match="digest"):
        await resolve(service)


@pytest.mark.asyncio
async def test_admission_context_and_repeated_submission_pin_real_sealed_comparison(tmp_path, monkeypatch):
    from unittest.mock import MagicMock
    from uuid import uuid4
    from api_service.db.models import MoonMindWorkflowState, TemporalExecutionCanonicalRecord, TemporalExecutionRemediationLink
    from moonmind.workflows.temporal.artifacts import TemporalArtifactRepository, TemporalArtifactService
    from moonmind.workflows.temporal.service import TemporalExecutionService, TemporalExecutionValidationError
    from tests.unit.workflows.temporal.test_remediation_context import temporal_db
    from tests.unit.workflows.temporal.test_temporal_service import _valid_user_workflow_parameters
    from tests.integration.security.test_recurring_assessments import inputs, invoke, put

    adapter = MagicMock()
    adapter.start_workflow = AsyncMock(side_effect=[SimpleNamespace(run_id="source-run"), SimpleNamespace(run_id="repair-run")])
    adapter.namespace_retention_seconds = AsyncMock(return_value=86400)
    async with temporal_db(tmp_path) as session:
        owner = uuid4()
        executions = TemporalExecutionService(session, client_adapter=adapter)
        base = dict(workflow_type="MoonMind.UserWorkflow", owner_id=owner, input_artifact_ref=None,
                    plan_artifact_ref=None, manifest_artifact_ref=None, failure_policy=None)
        source = await executions.create_execution(**base, title="Assessment succeeded", initial_parameters=_valid_user_workflow_parameters(), idempotency_key="source")
        source_record = await session.get(TemporalExecutionCanonicalRecord, source.workflow_id)
        source_record.state = MoonMindWorkflowState.COMPLETED
        await session.commit()
        await session.refresh(source_record)
        artifacts = TemporalArtifactService(TemporalArtifactRepository(session))
        request, inventory, feed = await inputs(artifacts)
        request["inventoryRef"] = await put(artifacts, inventory, workflow_id=source.workflow_id, run_id=source.run_id)
        request["feedRef"] = await put(artifacts, feed, workflow_id=source.workflow_id, run_id=source.run_id)
        outputs = await invoke(artifacts, request, workflow_id=source.workflow_id, run_id=source.run_id)
        comparison_ref = outputs["comparisonRef"]
        preview = await remediation_context.read_security_remediation_findings(
            service=artifacts, workflow_id=source.workflow_id, run_id=source.run_id,
            comparison_artifact_ref=comparison_ref,
            access=ArtifactAccessContext.for_execution(source.workflow_id, principal="service:test"),
        )
        from fastapi import FastAPI
        from httpx import ASGITransport, AsyncClient
        from api_service.api.routers import executions as routes
        from api_service.auth_providers import get_instance_access
        from api_service.db.base import get_async_session
        from moonmind.security.operator_admission import OperatorAdmission
        app = FastAPI()
        app.include_router(routes.router)
        app.dependency_overrides[routes._get_service] = lambda: executions
        app.dependency_overrides[get_async_session] = lambda: session
        app.dependency_overrides[get_instance_access] = lambda: OperatorAdmission(via="loopback")
        app.dependency_overrides[routes.get_artifact_access] = lambda: ArtifactAccessContext.for_operator(OperatorAdmission(via="loopback"))
        adapter.describe_workflow = AsyncMock(return_value=None)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            response = await client.get(f"/api/executions/{source.workflow_id}/remediation-security-findings",
                params={"comparisonArtifactRef": comparison_ref, "runId": source.run_id})
            assert response.status_code == 200, response.text
            assert response.json() == preview
            denied = await client.get(f"/api/executions/{source.workflow_id}/remediation-security-findings",
                params={"comparisonArtifactRef": comparison_ref, "runId": "foreign-run"})
            assert denied.status_code == 409
        selected = preview["findings"][0]
        params = {"workflow": {"instructions": "Investigate selected finding, do not publish", "remediation": {
            "target": {"workflowId": source.workflow_id, "runId": source.run_id,
                       "securityFinding": {"comparisonArtifactRef": comparison_ref, "findingId": selected["findingId"]}},
            "mode": "snapshot", "authorityMode": "observe_only", "trigger": {"type": "manual"},
        }}}
        repair = await executions.create_execution(**base, title="Investigate finding", initial_parameters=params, idempotency_key="repair")
        stored = await session.get(TemporalExecutionCanonicalRecord, repair.workflow_id)
        evidence = stored.parameters["workflow"]["remediation"]["securityEvidence"]
        assert evidence == {**selected, "sourceAssessmentScope": None}
        assert "assessmentScope" not in stored.parameters
        for ref in evidence["evidenceArtifactRefs"]:
            # The actual host input reader can read each admitted native input,
            # without acquiring source-workflow or mutation authority.
            await artifacts.read(artifact_id=ref,
                access=ArtifactAccessContext.for_execution(repair.workflow_id, principal="service:host"))
        from moonmind.workflows.temporal.activities import omnigent_session_activities
        from contextlib import asynccontextmanager
        @asynccontextmanager
        async def current_session():
            yield session
        monkeypatch.setattr("api_service.db.base.async_session_maker", current_session)
        assert await omnigent_session_activities._remediation_security_input_refs(repair.workflow_id) == [
            f"artifact://{ref}" for ref in evidence["evidenceArtifactRefs"]
        ]
        import copy
        tampered = copy.deepcopy(stored.parameters)
        tampered["workflow"]["remediation"]["securityEvidence"] = {
            "evidenceArtifactRefs": ["art_foreign"], "finding": {"state": "remediated_verified"},
        }
        stored.parameters = tampered
        await session.commit()
        assert await omnigent_session_activities._remediation_security_input_refs(repair.workflow_id) == [
            f"artifact://{ref}" for ref in evidence["evidenceArtifactRefs"]
        ]
        # Restore only the fixture's editable parameters; the immutable
        # admitted context was unchanged throughout the attempted substitution.
        stored.parameters = {**stored.parameters, "workflow": {**stored.parameters["workflow"],
            "remediation": {**stored.parameters["workflow"]["remediation"], "securityEvidence": evidence}}}
        await session.commit()

        link = await session.get(TemporalExecutionRemediationLink, repair.workflow_id)
        _, raw = await artifacts.read(artifact_id=link.context_artifact_ref,
            access=ArtifactAccessContext.for_service("service:remediation-context"))
        import json
        assert json.loads(raw)["securityEvidence"] == evidence
        # A lost acknowledgment reuses the existing admission and context, never
        # reruns scanners or creates a second repair/publication candidate.
        again = await executions.create_execution(**base, title="Investigate finding", initial_parameters=params, idempotency_key="repair")
        assert again.workflow_id == repair.workflow_id
        assert adapter.start_workflow.await_count == 2
        assert (await session.get(TemporalExecutionRemediationLink, repair.workflow_id)).context_artifact_ref == link.context_artifact_ref
        # A caller cannot use the snapshot field to upgrade the comparison.
        import copy
        forged = copy.deepcopy(params)
        forged["workflow"]["remediation"]["securityEvidence"] = {**evidence, "finding": {"state": "remediated_verified"}}
        with pytest.raises(TemporalExecutionValidationError, match="server-owned"):
            await executions.create_execution(**base, title="Forged verdict", initial_parameters=forged, idempotency_key="forged")


@pytest.mark.asyncio
async def test_seal_must_cover_citations_and_scan_not_just_exist():
    service, _ = comparison_service()
    service.verified_dependencies.return_value = SimpleNamespace(dependencies=())
    with pytest.raises(remediation_context.RemediationContextError, match="cover its original evidence"):
        await resolve(service)


@pytest.mark.asyncio
async def test_arbitrary_uploaded_comparison_json_is_not_a_producer_comparison():
    service, _ = comparison_service()
    service.get_metadata.return_value[1][0].link_type = "input.upload"
    with pytest.raises(remediation_context.RemediationContextError, match="source workflow/run"):
        await resolve(service)
