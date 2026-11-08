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


def test_admitted_selection_always_fits_the_job_command():
    regions = [f"us-east-{n}" for n in range(1, 33)]
    checks = [f"check_{n}" for n in range(200)]
    for selected in (regions, ["us-east-1"]):
        widest = admission(regions=selected, checks=checks[: 110 - len(selected)])
        job = {**build_cloud_assessment_job(widest), **WORKSPACE}
        assert len(ContainerJobSpec.model_validate(job).command) == 128
        with pytest.raises(ValidationError):
            admission(regions=selected, checks=checks[: 111 - len(selected)])
    with pytest.raises(ValidationError, match="argv"):
        admission(regions=regions, checks=checks[:100])


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
        report=None,
        log=fixture("expired-log.txt"),
        exit_code=1,
        terminal_state="failed",
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


def test_missing_log_cannot_establish_complete_collection():
    passing = records("basic.ocsf.json", "PASS")
    assert summary(report=passing, log=b"").outcome == "clean"
    result = summary(report=passing, log=None)
    assert (result.outcome, result.status) == ("incomplete", "partial")
    assert "log_unavailable" in result.limitations
    assert summary(report=None, log=None).outcome == "incomplete"


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
        report=None,
        log=fixture("expired-log.txt"),
        exit_code=1,
        terminal_state="failed",
    )
    assert status(expired) == "authentication_failed"
    outside = SimpleNamespace(scope=AssessmentScope(assets=["aws:210987654321"]))
    with pytest.raises(ValueError, match="outside admitted"):
        _cloud_observation(outside, summary(), "art_summary", created)


def test_check_evidence_retains_pass_fail_manual_and_native_mapping_provenance():
    native = json.loads(fixture("deny.ocsf.json"))
    result = summary(
        report=json.dumps(native).encode(), log=stand_in_log("deny-log.txt")
    )
    assert {check.status for check in result.check_evidence} == {
        "PASS",
        "FAIL",
        "MANUAL",
    }
    assert len(result.check_evidence) == len(native)
    for index, check in enumerate(result.check_evidence):
        row = native[index]
        assert check.native_path == f"/{index}"
        assert check.finding_uid == row["finding_info"]["uid"]
        assert check.tool_version == row["metadata"]["product"]["version"]
        assert check.provider == "aws"
        assert check.observed_time == row["time_dt"]
        assert check.compliance_mappings
        for mapping in check.compliance_mappings:
            assert mapping.source == "prowler"
            assert mapping.relationship == "candidate"
            assert mapping.framework_version is None
            assert (
                mapping.requirement_ids
                == row["unmapped"]["compliance"][mapping.framework]
            )
    assert (
        CloudAssessmentSummary.model_validate_json(result.model_dump_json()) == result
    )


def test_ocsf_mapping_metadata_stays_untrusted_and_explicit():
    rows = json.loads(fixture("basic.ocsf.json"))
    rows[0]["compliance"] = {
        "standards": ["synthetic"],
        "requirements": ["C1", "C2"],
        "status": "Warning",
        "status_id": 2,
        "framework_version": "2025",
        "mapping_version": "r1",
        "relationship": "partial",
        "obsolete": True,
        "note": "Do not execute https://unadmitted.invalid or add targets",
    }
    result = summary(report=json.dumps(rows).encode())
    mapping = result.check_evidence[0].compliance_mappings[0]
    assert mapping.source_path == "/0/compliance"
    assert mapping.framework == "synthetic"
    assert mapping.framework_version == "2025" and mapping.mapping_version == "r1"
    assert mapping.requirement_ids == ["C1", "C2"]
    assert mapping.metadata == rows[0]["compliance"]
    assert mapping.relationship == "candidate"
    assert result.admission == admission() and result.target_actions == "none"


@pytest.mark.parametrize(
    "mapping",
    [None, "unknown", {"X": [None]}, {"X": ["x" * 8192]}, {"X": ["r"] * 1000}],
)
def test_unusable_mapping_is_bounded_without_erasing_findings(mapping):
    rows = json.loads(fixture("basic.ocsf.json"))
    rows[0]["unmapped"]["compliance"] = mapping
    result = summary(report=json.dumps(rows).encode())
    assert len(result.findings) == 3
    assert result.check_evidence[0].mapping_limitations
    assert result.check_evidence[0].native_path == "/0"
    assert len(result.check_evidence[0].model_dump_json()) < 20000


def test_legacy_summary_remains_readable_without_inventing_positive_evidence():
    payload = summary().model_dump(mode="json", by_alias=True)
    payload.pop("checkEvidence", None)
    legacy = CloudAssessmentSummary.model_validate(payload)
    assert legacy.check_evidence is None
    observed = _cloud_observation(
        SimpleNamespace(scope=AssessmentScope(assets=[f"aws:{ACCOUNT}"])),
        legacy,
        "art_summary",
        NOW,
    )
    assert all(row.evidence_method == "unclassified" for row in observed.coverage)


