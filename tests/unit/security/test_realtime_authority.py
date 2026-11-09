"""Exact real-time authority survives signing, review and launch admission."""

import copy
import json

import pytest
from fastapi import HTTPException

from api_service.services.assessment_engagements import (
    build_assessment_proposal,
    validate_reviewed_proposal,
)
from moonmind.security.container_job_capabilities import (
    verify_container_job_session_capability,
)
from moonmind.security.workload_profiles import (
    TARGET_SCOPE_ENV,
    AdmittedAssessmentScope,
    AssessmentTarget,
    resolve_profile_job,
    resolved_job_within_scope,
)
from tests.unit.security.test_workload_profiles import (
    PROFILE,
    SCOPE,
    mint,
    settings,
    spec,
)
from tests.unit.services.test_assessment_engagements import payload, record


def realtime():
    return {
        "cases": [
            {
                "caseRef": "read",
                "protocol": "websocket",
                "path": "/ws",
                "anonymous": True,
                "messageType": "read",
                "payload": {"type": "read", "enabled": True},
                "effect": "read_only",
            }
        ],
        "maxConnections": 3,
        "maxMessages": 4,
        "maxBytes": 4096,
        "maxMessageBytes": 1024,
        "maxReceiveMessages": 2,
    }


def target(**changes):
    return {
        "webOrigin": "https://app.example.test",
        "webRoutes": [{"path": "/", "methods": ["GET", "POST"]}],
        "realtime": realtime(),
        **changes,
    }


def admission(configured=None, authority=None):
    configured = copy.deepcopy(configured if configured is not None else realtime())
    profile = copy.deepcopy(PROFILE)
    profile.update(networkMode="bridge", egressPolicyRef="target")
    profile["environment"][TARGET_SCOPE_ENV] = json.dumps(
        {
            "web": {
                "mode": "active",
                "activeStrength": "low",
                "origin": target()["webOrigin"],
                "routes": target()["webRoutes"],
                "egressPolicyRef": "target",
            },
            "credentialSlots": {"secret://target/other": "identity"},
            "realtime": configured,
        }
    )
    profiles = settings(profile).workload_profiles
    scope = AdmittedAssessmentScope.model_validate(
        {
            **SCOPE,
            "targets": [*SCOPE["targets"], authority or target()],
            "networkRefs": ["target"],
            "actionClasses": ["source-analysis", "web-active"],
        }
    )
    job = resolve_profile_job(
        spec(selection={"assessmentRef": SCOPE["assessmentRef"]}), profiles
    )
    return job, scope, profiles


def test_absent_authority_retains_legacy_serialization():
    old = AssessmentTarget.model_validate({"workspacePath": "app"})
    assert "realtime" not in old.model_dump()
    assert "realtime" not in json.loads(old.model_dump_json())


def test_named_scope_round_trips_signed_capability_and_accepts_subset():
    configured = realtime()
    authority = target()
    extra = copy.deepcopy(authority["realtime"]["cases"][0])
    extra["caseRef"] = "other"
    authority["realtime"]["cases"].append(extra)
    configured["maxConnections"] = 1
    job, scope, profiles = admission(configured, authority)
    signed = verify_container_job_session_capability(
        mint(assessment_scope=scope), secret="scope-secret"
    )
    assert (
        signed.assessment_scope.targets[-1].realtime.cases[0].payload["enabled"] is True
    )
    resolved_job_within_scope(job, signed.assessment_scope, profiles)


@pytest.mark.parametrize(
    "field,value",
    [
        ("caseRef", "unknown"),
        ("path", "/other"),
        ("effect", "mutation"),
        ("payload", {"type": "read", "enabled": 1}),
        ("payload", {"type": "read", "enabled": False}),
        ("credentialRef", "secret://target/other"),
    ],
)
def test_configured_case_cannot_exceed_exact_authority(field, value):
    configured = realtime()
    configured["cases"][0][field] = value
    if field == "credentialRef":
        configured["cases"][0]["anonymous"] = False
    job, scope, profiles = admission(configured)
    with pytest.raises(PermissionError, match="real-time"):
        resolved_job_within_scope(job, scope, profiles)


@pytest.mark.parametrize(
    "limit",
    [
        "maxConnections",
        "maxMessages",
        "maxBytes",
        "maxMessageBytes",
        "maxReceiveMessages",
    ],
)
def test_each_transport_limit_must_fit_authority(limit):
    configured = realtime()
    configured[limit] += 1
    job, scope, profiles = admission(configured)
    with pytest.raises(PermissionError, match="real-time"):
        resolved_job_within_scope(job, scope, profiles)


def test_legacy_message_names_do_not_authorize_named_cases():
    authority = target(realtime=None, webMessages={"/ws": ["read"]})
    job, scope, profiles = admission(authority=authority)
    with pytest.raises(PermissionError, match="real-time"):
        resolved_job_within_scope(job, scope, profiles)


@pytest.mark.parametrize("change", ["nonweb", "excluded", "no-get", "no-post"])
def test_authority_requires_web_and_admitted_routes(change):
    value = target()
    if change == "nonweb":
        value = {"workspacePath": "app", "realtime": realtime()}
    elif change == "excluded":
        value["webExclusions"] = ["/ws"]
    elif change == "no-get":
        value["webRoutes"][0]["methods"] = ["POST"]
    else:
        value["webRoutes"][0]["methods"] = ["GET"]
        value["realtime"]["cases"][0].update(
            protocol="signalr-json",
            negotiationPath="/negotiate",
            payload={"arguments": []},
        )
    with pytest.raises(ValueError, match="real-time"):
        AssessmentTarget.model_validate(value)


