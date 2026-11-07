"""Requirement conclusions preserve independent scope, evidence and coverage."""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from moonmind.security.assessments import (
    AssessmentRequest,
    Observation,
    compare_observations,
)

NOW = datetime(2026, 11, 7, 12, tzinfo=UTC)


def requirement(**changes):
    return {
        "id": "control-1",
        "frameworkId": "synthetic",
        "frameworkEdition": "2026",
        "title": "The selected population has no instance of the specified defect",
        "scope": {"assets": ["app"]},
        "period": {"start": "2026-11-07", "end": "2026-11-07"},
        "applicability": "applicable",
        "applicabilityReason": "The selected application uses this component.",
        "objectivesComplete": True,
        "objectiveBasis": "The synthetic requirement consists of this one explicit check.",
        "objectives": [
            {
                "id": "check",
                "description": "Check the selected defect",
                "source": "fixture",
                "tool": "fixture-check",
                "ruleIds": ["defect"],
            }
        ],
        **changes,
    }


def request_payload(**changes):
    return {
        "scope": {"assets": ["app"]},
        "tools": ["retained-report"],
        "reportRefs": ["art_source"],
        "credentialRefs": [],
        "networkRefs": [],
        "budget": {"baseSeconds": 60, "maxSeconds": 120, "progressStallSeconds": 60},
        "controlAssessment": {"requirements": [requirement()]},
        **changes,
    }


def observation(**changes):
    return Observation.model_validate(
        {
            "scope": {"assets": ["app"]},
            "observedAt": NOW.isoformat(),
            "expiresAt": (NOW + timedelta(days=1)).isoformat(),
            "coverage": [
                {
                    "source": "fixture",
                    "tool": "fixture-check",
                    "assets": ["app"],
                    "ruleIds": ["defect"],
                    "status": "complete",
                    "evidenceMethod": "deterministic_check",
                    "configRef": "fixture:1",
                }
            ],
            "findings": [],
            "provenance": {"nativeReport": "art_source"},
            **changes,
        }
    )


def test_assessment_admits_explicit_requirement_objectives():
    request = AssessmentRequest.model_validate(request_payload())
    assert request.control_assessment.requirements[0].framework_edition == "2026"


def evaluate(
    *,
    requirements=None,
    observed=None,
    availability=None,
    decisions=(),
    review_ref=None,
    review_status="not_requested",
    now=NOW,
    windows=None,
):
    from moonmind.security.assessments import Comparison, ControlAssessmentPlan
    from moonmind.security.control_assessments import assess_controls

    observed = observed or observation()
    compared = Comparison.model_validate(
        compare_observations(observed, None, decisions=decisions, now=now)
    )
    return assess_controls(
        ControlAssessmentPlan(requirements=requirements or [requirement()]),
        compared,
        scan_ref="art_scan",
        comparison_ref="art_comparison",
        now=now,
        coverage_windows=(
            windows
            if windows is not None
            else [
                {
                    "observedAt": observed.observed_at.isoformat(),
                    "expiresAt": observed.expires_at.isoformat(),
                }
                for _ in observed.coverage
            ]
        ),
        evidence_availability=availability or {},
        review_ref=review_ref,
        review_status=review_status,
    )


def supporting(**changes):
    return {
        "artifactRef": "art_statement",
        "method": "management_assertion",
        "position": "supports",
        "objectiveIds": ["check"],
        "scope": {"assets": ["app"]},
        "period": {"start": "2026-11-07", "end": "2026-11-07"},
        "author": "Fixture owner",
        "reason": "Attributed supporting statement",
        **changes,
    }


def failed_observation():
    return observation(
        findings=[
            {
                "source": "fixture",
                "tool": "fixture-check",
                "ruleId": "defect",
                "asset": "app",
                "nativeId": "one",
                "evidenceRefs": ["art_source"],
            }
        ]
    )


def test_complete_explicit_check_is_satisfied_and_round_trips():
    from moonmind.security.assessments import ControlAssessment

    report = evaluate()
    row = report.requirements[0]
    assert (row.conclusion, row.coverage, row.evidence_sufficiency) == (
        "satisfied",
        "complete",
        "sufficient",
    )
    assert row.methods == ["deterministic_check"]
    assert row.objectives[0].checked_cells == 1
    assert row.evidence_refs == ["art_scan", "art_comparison"]
    assert ControlAssessment.model_validate_json(report.model_dump_json()) == report
    assert report.assurance == "independent_assessment_unverified"


@pytest.mark.parametrize(
    "change",
    [
        {"objectivesComplete": False},
        {"objectives": []},
        {
            "objectives": [
                requirement()["objectives"][0],
                {
                    "id": "additional",
                    "description": "Another required check",
                    "source": "fixture",
                    "tool": "fixture-check",
                    "ruleIds": ["unrun"],
                },
            ]
        },
        {"period": {"start": "2026-01-01", "end": "2026-11-07"}},
        {"period": {"start": "2026-11-06", "end": "2026-11-06"}},
    ],
)
def test_zero_findings_and_partial_mappings_do_not_satisfy_whole_requirement(change):
    row = evaluate(requirements=[requirement(**change)]).requirements[0]
    assert row.conclusion == "inconclusive"
    assert row.coverage == "partial"
    assert row.next_actions


