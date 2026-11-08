"""Retained operator admission for legacy scoped assessment journey fixtures."""

from api_service.db.models import TemporalExecutionCanonicalRecord


async def seed_legacy_assessment_admission(
    environment, scope, *, workflow_id="assessment", run_id="run"
):
    """Model the canonical execution that preceded a signed legacy session.

    The scope comes from the fixture's operator setup, never the job payload.
    Seed once: subsequent requests cannot replace it, renew it, or recreate a
    deleted source. New record-backed operator admission has separate coverage.
    """
    if scope is None or getattr(environment, "assessment_admission_seeded", False):
        return
    assert scope.engagement is None, "This fixture represents legacy admission only"
    source = await environment.session.get(
        TemporalExecutionCanonicalRecord, workflow_id
    )
    assert source is None, "Do not overwrite an existing canonical admission"
    environment.session.add(
        TemporalExecutionCanonicalRecord(
            workflow_id=workflow_id,
            run_id=run_id,
            workflow_type="MoonMind.UserWorkflow",
            entry="run",
            parameters={
                "assessmentScope": scope.model_dump(mode="json", by_alias=True)
            },
        )
    )
    await environment.session.commit()
    environment.assessment_admission_seeded = True
