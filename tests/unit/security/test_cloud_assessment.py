"""Read-only Prowler cloud-posture admission, launch and evidence (#5043).

``tests/fixtures/prowler`` holds real Prowler 5.46.0 output: the upstream
``prowlercloud/prowler:stable`` CLI run against moto's AWS stand-in, Prowler's
own test seam. ``basic`` has a public and a locked bucket; ``deny`` raises
UnauthorizedOperation for EC2 in eu-west-1, AccessDenied for
GetBucketVersioning and Throttling for GetAccountPasswordPolicy; ``expired``
answers every call with ExpiredToken; ``invalid-check`` names an unknown
check. Fixtures were trimmed of compliance mappings only.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from moonmind.schemas.container_job_models import ContainerJobSpec
from moonmind.schemas.repository_scanner_models import CloudAssessment
from moonmind.security.assessments import AssessmentScope
from moonmind.security.cloud_assessments import (
    UNADMITTED_OPTIONS,
    CloudAssessmentSummary,
    CloudImage,
    build_cloud_assessment_job,
    cloud_policy_admits,
    parse_log,
    parse_report,
    summarize_cloud_assessment,
)
from moonmind.security.egress_policy import ScopedEgressPolicy
from moonmind.security.workload_credentials import (
    CLOUD_SESSION,
    authorize_workload_credentials,
    launch_authorizations,
)
from moonmind.security.workload_profiles import (
    AdmittedAssessmentScope,
    resolved_job_within_scope,
    selection_within_scope,
)
from moonmind.workflows.skills.security_assessment import _cloud_observation

FIXTURES = Path(__file__).parents[2] / "fixtures" / "prowler"
ACCOUNT = "123456789012"
ROLE = f"arn:aws:iam::{ACCOUNT}:role/audit"
CHECKS = [
    "s3_bucket_level_public_access_block",
    "s3_bucket_object_versioning",
    "iam_password_policy_minimum_length_14",
    "ec2_ebs_default_encryption",
]
IMAGE = CloudImage(
    sourceRef="cloud-posture",
    requestedReference="prowlercloud/prowler:stable",
    resolvedRef="sha256:" + "c" * 64,
)
NOW = datetime(2026, 10, 28, 12, tzinfo=UTC)
WORKSPACE = {"workspaceRef": {"kind": "sandbox", "workspaceId": "cloud"}}


def fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def stand_in_log(name: str) -> bytes:
    """Drop moto's own NotImplementedError gaps; real AWS implements them."""
    return b"\n".join(
        line
        for line in fixture(name).splitlines()
        if b"NotImplementedError" not in line
    )


def admission(**overrides) -> CloudAssessment:
    return CloudAssessment.model_validate(
        {
            "provider": "aws",
            "assessmentRef": "cloud-5043",
            "account": ACCOUNT,
            "credentialRef": "secret://cloud/audit",
            "regions": ["us-east-1", "eu-west-1"],
            "checks": CHECKS,
            "egressPolicyRef": "aws-apis",
            **overrides,
        }
    )


def summary(**overrides):
    values = {
        "image": IMAGE,
        "report": fixture("basic.ocsf.json"),
        "log": stand_in_log("basic-log.txt"),
        "exit_code": 0,
        "terminal_state": "succeeded",
        "native_ref": "art_native",
        "log_ref": "art_log",
        **overrides,
    }
    return summarize_cloud_assessment(admission(), **values)


def records(name: str, *statuses: str) -> bytes:
    rows = json.loads(fixture(name))
    return json.dumps([r for r in rows if r["status_code"] in statuses]).encode()


# -- admission ------------------------------------------------------------


@pytest.mark.parametrize("provider", ["azure", "gcp", "kubernetes"])
def test_other_providers_get_an_explicit_unsupported_result(provider):
    with pytest.raises(
        ValidationError, match=f"cloud provider {provider} is unsupported"
    ):
        admission(provider=provider)


