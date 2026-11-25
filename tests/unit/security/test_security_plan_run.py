"""Running a retained security plan: derivation, review coverage, plan against actual (#5432)."""

from __future__ import annotations

import copy
from datetime import UTC, datetime, timedelta

import pytest

from moonmind.security.assessments import AssessmentRequest, PlanRunInput
from moonmind.security.security_plan import (
    job_authority,
    native_job,
    parse_security_plan,
    plan_identity,
)
from moonmind.security.security_plan_run import (
    JOB_STATES,
    MAX_CONCLUSION_REPORTS,
    REQUIREMENT_STATES,
    RUN_PRESETS,
    ExcludedJob,
    LaunchBinding,
    PlanJobRecord,
    PlanRunError,
    conclusion_reports,
    conclusion_step,
    derive_plan_run,
    job_record_path,
    job_step_overlay,
    parse_run_inputs,
    plan_against_actual,
    render_plan_against_actual,
    review_items,
)
from moonmind.security.workload_profiles import AdmittedAssessmentScope
from tests.unit.security.test_security_plan import ORIGIN, hand_authored, source

PLAN_REF = "art_plan"
CLOUD_ACCOUNT = "123456789012"


def plan_with(*, cloud=False, tls=False):
    raw = hand_authored()
    raw["jobs"].append(
        {
            "id": "image-sbom",
            "kind": "image-assessment",
            "input": {
                "operation": "sbom-generate",
                "target": "archive",
                "inputArtifactRef": "art_openapi",
            },
            "assets": ["shop-app"],
            "sources": source("environment_field", "environments.prod.artifactRefs"),
        }
    )
    if cloud:
        raw["jobs"].append(
            {
                "id": "cloud-posture",
                "kind": "cloud-assessment",
                "input": {
                    "provider": "aws",
                    "account": CLOUD_ACCOUNT,
                    "regions": ["us-east-1"],
                    "egressPolicyRef": "aws-egress",
                    "checks": ["s3_bucket_public_access"],
                },
                "assets": ["shop-app"],
                "sources": source(),
            }
        )
    if tls:
        raw["jobs"].append(
            {
                "id": "tls-check",
                "kind": "tls-assessment",
                "input": {"origin": ORIGIN, "egressPolicyRef": "shop-egress"},
                "assets": [ORIGIN],
                "sources": source(),
            }
        )
    return parse_security_plan(raw)


def test_planned_jobs_launch_as_their_existing_technical_presets():
    plan = plan_with()
    launch, excluded = derive_plan_run(plan, assessment_ref="shop-assessment")
    assert [(job, preset) for job, preset, _ in launch] == [
        ("code-scan", "security-code-supply-chain"),
        ("web-baseline", "security-web-api"),
        ("image-sbom", "security-image-sbom"),
    ]
    # The plan's job input reaches the preset unchanged: nothing is widened.
    # Scoped jobs gain only the authorization's assessment reference.
    planned = {job.id: job.input for job in plan.jobs}
    for job, _, value in launch:
        bound = {"assessmentRef": "shop-assessment"} if job == "web-baseline" else {}
        assert value == {**planned[job], **bound}
    assert excluded == []
    # Without the reference, scoped work is reported as not launched.
    launch, excluded = derive_plan_run(plan)
    assert [job for job, _, _ in launch] == ["code-scan", "image-sbom"]
    assert "assessment reference" in excluded[0].reason


def test_unlaunchable_and_unbound_jobs_are_reported_never_dropped():
    plan = plan_with(cloud=True, tls=True)
    launch, excluded = derive_plan_run(
        plan,
        assessment_ref="shop-assessment",
        excluded={"web-baseline": "Outside the window."},
    )
    assert [job for job, _, _ in launch] == ["code-scan", "image-sbom"]
    reasons = {item.job_id: item.reason for item in excluded}
    assert reasons["web-baseline"] == "Outside the window."
    assert "cloud credentialRef" in reasons["cloud-posture"]
    assert "No launchable job step exists for tls-assessment" in reasons["tls-check"]
    # Launch bindings come from the saved authorization, never from the plan.
    launch, excluded = derive_plan_run(
        plan,
        assessment_ref="shop-assessment",
        bindings={"cloud-posture": LaunchBinding(credential_ref="db://aws-audit")},
    )
    bound = {job: value for job, _, value in launch}["cloud-posture"]
    assert bound["assessmentRef"] == "shop-assessment"
    assert bound["credentialRef"] == "db://aws-audit"
    assert "credentialRef" not in plan.jobs[-2].input
    with pytest.raises(PlanRunError, match="unknown planned job IDs: ghost"):
        derive_plan_run(plan, excluded={"ghost": "x"})


