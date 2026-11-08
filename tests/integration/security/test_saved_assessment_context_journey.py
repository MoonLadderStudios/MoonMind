"""Saved context travels through the production assessment/report owner."""

import copy

import pytest

from moonmind.security.assessment_projects import resolve_assessment_project
from tests.artifact_access import access_for_test
from tests.integration.security import test_recurring_assessments as recurring

environment = recurring.environment
pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]


async def test_context_survives_saved_edits_without_invalidating_native_scan(
    environment,
):
    request, _, _ = await recurring.inputs(environment.service)
    document = {
        "projects": [
            {
                "id": "shop",
                "name": "Shop",
                "purpose": "Checkout evidence",
                "boundaries": "No payment provider",
                "environments": [
                    {
                        "id": "test",
                        "name": "Test",
                        "assets": ["app"],
                        "period": {"start": "2026-10-01", "end": "2026-10-31"},
                        "retentionDays": 90,
                    }
                ],
            }
        ]
    }
    first_context = resolve_assessment_project(
        document, project_id="shop", environment_id="test", version=1
    )
    request["assessmentProject"] = first_context.model_dump(mode="json", by_alias=True)
    first = await recurring.invoke(environment.service, request, key="project-first")
    assert first["publicationOutcome"] == "published"
    retained = await recurring.read(environment.service, first["assessmentProjectRef"])
    assert retained == request["assessmentProject"]
    _, old_report = await environment.service.read(
        artifact_id=recurring.report_ref(first),
        access=access_for_test("workflow:assessment"),
    )
    assert b"Checkout evidence" in old_report
    assert b"not execution authority" in old_report
    document["projects"][0]["purpose"] = "Changed future assessment purpose"
    changed = copy.deepcopy(request)
    changed["assessmentProject"] = resolve_assessment_project(
        document, project_id="shop", environment_id="test", version=2
    ).model_dump(mode="json", by_alias=True)
    second = await recurring.invoke(environment.service, changed, key="project-second")
    assert second["scanRef"] == first["scanRef"]
    assert second["assessmentProjectRef"] != first["assessmentProjectRef"]
    assert recurring.report_ref(second) != recurring.report_ref(first)
    assert (
        await recurring.read(environment.service, first["assessmentProjectRef"])
        == retained
    )
    rerun = await recurring.invoke(environment.service, request, key="project-rerun")
    assert rerun["assessmentProjectRef"] == first["assessmentProjectRef"]
    assert recurring.report_ref(rerun) == recurring.report_ref(first)
    assert first["assessmentProjectRef"] in await recurring.required(
        environment.service, recurring.report_ref(first)
    )


async def test_current_artifact_owner_blocks_foreign_and_quarantined_context(
    environment,
    monkeypatch,
):
    from api_service.services.assessment_projects import assessment_project_gaps
    from moonmind.workflows.temporal.artifacts import (
        ArtifactAccessContext,
        TemporalArtifactService,
    )

    # Use the fixture's local store with the real metadata/authorization owner,
    # rather than the unrelated deployment S3 setting used by other tests.
    monkeypatch.setattr(
        "api_service.services.assessment_projects.TemporalArtifactService",
        lambda repository: TemporalArtifactService(
            repository, store=environment.service._store
        ),
    )
    ref = await recurring.put(environment.service, {"private": "do-not-disclose"})
    document = {
        "projects": [
            {
                "id": "shop",
                "name": "Shop",
                "purpose": "Evidence",
                "boundaries": "Local",
                "environments": [{"id": "test", "name": "Test", "artifactRefs": [ref]}],
            }
        ]
    }
    snapshot = resolve_assessment_project(
        document, project_id="shop", environment_id="test", version=1
    )
    foreign = await assessment_project_gaps(
        snapshot,
        session=environment.session,
        access=ArtifactAccessContext.for_service("system:foreign"),
        principal="system:foreign",
    )
    assert foreign[0]["code"] == "artifact_unavailable"
    assert "do-not-disclose" not in str(foreign)
    # The fixture belongs to this execution, not the instance operator.
    operator = access_for_test("workflow:assessment")
    assert not await assessment_project_gaps(
        snapshot,
        session=environment.session,
        access=operator,
        principal=operator.principal,
    )
    artifact = await environment.service._repository.get_artifact(ref)
    artifact.metadata_json = {**(artifact.metadata_json or {}), "quarantine": True}
    await environment.session.flush()
    blocked = await assessment_project_gaps(
        snapshot,
        session=environment.session,
        access=operator,
        principal=operator.principal,
    )
    assert blocked[0]["code"] == "artifact_unavailable"
    from datetime import UTC, datetime, timedelta

    from api_service.db.models import TemporalArtifactRetentionClass

    artifact.metadata_json = {}
    artifact.expires_at = datetime.now(UTC) - timedelta(days=1)
    await environment.session.flush()
    expired = await assessment_project_gaps(
        snapshot,
        session=environment.session,
        access=operator,
        principal=operator.principal,
    )
    assert expired[0]["code"] == "artifact_unavailable"
    artifact.retention_class = TemporalArtifactRetentionClass.PINNED
    await environment.session.flush()
    assert not await assessment_project_gaps(
        snapshot,
        session=environment.session,
        access=operator,
        principal=operator.principal,
    )