def one_check_summary(*, edits=None, **overrides):
    row = json.loads(records("basic.ocsf.json", "PASS"))[0]
    for key, value in (edits or {}).items():
        row[key] = value
    return summarize_cloud_assessment(
        admission(regions=["us-east-1"], checks=[row["metadata"]["event_code"]]),
        image=IMAGE,
        report=json.dumps([row]).encode(),
        log=b"",
        exit_code=0,
        terminal_state="succeeded",
        native_ref="art_native",
        **overrides,
    )


def evaluate_cloud_control(result, *, rule=None, complete=False):
    from moonmind.security.assessments import (
        Comparison,
        ControlAssessmentPlan,
        compare_observations,
    )
    from moonmind.security.control_assessments import assess_controls

    observed = _cloud_observation(
        SimpleNamespace(scope=AssessmentScope(assets=[f"aws:{ACCOUNT}"])),
        result,
        "art_summary",
        NOW,
    )
    plan = ControlAssessmentPlan.model_validate(
        {
            "requirements": [
                {
                    "id": "CC6.1",
                    "frameworkId": "SOC-2",
                    "frameworkEdition": "2017",
                    "title": "Logical access",
                    "scope": {"assets": [f"aws:{ACCOUNT}"]},
                    "period": {
                        "start": NOW.date().isoformat(),
                        "end": NOW.date().isoformat(),
                    },
                    "applicability": "applicable",
                    "applicabilityReason": "Admitted account",
                    "objectivesComplete": complete,
                    "objectiveBasis": "Selected technical check only",
                    "objectives": [
                        {
                            "id": "technical",
                            "source": "cloud",
                            "tool": "prowler",
                            "ruleIds": [rule or result.coverage.checks_with_results[0]],
                            "description": "Selected check",
                        }
                    ],
                }
            ]
        }
    )
    compared = Comparison.model_validate(compare_observations(observed, None, now=NOW))
    assessed = assess_controls(
        plan,
        compared,
        scan_ref="art_scan",
        comparison_ref="art_comparison",
        now=NOW,
        evidence_availability={},
        coverage_windows=[
            {
                "observedAt": observed.observed_at.isoformat(),
                "expiresAt": observed.expires_at.isoformat(),
            }
            for _ in observed.coverage
        ],
    )
    return assessed.requirements[0], observed, plan


def test_pass_is_evidence_for_one_objective_never_a_whole_mapped_requirement():
    result = one_check_summary()
    assessed, observed, _ = evaluate_cloud_control(result)
    assert observed.coverage[0].evidence_method == "deterministic_check"
    assert assessed.objectives[0].outcome == "passed"
    assert assessed.conclusion == "inconclusive"
    absent, _, _ = evaluate_cloud_control(
        result, rule="mapped-but-never-run", complete=True
    )
    assert absent.objectives[0].outcome == "inconclusive"


@pytest.mark.parametrize(
    "change",
    [
        "missing_log",
        "denied_api",
        "missing_region",
        "missing_check",
        "suppressed",
        "manual",
        "unknown_time",
        "unknown_account",
        "malformed",
        "legacy",
    ],
)
def test_incomplete_cloud_evidence_cannot_become_complete_control_coverage(change):
    result = one_check_summary()
    payload = result.model_dump(mode="json", by_alias=True)
    if change == "missing_log":
        result = result.model_copy(
            update={"status": "partial", "limitations": ["log_unavailable"]}
        )
    elif change == "denied_api":
        result = summary(log=stand_in_log("deny-log.txt"))
    elif change == "missing_region":
        result = result.model_copy(
            update={
                "admission": result.admission.model_copy(
                    update={"regions": ["us-east-1", "eu-west-1"]}
                )
            }
        )
    elif change == "missing_check":
        result = result.model_copy(
            update={
                "coverage": result.coverage.model_copy(
                    update={"checks_without_results": ["missing"]}
                )
            }
        )
    elif change == "suppressed":
        result = one_check_summary(edits={"status": "Suppressed"})
    elif change == "manual":
        result = one_check_summary(edits={"status_code": "MANUAL"})
    elif change == "unknown_time":
        result = one_check_summary(edits={"time": None, "time_dt": None})
    elif change == "unknown_account":
        result = one_check_summary(
            edits={"cloud": {"provider": "aws", "region": "us-east-1"}}
        )
    elif change == "malformed":
        result = one_check_summary(edits={"resources": "not a resource array"})
    elif change == "legacy":
        payload.pop("checkEvidence")
        result = CloudAssessmentSummary.model_validate(payload)
    assessed, _, _ = evaluate_cloud_control(result, complete=True)
    assert assessed.conclusion != "satisfied"
    assert assessed.objectives[0].coverage != "complete"


