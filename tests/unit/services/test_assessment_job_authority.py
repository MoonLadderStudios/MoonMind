"""Durable assessment limits, including old admitted workflow continuations."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from api_service.db.models import (
    ContainerJobRecord,
    SettingsOverride,
    TemporalExecutionCanonicalRecord,
)
from api_service.services.container_jobs import (
    ContainerJobScopeDeniedError,
    ContainerJobService,
)
from moonmind.schemas.container_job_models import (
    ContainerJobActivityRequest,
    ContainerJobSubmitRequest,
    OwnerIdentity,
)
from moonmind.security.assessment_authorizations import (
    SETTINGS_KEY,
    AssessmentAuthorization,
    authorization_scope,
)
from moonmind.security.container_job_capabilities import (
    mint_container_job_session_capability,
    verify_container_job_session_capability,
)
from moonmind.security.workload_profiles import AdmittedAssessmentScope
from tests.unit.security.test_workload_profiles import SCOPE, scoped, settings
from tests.unit.services import test_container_jobs as container_job_fixtures

# Pytest discovers these shared fixture objects through module-level aliases.
session_factory = container_job_fixtures.session_factory
temporal = container_job_fixtures.temporal


def authorization(**overrides):
    now = datetime.now(UTC)
    return AssessmentAuthorization.model_validate(
        {
            "ref": "approved",
            "name": "Approved lab",
            "generation": str(uuid4()),
            "authorityKind": "self_hosted",
            "authoritySource": "Operator-owned fixture",
            "validFrom": now - timedelta(hours=1),
            "validUntil": now + timedelta(hours=1),
            "maxTotalJobSeconds": 600,
            "scope": {**SCOPE, "maxCpuMillis": 2000, "maxMemoryMiB": 2048},
            **overrides,
        }
    )


async def save_scope(session, scope, *, workflow="workflow-1", auth=None):
    session.add(
        TemporalExecutionCanonicalRecord(
            workflow_id=workflow,
            run_id="run-1",
            workflow_type="MoonMind.UserWorkflow",
            entry="run",
            parameters={
                "assessmentScope": scope.model_dump(mode="json", by_alias=True)
            },
        )
    )
    if auth is not None:
        session.add(
            SettingsOverride(
                key=SETTINGS_KEY,
                value_json={
                    "authorizations": [auth.model_dump(mode="json", by_alias=True)],
                },
            )
        )
    await session.commit()


def capability(scope, *, owner="step-1", workflow="workflow-1"):
    return verify_container_job_session_capability(
        mint_container_job_session_capability(
            secret="fixture-secret",
            owner=OwnerIdentity(principalId=owner, principalType="service"),
            agent_run_id=owner,
            workflow_id=workflow,
            session_id="session-1",
            runtime_id="fixture",
            assessment_scope=scope,
            lifetime_seconds=3600,
        ),
        secret="fixture-secret",
    )


def request(*, key="job-1", workflow="workflow-1", seconds=300):
    return ContainerJobSubmitRequest(
        idempotencyKey=key,
        source={"source": "workflow", "workflowId": workflow},
        spec=scoped().model_copy(update={"timeout_seconds": seconds}),
    )


async def submit(
    session,
    temporal,
    scope,
    *,
    owner="step-1",
    workflow="workflow-1",
    key="job-1",
    seconds=300,
):
    cap = capability(scope, owner=owner, workflow=workflow)
    return await ContainerJobService(
        session, temporal=temporal, backend_settings=settings(), capability=cap
    ).submit(
        owner=cap.owner, request=request(key=key, workflow=workflow, seconds=seconds)
    )


@pytest.mark.asyncio
async def test_budget_does_not_reset_with_owner_or_rerun(session_factory, temporal):
    auth = authorization()
    scope = authorization_scope(auth)
    async with session_factory() as session:
        await save_scope(session, scope, auth=auth)
        await save_scope(session, scope, workflow="rerun-1")
        first = await submit(session, temporal, scope)
        await submit(
            session, temporal, scope, owner="step-2", workflow="rerun-1", key="job-2"
        )
        with pytest.raises(ContainerJobScopeDeniedError, match="budget"):
            await submit(session, temporal, scope, owner="step-3", key="job-3")
        replay = await submit(session, temporal, scope)
        assert replay.job_id == first.job_id and replay.replayed
        stored = await session.get(ContainerJobRecord, first.job_id)
        assert stored.assessment_scope_json == scope.model_dump(
            mode="json", by_alias=True, exclude_none=True
        )
        assert stored.assessment_deadline_at is not None


@pytest.mark.asyncio
async def test_total_seconds_reservation_not_completed_duration(
    session_factory, temporal
):
    auth = authorization(maxTotalJobSeconds=450)
    scope = authorization_scope(auth)
    async with session_factory() as session:
        await save_scope(session, scope, auth=auth)
        await submit(session, temporal, scope)
        with pytest.raises(ContainerJobScopeDeniedError, match="budget"):
            await submit(session, temporal, scope, owner="step-2", key="job-2")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change", ["expired", "not_started", "revoked", "removed", "generation"]
)
async def test_current_authority_refuses_new_work(session_factory, temporal, change):
    auth = authorization()
    if change == "expired":
        auth = authorization(
            validFrom=datetime.now(UTC) - timedelta(hours=2),
            validUntil=datetime.now(UTC) - timedelta(hours=1),
        )
    if change == "not_started":
        auth = authorization(
            validFrom=datetime.now(UTC) + timedelta(hours=1),
            validUntil=datetime.now(UTC) + timedelta(hours=2),
        )
    scope = authorization_scope(auth)
    async with session_factory() as session:
        await save_scope(session, scope, auth=auth)
        row = await session.scalar(
            select(SettingsOverride).where(SettingsOverride.key == SETTINGS_KEY)
        )
        if change == "revoked":
            row.value_json = {
                "authorizations": [
                    auth.model_copy(update={"enabled": False}).model_dump(
                        mode="json", by_alias=True
                    )
                ]
            }
        elif change == "removed":
            row.value_json = {"authorizations": []}
        elif change == "generation":
            row.value_json = {
                "authorizations": [
                    auth.model_copy(update={"generation": str(uuid4())}).model_dump(
                        mode="json", by_alias=True
                    )
                ]
            }
        await session.commit()
        with pytest.raises(ContainerJobScopeDeniedError):
            await submit(session, temporal, scope)
        temporal.start_container_job.assert_not_awaited()


@pytest.mark.asyncio
async def test_legacy_continuation_requires_canonical_scope(session_factory, temporal):
    scope = AdmittedAssessmentScope.model_validate(SCOPE)
    async with session_factory() as session:
        with pytest.raises(ContainerJobScopeDeniedError, match="canonical"):
            await submit(session, temporal, scope)
        await session.rollback()
        await save_scope(session, scope)
        await save_scope(session, scope, workflow="rerun-1")
        await submit(session, temporal, scope)
        await submit(
            session,
            temporal,
            scope,
            owner="replacement",
            workflow="rerun-1",
            key="job-2",
        )
        with pytest.raises(ContainerJobScopeDeniedError, match="budget"):
            await submit(session, temporal, scope, owner="another", key="job-3")


@pytest.mark.asyncio
async def test_terminal_replay_survives_revocation_and_preserves_evidence(
    session_factory, temporal
):
    auth = authorization()
    scope = authorization_scope(auth)
    async with session_factory() as session:
        await save_scope(session, scope, auth=auth)
        accepted = await submit(session, temporal, scope)
        record = await session.get(ContainerJobRecord, accepted.job_id)
        record.state = "succeeded"
        record.logs_ref = "art_retained_logs"
        row = await session.scalar(
            select(SettingsOverride).where(SettingsOverride.key == SETTINGS_KEY)
        )
        row.value_json = {"authorizations": []}
        await session.commit()
        temporal.reset_mock()
        replay = await submit(session, temporal, scope)
        assert replay.job_id == accepted.job_id and replay.replayed
        assert record.logs_ref == "art_retained_logs"
        temporal.start_container_job.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    ["revoked", "cancel", "parent_cancel", "expired_deadline", "changed_request"],
)
async def test_persisted_worker_authority_refuses_late_actions(
    session_factory, temporal, change
):
    from api_service.services.assessment_authorizations import (
        AssessmentAuthorizationDenied,
        validate_job_assessment_authority,
    )

    auth = authorization()
    scope = authorization_scope(auth)
    async with session_factory() as session:
        await save_scope(session, scope, auth=auth)
        accepted = await submit(session, temporal, scope)
        record = await session.get(ContainerJobRecord, accepted.job_id)
        saved = ContainerJobSubmitRequest.model_validate(record.request_json)
        if change == "revoked":
            row = await session.scalar(
                select(SettingsOverride).where(SettingsOverride.key == SETTINGS_KEY)
            )
            row.value_json = {"authorizations": []}
        elif change == "cancel":
            record.cancel_idempotency_key = "stop"
            record.state = "running"  # A delayed projection cannot undo stop intent.
        elif change == "parent_cancel":
            source = await session.get(TemporalExecutionCanonicalRecord, "workflow-1")
            source.memo = {"temporalCancel": {"state": "pending"}}
        elif change == "expired_deadline":
            record.assessment_deadline_at = datetime.now(UTC) - timedelta(seconds=1)
        elif change == "changed_request":
            saved.spec.timeout_seconds += 1
        await session.commit()
        with pytest.raises(AssessmentAuthorizationDenied):
            await validate_job_assessment_authority(
                session, record, saved, profiles=settings().workload_profiles
            )
        if change != "changed_request":
            # Historical observations/publication do not need live authority.
            await validate_job_assessment_authority(session, record, saved, live=False)


@pytest.mark.asyncio
async def test_old_job_binding_recovers_only_from_canonical_source(
    session_factory, temporal
):
    from api_service.services.assessment_authorizations import (
        AssessmentAuthorizationDenied,
        validate_job_assessment_authority,
    )

    scope = AdmittedAssessmentScope.model_validate(SCOPE)
    async with session_factory() as session:
        await save_scope(session, scope)
        accepted = await submit(session, temporal, scope)
        record = await session.get(ContainerJobRecord, accepted.job_id)
        saved = ContainerJobSubmitRequest.model_validate(record.request_json)
        expected_deadline = record.assessment_deadline_at
        record.assessment_scope_json = None
        record.assessment_deadline_at = None
        await session.commit()
        deadline = await validate_job_assessment_authority(session, record, saved)
        assert deadline.replace(tzinfo=None) == expected_deadline.replace(tzinfo=None)
        assert record.assessment_scope_json["assessmentRef"] == scope.assessment_ref
        record.assessment_scope_json = None
        source = await session.get(TemporalExecutionCanonicalRecord, "workflow-1")
        source.parameters = {}
        await session.commit()
        with pytest.raises(AssessmentAuthorizationDenied, match="canonical"):
            await validate_job_assessment_authority(session, record, saved)


@pytest.mark.asyncio
async def test_recovery_does_not_reset_reserved_deadline(session_factory, temporal):
    scope = AdmittedAssessmentScope.model_validate(SCOPE)
    async with session_factory() as session:
        await save_scope(session, scope)
        accepted = await submit(session, temporal, scope)
        record = await session.get(ContainerJobRecord, accepted.job_id)
        record.state = "failed"
        record.terminal_outcome_json = {"failureClass": "temporal_start"}
        original = datetime.now(UTC) - timedelta(seconds=1)
        record.assessment_deadline_at = original
        await session.commit()
        temporal.reset_mock()
        with pytest.raises(ContainerJobScopeDeniedError, match="original deadline"):
            await submit(session, temporal, scope)
        assert record.assessment_deadline_at.replace(tzinfo=None) == original.replace(
            tzinfo=None
        )
        temporal.start_container_job.assert_not_awaited()


@pytest.mark.asyncio
async def test_worker_callback_binds_actual_activity_and_saved_owner(
    session_factory, temporal, monkeypatch
):
    from types import SimpleNamespace

    from temporalio import activity

    from moonmind.schemas.container_job_models import (
        ContainerJobBackendError,
        container_job_workflow_id,
    )
    from moonmind.workflows.temporal import worker_runtime

    scope = AdmittedAssessmentScope.model_validate(SCOPE)
    async with session_factory() as session:
        await save_scope(session, scope)
        accepted = await submit(session, temporal, scope)
        record = await session.get(ContainerJobRecord, accepted.job_id)
        worker_request = ContainerJobActivityRequest(
            jobId=record.job_id,
            ownershipToken="owned",
            owner=capability(scope).owner,
            request=ContainerJobSubmitRequest.model_validate(record.request_json),
        )
    monkeypatch.setattr(worker_runtime, "get_async_session_context", session_factory)
    monkeypatch.setattr(activity, "in_activity", lambda: True)
    monkeypatch.setattr(
        activity,
        "info",
        lambda: SimpleNamespace(workflow_id=container_job_workflow_id(accepted.job_id)),
    )
    authority = worker_runtime._container_job_assessment_authority(settings())
    async with authority(worker_request) as deadline:
        assert deadline > datetime.now(UTC)
    monkeypatch.setattr(
        activity, "info", lambda: SimpleNamespace(workflow_id="unrelated-workflow")
    )
    with pytest.raises(ContainerJobBackendError, match="actual container job Activity"):
        async with authority(worker_request):
            pytest.fail("unrelated Activity acquired authority")
    monkeypatch.setattr(
        activity,
        "info",
        lambda: SimpleNamespace(workflow_id=container_job_workflow_id(accepted.job_id)),
    )
    worker_request.owner = OwnerIdentity(
        principalId="different", principalType="service"
    )
    with pytest.raises(ContainerJobBackendError, match="matching durable admission"):
        async with authority(worker_request):
            pytest.fail("changed owner acquired authority")


@pytest.mark.asyncio
async def test_evidence_reader_uses_retained_scope_after_revocation_and_parent_loss(
    session_factory, temporal, monkeypatch
):
    from types import SimpleNamespace

    from temporalio import activity

    from moonmind.schemas.container_job_models import container_job_workflow_id
    from moonmind.workflows.temporal import worker_runtime

    auth = authorization()
    scope = authorization_scope(auth)
    async with session_factory() as session:
        await save_scope(session, scope, auth=auth)
        accepted = await submit(session, temporal, scope)
        record = await session.get(ContainerJobRecord, accepted.job_id)
        worker_request = ContainerJobActivityRequest(
            jobId=record.job_id,
            ownershipToken="owned",
            owner=capability(scope).owner,
            request=ContainerJobSubmitRequest.model_validate(record.request_json),
        )
        row = await session.scalar(
            select(SettingsOverride).where(SettingsOverride.key == SETTINGS_KEY)
        )
        row.value_json = {"authorizations": []}
        source = await session.get(TemporalExecutionCanonicalRecord, "workflow-1")
        source.parameters = {}
        source.memo = {"temporalCancel": {"state": "confirmed"}}
        await session.commit()
    monkeypatch.setattr(worker_runtime, "get_async_session_context", session_factory)
    monkeypatch.setattr(activity, "in_activity", lambda: True)
    monkeypatch.setattr(
        activity,
        "info",
        lambda: SimpleNamespace(workflow_id=container_job_workflow_id(accepted.job_id)),
    )
    snapshot = await worker_runtime._container_job_assessment_evidence_reader()(
        worker_request
    )
    assert snapshot["assessmentScope"] == scope.model_dump(
        mode="json", by_alias=True, exclude_none=True
    )
    assert snapshot["originalDeadline"]
    assert (
        snapshot["assessmentScope"]["engagement"]["authoritySource"]
        == auth.authority_source
    )


def accepted_review(auth, *, title="Assess retained fixture"):
    from api_service.services.assessment_engagements import build_assessment_proposal

    return {
        **build_assessment_proposal({"workflow": {"title": title}}, auth),
        "admission": {"via": "loopback", "admittedAt": datetime.now(UTC).isoformat()},
    }


async def read_engagement(session_factory, monkeypatch, worker_request):
    from types import SimpleNamespace

    from temporalio import activity

    from moonmind.schemas.container_job_models import container_job_workflow_id
    from moonmind.workflows.temporal import worker_runtime

    monkeypatch.setattr(worker_runtime, "get_async_session_context", session_factory)
    monkeypatch.setattr(activity, "in_activity", lambda: True)
    monkeypatch.setattr(
        activity,
        "info",
        lambda: SimpleNamespace(
            workflow_id=container_job_workflow_id(worker_request.job_id)
        ),
    )
    return await worker_runtime._container_job_assessment_evidence_reader()(
        worker_request
    )


def evidence_request(record, scope):
    return ContainerJobActivityRequest(
        jobId=record.job_id,
        ownershipToken="owned",
        owner=capability(scope).owner,
        request=ContainerJobSubmitRequest.model_validate(record.request_json),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("source_change", ["edited", "removed"])
async def test_job_retains_original_readable_review_after_source_change_and_revocation(
    session_factory, temporal, monkeypatch, source_change
):
    from copy import deepcopy

    auth = authorization()
    scope = authorization_scope(auth)
    review = accepted_review(auth)
    async with session_factory() as session:
        await save_scope(session, scope, auth=auth)
        source = await session.get(TemporalExecutionCanonicalRecord, "workflow-1")
        source.parameters = {**source.parameters, "assessmentReview": review}
        await session.commit()
        accepted = await submit(session, temporal, scope)
        record = await session.get(ContainerJobRecord, accepted.job_id)
        assert record.assessment_review_json == {
            "state": "retained",
            "review": review,
            "limitations": [],
        }
        changed = deepcopy(review)
        changed["sections"][0]["items"] = ["A later edited plan"]
        changed["limitations"] = ["Later limitations must not replace accepted ones."]
        source.parameters = {**source.parameters, "assessmentReview": changed}
        await session.commit()
        replay = await submit(session, temporal, scope)
        assert replay.replayed
        if source_change == "removed":
            source.parameters = {}
        source.memo = {"temporalCancel": {"state": "confirmed"}}
        row = await session.scalar(
            select(SettingsOverride).where(SettingsOverride.key == SETTINGS_KEY)
        )
        row.value_json = {"authorizations": []}
        record.cancel_idempotency_key = "canceled-after-admission"
        await session.commit()
        worker_request = evidence_request(record, scope)
    snapshot = await read_engagement(session_factory, monkeypatch, worker_request)
    assert snapshot["assessmentReview"]["review"] == review
    assert snapshot["assessmentReview"]["state"] == "retained"
    assert any(s["label"] == "Exclusions" for s in review["sections"])
    assert snapshot["assessmentReview"]["review"]["limitations"]
    assert (
        snapshot["assessmentScope"]["engagement"]["authorizationGeneration"]
        == auth.generation
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy_null", [False, True])
async def test_missing_original_review_is_not_backfilled_from_later_canonical_review(
    session_factory, temporal, monkeypatch, legacy_null
):
    scope = AdmittedAssessmentScope.model_validate(SCOPE)
    async with session_factory() as session:
        await save_scope(session, scope)
        accepted = await submit(session, temporal, scope)
        record = await session.get(ContainerJobRecord, accepted.job_id)
        if legacy_null:
            record.assessment_scope_json = None
            record.assessment_review_json = None
        source = await session.get(TemporalExecutionCanonicalRecord, "workflow-1")
        source.parameters = {
            **source.parameters,
            "assessmentReview": {"sections": [{"label": "Later review", "items": []}]},
        }
        await session.commit()
        worker_request = evidence_request(record, scope)
    snapshot = await read_engagement(session_factory, monkeypatch, worker_request)
    retained = snapshot["assessmentReview"]
    assert retained["state"] == "unavailable"
    assert "review" not in retained
    assert "original" in " ".join(retained["limitations"]).lower()
    assert "Later review" not in str(snapshot)
    assert snapshot["assessmentScope"]["assessmentRef"] == scope.assessment_ref


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid", ["scope", "digest", "generation", "admission", "oversized"]
)
async def test_unmatched_or_unaccepted_readable_review_is_never_claimed_original(
    session_factory, temporal, monkeypatch, invalid
):
    auth = authorization()
    scope = authorization_scope(auth)
    review = accepted_review(auth)
    if invalid == "scope":
        review["scope"]["maxJobs"] += 1
    elif invalid == "digest":
        review["scopeDigest"] = "0" * 64
    elif invalid == "generation":
        review["authorizationGeneration"] = str(uuid4())
    elif invalid == "admission":
        del review["admission"]
    else:
        review["limitations"] = ["x" * 65537]
    async with session_factory() as session:
        await save_scope(session, scope, auth=auth)
        source = await session.get(TemporalExecutionCanonicalRecord, "workflow-1")
        source.parameters = {**source.parameters, "assessmentReview": review}
        await session.commit()
        accepted = await submit(session, temporal, scope)
        record = await session.get(ContainerJobRecord, accepted.job_id)
        worker_request = evidence_request(record, scope)
    snapshot = await read_engagement(session_factory, monkeypatch, worker_request)
    assert snapshot["assessmentReview"]["state"] == "unavailable"
    assert "review" not in snapshot["assessmentReview"]


@pytest.mark.asyncio
async def test_readable_review_retention_projects_known_fields_and_redacts_credentials(
    session_factory, temporal, monkeypatch
):
    import json

    auth = authorization()
    scope = authorization_scope(auth)
    review = accepted_review(auth, title="Fixture token=raw-secret-for-test")
    review["rawCredentials"] = "extra-secret-must-not-be-copied"
    async with session_factory() as session:
        await save_scope(session, scope, auth=auth)
        source = await session.get(TemporalExecutionCanonicalRecord, "workflow-1")
        source.parameters = {**source.parameters, "assessmentReview": review}
        await session.commit()
        accepted = await submit(session, temporal, scope)
        record = await session.get(ContainerJobRecord, accepted.job_id)
        stored = json.dumps(record.assessment_review_json)
        assert "raw-secret-for-test" not in stored
        assert "extra-secret-must-not-be-copied" not in stored
        worker_request = evidence_request(record, scope)
    snapshot = await read_engagement(session_factory, monkeypatch, worker_request)
    retained = snapshot["assessmentReview"]
    assert retained["state"] == "redacted"
    assert retained["limitations"]
    assert retained["review"]["scopeDigest"] == review["scopeDigest"]
    assert "[REDACTED]" in json.dumps(retained)
    assert "rawCredentials" not in retained["review"]


@pytest.mark.asyncio
@pytest.mark.parametrize("budget", ["jobs", "seconds"])
@pytest.mark.parametrize("history", ["retargeted", "missing", "unchanged"])
async def test_legacy_reservations_count_without_reauthorizing_or_rebinding_history(
    session_factory, temporal, budget, history
):
    from api_service.services.assessment_authorizations import (
        AssessmentAuthorizationDenied,
        assessment_reserved_budget,
        validate_job_assessment_authority,
    )

    original = AdmittedAssessmentScope.model_validate(SCOPE)
    target = "app" if history == "unchanged" else "replacement"
    auth = authorization(
        maxTotalJobSeconds=500 if budget == "seconds" else 900,
        scope={
            **SCOPE,
            "targets": [{"workspacePath": target}],
            "maxJobs": 2 if budget == "jobs" else 10,
            "maxCpuMillis": 2000,
            "maxMemoryMiB": 2048,
        },
    )
    current = authorization_scope(auth)
    async with session_factory() as session:
        await save_scope(session, original)
        accepted = await submit(session, temporal, original)
        historical = await session.get(ContainerJobRecord, accepted.job_id)
        old_request = ContainerJobSubmitRequest.model_validate(historical.request_json)
        historical.state = "succeeded"
        historical.logs_ref = "art_completed_history"
        historical.assessment_scope_json = None
        historical.assessment_deadline_at = None
        source = await session.get(TemporalExecutionCanonicalRecord, "workflow-1")
        workflow_id = "workflow-1"
        if history == "missing":
            await session.delete(source)
            await session.commit()
            workflow_id = "reviewed-workflow"
            await save_scope(session, current, workflow=workflow_id)
        else:
            source.parameters = {
                "assessmentScope": current.model_dump(mode="json", by_alias=True)
            }
        row = await session.scalar(
            select(SettingsOverride).where(SettingsOverride.key == SETTINGS_KEY)
        )
        row.value_json = {
            "authorizations": [auth.model_dump(mode="json", by_alias=True)]
        }
        await session.commit()

        cap = capability(current, owner="new-step", workflow=workflow_id)
        jobs = ContainerJobService(
            session, temporal=temporal, backend_settings=settings(), capability=cap
        )

        def fresh(key, seconds):
            return ContainerJobSubmitRequest(
                idempotencyKey=key,
                source={"source": "workflow", "workflowId": workflow_id},
                spec=scoped(
                    [{"name": "source", "workspacePath": f"{target}/routes"}]
                ).model_copy(update={"timeout_seconds": seconds}),
            )

        await jobs.submit(owner=cap.owner, request=fresh("new-target", 200))
        assert await assessment_reserved_budget(session, current) == (2, 500)
        # Accounting cannot claim today's reviewed targets/generation as an
        # old job's original authority, or manufacture a fresh deadline.
        assert historical.assessment_scope_json is None
        assert historical.assessment_deadline_at is None
        assert historical.logs_ref == "art_completed_history"
        with pytest.raises(ContainerJobScopeDeniedError, match="budget"):
            await jobs.submit(owner=cap.owner, request=fresh("over-budget", 100))
        assert await assessment_reserved_budget(session, current) == (2, 500)
        if history != "unchanged":
            with pytest.raises(AssessmentAuthorizationDenied, match="canonical"):
                await validate_job_assessment_authority(
                    session,
                    historical,
                    old_request,
                    profiles=settings().workload_profiles,
                )
        # Observation of retained completed work still needs no live authority.
        await validate_job_assessment_authority(
            session, historical, old_request, live=False
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "counter",
    [
        "max_connections",
        "max_bytes",
        "max_connections_per_second",
        "max_concurrent_connections",
    ],
)
async def test_native_allocations_are_durable_across_failed_jobs_and_exact_replay(
    session_factory, temporal, counter
):
    from dataclasses import replace
    from moonmind.security.egress_policy import ScopedEgressPolicy
    from moonmind.security.workload_profiles import WorkloadProfile
    from tests.unit.security.test_native_network import native
    from tests.unit.security.test_workload_profiles import PROFILE

    allocation = native()
    totals = allocation.limits.model_copy(
        update={
            name: getattr(allocation.limits, name) * (1 if name == counter else 3)
            for name in (
                "max_connections",
                "max_bytes",
                "max_connections_per_second",
                "max_concurrent_connections",
            )
        }
    )
    authority = allocation.model_copy(update={"limits": totals})
    profile = WorkloadProfile.model_validate(
        {
            **PROFILE,
            "networkMode": "bridge",
            "egressPolicyRef": "native",
            "nativeNetwork": authority.model_dump(),
        }
    )
    policy = ScopedEgressPolicy.model_validate(
        {
            "policyRef": "native",
            "allowedWorkflowIds": ["workflow-1"],
            "allowedImageSourceRefs": ["repository-security"],
            "nativeNetwork": authority.model_dump(),
        }
    )
    backend = replace(
        settings(), workload_profiles=(profile,), egress_policies=(policy,)
    )
    scope = AdmittedAssessmentScope.model_validate(
        {
            **SCOPE,
            "maxJobs": 10,
            "networkRefs": ["native"],
            "targets": [*SCOPE["targets"], {"nativeNetwork": authority.model_dump()}],
        }
    )
    async with session_factory() as session:
        await save_scope(session, scope)
        cap = capability(scope)
        jobs = ContainerJobService(
            session, temporal=temporal, backend_settings=backend, capability=cap
        )

        def fresh(key):
            return request(key=key).model_copy(
                update={"spec": scoped(nativeNetwork=allocation.model_dump())}
            )

        first = await jobs.submit(owner=cap.owner, request=fresh("native-first"))
        record = await session.get(ContainerJobRecord, first.job_id)
        record.state = "failed"
        record.terminal_outcome_json = {"failureClass": "execution", "state": "failed"}
        await session.commit()
        replay = await jobs.submit(owner=cap.owner, request=fresh("native-first"))
        assert replay.replayed and replay.job_id == first.job_id
        with pytest.raises(ContainerJobScopeDeniedError, match="native network budget"):
            await jobs.submit(owner=cap.owner, request=fresh("native-next"))


@pytest.mark.asyncio
async def test_recurring_per_run_reservations_and_deadline(session_factory, temporal):
    auth = authorization(recurring={
        "minIntervalSeconds": 60, "maxOccurrences": 2,
        "maxJobsPerOccurrence": 1, "maxJobSecondsPerOccurrence": 300,
        "maxRunSeconds": 60,
    })
    scope = authorization_scope(auth)
    async with session_factory() as session:
        await save_scope(session, scope, auth=auth)
        first = await submit(session, temporal, scope, seconds=300)
        stored = await session.get(ContainerJobRecord, first.job_id)
        assert stored.assessment_deadline_at.replace(tzinfo=UTC) <= datetime.now(UTC)+timedelta(seconds=60)
        with pytest.raises(ContainerJobScopeDeniedError, match="per-occurrence"):
            await submit(session, temporal, scope, owner="step-2", key="job-2", seconds=1)
        replay = await submit(session, temporal, scope)
        assert replay.job_id == first.job_id and replay.replayed
