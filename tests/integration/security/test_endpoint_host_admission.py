"""A live endpoint job crosses signed MCP and persisted scope admission (#5440)."""

from __future__ import annotations

import copy
import json
import os
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from moonmind.config.container_backend_settings import (
    resolve_container_backend_settings,
)
from moonmind.schemas.repository_scanner_models import EndpointAssessment
from moonmind.security.endpoint_assessments import build_endpoint_assessment_job
from tests.integration.security import test_admitted_assessment_journeys as journeys
from tests.integration.security.assessment_admission import (
    seed_legacy_assessment_admission,
)
from tests.integration.security.test_admitted_assessment_journeys import (
    NO_GRANTS,
    Session,
)
from tests.unit.security.test_endpoint_hosts import (
    OTHER_KEY,
    PROFILE,
    assessment,
    grant,
    host_target,
    scope,
)

environment = journeys.environment

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]


def bundle(**scope_changes):
    policy = {
        "policyRef": "native-lab",
        "allowedWorkflowIds": ["assessment"],
        "allowedImageSourceRefs": ["repository-security"],
        "nativeNetwork": PROFILE.native_network.model_dump(mode="json", by_alias=True),
    }
    settings = resolve_container_backend_settings(
        {
            "MOONMIND_CONTAINER_BACKEND_IMAGE_SOURCES": json.dumps(
                [{"sourceRef": "repository-security", "image": "ghcr.io/x/sec:test"}]
            ),
            "MOONMIND_CONTAINER_BACKEND_WORKLOAD_PROFILES": json.dumps(
                [PROFILE.model_dump(mode="json", by_alias=True)]
            ),
            "MOONMIND_CONTAINER_BACKEND_EGRESS_POLICIES": json.dumps([policy]),
        }
    )
    expires = (datetime.now(UTC) + timedelta(hours=4)).isoformat()
    grants = scope_changes.pop("grants", [grant(expiresAt=expires)])
    targets = scope_changes.pop("targets", ())
    admitted = scope(*targets, grants=grants, **scope_changes)
    return settings, admitted


def endpoint_spec(**changes):
    job = build_endpoint_assessment_job(
        EndpointAssessment.model_validate(assessment(**changes))
    )
    job["workspaceRef"] = {
        "kind": "sandbox",
        "workspaceId": "assess",
        "relativePath": "repo",
    }
    return job


async def submit(session, spec, key):
    from moonmind.container_job_cli import ContainerJobMcpClient

    client = ContainerJobMcpClient(
        endpoint="http://api:8000/mcp/container",
        bearer_token=os.environ["MOONMIND_CONTAINER_JOBS_BEARER_TOKEN"],
    )
    try:
        async with AsyncClient(
            transport=ASGITransport(app=session.app), base_url="http://api:8000"
        ) as http:
            return await http.post(
                "/mcp/container/tools/call",
                headers=client._authorization_headers(),
                json={
                    "tool": "container.submit",
                    "arguments": {
                        "idempotencyKey": key,
                        "source": {
                            "source": "omnigent",
                            "agentRunId": "run",
                            "workflowId": "assessment",
                            "stepId": "assess",
                            "omnigentConversationId": "session",
                        },
                        "spec": spec,
                    },
                },
            )
    finally:
        client.close()


async def admitted_session(environment, monkeypatch, **scope_changes):
    settings, admitted = bundle(**scope_changes)
    session = Session(environment, monkeypatch, scope=admitted)
    await seed_legacy_assessment_admission(environment, admitted)
    temporal = AsyncMock()
    session.service = {
        "temporal": temporal,
        "backend_settings": settings,
        "authorizer": NO_GRANTS,
    }
    return session, temporal