def test_a_registry_image_job_takes_its_bound_target_credential():
    raw = hand_authored()
    raw["jobs"].append(
        {
            "id": "image-registry",
            "kind": "image-assessment",
            "input": {
                "operation": "assess",
                "target": "registry",
                "imageReference": "registry.example.com/shop/app:1.0",
                "egressPolicyRef": "registry-egress",
            },
            "assets": ["shop-app"],
            "sources": source(),
        }
    )
    plan = parse_security_plan(raw)
    planned = {job.id: job.input for job in plan.jobs}["image-registry"]
    # Without a binding the registry is read anonymously, as the preset does.
    launch, _ = derive_plan_run(plan, assessment_ref="shop-assessment")
    assert {job: value for job, _, value in launch}["image-registry"] == planned
    launch, _ = derive_plan_run(
        plan,
        assessment_ref="shop-assessment",
        bindings={"image-registry": LaunchBinding(credential_ref="db://registry")},
    )
    bound = {job: value for job, _, value in launch}["image-registry"]
    # The private registry credential reaches the preset's own credential field;
    # an unscoped image job never gains the assessment reference.
    assert bound == {**planned, "targetCredentialRef": "db://registry"}
    assert "targetCredentialRef" not in planned


def test_run_inputs_are_job_bindings_and_excluded_job_ids():
    bindings, excluded = parse_run_inputs(
        {"cloud-posture": {"assessmentRef": "a-1", "credentialRef": "db://x"}},
        ["web-baseline", {"jobId": "code-scan", "reason": "Not covered."}],
    )
    assert bindings["cloud-posture"].assessment_ref == "a-1"
    assert excluded == {
        "web-baseline": "Removed by the operator before launch.",
        "code-scan": "Not covered.",
    }
    with pytest.raises(PlanRunError, match="launch_bindings"):
        parse_run_inputs({"cloud-posture": {"token": "x"}}, [])


def test_conclusion_is_one_multi_pack_assessment_bound_to_the_plan():
    plan = plan_with()
    identity = plan_identity(plan)
    excluded = [ExcludedJob(job_id="web-baseline", kind="web-assessment", reason="x")]
    step = conclusion_step(
        plan,
        plan_ref=PLAN_REF,
        identity=identity,
        excluded=excluded,
        previous_assessment_ref="art_previous",
    )
    inputs = step["tool"]["inputs"]
    assert step["tool"]["id"] == "security.assess"
    assert [p["packId"] for p in inputs["frameworkPacks"]] == [
        "synthetic-app",
        "synthetic-ops",
    ]
    assert inputs["planRun"] == {
        "planRef": PLAN_REF,
        "planIdentity": identity,
        "excluded": [
            {"jobId": "web-baseline", "kind": "web-assessment", "reason": "x"}
        ],
    }
    # Offline conclusion: no credential, network or job of its own.
    assert inputs["credentialRefs"] == [] and inputs["networkRefs"] == []
    assert inputs["scope"]["assets"] == ["shop-app", ORIGIN]
    request = AssessmentRequest.model_validate(inputs)
    assert request.plan_run == PlanRunInput.model_validate(inputs["planRun"])
    assert request.previous_assessment_ref == "art_previous"


def test_conclusion_refuses_hand_copied_reports_and_needs_the_standards():
    inputs = conclusion_step(
        plan_with(), plan_ref=PLAN_REF, identity="a" * 64, excluded=[]
    )["tool"]["inputs"]
    with pytest.raises(ValueError, match="omit reportRefs"):
        AssessmentRequest.model_validate(
            {**inputs, "tools": ["retained-report"], "reportRefs": ["art_x"]}
        )
    with pytest.raises(ValueError, match="planRun concludes"):
        AssessmentRequest.model_validate(
            {**inputs, "frameworkPacks": [], "reviewRef": "art_r"}
        )


