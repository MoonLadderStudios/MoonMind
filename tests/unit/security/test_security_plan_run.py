"""Plan against actual for a security plan run (#5432).

Each scenario is what the planned jobs' steps recorded and what their native
summaries actually are. Recorded states can only be lowered by the evidence,
and no scenario turns missing evidence into a satisfied requirement.
"""

import pytest

from moonmind.security.security_plan import SecurityPlanError, plan_identity
from moonmind.security.security_plan_run import (
    ExcludedJob,
    JobOutcome,
    PlanRunInput,
    PlanRunSelection,
    derive_plan_run,
    plan_against_actual,
    render_plan_against_actual,
)
from tests.unit.security.test_security_plan import hand_authored, validate


@pytest.fixture
def plan():
    return validate(hand_authored())


def run(plan, skipped=()):
    return PlanRunInput.model_validate(
        {
            "identity": plan_identity(plan),
            "jobs": [
                {"jobId": "code-scan", "kind": "repository-scan", "stepIds": ["s1"]},
                {"jobId": "web-baseline", "kind": "web-assessment", "stepIds": ["s2"]},
            ],
            "skipped": list(skipped),
        }
    )


def outcome(job_id, status, refs=("art_summary",), **extra):
    return JobOutcome(job_id=job_id, status=status, report_refs=list(refs), **extra)


def states(result):
    return {row["jobId"]: row["state"] for row in result["jobs"]}


def requirement(result, rid):
    return next(row for row in result["requirements"] if row["requirementId"] == rid)


CLEAN = {
    "code-scan": outcome("code-scan", "completed", ["art_code"]),
    "web-baseline": outcome("web-baseline", "completed", ["art_web"]),
}
VALID = {"art_code": True, "art_web": True}


@pytest.mark.parametrize("scenario", ["clean", "vulnerable", "repaired"])
def test_collected_evidence_is_never_itself_a_conclusion(plan, scenario):
    # Clean, vulnerable and repaired runs differ in their native findings,
    # which the standards' evaluation reads. Collection alone concludes nothing.
    result = plan_against_actual(plan, run(plan), CLEAN, VALID)
    assert states(result) == {"code-scan": "completed", "web-baseline": "completed"}
    assert requirement(result, "APP-1")["status"] == "evidence_collected"
    assert requirement(result, "OPS-1")["status"] == "evidence_collected"
    assert not {row["status"] for row in result["requirements"]} & {"met", "satisfied"}


@pytest.mark.parametrize(
    "recorded, checked, expected, reason",
    [
        # Partial collection keeps its own reason.
        (
            outcome("web-baseline", "partial", ["art_web"], reason="Spider stopped"),
            {"art_web": True},
            "partial",
            "Spider stopped",
        ),
        # Failed authentication is recorded as failed, not as a clean scan.
        (
            outcome("web-baseline", "failed", [], reason="Login marker absent"),
            {},
            "failed",
            "Login marker absent",
        ),
        # Malformed output: a claimed completion without a usable summary fails.
        (
            outcome("web-baseline", "completed", ["art_garbage"]),
            {"art_garbage": False},
            "failed",
            "No readable native summary",
        ),
        # One usable and one unusable summary is partial, never complete.
        (
            outcome("web-baseline", "completed", ["art_web", "art_garbage"]),
            {"art_web": True, "art_garbage": False},
            "partial",
            "unreadable or of another kind",
        ),
        # A refused admission or blocked readiness is reported as such.
        (
            outcome("web-baseline", "blocked_readiness", [], reason="feed stale"),
            {},
            "blocked_readiness",
            "feed stale",
        ),
        # Cancelled or crashed before recording: nothing ran as far as evidence shows.
        (None, {}, "not_run", "No outcome was recorded"),
    ],
)
def test_incomplete_jobs_leave_their_requirements_inconclusive(
    plan, recorded, checked, expected, reason
):
    outcomes = {"code-scan": CLEAN["code-scan"]}
    if recorded is not None:
        outcomes["web-baseline"] = recorded
    result = plan_against_actual(
        plan, run(plan), outcomes, {**checked, "art_code": True}
    )
    web = next(row for row in result["jobs"] if row["jobId"] == "web-baseline")
    assert web["state"] == expected and reason in web["reason"]
    assert all(ref != "art_garbage" for ref in web["reportRefs"])
    app1 = requirement(result, "APP-1")
    assert app1["status"] == "inconclusive"
    assert f"job web-baseline {expected}" in app1["reason"]
    # The other requirement's job completed; it is unaffected.
    assert requirement(result, "OPS-1")["status"] == "evidence_collected"


def test_restarted_step_is_judged_by_its_latest_recorded_outcome(plan):
    # prepare_plan_run keeps the newest outcome per step; the earlier failed
    # attempt does not count against a completed restart.
    result = plan_against_actual(plan, run(plan), CLEAN, VALID)
    assert states(result)["web-baseline"] == "completed"


def test_uncovered_job_is_not_assessed_with_its_reason_and_gaps_are_carried(plan):
    reason = "The selected authorization does not admit the shop origin."
    skipped = [
        {
            "jobId": "web-baseline",
            "kind": "web-assessment",
            "state": "blocked_admission",
            "reason": reason,
        }
    ]
    result = plan_against_actual(
        plan, run(plan, skipped), {"code-scan": CLEAN["code-scan"]}, VALID
    )
    web = next(row for row in result["jobs"] if row["jobId"] == "web-baseline")
    assert (web["state"], web["reason"]) == ("blocked_admission", reason)
    assert reason in requirement(result, "APP-1")["reason"]
    # Organisational and external-only gaps say who can close them.
    gaps = {row["requirementId"]: row for row in result["gaps"]}
    assert gaps["APP-3"]["closableBy"] == "Accredited laboratory"
    assert requirement(result, "APP-3")["status"] == "external_only"
    assert requirement(result, "APP-2")["status"] == "organisational_evidence"
    assert gaps["APP-2"]["evidenceRequestIds"] == ["policy"]
    assert requirement(result, "OPS-2")["status"] == "not_applicable"
    assert result["counts"] == {"completed": 1, "blocked_admission": 1}
    text = render_plan_against_actual(result)
    assert "## Plan against actual" in text
    assert "Zero findings never means a requirement is met" in text
    assert "- web-baseline (web-assessment): blocked_admission — " + reason[:-1] in text


def test_derivation_binds_only_named_launch_fields_and_checks_identity(plan):
    selection = PlanRunSelection(plan_ref="art_plan", identity=plan_identity(plan))
    derived = derive_plan_run(plan, selection=selection, assessment_ref="shop")
    assert [job["slug"] for job in derived["jobs"]] == [
        "security-code-supply-chain",
        "security-web-api",
    ]
    assert "assessmentRef" not in derived["jobs"][0]["inputs"]["assessment"]
    assert derived["jobs"][1]["inputs"]["assessment"]["assessmentRef"] == "shop"
    with pytest.raises(SecurityPlanError, match="identity"):
        derive_plan_run(
            plan,
            selection=PlanRunSelection(plan_ref="art_plan", identity="0" * 64),
            assessment_ref="shop",
        )
    excluded = derive_plan_run(
        plan,
        selection=selection,
        excluded=[ExcludedJob(job_id="web-baseline")],
    )
    assert [job["jobId"] for job in excluded["jobs"]] == ["code-scan"]
    assert (
        excluded["conclusion"]["planRun"]["skipped"][0]["state"] == "blocked_admission"
    )
