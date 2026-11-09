"""Continuing authority uses durable occurrences, not mutable schedule counters."""

from datetime import UTC, datetime, timedelta

import pytest

from api_service.services import assessment_authorizations as owner
from api_service.db.models import (
    AssessmentOccurrenceRecord,
    TemporalExecutionCanonicalRecord,
)
from moonmind.security.assessment_authorizations import authorization_scope
from tests.unit.services.test_assessment_job_authority import authorization, save_scope
from tests.unit.services.test_recurring_workflows_service import recurring_db

NOW = datetime(2026, 11, 8, 12, tzinfo=UTC)
TERMS = dict(
    minIntervalSeconds=3600,
    maxOccurrences=2,
    maxJobsPerOccurrence=2,
    maxJobSecondsPerOccurrence=200,
    maxRunSeconds=900,
)


def auth():
    return authorization(
        validFrom=NOW - timedelta(hours=1),
        validUntil=NOW + timedelta(days=1),
        recurring=TERMS,
    )


@pytest.mark.asyncio
async def test_restart_retry_cadence_and_aggregate_occurrences(tmp_path):
    record = auth()
    scope = authorization_scope(record)
    async with recurring_db(tmp_path) as sessions:
        async with sessions() as session:
            await save_scope(session, scope, auth=record)
            first = await owner.admit_assessment_occurrence(
                session, workflow_id="workflow-1", now=NOW
            )
            await session.commit()
            assert first["expiresAt"] == (NOW + timedelta(seconds=900)).isoformat()
        async with sessions() as session:
            assert (
                await owner.admit_assessment_occurrence(
                    session, workflow_id="workflow-1", now=NOW + timedelta(seconds=1)
                )
                == first
            )
            await save_scope(session, scope, workflow="workflow-2")
            with pytest.raises(owner.AssessmentAuthorizationDenied, match="cadence"):
                await owner.admit_assessment_occurrence(
                    session, workflow_id="workflow-2", now=NOW + timedelta(seconds=60)
                )
            second = await owner.admit_assessment_occurrence(
                session, workflow_id="workflow-2", now=NOW + timedelta(hours=1)
            )
            await session.commit()
            assert second["ordinal"] == 2
            await save_scope(session, scope, workflow="workflow-3")
            with pytest.raises(
                owner.AssessmentAuthorizationDenied, match="occurrence budget"
            ):
                await owner.admit_assessment_occurrence(
                    session, workflow_id="workflow-3", now=NOW + timedelta(hours=2)
                )


@pytest.mark.asyncio
async def test_run_deadline_cannot_be_renewed_and_cancel_stops_retry(tmp_path):
    record = auth()
    scope = authorization_scope(record)
    async with recurring_db(tmp_path) as sessions:
        async with sessions() as session:
            await save_scope(session, scope, auth=record)
            await owner.admit_assessment_occurrence(
                session, workflow_id="workflow-1", now=NOW
            )
            await session.commit()
            with pytest.raises(owner.AssessmentAuthorizationDenied, match="deadline"):
                await owner.admit_assessment_occurrence(
                    session, workflow_id="workflow-1", now=NOW + timedelta(seconds=900)
                )
            source = await session.get(TemporalExecutionCanonicalRecord, "workflow-1")
            source.memo = {"temporalCancel": {"requested": True}}
            await session.commit()
            with pytest.raises(owner.AssessmentAuthorizationDenied, match="canceled"):
                await owner.admit_assessment_occurrence(
                    session, workflow_id="workflow-1", now=NOW + timedelta(seconds=30)
                )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change", ["before", "expired", "revoked", "target", "generation", "grant_expired"]
)
async def test_current_authority_checked_at_each_occurrence(tmp_path, change):
    from sqlalchemy import select
    from api_service.db.models import SettingsOverride

    record = auth()
    if change == "grant_expired":
        record = record.model_copy(
            update={
                "scope": record.scope.model_copy(
                    update={
                        "grants": (
                            __import__(
                                "moonmind.security.workload_profiles",
                                fromlist=["CredentialGrant"],
                            ).CredentialGrant(
                                ref="secret://fixture",
                                purpose="application_auth",
                                account="fixture",
                                profileRefs=["source-scan"],
                                expiresAt=NOW - timedelta(seconds=1),
                                revision=1,
                            ),
                        )
                    }
                )
            }
        )
    scope = authorization_scope(record)
    async with recurring_db(tmp_path) as sessions:
        async with sessions() as session:
            await save_scope(session, scope, auth=record)
            moment = NOW
            if change in {"before", "expired"}:
                moment = (
                    record.valid_from - timedelta(seconds=1)
                    if change == "before"
                    else record.valid_until
                )
            elif change in {"revoked", "target", "generation"}:
                row = await session.scalar(select(SettingsOverride))
                data = record.model_dump(mode="json", by_alias=True)
                if change == "revoked":
                    data["enabled"] = False
                if change == "generation":
                    data["generation"] = "replacement"
                if change == "target":
                    data["scope"]["targets"] = [{"artifactRef": "art_other"}]
                row.value_json = {"authorizations": [data]}
                await session.commit()
            with pytest.raises(owner.AssessmentAuthorizationDenied):
                await owner.admit_assessment_occurrence(
                    session, workflow_id="workflow-1", now=moment
                )