def test_partial_population_remains_partial():
    obs = observation(scope={"assets": ["app", "unexamined"]})
    row = evaluate(
        observed=obs,
        requirements=[requirement(scope={"assets": ["app", "unexamined"]})],
    ).requirements[0]
    assert row.coverage == "partial" and row.conclusion == "inconclusive"
    assert (
        row.objectives[0].checked_cells == 1 and row.objectives[0].required_cells == 2
    )


@pytest.mark.parametrize("status", ["authentication_failed", "failed", "unsupported"])
def test_inaccessible_or_unsupported_check_is_blocked(status):
    obs = observation()
    obs.coverage[0].status = status
    row = evaluate(observed=obs).requirements[0]
    assert row.coverage == "blocked" and row.conclusion == "inconclusive"


def test_all_configured_rules_does_not_prove_an_arbitrary_selected_rule_ran():
    obs = observation()
    obs.coverage[0].all_rules = True
    obs.coverage[0].rule_ids = []
    assert evaluate(observed=obs).requirements[0].conclusion == "inconclusive"


@pytest.mark.parametrize(
    "expires,observed",
    [
        (NOW, NOW - timedelta(hours=1)),
        (NOW + timedelta(days=2), NOW + timedelta(days=1)),
    ],
)
def test_stale_and_future_evidence_are_not_sufficient(expires, observed):
    row = evaluate(
        observed=observation(
            observedAt=observed.isoformat(), expiresAt=expires.isoformat()
        )
    ).requirements[0]
    assert row.evidence_sufficiency == "stale" and row.conclusion == "inconclusive"


def test_legacy_unknown_source_window_cannot_borrow_newer_scan_time():
    assert evaluate(windows=[None]).requirements[0].conclusion == "inconclusive"
    assert evaluate(windows=[]).requirements[0].conclusion == "inconclusive"


def test_same_evidence_has_independent_scope_and_period_conclusions():
    one = requirement()
    two = requirement(
        id="control-2", period={"start": "2026-01-01", "end": "2026-11-07"}
    )
    rows = evaluate(requirements=[one, two]).requirements
    assert [row.conclusion for row in rows] == ["satisfied", "inconclusive"]
    assert rows[0].evidence_refs == rows[1].evidence_refs


@pytest.mark.parametrize("applicability", ["not_applicable", "undetermined"])
def test_applicability_is_separate_from_conclusion(applicability):
    row = evaluate(
        observed=failed_observation(),
        requirements=[
            requirement(
                applicability=applicability,
                applicabilityReason="The documented scope requires separate consideration",
            )
        ],
    ).requirements[0]
    assert row.conclusion == "not_assessed"
    assert row.requirement.applicability == applicability
    assert row.objectives[0].outcome == "not_assessed"


@pytest.mark.parametrize("review_due", [False, True])
def test_accepted_risk_does_not_turn_failure_green_and_preserves_review_semantics(
    review_due,
):
    from moonmind.security.assessments import TriageDecision

    obs = failed_observation()
    decision = TriageDecision(
        findingId=obs.findings[0].finding_id,
        disposition="accepted_risk",
        reason="Temporary documented exception",
        evidenceRefs=["art_exception"],
        author="Fixture risk owner",
        source="fixture",
        scope=obs.scope,
        reviewAt=NOW + timedelta(days=-1 if review_due else 1),
        reviewConditions=["Revisit after repair"],
    )
    row = evaluate(observed=obs, decisions=[decision]).requirements[0]
    assert row.conclusion == "not_satisfied"
    assert row.exceptions[0].status == ("review_required" if review_due else "active")
    assert row.exceptions[0].decision.author == "Fixture risk owner"
    assert row.exceptions[0].decision.reason == decision.reason


@pytest.mark.parametrize("method", ["management_assertion", "agent_interpretation"])
def test_supporting_claim_alone_cannot_replace_unrun_native_check(method):
    row = evaluate(
        requirements=[
            requirement(
                objectivesComplete=False, supportingEvidence=[supporting(method=method)]
            )
        ],
        availability={"art_statement": None},
    ).requirements[0]
    assert row.conclusion == "inconclusive" and method in row.methods


def test_contradictory_attributed_evidence_remains_inconclusive():
    row = evaluate(
        requirements=[
            requirement(supportingEvidence=[supporting(position="contradicts")])
        ],
        availability={"art_statement": None},
    ).requirements[0]
    assert (
        row.conclusion == "inconclusive" and row.evidence_sufficiency == "contradictory"
    )
    assert "art_statement" in row.evidence_refs


@pytest.mark.parametrize("error", ["missing", "denied", "invalid"])
def test_unavailable_required_evidence_cannot_be_satisfied(error):
    row = evaluate(
        requirements=[requirement(supportingEvidence=[supporting()])],
        availability={"art_statement": error},
    ).requirements[0]
    assert row.coverage == "blocked" and row.conclusion == "inconclusive"
    assert row.unavailable_evidence_refs == ["art_statement"]
    assert "art_statement" not in row.evidence_refs