@pytest.mark.parametrize(
    "overrides",
    [
        {"checks": [], "services": []},
        {"services": ["s3"]},
        {"regions": []},
        {"regions": ["us-east-1", "us-east-1"]},
        {"regions": ["us-east-1; rm"]},
        {"checks": ["s3 --fixer"]},
        {"account": "12345"},
        {"credentialRef": "plain-secret-value"},
    ],
)
def test_admission_requires_an_explicit_bounded_selection(overrides):
    with pytest.raises(ValidationError):
        admission(**overrides)


def test_launch_is_read_only_prowler_with_admitted_selection_only():
    job = build_cloud_assessment_job(admission())
    argv = job["command"]
    assert argv[:4] == ["aws", "--region", "us-east-1", "eu-west-1"]
    assert argv[4 : 4 + 1 + len(CHECKS)] == ["--check", *CHECKS]
    assert not set(argv) & UNADMITTED_OPTIONS
    assert ["--output-formats", "json-ocsf"] == argv[
        argv.index("--output-formats") : argv.index("--output-formats") + 2
    ]
    assert "/outputs/cloud-assessment/prowler.log" in argv
    # The upstream image's own entrypoint, no shell and no live workspace.
    assert "entrypoint" not in job and job["inputAttachments"] == []
    assert job["imageSourceRef"] == "cloud-posture"
    services = build_cloud_assessment_job(admission(checks=[], services=["s3"]))
    assert services["command"][4:6] == ["--service", "s3"]

    spec = ContainerJobSpec.model_validate({**job, **WORKSPACE})
    assert spec.cloud_assessment.account == ACCOUNT


@pytest.mark.parametrize(
    "edit",
    [
        lambda job: job["command"].append("--fixer"),
        lambda job: job["command"].extend(["--security-hub"]),
        lambda job: job.update(entrypoint=["sh", "-c"]),
        lambda job: job.update(environment=[{"name": "AWS_PROFILE", "value": "x"}]),
        lambda job: job.update(inputAttachments=None),
        lambda job: job.update(outputs=[{"name": "report", "relativePath": "x.json"}]),
        lambda job: job.update(timeoutSeconds=1801),
        lambda job: job.update(networkMode="none", egressPolicyRef=None),
    ],
)
def test_any_workload_edit_is_refused(edit):
    job = {**build_cloud_assessment_job(admission()), **WORKSPACE}
    ContainerJobSpec.model_validate(job)
    edit(job)
    with pytest.raises(ValidationError):
        ContainerJobSpec.model_validate(job)


def policy(*destinations):
    return ScopedEgressPolicy.model_validate(
        {
            "policyRef": "aws-apis",
            "allowedWorkflowIds": ["assessment"],
            "allowedImageSourceRefs": ["cloud-posture"],
            "destinations": [
                {"host": host, "purpose": purpose, "protocol": "https", "port": 443}
                for host, purpose in destinations
            ],
        }
    )


def test_egress_reaches_only_provider_api_endpoints():
    assert cloud_policy_admits(
        policy(
            ("sts.amazonaws.com", "provider-api"), ("s3.amazonaws.com", "provider-api")
        ),
        admission(),
    )
    assert not cloud_policy_admits(
        policy(("sts.amazonaws.com", "live-target")), admission()
    )
    assert not cloud_policy_admits(
        policy(("api.example.com", "provider-api")), admission()
    )


def scope(**overrides):
    expires = (NOW + timedelta(hours=2)).isoformat()
    return AdmittedAssessmentScope.model_validate(
        {
            "assessmentRef": "cloud-5043",
            "profileRefs": ["cloud-posture"],
            "actionClasses": ["cloud-posture"],
            "targets": [
                {
                    "cloudProvider": "aws",
                    "cloudAccount": ACCOUNT,
                    "cloudRegions": ["us-east-1", "eu-west-1", "us-west-2"],
                }
            ],
            "networkRefs": ["aws-apis"],
            "maxJobs": 5,
            "maxJobSeconds": 3600,
            "evidenceIntent": "read-only posture of the admitted account",
            "grants": [
                {
                    "ref": "secret://cloud/audit",
                    "purpose": "cloud_inventory",
                    "account": ACCOUNT,
                    "profileRefs": ["cloud-posture"],
                    "expiresAt": expires,
                    "roleArn": ROLE,
                    "revision": 2,
                }
            ],
            **overrides,
        }
    )