def test_job_steps_declare_their_outcome_record_and_plan_identity():
    overlay = job_step_overlay("code-scan", "f" * 64)
    assert overlay["args"]["securityPlanJobPath"] == job_record_path("code-scan")
    assert overlay["annotations"]["securityPlanJob"] == {
        "jobId": "code-scan",
        "planIdentity": "f" * 64,
    }
    assert (
        "never record completion without retained native evidence"
        in overlay["instructions"]
    )


def test_unscoped_step_kinds_are_exactly_the_unscoped_plan_jobs():
    from moonmind.workflows.temporal.workflows.run import _UNSCOPED_ASSESSMENT_JOB_KINDS

    plan = plan_with()
    unscoped = set()
    for job in plan.jobs:
        model, facts, scope = native_job(job.kind, job.input)
        if not job_authority(job.kind, model, facts, scope).scoped:
            unscoped.add(job.kind)
    assert unscoped == set(_UNSCOPED_ASSESSMENT_JOB_KINDS)
    assert unscoped <= set(RUN_PRESETS)


# --- Review -------------------------------------------------------------------


def scope(**overrides):
    value = {
        "assessmentRef": "shop-assessment",
        "profileRefs": ["web-assessment"],
        "actionClasses": ["web-passive"],
        "targets": [
            {"webOrigin": ORIGIN, "webRoutes": [{"path": "/", "methods": ["GET"]}]}
        ],
        "networkRefs": ["shop-egress"],
        "maxJobs": 4,
        "maxJobSeconds": 1800,
        "maxRequestsPerSecond": 10,
        "evidenceIntent": "Shop assessment",
        **overrides,
    }
    return AdmittedAssessmentScope.model_validate(value)


def web_step(origin=ORIGIN):
    admission = {
        "mode": "baseline",
        "origin": origin,
        "routes": [{"path": "/", "methods": ["GET"]}],
        "egressPolicyRef": "shop-egress",
    }
    return {
        "title": "Collect evidence and assess",
        "annotations": {
            "securityAssessment": {
                "kind": "web-assessment",
                "admission": admission,
                "phase": "collect",
            }
        },
    }


def test_review_names_uncovered_scoped_jobs_and_unlaunched_plan_jobs():
    plan_run = conclusion_step(
        plan_with(),
        plan_ref=PLAN_REF,
        identity="a" * 64,
        excluded=[
            ExcludedJob(
                job_id="cloud-posture",
                kind="cloud-assessment",
                reason="Not launched: needs a credential.",
            )
        ],
    )
    workflow = {
        "workflow": {
            "steps": [web_step(), web_step("https://admin.example.test"), plan_run]
        }
    }
    items = review_items(workflow, scope())
    assert len(items) == 2
    assert items[0].startswith("Collect evidence and assess: not covered (web origin")
    assert "Excluded jobs" in items[0]
    assert items[1].startswith("Planned job cloud-posture (cloud-assessment)")
    assert "reported as not assessed" in items[1]
    # A covered plan run says so; other workflows keep their proposal unchanged.
    covered = {
        "workflow": {
            "steps": [
                web_step(),
                conclusion_step(
                    plan_with(), plan_ref=PLAN_REF, identity="a" * 64, excluded=[]
                ),
            ]
        }
    }
    assert review_items(covered, scope()) == [
        "Every launched planned job is within this authorization."
    ]
    assert review_items({"workflow": {"steps": [web_step()]}}, scope()) is None


def test_review_names_a_missing_action_class_or_credential_grant():
    narrow = scope(actionClasses=["web-active"])
    items = review_items({"workflow": {"steps": [web_step()]}}, narrow)
    assert items and "action class is not admitted" in items[0]


# --- Plan against actual ------------------------------------------------------


