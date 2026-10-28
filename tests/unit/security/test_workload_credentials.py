"""Scoped workload credential grants, decisions and the STS adapter (#5037)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from moonmind.schemas.container_job_models import (
    ContainerJobActivityRequest,
    ContainerJobSpec,
    ContainerJobSubmitRequest,
    OwnerIdentity,
    WorkloadCredentialAuthorization,
)
from moonmind.security.workload_credentials import (
    READ_ONLY_POLICY_ARNS,
    AwsStsSessionIssuer,
    CredentialIssueError,
    authorize_workload_credentials,
    launch_authorizations,
    session_seconds,
)
from moonmind.security.workload_profiles import (
    AdmittedAssessmentScope,
    ProfileCredential,
    WorkloadProfile,
    resolve_profile_job,
)

NOW = datetime(2026, 10, 27, 12, tzinfo=timezone.utc)
OWNER = OwnerIdentity(principalId="operator", principalType="user")
JOB = "container-job:" + "a" * 32
ROLE = "arn:aws:iam::123456789012:role/audit"

PROFILE = WorkloadProfile.model_validate(
    {
        "profileRef": "cloud-audit",
        "imageSourceRef": "cloud-scanner",
        "modes": [
            {
                "name": "audit",
                "actionClass": "cloud-inventory",
                "argv": ["audit", "{target}"],
                "arguments": [{"name": "target", "type": "input", "required": True}],
            }
        ],
        "credentials": [
            {
                "name": "cloud",
                "purpose": "cloud_inventory",
                "delivery": "file",
                "issuer": "aws-sts",
            },
            {
                "name": "app",
                "purpose": "application_auth",
                "delivery": "env",
                "environmentName": "APP_LOGIN",
            },
        ],
        "maxCpuMillis": 2000,
        "maxMemoryMiB": 2048,
        "maxTimeoutSeconds": 900,
    }
)


def grant(**overrides):
    return {
        "ref": "db://aws-audit",
        "purpose": "cloud_inventory",
        "account": "123456789012",
        "profileRefs": ["cloud-audit"],
        "expiresAt": (NOW + timedelta(hours=2)).isoformat(),
        "roleArn": ROLE,
        "revision": 3,
        **overrides,
    }


APP_GRANT = grant(
    ref="db://app-login",
    purpose="application_auth",
    account="https://app.test",
    roleArn=None,
    revision=None,
)


def scope(*grants):
    return AdmittedAssessmentScope.model_validate(
        {
            "assessmentRef": "assessment-1",
            "profileRefs": ["cloud-audit"],
            "actionClasses": ["cloud-inventory"],
            "targets": [{"workspacePath": "."}],
            "maxJobs": 2,
            "maxJobSeconds": 900,
            "evidenceIntent": "cloud inventory",
            "grants": list(grants) or [grant(), APP_GRANT],
        }
    )


def spec(*credentials, timeout=600):
    raw = ContainerJobSpec.model_validate(
        {
            "workloadProfile": {
                "profileRef": "cloud-audit",
                "mode": "audit",
                "arguments": {"target": "config"},
                "assessmentRef": "assessment-1",
                "credentials": list(credentials),
            },
            "workspaceRef": {"kind": "external_state", "artifactRef": "art_ws"},
            "inputAttachments": [{"name": "config", "workspacePath": "."}],
            "resources": {"cpuMillis": 1000, "memoryMiB": 512},
            "timeoutSeconds": timeout,
        }
    )
    return resolve_profile_job(raw, (PROFILE,))


CLOUD = {"slot": "cloud", "credentialRef": "db://aws-audit", "account": "123456789012"}
APP = {"slot": "app", "credentialRef": "db://app-login", "account": "https://app.test"}


def decide(job_spec, admitted=None):
    return authorize_workload_credentials(
        job_spec, admitted or scope(), (PROFILE,), principal="user:operator", now=NOW
    )


def test_slots_render_env_references_and_admission_binds_each_grant():
    job = spec(CLOUD, APP)
    # Only the reference is rendered; file slots add nothing to environment.
    assert [
        e.model_dump(by_alias=True, exclude_none=True) for e in job.environment
    ] == [{"name": "APP_LOGIN", "secretRef": "db://app-login"}]
    decisions = decide(job)
    assert [(d["slot"], d["purpose"], d["account"]) for d in decisions] == [
        ("cloud", "cloud_inventory", "123456789012"),
        ("app", "application_auth", "https://app.test"),
    ]
    assert decisions[0]["roleArn"] == ROLE and decisions[0]["credentialRevision"] == 3
    assert decisions[0]["accessLevel"] == "read_only"


@pytest.mark.parametrize(
    ("credential", "admitted", "message"),
    [
        (
            {**CLOUD, "credentialRef": "db://other"},
            None,
            "not admitted for this assessment",
        ),
        # A test-account login cannot fill the cloud role slot, and vice versa.
        (
            {**CLOUD, "credentialRef": "db://app-login"},
            None,
            "not admitted for this purpose",
        ),
        (
            {**APP, "credentialRef": "db://aws-audit"},
            None,
            "not admitted for this purpose",
        ),
        ({**CLOUD, "account": "210987654321"}, None, "not admitted for this account"),
        (
            CLOUD,
            scope(grant(profileRefs=["other-profile"])),
            "not admitted for this profile",
        ),
        (
            CLOUD,
            scope(grant(expiresAt=(NOW - timedelta(seconds=1)).isoformat())),
            "has expired",
        ),
        (
            CLOUD,
            scope(grant(expiresAt=(NOW + timedelta(minutes=5)).isoformat())),
            "before the job budget",
        ),
    ],
)
def test_admission_refuses_references_outside_the_grant(credential, admitted, message):
    with pytest.raises(PermissionError, match=message):
        decide(spec(credential), admitted)


def test_unscoped_and_generic_secret_references_are_refused():
    with pytest.raises(PermissionError, match="admitted assessment"):
        authorize_workload_credentials(
            spec(CLOUD), None, (PROFILE,), principal="user:operator", now=NOW
        )
    generic = ContainerJobSpec.model_validate(
        {
            "image": "alpine",
            "workspaceRef": {"kind": "external_state", "artifactRef": "art_ws"},
            "environment": [{"name": "API_TOKEN", "secretRef": "db://api-token"}],
            "resources": {"cpuMillis": 100, "memoryMiB": 64},
        }
    )
    with pytest.raises(PermissionError, match="secretRef"):
        authorize_workload_credentials(
            generic, None, (), principal="user:operator", now=NOW
        )


def test_grant_and_slot_shapes_fail_closed():
    with pytest.raises(ValueError, match="another account"):
        scope(grant(roleArn="arn:aws:iam::210987654321:role/audit"))
    with pytest.raises(ValueError, match="role"):
        scope(grant(purpose="application_auth"))
    with pytest.raises(ValueError):
        # Active or remediation access is not expressible in an audit grant.
        scope(grant(accessLevel="read_write"))
    with pytest.raises(ValueError, match="aws-sts"):
        ProfileCredential(name="cloud", purpose="cloud_inventory", delivery="file")
    with pytest.raises(ValueError, match="files"):
        ProfileCredential(
            name="cloud",
            purpose="cloud_inventory",
            delivery="env",
            environmentName="AWS_KEY",
            issuer="aws-sts",
        )


def activity_request(job_spec, decisions, *, job_id=JOB, owner=OWNER):
    return ContainerJobActivityRequest(
        jobId=job_id,
        owner=owner,
        ownershipToken=f"{job_id}:v1",
        request=ContainerJobSubmitRequest(
            idempotencyKey="k", source={"source": "mcp"}, spec=job_spec
        ),
        workloadCredentials=tuple(
            WorkloadCredentialAuthorization.model_validate({**d, "jobId": JOB})
            for d in decisions
        ),
    )


def test_worker_recheck_refuses_adoption_and_expiry():
    job = spec(CLOUD, APP)
    decisions = decide(job)
    pairs = launch_authorizations(activity_request(job, decisions), (PROFILE,), now=NOW)
    assert [slot.name for slot, _ in pairs] == ["cloud", "app"]

    sibling = "container-job:" + "b" * 32
    for request in (
        activity_request(job, decisions, job_id=sibling),
        activity_request(
            job, decisions, owner=OwnerIdentity(principalId="x", principalType="user")
        ),
        activity_request(job, decisions[:1]),
        activity_request(job, []),
        activity_request(
            job.model_copy(
                update={
                    "workload_profile": job.workload_profile.model_copy(
                        update={"assessment_ref": "assessment-2"}
                    )
                }
            ),
            decisions,
        ),
    ):
        with pytest.raises(PermissionError):
            launch_authorizations(request, (PROFILE,), now=NOW)
    with pytest.raises(TimeoutError):
        launch_authorizations(
            activity_request(job, decisions), (PROFILE,), now=NOW + timedelta(hours=3)
        )


def test_session_length_stays_within_grant_and_budget():
    decision = WorkloadCredentialAuthorization.model_validate(
        {**decide(spec(CLOUD))[0], "jobId": JOB}
    )
    assert (
        session_seconds(decision, now=NOW, budget_end=NOW + timedelta(hours=5)) == 3600
    )
    assert (
        session_seconds(decision, now=NOW, budget_end=NOW + timedelta(minutes=20))
        == 1200
    )
    # STS cannot issue less than 15 minutes; a short remaining budget gets the
    # minimum while the grant still covers it.
    assert (
        session_seconds(decision, now=NOW, budget_end=NOW + timedelta(minutes=1)) == 900
    )
    with pytest.raises(TimeoutError):
        session_seconds(
            decision,
            now=NOW + timedelta(hours=1, minutes=50),
            budget_end=NOW + timedelta(hours=3),
        )


class FakeSts:
    def __init__(self, error=None):
        self.calls = []
        self.error = error

    def assume_role(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return {
            "Credentials": {
                "AccessKeyId": "ASIAFIXTURE",
                "SecretAccessKey": "issued-secret",
                "SessionToken": "issued-session",
                "Expiration": NOW + timedelta(hours=1),
            }
        }


@pytest.mark.asyncio
async def test_sts_adapter_uses_only_the_stored_source_and_read_only_policies():
    client = FakeSts()
    sources = []
    issuer = AwsStsSessionIssuer(lambda source: sources.append(source) or client)
    session = await issuer.issue(
        source={"accessKeyId": "AKIASOURCE", "secretAccessKey": "source-secret"},
        role_arn=ROLE,
        session_name="moonmind-job",
        duration_seconds=900,
        policy_arns=READ_ONLY_POLICY_ARNS,
    )
    assert sources == [
        {"accessKeyId": "AKIASOURCE", "secretAccessKey": "source-secret"}
    ]
    assert client.calls == [
        {
            "RoleArn": ROLE,
            "RoleSessionName": "moonmind-job",
            "DurationSeconds": 900,
            "PolicyArns": [{"arn": arn} for arn in READ_ONLY_POLICY_ARNS],
        }
    ]
    assert b"aws_session_token = issued-session" in session.credentials_file()


@pytest.mark.asyncio
async def test_sts_adapter_keeps_only_the_provider_code():
    class Denied(Exception):
        response = {"Error": {"Code": "AccessDenied", "Message": "echo source-secret"}}

    issuer = AwsStsSessionIssuer(lambda _source: FakeSts(Denied("echo source-secret")))
    with pytest.raises(CredentialIssueError) as denied:
        await issuer.issue(
            source={},
            role_arn=ROLE,
            session_name="s",
            duration_seconds=900,
            policy_arns=READ_ONLY_POLICY_ARNS,
        )
    assert denied.value.code == "AccessDenied" and not denied.value.uncertain
    assert "source-secret" not in str(denied.value)
    assert denied.value.__cause__ is None

    issuer = AwsStsSessionIssuer(
        lambda _source: FakeSts(TimeoutError("read timed out"))
    )
    with pytest.raises(CredentialIssueError) as lost:
        await issuer.issue(
            source={},
            role_arn=ROLE,
            session_name="s",
            duration_seconds=900,
            policy_arns=READ_ONLY_POLICY_ARNS,
        )
    assert lost.value.code == "TimeoutError" and lost.value.uncertain