async def test_admitted_host_job_is_persisted_and_its_replay_reserves_nothing_more(
    environment, monkeypatch
):
    # Two jobs' worth of native budget: if the exact replay reserved again,
    # the second job would exceed the authorization.
    session, temporal = await admitted_session(
        environment, monkeypatch, maxJobs=3, native_jobs=2
    )
    first = await submit(session, endpoint_spec(), "endpoint-host-1")
    assert first.status_code == 200, first.text
    job_id = first.json()["result"]["jobId"]
    replay = await submit(session, endpoint_spec(), "endpoint-host-1")
    assert replay.status_code == 200, replay.text
    assert replay.json()["result"]["jobId"] == job_id
    assert replay.json()["result"]["replayed"] is True
    started = temporal.start_container_job.await_args_list[0].args[0]
    spec = started.request.spec
    assert spec.network_mode == "bridge" and spec.egress_policy_ref == "native-lab"
    assert spec.command[:3] == [
        "-I",
        "-m",
        "moonmind.security.endpoint_assessment_runner",
    ]
    assert [
        (d.slot, d.purpose, d.credential_ref, d.credential_revision)
        for d in started.workload_credentials
    ] == [("host", "host_inspection", "db://host-key", 2)]
    persisted = json.dumps(started.model_dump(mode="json", by_alias=True))
    assert "BEGIN OPENSSH" not in persisted
    second = await submit(session, endpoint_spec(), "endpoint-host-2")
    assert second.status_code == 200, second.text
    assert second.json()["result"]["jobId"] != job_id
    third = await submit(session, endpoint_spec(), "endpoint-host-3")
    assert third.status_code == 403, third.text
    assert third.json()["detail"] == {
        "code": "assessment_scope_denied",
        "message": "Container job exceeds the admitted assessment scope: "
        "assessment native network budget is exhausted.",
    }


@pytest.mark.parametrize(
    "change,reason",
    [
        ({"host": {"address": "93.184.216.33"}}, "is not an admitted endpoint"),
        ({"host": {"hostKey": OTHER_KEY}}, "host identity differs"),
        (
            {"profile": "xccdf_org.ssgproject.content_profile_anssi_np_nt28_minimal"},
            "is not admitted for host",
        ),
        ({"skipRuleIds": []}, "omits a rule the authorization excludes"),
        ({"host": {"elevated": True}}, "elevated evaluation is not admitted"),
    ],
)
async def test_signed_submission_outside_the_host_target_is_refused_with_a_reason(
    environment, monkeypatch, change, reason
):
    session, temporal = await admitted_session(environment, monkeypatch)
    response = await submit(session, endpoint_spec(**change), "endpoint-host-refused")
    assert response.status_code == 403, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "container_capability_scope_mismatch"
    assert reason in detail["message"]
    temporal.start_container_job.assert_not_awaited()


@pytest.mark.parametrize(
    "grants",
    [
        [],
        [grant(purpose="application_auth")],
        [
            grant(
                purpose="cloud_inventory",
                account="123456789012",
                roleArn="arn:aws:iam::123456789012:role/audit",
            )
        ],
    ],
)
async def test_a_credential_for_another_purpose_cannot_fill_the_host_slot(
    environment, monkeypatch, grants
):
    expires = (datetime.now(UTC) + timedelta(hours=4)).isoformat()
    session, temporal = await admitted_session(
        environment,
        monkeypatch,
        grants=[{**g, "expiresAt": expires} for g in copy.deepcopy(grants)],
    )
    response = await submit(session, endpoint_spec(), "endpoint-host-credential")
    assert response.status_code in {403, 409}, response.text
    assert "credential reference is not admitted" in response.text
    temporal.start_container_job.assert_not_awaited()


async def test_a_changed_pinned_identity_in_the_authorization_refuses_the_job(
    environment, monkeypatch
):
    session, temporal = await admitted_session(
        environment, monkeypatch, targets=[host_target(hostKey=OTHER_KEY)]
    )
    response = await submit(session, endpoint_spec(), "endpoint-host-rekeyed")
    assert response.status_code == 403
    assert "pinned key" in response.json()["detail"]["message"]
    temporal.start_container_job.assert_not_awaited()