def record(job_id, state, reports=(), reason="Recorded outcome.", review=None):
    return PlanJobRecord(
        plan_identity="a" * 64,
        job_id=job_id,
        state=state,
        reason=reason,
        report_refs=list(reports),
        review_ref=review,
    )


def actual(records, reports, excluded=()):
    plan = plan_with()
    return plan_against_actual(
        plan,
        plan_ref=PLAN_REF,
        identity=plan_identity(plan),
        excluded=list(excluded),
        records={item.job_id: item for item in records},
        reports=reports,
    )


def rows(result, key="jobs"):
    field = "jobId" if key == "jobs" else "requirementId"
    return {row[field]: row for row in result[key]}


CLEAN = {"art_code": "readable", "art_web": "readable", "art_image": "readable"}


@pytest.mark.parametrize(
    ("scenario", "records", "reports", "jobs", "app1"),
    [
        (
            "clean",
            [
                record("code-scan", "completed", ["art_code"]),
                record("web-baseline", "completed", ["art_web"]),
                record("image-sbom", "completed", ["art_image"]),
            ],
            CLEAN,
            {"code-scan": "completed", "web-baseline": "completed"},
            "evidence_collected",
        ),
        (
            # Findings or a repair change the standard's conclusion, never the
            # plan state: the evidence materialized either way.
            "vulnerable_or_repaired",
            [
                record("code-scan", "completed", ["art_code"], review="art_review"),
                record("web-baseline", "completed", ["art_web"], review="art_gone"),
            ],
            {**CLEAN, "art_review": "readable", "art_gone": "unavailable"},
            {"code-scan": "completed", "image-sbom": "not_run"},
            "evidence_collected",
        ),
        (
            "partial_collection",
            [
                record("code-scan", "completed", ["art_code"]),
                record("web-baseline", "partial", ["art_web"], reason="2 of 3 routes."),
            ],
            CLEAN,
            {"web-baseline": "partial"},
            "inconclusive",
        ),
        (
            "failed_authentication",
            [
                record("code-scan", "completed", ["art_code"]),
                record(
                    "web-baseline",
                    "failed",
                    ["art_web"],
                    reason="Test identity failed authentication.",
                ),
            ],
            CLEAN,
            {"web-baseline": "failed"},
            "inconclusive",
        ),
        (
            "malformed_output",
            [
                record("code-scan", "completed", ["art_bad"]),
                record("web-baseline", "completed", ["art_web", "art_gone"]),
            ],
            {"art_bad": "malformed", "art_web": "readable", "art_gone": "unavailable"},
            {"code-scan": "failed", "web-baseline": "partial"},
            "inconclusive",
        ),
        (
            "cancelled",
            [
                record("code-scan", "completed", ["art_code"]),
                record("web-baseline", "cancelled", reason="Operator cancelled."),
            ],
            CLEAN,
            {"web-baseline": "not_run"},
            "inconclusive",
        ),
        (
            "blocked_admission",
            [
                record("code-scan", "completed", ["art_code"]),
                record("web-baseline", "blocked_admission", reason="scope denied"),
            ],
            CLEAN,
            {"web-baseline": "blocked_admission"},
            "inconclusive",
        ),
        (
            "not_run",
            [],
            CLEAN,
            {"code-scan": "not_run", "web-baseline": "not_run"},
            "inconclusive",
        ),
    ],
)
def test_scenarios_yield_truthful_plan_against_actual_states(
    scenario, records, reports, jobs, app1
):
    result = actual(records, reports)
    job_rows = rows(result)
    for job_id, state in jobs.items():
        assert job_rows[job_id]["state"] == state, scenario
    requirement_rows = rows(result, "requirements")
    assert requirement_rows["APP-1"]["state"] == app1
    # Every state is from the declared vocabulary and nothing claims "met".
    assert {row["state"] for row in result["jobs"]} <= set(JOB_STATES)
    assert {row["state"] for row in result["requirements"]} <= set(REQUIREMENT_STATES)
    assert all(row["reason"] for row in result["jobs"] + result["requirements"])
    # Only readable native evidence is carried to the conclusion.
    for row in result["jobs"]:
        assert all(reports.get(ref) == "readable" for ref in row["reportRefs"])
    if scenario == "malformed_output":
        assert "art_bad" in job_rows["code-scan"]["reason"]
        assert job_rows["web-baseline"]["reportRefs"] == ["art_web"]
    if scenario == "vulnerable_or_repaired":
        assert job_rows["code-scan"]["reviewRef"] == "art_review"
        # An interpretation the conclusion cannot read is not cited.
        assert "reviewRef" not in job_rows["web-baseline"]