def spec(**overrides):
    return ContainerJobSpec.model_validate(
        {**build_cloud_assessment_job(admission(**overrides)), **WORKSPACE}
    )


def test_scope_admits_only_its_account_regions_class_network_and_budget():
    admitted = scope()
    resolved_job_within_scope(spec(), admitted, ())
    assert selection_within_scope(spec(), admitted)
    for job, message in (
        (spec(regions=["ap-south-1"]), "region"),
        (spec(account="210987654321"), "account"),
        (spec(egressPolicyRef="other"), "network"),
        (spec(timeoutSeconds=7200), "budget"),
        (spec(assessmentRef="other"), "another assessment"),
    ):
        with pytest.raises(PermissionError, match=message):
            resolved_job_within_scope(job, admitted, ())
        assert not selection_within_scope(job, admitted)
    with pytest.raises(PermissionError, match="action class"):
        resolved_job_within_scope(spec(), scope(actionClasses=["web-passive"]), ())


def test_scope_cloud_targets_name_provider_and_regions_together():
    with pytest.raises(ValidationError):
        scope(targets=[{"cloudAccount": ACCOUNT, "cloudRegions": ["us-east-1"]}])
    with pytest.raises(ValidationError):
        scope(targets=[{"cloudAccount": ACCOUNT, "cloudProvider": "aws"}])


def authorize(job, admitted, now=NOW):
    return authorize_workload_credentials(
        job, admitted, (), principal="user:operator", now=now
    )


def test_only_the_accounts_cloud_inventory_grant_yields_one_session():
    (decision,) = authorize(spec(), scope())
    assert decision["slot"] == "cloud" and decision["roleArn"] == ROLE
    assert decision["profileRef"] == "cloud-posture"
    assert decision["credentialRevision"] == 2

    grant = scope().grants[0].model_dump(by_alias=True)
    for changed, message in (
        ({"ref": "secret://cloud/other"}, "assessment"),
        ({"profileRefs": ["web-assessment"]}, "cloud assessment"),
        ({"expiresAt": (NOW + timedelta(minutes=20)).isoformat()}, "budget"),
        ({"maxSessionSeconds": 900}, "one cloud session"),
    ):
        with pytest.raises(PermissionError, match=message):
            authorize(spec(), scope(grants=[{**grant, **changed}]))
    with pytest.raises(PermissionError, match="admitted assessment"):
        authorize(spec(), None)


def test_worker_delivers_the_session_slot_only_for_the_admitted_decision():
    job = spec()
    (decided,) = authorize(job, scope())
    owner = SimpleNamespace(principal_type="user", principal_id="operator")
    decision = SimpleNamespace(
        job_id="job-1",
        principal="user:operator",
        assessment_ref=decided["assessmentRef"],
        profile_ref=decided["profileRef"],
        slot=decided["slot"],
        purpose=decided["purpose"],
        credential_ref=decided["credentialRef"],
        account=decided["account"],
        expires_at=decided["expiresAt"],
    )
    request = SimpleNamespace(
        job_id="job-1",
        owner=owner,
        request=SimpleNamespace(spec=job),
        workload_credentials=[decision],
    )
    assert launch_authorizations(request, (), now=NOW) == [(CLOUD_SESSION, decision)]
    assert CLOUD_SESSION.issuer == "aws-sts" and CLOUD_SESSION.delivery == "file"
    decision.job_id = "job-2"
    with pytest.raises(PermissionError):
        launch_authorizations(request, (), now=NOW)
    decision.job_id = "job-1"
    with pytest.raises(TimeoutError):
        launch_authorizations(request, (), now=NOW + timedelta(hours=3))


# -- evidence -------------------------------------------------------------