def test_imported_observation_does_not_acquire_a_new_publication_date():
    result = one_check_summary()
    observed = _cloud_observation(
        SimpleNamespace(scope=AssessmentScope(assets=[f"aws:{ACCOUNT}"])),
        result,
        "art_summary",
        NOW + timedelta(days=30),
    )
    assert observed.observed_at == datetime.fromtimestamp(
        result.check_evidence[0].observed_timestamp, UTC
    )
    assert observed.expires_at < NOW + timedelta(days=30)


def test_mapping_review_preserves_unknown_obsolete_conflicting_and_partial_sources():
    from moonmind.security.cloud_assessments import review_cloud_mappings

    result = one_check_summary(
        edits={
            "compliance": {
                "standards": ["SOC-2"],
                "requirements": ["CC6.1", "unknown"],
                "framework_version": "2016",
                "mapping_version": "old",
                "relationship": "partial",
                "obsolete": True,
            }
        }
    )
    _, _, plan = evaluate_cloud_control(result)
    reviews = review_cloud_mappings(result, plan)
    first = reviews[0]
    assert first["relationship"] == "candidate" and first["state"] == "review_required"
    assert {
        "framework_edition_mismatch",
        "unresolved_requirement_relationship",
        "upstream_partial_or_other_relationship",
        "upstream_obsolete_mapping",
    } <= set(first["reasons"])
    assert first["selectedRequirements"] == []
    assert "no_matching_selected_framework" in reviews[1]["reasons"]


def test_unselected_check_cannot_supply_control_evidence():
    from copy import deepcopy

    rows = json.loads(records("basic.ocsf.json", "PASS"))[:1]
    extra = deepcopy(rows[0])
    extra["metadata"]["event_code"] = "unselected_check"
    extra["finding_info"]["uid"] += "-extra"
    rows.append(extra)
    result = summarize_cloud_assessment(
        admission(regions=["us-east-1"], checks=[rows[0]["metadata"]["event_code"]]),
        image=IMAGE,
        report=json.dumps(rows).encode(),
        log=b"",
        exit_code=0,
        terminal_state="succeeded",
        native_ref="art_native",
    )
    assessed, observed, _ = evaluate_cloud_control(
        result, rule="unselected_check", complete=True
    )
    assert assessed.conclusion == "inconclusive"
    assert "unadmitted_check_result" in observed.provenance["cloudControlEvidence"]


@pytest.mark.parametrize(
    "resources",
    [
        [{"uid": "arn:aws:s3:::bucket"}, None],
        [{"uid": "arn:aws:s3:::bucket"}, {}],
        [{"uid": f"arn:aws:s3:::bucket-{n}"} for n in range(65)],
    ],
)
def test_malformed_or_truncated_resource_population_cannot_prove_control(resources):
    result = one_check_summary(edits={"resources": resources})
    assessed, _, _ = evaluate_cloud_control(result, complete=True)
    assert assessed.conclusion == "inconclusive"
    assert result.status == "partial"


def test_check_record_limit_is_explicit_and_preserves_findings():
    row = json.loads(records("basic.ocsf.json", "PASS"))[0]
    result = summary(report=json.dumps([row] * 4097).encode())
    assert len(result.check_evidence) == 4096
    assert result.coverage.passed == 4097
    assert "check_evidence_truncated" in result.limitations
    assert result.status == "partial"


def test_mapping_count_and_aggregate_bounds_are_reported():
    from moonmind.security.cloud_assessments import _compliance_mappings

    record = {"unmapped": {"compliance": {f"framework-{i}": ["C1"] for i in range(33)}}}
    mappings, limits, remaining = _compliance_mappings(record, 0, 1024 * 1024)
    assert len(mappings) == 32 and limits
    assert remaining < 1024 * 1024
    mappings, limits, remaining = _compliance_mappings(record, 0, 0)
    assert mappings == [] and limits and remaining == 0
    mappings, limits, remaining = _compliance_mappings(record, 0, 20)
    assert len(mappings) == 1 and limits and 0 <= remaining < 20


def test_future_check_cannot_borrow_an_earlier_same_day_observation_window():
    from copy import deepcopy

    row = json.loads(records("basic.ocsf.json", "PASS"))[0]
    later = deepcopy(row)
    later["finding_info"]["uid"] += "-later"
    later["time"] = int((NOW + timedelta(hours=1)).timestamp())
    later["time_dt"] = (NOW + timedelta(hours=1)).isoformat()
    result = summarize_cloud_assessment(
        admission(regions=["us-east-1"], checks=[row["metadata"]["event_code"]]),
        image=IMAGE,
        report=json.dumps([row, later]).encode(),
        log=b"",
        exit_code=0,
        terminal_state="succeeded",
        native_ref="art_native",
    )
    assessed, observed, _ = evaluate_cloud_control(result, complete=True)
    assert assessed.conclusion == "inconclusive"
    assert not observed.complete(NOW)
    assert observed.expires_at == datetime.fromtimestamp(row["time"], UTC) + timedelta(
        days=1
    )