def test_review_discloses_exact_operations_and_rejects_tampering():
    authority = target()
    authority["realtime"]["cases"][0]["payload"]["text"] = (
        "<script>\n[link](https://evil.test)"
    )
    auth = record()
    auth = auth.model_copy(
        update={
            "scope": auth.scope.model_copy(
                update={"targets": (AssessmentTarget.model_validate(authority),)}
            )
        }
    )
    review = build_assessment_proposal(payload(), auth)
    items = next(
        s["items"] for s in review["sections"] if s["label"] == "Real-time operations"
    )
    text = " ".join(items)
    for disclosed in (
        "read",
        "websocket",
        "/ws",
        "anonymous",
        r"read\_only",
        "enabled",
        "maxConnections",
        "maxReceiveMessages",
    ):
        assert disclosed in text
    assert "<script>" not in text and "[link](https://evil.test)" not in text
    assert "\n" not in text
    submitted = {**payload(), "assessmentReview": review}
    assert validate_reviewed_proposal(submitted, auth) == review
    tampered = copy.deepcopy(submitted)
    tampered["assessmentReview"]["scope"]["targets"][0]["realtime"]["cases"][0][
        "payload"
    ]["enabled"] = 1
    with pytest.raises(HTTPException):
        validate_reviewed_proposal(tampered, auth)


@pytest.mark.asyncio
@pytest.mark.parametrize("recurring", [False, True])
async def test_worker_rechecks_realtime_authority_before_launch(monkeypatch, recurring):
    from datetime import UTC, datetime, timedelta
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from api_service.db.models import AssessmentOccurrenceRecord
    from api_service.services import assessment_authorizations as service
    from moonmind.schemas.container_job_models import ContainerJobSubmitRequest

    configured = realtime()
    configured["cases"][0]["payload"]["enabled"] = 1
    job, scope, profiles = admission(configured)
    request = ContainerJobSubmitRequest(
        idempotencyKey="job-1",
        source={"source": "workflow", "workflowId": "workflow-1"},
        spec=job,
    )
    durable = SimpleNamespace(
        request_json=request.model_dump(mode="json", by_alias=True),
        source_json=request.source.model_dump(mode="json", by_alias=True),
        cancel_idempotency_key=None,
        assessment_deadline_at=datetime.now(UTC) + timedelta(minutes=10),
    )
    monkeypatch.setattr(
        service, "persisted_assessment_scope", AsyncMock(return_value=scope)
    )
    monkeypatch.setattr(service, "lock_assessment_admission", AsyncMock())
    monkeypatch.setattr(service, "bind_assessment_job", lambda *args: None)
    # This focused launch recheck stubs scope/lock owners above, but still
    # supplies the durable occurrence lookup used by the real worker session.
    # Both ordinary jobs and bounded recurring jobs must reject altered cases.
    occurrence_deadline = datetime.now(UTC) + timedelta(minutes=5)

    async def get_occurrence(model, workflow_id):
        assert model is AssessmentOccurrenceRecord
        assert workflow_id == request.source.workflow_id
        return (
            SimpleNamespace(
                authority_json={"expiresAt": occurrence_deadline.isoformat()}
            )
            if recurring
            else None
        )

    session = SimpleNamespace(get=AsyncMock(side_effect=get_occurrence))
    with pytest.raises(service.AssessmentAuthorizationDenied, match="real-time"):
        await service.validate_job_assessment_authority(
            session, durable, request, profiles=profiles
        )
    session.get.assert_awaited_once_with(AssessmentOccurrenceRecord, "workflow-1")
    if recurring:
        assert durable.assessment_deadline_at == occurrence_deadline


def test_signalr_negotiation_is_exact_authority_and_disclosed():
    authority = target()
    authority["realtime"]["cases"][0].update(
        protocol="signalr-json",
        negotiationPath="/negotiate",
        payload={"arguments": ["room-one"]},
    )
    configured = copy.deepcopy(authority["realtime"])
    job, scope, profiles = admission(configured, authority)
    resolved_job_within_scope(job, scope, profiles)
    configured["cases"][0]["negotiationPath"] = "/other-negotiate"
    job, scope, profiles = admission(configured, authority)
    with pytest.raises(PermissionError, match="real-time"):
        resolved_job_within_scope(job, scope, profiles)
    auth = record()
    auth = auth.model_copy(
        update={
            "scope": auth.scope.model_copy(
                update={"targets": (AssessmentTarget.model_validate(authority),)}
            )
        }
    )
    review = build_assessment_proposal(payload(), auth)
    text = " ".join(
        next(
            s["items"]
            for s in review["sections"]
            if s["label"] == "Real-time operations"
        )
    )
    assert "negotiation POST /negotiate" in text
    assert "GET https://app" in text
    assert "arguments" in text
    authority["webExclusions"] = ["/negotiate"]
    with pytest.raises(ValueError, match="real-time"):
        AssessmentTarget.model_validate(authority)
