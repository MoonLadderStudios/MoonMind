"""Restricted assessment artifacts through actual Skill Activity admission."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete
from temporalio.exceptions import ApplicationError

from api_service.db.models import (
    MoonMindWorkflowState,
    TemporalArtifactLink,
    TemporalArtifactRedactionLevel,
    TemporalExecutionCanonicalRecord,
    TemporalWorkflowType,
)
from moonmind.workflows.temporal.artifacts import TemporalArtifactAuthorizationError
from tests.artifact_access import access_for_test
from tests.integration.security import test_recurring_assessments as recurring

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]
environment = recurring.environment


async def test_real_assessment_retries_keep_restricted_native_reads_and_operator_ownership(
    environment,
):
    service = environment.service
    operator = access_for_test("operator")
    environment.session.add(
        TemporalExecutionCanonicalRecord(
            namespace="default",
            workflow_id="assessment",
            run_id="run",
            workflow_type=TemporalWorkflowType.USER_WORKFLOW,
            owner_id=operator.principal,
            state=MoonMindWorkflowState.COMPLETED,
            entry="run",
        )
    )
    await environment.session.commit()
    request, _, _ = await recurring.inputs(service)
    first = await recurring.invoke(service, request)
    retry = await recurring.invoke(service, request)
    for key in ("scanRef", "comparisonRef"):
        assert retry[key] == first[key]
        artifact, body = await service.read(artifact_id=first[key], access=operator)
        assert (
            body
            and artifact.redaction_level is TemporalArtifactRedactionLevel.RESTRICTED
        )
        assert (
            await service.dependency_proof(root_artifact_id=first[key], access=operator)
            is not None
        )
        reader = access_for_test("workflow:other")._with_verified_inputs([first[key]])
        _, _, _, policy = await service.get_metadata(
            artifact_id=first[key], access=reader
        )
        assert not policy.raw_access_allowed and policy.preview_artifact_ref
        with pytest.raises(TemporalArtifactAuthorizationError):
            await service.read(artifact_id=first[key], access=reader)
        _, safe = await service.read(
            artifact_id=policy.default_read_ref.artifact_id, access=reader
        )
        assert safe


@pytest.mark.parametrize("fault", ["foreign", "expired", "revoked"])
async def test_real_assessment_rechecks_current_evidence_before_saved_work(
    environment, fault
):
    service = environment.service
    request, _, _ = await recurring.inputs(service)
    if fault == "foreign":
        request["inventoryRef"] = await recurring.put(
            service, {"private": "synthetic-protected-evidence"}, workflow_id="other"
        )
    elif fault == "expired":
        item = await service._repository.get_artifact(request["inventoryRef"])
        item.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await environment.session.commit()
    else:
        original = await recurring.invoke(service, request)
        # A retained baseline is metadata-readable, but removing its current
        # explicit raw grant cannot turn historical provenance into permission.
        request["baselineRef"] = original["comparisonRef"]
        await environment.session.execute(
            delete(TemporalArtifactLink).where(
                TemporalArtifactLink.artifact_id == original["comparisonRef"],
                TemporalArtifactLink.link_type == "input.artifact.raw",
            )
        )
        await environment.session.commit()
        result = await recurring.invoke(service, request)
        assert result["baselineStatus"] == "unavailable"
        assert result["scanOutcome"] == "completed"
        assert "synthetic-protected-evidence" not in str(result)
        return
    with pytest.raises(ApplicationError) as raised:
        await recurring.invoke(service, request)
    assert "synthetic-protected-evidence" not in str(raised.value)