def test_a_completion_claim_without_native_evidence_is_a_failure():
    result = actual([record("code-scan", "completed")], CLEAN)
    row = rows(result)["code-scan"]
    assert row["state"] == "failed"
    assert "without readable native evidence" in row["reason"]


def test_reports_beyond_the_conclusion_limit_are_named_never_dropped():
    plan = plan_with(cloud=True, tls=True)
    records = {
        job.id: record(job.id, "completed", [f"art_{job.id}_{n}" for n in range(8)])
        for job in plan.jobs
    }
    readable = {
        ref: "readable" for item in records.values() for ref in item.report_refs
    }
    assert len(readable) > MAX_CONCLUSION_REPORTS
    first = plan_against_actual(
        plan,
        plan_ref=PLAN_REF,
        identity=plan_identity(plan),
        excluded=[],
        records=records,
        reports=readable,
    )
    evaluated, statuses = conclusion_reports(first, readable)
    assert len(evaluated) == MAX_CONCLUSION_REPORTS
    # Plan job order decides which reports one conclusion evaluates.
    assert (
        evaluated
        == [ref for job in plan.jobs for ref in records[job.id].report_refs][
            :MAX_CONCLUSION_REPORTS
        ]
    )
    overflow = set(readable) - set(evaluated)
    assert {statuses[ref] for ref in overflow} == {"unevaluated"}
    result = plan_against_actual(
        plan,
        plan_ref=PLAN_REF,
        identity=plan_identity(plan),
        excluded=[],
        records=records,
        reports=statuses,
    )
    job_rows = rows(result)
    # Every report the record carries is one the conclusion evaluates.
    assert {ref for row in result["jobs"] for ref in row["reportRefs"]} == set(
        evaluated
    )
    last = plan.jobs[-1].id
    assert job_rows[last]["state"] == "failed"
    assert "not evaluated" in job_rows[last]["reason"]
    assert all(job_rows[job.id]["state"] == "completed" for job in plan.jobs[:4])
    # A requirement whose job was not fully evaluated is never collected.
    for row in result["requirements"]:
        if last in row["jobIds"]:
            assert row["state"] == "inconclusive"


def test_a_report_the_retained_report_owner_rejects_is_not_evidence():
    result = actual(
        [
            record("code-scan", "completed", ["art_code"]),
            record("web-baseline", "completed", ["art_web", "art_invalid"]),
        ],
        {**CLEAN, "art_invalid": "rejected"},
    )
    row = rows(result)["web-baseline"]
    assert row["state"] == "partial" and row["reportRefs"] == ["art_web"]
    assert "art_invalid rejected" in row["reason"]
    alone = rows(
        actual(
            [record("code-scan", "completed", ["art_invalid"])],
            {"art_invalid": "rejected"},
        )
    )["code-scan"]
    assert alone["state"] == "failed" and alone["reportRefs"] == []


def test_work_removed_before_launch_leaves_its_requirements_not_assessed():
    result = actual(
        [record("code-scan", "completed", ["art_code"])],
        CLEAN,
        excluded=[
            ExcludedJob(
                job_id="web-baseline",
                kind="web-assessment",
                reason="The authorization does not cover the origin.",
            )
        ],
    )
    assert rows(result)["web-baseline"]["state"] == "not_run"
    app1 = rows(result, "requirements")["APP-1"]
    assert app1["state"] == "not_assessed"
    assert "does not cover the origin" in app1["reason"]
    # OPS-1 cites only the completed job.
    assert rows(result, "requirements")["OPS-1"]["state"] == "evidence_collected"


