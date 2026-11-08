"""Typed TLS jobs share durable engagement authority and reservation limits."""

import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from api_service.db.models import ContainerJobRecord, SettingsOverride
from api_service.services import assessment_authorizations as authority
from api_service.services.container_jobs import (
    ContainerJobScopeDeniedError,
    ContainerJobService,
)
from moonmind.config.container_backend_settings import (
    EGRESS_POLICIES_ENV_KEY,
    IMAGE_SOURCES_ENV_KEY,
    resolve_container_backend_settings,
)
from moonmind.schemas.container_job_models import ContainerJobSubmitRequest
from moonmind.security.assessment_authorizations import (
    SETTINGS_KEY,
    authorization_scope,
)
from moonmind.security.tls_assessments import TLSAssessment, build_tls_assessment_job
from moonmind.security.workload_profiles import (
    resolved_job_within_scope,
    selection_within_scope,
)
from tests.unit.security.test_tls_job_integration import scope as tls_scope
from tests.unit.services import test_container_jobs as _job_fixtures
from tests.unit.services.test_assessment_job_authority import (
    authorization,
    capability,
    save_scope,
)

# Pytest discovers the shared fixtures through explicit local aliases.
session_factory = _job_fixtures.session_factory
temporal = _job_fixtures.temporal


def settings():
    return resolve_container_backend_settings(
        {
            IMAGE_SOURCES_ENV_KEY: json.dumps(
                [{"sourceRef": "repository-security", "image": "ghcr.io/x/scan:1"}]
            ),
            EGRESS_POLICIES_ENV_KEY: json.dumps(
                [
                    {
                        "policyRef": "tls",
                        "allowedWorkflowIds": ["workflow-1", "rerun-1"],
                        "allowedImageSourceRefs": ["repository-security"],
                        "destinations": [
                            {
                                "host": "example.com",
                                "port": 443,
                                "protocol": "https",
                                "purpose": "live-target",
                            }
                        ],
                    }
                ]
            ),
        }
    )


def record(*, limits=None, **changes):
    return authorization(
        scope={
            **tls_scope().model_dump(mode="json", by_alias=True, exclude_none=True),
            "maxCpuMillis": 1000,
            "maxMemoryMiB": 1024,
            "maxRequestsPerSecond": 1,
            **(limits or {}),
        },
        **changes,
    )


def request(*, key="tls-1", workflow="workflow-1", rate=1):
    return ContainerJobSubmitRequest(
        idempotencyKey=key,
        source={"source": "workflow", "workflowId": workflow},
        spec=build_tls_assessment_job(
            TLSAssessment(
                origin="https://example.com",
                egress_policy_ref="tls",
                assessment_ref="review-one",
                max_requests_per_second=rate,
            )
        ),
    )


def service(session, temporal, scope, *, owner="step-1", workflow="workflow-1"):
    cap = capability(scope, owner=owner, workflow=workflow)
    return ContainerJobService(
        session, temporal=temporal, backend_settings=settings(), capability=cap
    ), cap.owner


@pytest.mark.asyncio
@pytest.mark.parametrize("signed", [False, True])
async def test_tls_requires_assessment_scope_at_service_boundary(
    session_factory, temporal, signed
):
    async with session_factory() as session:
        cap = capability(None)
        jobs = ContainerJobService(
            session,
            temporal=temporal,
            backend_settings=settings(),
            capability=cap if signed else None,
        )
        with pytest.raises(
            ContainerJobScopeDeniedError, match="admitted assessment scope"
        ):
            await jobs.submit(owner=cap.owner, request=request())
        assert (
            await session.scalar(select(func.count()).select_from(ContainerJobRecord))
            == 0
        )
        temporal.start_container_job.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["expired", "revoked"])