def test_findings_keep_upstream_identifiers_and_declared_coverage():
    result = summary()
    assert (result.outcome, result.status) == ("findings", "complete")
    assert result.diagnostics == [] and result.limitations == []
    failed = {(f.check_id, tuple(f.resource_uids)) for f in result.findings}
    assert failed == {
        (
            "iam_password_policy_minimum_length_14",
            (f"arn:aws:iam:us-east-1:{ACCOUNT}:password-policy",),
        ),
        ("s3_bucket_level_public_access_block", ("arn:aws:s3:::open-bucket",)),
        ("s3_bucket_object_versioning", ("arn:aws:s3:::open-bucket",)),
    }
    native = {r["finding_info"]["uid"] for r in json.loads(fixture("basic.ocsf.json"))}
    assert {f.finding_uid for f in result.findings} <= native
    coverage = result.coverage
    assert coverage.compliant_resources == ["arn:aws:s3:::locked-bucket"]
    assert coverage.requested_regions == ["us-east-1", "eu-west-1"]
    assert coverage.regions_with_results == ["us-east-1"]
    assert coverage.scanned_accounts == [ACCOUNT] == coverage.discovered_accounts
    assert coverage.checks_without_results == ["ec2_ebs_default_encryption"]
    assert coverage.session_policies == [
        "arn:aws:iam::aws:policy/SecurityAudit",
        "arn:aws:iam::aws:policy/job-function/ViewOnlyAccess",
    ]
    assert result.execution.version == "5.46.0"
    assert result.image == IMAGE
    assert [f.asset for f in result.result.findings] == [f"aws:{ACCOUNT}"] * 3
    assert all(f.evidence_refs == ["art_native"] for f in result.result.findings)
    assert result.target_actions == "none"


def test_compliant_resources_with_full_coverage_are_clean():
    result = summary(report=records("basic.ocsf.json", "PASS"))
    assert (result.outcome, result.status) == ("clean", "complete")
    assert result.findings == []
    # A finished run that wrote no report found nothing to report.
    empty = summary(report=None)
    assert (empty.outcome, empty.status) == ("clean", "complete")
    assert empty.coverage.checks_without_results == CHECKS


def test_denial_and_throttling_are_incomplete_never_compliant():
    result = summary(report=fixture("deny.ocsf.json"), log=stand_in_log("deny-log.txt"))
    assert (result.outcome, result.status) == ("findings", "partial")
    kinds = {
        (d.kind, d.code, d.service, d.region, d.operation) for d in result.diagnostics
    }
    assert (
        "access_denied",
        "UnauthorizedOperation",
        "ec2",
        "eu-west-1",
        "DescribeVolumes",
    ) in kinds
    assert ("access_denied", "AccessDenied", "s3", None, "GetBucketVersioning") in kinds
    assert (
        "throttled",
        "Throttling",
        "iam",
        "us-east-1",
        "GetAccountPasswordPolicy",
    ) in kinds
    assert {"access_denied", "throttled", "manual_results"} <= set(result.limitations)
    # Denied versioning reads are MANUAL: unevaluated, never compliant.
    assert result.coverage.manual_checks == ["s3_bucket_object_versioning"]
    assert "arn:aws:s3:::locked-bucket" in result.coverage.unevaluated_resources
    # It passed another check, but one unevaluated result keeps it unproven.
    assert "arn:aws:s3:::locked-bucket" not in result.coverage.compliant_resources
    assert result.coverage.regions_with_errors == ["eu-west-1", "us-east-1"]
    # Provider message text is never retained.
    assert "not authorized" not in result.model_dump_json()

    passing_only = summary(
        report=records("deny.ocsf.json", "PASS"), log=stand_in_log("deny-log.txt")
    )
    assert (passing_only.outcome, passing_only.status) == ("incomplete", "partial")


def test_expired_credentials_fail_the_execution_visibly():
    result = summary(
        report=None, log=fixture("expired-log.txt"), exit_code=1, terminal_state="failed"
    )
    assert (result.outcome, result.status) == ("execution_failed", "partial")
    assert {d.kind for d in result.diagnostics} == {"credentials_rejected"}
    assert result.coverage.scanned_accounts == []
    assert "execution_failed" in result.limitations


