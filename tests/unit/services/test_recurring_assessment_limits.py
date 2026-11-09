"""Continuing authority uses durable occurrences, not mutable schedule counters."""
from datetime import UTC, datetime, timedelta

import pytest

from api_service.services import assessment_authorizations as owner
from api_service.db.models import TemporalExecutionCanonicalRecord
from moonmind.security.assessment_authorizations import authorization_scope
from tests.unit.services.test_assessment_job_authority import authorization, save_scope
from tests.unit.services.test_recurring_workflows_service import recurring_db

NOW = datetime(2026, 11, 8, 12, tzinfo=UTC)
TERMS = dict(minIntervalSeconds=3600, maxOccurrences=2, maxJobsPerOccurrence=2,
             maxJobSecondsPerOccurrence=200, maxRunSeconds=900)


def auth():
    return authorization(validFrom=NOW-timedelta(hours=1), validUntil=NOW+timedelta(days=1), recurring=TERMS)


@pytest.mark.asyncio
async def test_restart_retry_cadence_and_aggregate_occurrences(tmp_path):
    record = auth()
    scope = authorization_scope(record)
    async with recurring_db(tmp_path) as sessions:
        async with sessions() as session:
            await save_scope(session, scope, auth=record)
            first = await owner.admit_assessment_occurrence(session, workflow_id="workflow-1", now=NOW)
            await session.commit()
            assert first["expiresAt"] == (NOW+timedelta(seconds=900)).isoformat()
        async with sessions() as session:
            assert await owner.admit_assessment_occurrence(session, workflow_id="workflow-1", now=NOW+timedelta(seconds=1)) == first
            await save_scope(session, scope, workflow="workflow-2")
            with pytest.raises(owner.AssessmentAuthorizationDenied, match="cadence"):
                await owner.admit_assessment_occurrence(session, workflow_id="workflow-2", now=NOW+timedelta(seconds=60))
            second = await owner.admit_assessment_occurrence(session, workflow_id="workflow-2", now=NOW+timedelta(hours=1))
            await session.commit()
            assert second["ordinal"] == 2
            await save_scope(session, scope, workflow="workflow-3")
            with pytest.raises(owner.AssessmentAuthorizationDenied, match="occurrence budget"):
                await owner.admit_assessment_occurrence(session, workflow_id="workflow-3", now=NOW+timedelta(hours=2))


@pytest.mark.asyncio
async def test_run_deadline_cannot_be_renewed_and_cancel_stops_retry(tmp_path):
    record = auth()
    scope = authorization_scope(record)
    async with recurring_db(tmp_path) as sessions:
        async with sessions() as session:
            await save_scope(session, scope, auth=record)
            await owner.admit_assessment_occurrence(session, workflow_id="workflow-1", now=NOW)
            await session.commit()
            with pytest.raises(owner.AssessmentAuthorizationDenied, match="deadline"):
                await owner.admit_assessment_occurrence(session, workflow_id="workflow-1", now=NOW+timedelta(seconds=900))
            source = await session.get(TemporalExecutionCanonicalRecord, "workflow-1")
            source.memo = {"temporalCancel": {"requested": True}}
            await session.commit()
            with pytest.raises(owner.AssessmentAuthorizationDenied, match="canceled"):
                await owner.admit_assessment_occurrence(session, workflow_id="workflow-1", now=NOW+timedelta(seconds=30))


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["before", "expired", "revoked", "target", "generation", "grant_expired"])
async def test_current_authority_checked_at_each_occurrence(tmp_path, change):
    from sqlalchemy import select
    from api_service.db.models import SettingsOverride
    record = auth()
    if change == "grant_expired":
        record = record.model_copy(update={"scope": record.scope.model_copy(update={"grants": (
            __import__("moonmind.security.workload_profiles", fromlist=["CredentialGrant"]).CredentialGrant(
                ref="secret://fixture", purpose="application_auth", account="fixture", profileRefs=["source-scan"], expiresAt=NOW-timedelta(seconds=1), revision=1,
            ),)})})
    scope = authorization_scope(record)
    async with recurring_db(tmp_path) as sessions:
        async with sessions() as session:
            await save_scope(session, scope, auth=record)
            moment = NOW
            if change in {"before", "expired"}:
                moment = record.valid_from-timedelta(seconds=1) if change == "before" else record.valid_until
            elif change in {"revoked", "target", "generation"}:
                row = await session.scalar(select(SettingsOverride))
                data = record.model_dump(mode="json", by_alias=True)
                if change == "revoked": data["enabled"] = False
                if change == "generation": data["generation"] = "replacement"
                if change == "target": data["scope"]["targets"] = [{"artifactRef": "art_other"}]
                row.value_json = {"authorizations": [data]}
                await session.commit()
            with pytest.raises(owner.AssessmentAuthorizationDenied):
                await owner.admit_assessment_occurrence(session, workflow_id="workflow-1", now=moment)


@pytest.mark.asyncio
async def test_renewal_and_changed_schedule_keep_previous_occurrence_consumption(tmp_path):
    from sqlalchemy import select
    from api_service.db.models import SettingsOverride
    record = auth()
    async with recurring_db(tmp_path) as sessions:
        async with sessions() as session:
            await save_scope(session, authorization_scope(record), auth=record)
            await owner.admit_assessment_occurrence(session, workflow_id="workflow-1", now=NOW)
            renewed = record.model_copy(update={"generation": "new-generation", "valid_until": record.valid_until+timedelta(days=1)})
            row = await session.scalar(select(SettingsOverride))
            row.value_json = {"authorizations": [renewed.model_dump(mode="json", by_alias=True)]}
            await session.commit()
            await save_scope(session, authorization_scope(renewed), workflow="replacement-schedule")
            second = await owner.admit_assessment_occurrence(session, workflow_id="replacement-schedule", now=NOW+timedelta(hours=1))
            assert second["ordinal"] == 2
            await session.commit()
            await save_scope(session, authorization_scope(renewed), workflow="replacement-schedule-2")
            with pytest.raises(owner.AssessmentAuthorizationDenied, match="occurrence budget"):
                await owner.admit_assessment_occurrence(session, workflow_id="replacement-schedule-2", now=NOW+timedelta(hours=2))