async def test_tls_current_authority_is_required_before_new_admission(
    session_factory, temporal, reason
):
    auth = record()
    if reason == "expired":
        auth = record(
            validFrom=datetime.now(UTC) - timedelta(hours=2),
            validUntil=datetime.now(UTC) - timedelta(hours=1),
        )
    scope = authorization_scope(auth)
    async with session_factory() as session:
        await save_scope(session, scope, auth=auth)
        if reason == "revoked":
            row = await session.scalar(
                select(SettingsOverride).where(SettingsOverride.key == SETTINGS_KEY)
            )
            row.value_json = {"authorizations": []}
            await session.commit()
        jobs, owner = service(session, temporal, scope)
        with pytest.raises(ContainerJobScopeDeniedError, match="validity|revoked"):
            await jobs.submit(owner=owner, request=request())
        assert (
            await session.scalar(select(func.count()).select_from(ContainerJobRecord))
            == 0
        )
        temporal.start_container_job.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "limit,message,rate",
    [
        ({"maxCpuMillis": 500}, "CPU", 1),
        ({"maxMemoryMiB": 512}, "memory", 1),
        ({"maxRequestsPerSecond": 1}, "request rate", 2),
    ],
)
async def test_tls_respects_generic_resource_and_rate_limits(
    session_factory, temporal, limit, message, rate
):
    auth = record(limits=limit)
    scope = authorization_scope(auth)
    async with session_factory() as session:
        await save_scope(session, scope, auth=auth)
        jobs, owner = service(session, temporal, scope)
        with pytest.raises(ContainerJobScopeDeniedError, match=message):
            await jobs.submit(owner=owner, request=request(rate=rate))
        temporal.start_container_job.assert_not_awaited()
        # The production worker uses the same generic rendered-scope owner.
        with pytest.raises(PermissionError, match=message):
            resolved_job_within_scope(request(rate=rate).spec, scope, ())
        if message == "request rate":
            assert not selection_within_scope(request(rate=rate).spec, scope)


@pytest.mark.asyncio
@pytest.mark.parametrize("budget", ["jobs", "seconds"])
async def test_tls_reservations_survive_owner_rerun_and_null_legacy_binding(
    session_factory, temporal, budget
):
    auth = record(
        limits={"maxJobs": 1 if budget == "jobs" else 10},
        maxTotalJobSeconds=250 if budget == "seconds" else 1000,
    )
    scope = authorization_scope(auth)
    async with session_factory() as session:
        await save_scope(session, scope, auth=auth)
        await save_scope(session, scope, workflow="rerun-1")
        jobs, owner = service(session, temporal, scope)
        first = await jobs.submit(owner=owner, request=request())
        stored = await session.get(ContainerJobRecord, first.job_id)
        original_deadline = stored.assessment_deadline_at
        assert stored.assessment_scope_json["assessmentRef"] == scope.assessment_ref
        stored.assessment_scope_json = None
        stored.state = "succeeded"
        await session.commit()
        replay = await jobs.submit(owner=owner, request=request())
        assert replay.replayed and replay.job_id == first.job_id
        successor, owner = service(
            session, temporal, scope, owner="step-2", workflow="rerun-1"
        )
        with pytest.raises(ContainerJobScopeDeniedError, match="budget"):
            await successor.submit(
                owner=owner, request=request(key="tls-2", workflow="rerun-1")
            )
        assert await authority.assessment_reserved_budget(session, scope) == (1, 135)
        assert stored.assessment_scope_json is None
        assert stored.assessment_deadline_at == original_deadline
        assert temporal.start_container_job.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["expired", "revoked"])
async def test_tls_worker_rechecks_live_authority_but_retains_completed_evidence(
    session_factory, temporal, monkeypatch, reason
):
    auth = record()
    scope = authorization_scope(auth)
    async with session_factory() as session:
        await save_scope(session, scope, auth=auth)
        jobs, owner = service(session, temporal, scope)
        accepted = await jobs.submit(owner=owner, request=request())
        stored = await session.get(ContainerJobRecord, accepted.job_id)
        saved = ContainerJobSubmitRequest.model_validate(stored.request_json)
        original_deadline = stored.assessment_deadline_at
        if reason == "revoked":
            row = await session.scalar(
                select(SettingsOverride).where(SettingsOverride.key == SETTINGS_KEY)
            )
            row.value_json = {"authorizations": []}
            await session.commit()
        else:

            class Later(datetime):
                @classmethod
                def now(cls, tz=None):
                    return auth.valid_until + timedelta(seconds=1)

            monkeypatch.setattr(authority, "datetime", Later)
        with pytest.raises(
            authority.AssessmentAuthorizationDenied, match="validity|revoked"
        ):
            await authority.validate_job_assessment_authority(
                session, stored, saved, profiles=()
            )
        assert (
            await authority.validate_job_assessment_authority(
                session, stored, saved, live=False
            )
            == original_deadline
        )


def test_new_tls_authorization_requires_an_explicit_reviewed_request_rate():
    with pytest.raises(ValueError, match="explicit request rate"):
        authorization(
            scope={
                **tls_scope().model_dump(mode="json", by_alias=True, exclude_none=True),
                "maxCpuMillis": 1000,
                "maxMemoryMiB": 1024,
            }
        )


def test_legacy_tls_scope_without_separate_rate_retains_its_existing_limits():
    legacy = tls_scope()
    assert legacy.max_requests_per_second is None
    spec = request(rate=10).spec
    assert selection_within_scope(spec, legacy)
    resolved_job_within_scope(spec, legacy, ())