def test_external_organisational_and_unassessable_rows_say_who_can_close_them():
    result = actual([], CLEAN)
    requirement_rows = rows(result, "requirements")
    assert requirement_rows["APP-2"]["state"] == "organisational"
    assert requirement_rows["APP-2"]["closableBy"] == "The operator, supplying policy"
    assert requirement_rows["APP-3"]["state"] == "external_only"
    assert requirement_rows["APP-3"]["closableBy"] == "Accredited laboratory"
    assert requirement_rows["APP-4"]["state"] == "not_assessed"
    assert requirement_rows["OPS-2"]["state"] == "not_applicable"
    text = render_plan_against_actual(result, ref="art_run")
    assert "## Plan against actual" in text
    assert "Closable by: Accredited laboratory" in text
    assert "zero findings never mean a requirement is met" in text


def test_identity_of_the_record_is_stable_for_the_same_outcomes():
    first = actual([record("code-scan", "completed", ["art_code"])], CLEAN)
    again = actual([record("code-scan", "completed", ["art_code"])], CLEAN)
    assert first == again
    changed = actual([record("code-scan", "failed", ["art_code"])], CLEAN)
    assert changed != first


def test_a_changed_plan_is_a_new_identity_and_a_new_conclusion_binding():
    raw = hand_authored()
    edited = copy.deepcopy(raw)
    edited["jobs"][1]["input"]["routes"] = [{"path": "/", "methods": ["GET", "HEAD"]}]
    edited["jobs"][1]["authority"]["targets"][0]["routes"] = edited["jobs"][1]["input"][
        "routes"
    ]
    first, second = parse_security_plan(raw), parse_security_plan(edited)
    assert plan_identity(first) != plan_identity(second)
    steps = [
        conclusion_step(p, plan_ref=PLAN_REF, identity=plan_identity(p), excluded=[])
        for p in (first, second)
    ]
    assert (
        steps[0]["tool"]["inputs"]["planRun"] != steps[1]["tool"]["inputs"]["planRun"]
    )


def test_grants_needed_by_bound_cloud_jobs_are_checked_in_the_review():
    def cloud_scope(grants):
        return scope(
            profileRefs=["cloud-posture"],
            actionClasses=["cloud-posture"],
            targets=[
                {
                    "cloudProvider": "aws",
                    "cloudAccount": CLOUD_ACCOUNT,
                    "cloudRegions": ["us-east-1"],
                }
            ],
            networkRefs=["aws-egress"],
            grants=grants,
        )

    admission = {
        "provider": "aws",
        "account": CLOUD_ACCOUNT,
        "regions": ["us-east-1"],
        "egressPolicyRef": "aws-egress",
        "checks": ["s3_bucket_public_access"],
        "assessmentRef": "shop-assessment",
        "credentialRef": "db://aws-audit",
    }
    workflow = {
        "workflow": {
            "steps": [
                {
                    "title": "Cloud posture",
                    "annotations": {
                        "securityAssessment": {
                            "kind": "cloud-assessment",
                            "admission": admission,
                        }
                    },
                }
            ]
        }
    }
    items = review_items(workflow, cloud_scope([]))
    assert items and "no cloud_inventory credential grant" in items[0]
    grant = {
        "ref": "db://aws-audit",
        "purpose": "cloud_inventory",
        "account": CLOUD_ACCOUNT,
        "profileRefs": ["cloud-posture"],
        "expiresAt": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
        "roleArn": f"arn:aws:iam::{CLOUD_ACCOUNT}:role/audit",
    }
    assert review_items(workflow, cloud_scope([grant])) is None


def test_admission_delegates_the_plan_and_previous_conclusion_to_the_run():
    from moonmind.security.organisational_evidence import organisational_evidence_refs

    step = conclusion_step(
        plan_with(),
        plan_ref=PLAN_REF,
        identity="a" * 64,
        excluded=[],
        previous_assessment_ref="art_previous",
    )
    assert {PLAN_REF, "art_previous"} <= set(
        organisational_evidence_refs({"steps": [step]})
    )
    step["tool"]["inputs"]["planRun"]["planRef"] = "https://example.test/plan"
    with pytest.raises(ValueError, match="invalid plan run reference"):
        organisational_evidence_refs({"steps": [step]})