def test_partial_supporting_evidence_is_not_sufficient():
    row = evaluate(
        requirements=[
            requirement(supportingEvidence=[supporting(scope={"assets": ["other"]})])
        ],
        availability={"art_statement": None},
    ).requirements[0]
    assert row.conclusion == "inconclusive"
    assert row.evidence_sufficiency == "insufficient"


def test_requested_unavailable_interpretation_cannot_silently_disappear():
    row = evaluate(review_ref="art_review", review_status="unavailable").requirements[0]
    assert row.conclusion == "inconclusive"
    assert row.unavailable_evidence_refs == ["art_review"]


@pytest.mark.parametrize(
    "changes",
    [
        {"scope": {"assets": ["outside"]}},
        {"objectives": [requirement()["objectives"][0]] * 2},
        {"frameworkEdition": ""},
        {"applicabilityReason": ""},
        {"supportingEvidence": [supporting(objectiveIds=["unknown"])]},
    ],
)
def test_invalid_or_out_of_scope_requirement_is_rejected(changes):
    with pytest.raises(ValidationError):
        AssessmentRequest.model_validate(
            request_payload(
                controlAssessment={"requirements": [requirement(**changes)]}
            )
        )


def test_requirement_cannot_drop_admitted_exclusions():
    with pytest.raises(ValidationError, match="exclusions"):
        AssessmentRequest.model_validate(
            request_payload(scope={"assets": ["app"], "exclusions": ["private"]})
        )


def test_empty_duplicate_and_oversized_control_plans_are_rejected():
    from moonmind.security.assessments import ControlAssessmentPlan

    for requirements in ([], [requirement(), requirement()]):
        with pytest.raises(ValidationError):
            ControlAssessmentPlan(requirements=requirements)
    big = requirement(
        scope={"assets": [f"asset-{i}" for i in range(256)]},
        objectives=[
            dict(
                requirement()["objectives"][0],
                id=f"check-{i}",
                ruleIds=[f"rule-{j}" for j in range(32)],
            )
            for i in range(3)
        ],
    )
    with pytest.raises(ValidationError, match="population exceeds"):
        ControlAssessmentPlan(requirements=[big])


def test_control_cannot_drop_admitted_repository_exceptions():
    with pytest.raises(ValidationError, match="exceptions"):
        AssessmentRequest.model_validate(
            request_payload(
                scope={
                    "assets": ["app"],
                    "repositoryExceptions": ["inline-suppressions"],
                }
            )
        )


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize(
    "status", ["partial", "authentication_failed", "failed", "unsupported"]
)
def test_complete_row_cannot_hide_conflicting_incomplete_check(status, reverse):
    obs = observation()
    obs.coverage.append(obs.coverage[0].model_copy(update={"status": status}))
    if reverse:
        obs.coverage.reverse()
    row = evaluate(observed=obs).requirements[0]
    assert row.conclusion == "inconclusive"
    assert row.coverage != "complete" and row.limitations


def test_supporting_evidence_cannot_hide_suppression_exceptions():
    row = evaluate(
        requirements=[
            requirement(
                supportingEvidence=[
                    supporting(
                        scope={
                            "assets": ["app"],
                            "repositoryExceptions": ["inline-suppressions"],
                        }
                    )
                ]
            )
        ],
        availability={"art_statement": None},
    ).requirements[0]
    assert row.conclusion == "inconclusive"


@pytest.mark.parametrize(
    "method", ["agent_interpretation", "management_assertion", "unclassified"]
)
def test_non_deterministic_native_coverage_is_not_a_passing_check(method):
    obs = observation()
    obs.coverage[0].evidence_method = method
    row = evaluate(observed=obs).requirements[0]
    assert row.conclusion == "inconclusive"
    assert "deterministic_check" not in row.methods


def test_rng_implementation_concerns_cannot_become_deterministic_satisfaction():
    from moonmind.security.rng_assessment import (
        REVIEW_TOPICS,
        RngReviewSpec,
        assess_rng,
    )
    from moonmind.workflows.skills.security_assessment import _rng_observation

    summary = assess_rng(
        RngReviewSpec(
            gameRef="app",
            implementationReview=[
                {
                    "topic": topic,
                    "status": "concern",
                    "observation": "Fixture implementation concern",
                    "evidenceRefs": ["fixture:review"],
                }
                for topic in REVIEW_TOPICS
            ],
        )
    )
    obs = _rng_observation(
        AssessmentRequest.model_validate(request_payload()), summary, "art_rng", NOW
    )
    assert obs.coverage[0].status == "complete" and not obs.findings
    row = evaluate(
        observed=obs,
        requirements=[
            requirement(
                objectives=[
                    {
                        "id": "check",
                        "description": "Review generator implementation",
                        "source": "rng",
                        "tool": "rng",
                        "ruleIds": ["implementation-review"],
                    }
                ]
            )
        ],
    ).requirements[0]
    assert row.conclusion == "inconclusive"
    assert row.methods == ["agent_interpretation"]