@pytest.mark.asyncio
async def test_renewal_and_changed_schedule_keep_previous_occurrence_consumption(
    tmp_path,
):
    from sqlalchemy import select
    from api_service.db.models import SettingsOverride

    record = auth()
    async with recurring_db(tmp_path) as sessions:
        async with sessions() as session:
            await save_scope(session, authorization_scope(record), auth=record)
            await owner.admit_assessment_occurrence(
                session, workflow_id="workflow-1", now=NOW
            )
            renewed = record.model_copy(
                update={
                    "generation": "new-generation",
                    "valid_until": record.valid_until + timedelta(days=1),
                }
            )
            row = await session.scalar(select(SettingsOverride))
            row.value_json = {
                "authorizations": [renewed.model_dump(mode="json", by_alias=True)]
            }
            await session.commit()
            await save_scope(
                session, authorization_scope(renewed), workflow="replacement-schedule"
            )
            second = await owner.admit_assessment_occurrence(
                session,
                workflow_id="replacement-schedule",
                now=NOW + timedelta(hours=1),
            )
            assert second["ordinal"] == 2
            await session.commit()
            await save_scope(
                session, authorization_scope(renewed), workflow="replacement-schedule-2"
            )
            with pytest.raises(
                owner.AssessmentAuthorizationDenied, match="occurrence budget"
            ):
                await owner.admit_assessment_occurrence(
                    session,
                    workflow_id="replacement-schedule-2",
                    now=NOW + timedelta(hours=2),
                )


@pytest.mark.asyncio
async def test_native_scheduled_activity_uses_current_canonical_authority(
    tmp_path, monkeypatch
):
    from dataclasses import replace
    from temporalio.testing import ActivityEnvironment
    from temporalio.exceptions import ApplicationError
    from api_service.db import base
    from moonmind.workflows.temporal.activities.omnigent_activities import (
        omnigent_admit_scheduled_occurrence_plan_activity,
    )
    from sqlalchemy import select
    from api_service.db.models import SettingsOverride

    # Exercise the registered Activity with its real SDK identity and database;
    # no agent plan or external provider is needed by a native assessment.
    from moonmind.workflows.temporal.artifacts import (
        TemporalArtifactService,
        LocalTemporalArtifactStore,
    )

    monkeypatch.setattr(
        TemporalArtifactService,
        "_build_store_from_settings",
        staticmethod(lambda: LocalTemporalArtifactStore(tmp_path / "artifacts")),
    )
    record = authorization(recurring=TERMS)
    scope = authorization_scope(record)
    async with recurring_db(tmp_path) as sessions:
        monkeypatch.setattr(base, "async_session_maker", sessions)
        async with sessions() as session:
            await save_scope(session, scope, auth=record)
        environment = ActivityEnvironment()
        environment.info = replace(
            environment.info, workflow_id="workflow-1", workflow_run_id="run-1"
        )
        payload = {
            "workflowId": "workflow-1",
            "initialParameters": {
                "assessmentScope": scope.model_dump(mode="json", by_alias=True)
            },
        }
        first = await environment.run(
            omnigent_admit_scheduled_occurrence_plan_activity, payload
        )
        assert first["disposition"] == "recorded"
        async with sessions() as session:
            source = await session.get(AssessmentOccurrenceRecord, "workflow-1")
            reservation = source.authority_json
            assert reservation["ordinal"] == 1
        # A replay through a fresh Activity/session must retain that reservation.
        assert (
            await environment.run(
                omnigent_admit_scheduled_occurrence_plan_activity, payload
            )
            == first
        )
        async with sessions() as session:
            source = await session.get(AssessmentOccurrenceRecord, "workflow-1")
            assert source.authority_json == reservation
            row = await session.scalar(select(SettingsOverride))
            row.value_json = {"authorizations": []}
            await session.commit()
        with pytest.raises(ApplicationError, match="revoked") as denied:
            await environment.run(
                omnigent_admit_scheduled_occurrence_plan_activity, payload
            )
        assert denied.value.non_retryable


def test_expired_or_missing_observations_project_an_actionable_gap():
    value = {
        "expiresAt": (NOW + timedelta(seconds=60)).isoformat(),
        "coverage": "complete",
        "comparisonDisposition": "available",
        "limitations": [],
    }
    assert owner.project_assessment_occurrence(value, now=NOW)["coverage"] == "gap"
    value["observations"] = {
        "art_comparison": {"expiresAt": (NOW + timedelta(seconds=10)).isoformat()}
    }
    assert owner.project_assessment_occurrence(value, now=NOW)["coverage"] == "complete"
    stale = owner.project_assessment_occurrence(value, now=NOW + timedelta(seconds=10))
    assert stale["coverage"] == "gap"
    assert stale["comparisonDisposition"] == "review_required"
    assert value["coverage"] == "complete"  # historical evidence is unchanged