def test_invalid_selection_is_reported_as_unsupported_checks():
    result = summary(
        report=None,
        log=fixture("invalid-check-log.txt"),
        exit_code=1,
        terminal_state="failed",
    )
    assert result.outcome == "execution_failed"
    assert [(d.kind, d.check) for d in result.diagnostics] == [
        ("invalid_selection", "no_such_check_xyz")
    ]


@pytest.mark.parametrize(
    "body, limitation",
    [
        (b"not json", "report_malformed"),
        (b'{"findings": []}', "report_malformed"),
        (b"\xff\xfe", "report_malformed"),
        (fixture("basic.ocsf.json")[:-400], "report_truncated"),
    ],
)
def test_malformed_or_truncated_reports_are_incomplete(body, limitation):
    result = summary(report=body)
    assert result.status == "partial" and limitation in result.limitations


def test_truncation_keeps_every_complete_record():
    body = fixture("basic.ocsf.json")
    rows, problem = parse_report(body[: body.rindex(b"{")])
    assert problem == "report_truncated"
    assert len(rows) == len(json.loads(body)) - 1


@pytest.mark.parametrize("state", ["canceled", "timed_out"])
def test_canceled_or_timed_out_runs_keep_usable_findings(state):
    result = summary(exit_code=None, terminal_state=state)
    assert result.execution.state == state and not result.execution.completed
    assert result.outcome == "findings" and result.status == "partial"
    assert f"execution_{state}" in result.limitations
    passing = summary(
        report=records("basic.ocsf.json", "PASS"), exit_code=None, terminal_state=state
    )
    assert passing.outcome == "incomplete"


def test_output_limits_and_unadmitted_accounts_are_incomplete():
    assert (
        summary(report=None, limitations=["report_exceeds_limit"]).outcome
        == "incomplete"
    )
    rows = json.loads(records("basic.ocsf.json", "PASS"))
    rows[0]["cloud"]["account"]["uid"] = "210987654321"
    other = summary(report=json.dumps(rows).encode())
    assert "unadmitted_account_results" in other.limitations
    assert other.outcome == "incomplete"


def test_absent_configuration_answers_are_not_collection_gaps():
    # GetBucketEncryption's NotFound and S3 Control's 404 are answers.
    assert parse_log(stand_in_log("basic-log.txt"), CHECKS) == []
    assert {d.kind for d in parse_log(fixture("basic-log.txt"), CHECKS)} == {
        "collection_error"
    }


def test_interpretation_cannot_promote_incomplete_visibility():
    partial = summary(
        report=records("deny.ocsf.json", "PASS"), log=stand_in_log("deny-log.txt")
    )
    payload = json.loads(partial.model_dump_json(by_alias=True))
    for tampered in (
        {**payload, "outcome": "clean"},
        {**payload, "status": "complete"},
        {
            **payload,
            "outcome": "clean",
            "status": "complete",
            "limitations": [],
            "diagnostics": [],
        },
    ):
        with pytest.raises(ValidationError):
            CloudAssessmentSummary.model_validate(tampered)
    findings = json.loads(summary().model_dump_json(by_alias=True))
    with pytest.raises(ValidationError):
        CloudAssessmentSummary.model_validate({**findings, "outcome": "clean"})
    with pytest.raises(ValidationError):
        CloudAssessmentSummary.model_validate(
            {**findings, "targetActions": "remediate"}
        )


def test_security_review_keeps_the_observed_coverage_status():
    request = SimpleNamespace(scope=AssessmentScope(assets=[f"aws:{ACCOUNT}"]))
    created = NOW

    def status(result):
        observation = _cloud_observation(request, result, "art_summary", created)
        return observation.coverage[0].status

    assert status(summary()) == "complete"
    assert status(summary(log=stand_in_log("deny-log.txt"))) == "partial"
    expired = summary(
        report=None, log=fixture("expired-log.txt"), exit_code=1, terminal_state="failed"
    )
    assert status(expired) == "authentication_failed"
    outside = SimpleNamespace(scope=AssessmentScope(assets=["aws:210987654321"]))
    with pytest.raises(ValueError, match="outside admitted"):
        _cloud_observation(outside, summary(), "art_summary", created)
