"""Comparison and bounds for retained repository scanner evidence."""

from datetime import UTC, datetime

import pytest

from moonmind.security.assessments import (
    AssessmentRequest,
    Finding,
    compare_observations,
)
from moonmind.security.game_math import (
    GameMathSpec,
    RoundRecord,
    SimulationRequest,
    assess_game_math,
)
from moonmind.security.repository_scanners import (
    NativeResult,
    RepositoryScan,
    ScannerExecution,
    ScannerSummary,
)
from moonmind.security.rng_assessment import (
    RngAssessmentRequest,
    assess_rng,
    fixture_sample,
)
from moonmind.workflows.skills.security_assessment import (
    _game_math_observation,
    _rng_observation,
    _scanner_observation,
)

NOW = datetime(2026, 10, 26, tzinfo=UTC)


def summary(scanner="opengrep", *, rule="native-rule", findings=True):
    options = {}
    if scanner == "opengrep":
        options["rules"] = [
            {
                "id": "selected-config",
                "path": "/opt/moonmind-scanner-rules/source.yaml",
                "sha256": "b" * 64,
                "license": "MIT",
            }
        ]
    elif scanner == "trivy":
        options["trivyScanners"] = ["misconfig"]
    admission = RepositoryScan(
        scanner=scanner, outputDirectory="artifacts/baseline", **options
    )
    refs = {mode: f"art_{mode}" for mode in admission.modes}
    return ScannerSummary(
        scanner=scanner,
        admission=admission,
        execution=ScannerExecution(
            inputDigest="a" * 64,
            assets=["app.py"],
            version="1.2.3",
            exits={mode: 0 for mode in admission.modes},
            states={mode: "succeeded" for mode in admission.modes},
        ),
        results={
            mode: NativeResult(
                execution="succeeded",
                coverage="complete",
                examinedAssets=["app.py"],
                findings=(
                    [
                        Finding(
                            source="repository",
                            tool=scanner,
                            ruleId=rule,
                            asset="app.py",
                            nativeId=mode,
                            evidenceRefs=[refs[mode]],
                        )
                    ]
                    if findings
                    else []
                ),
            )
            for mode in admission.modes
        },
        nativeRefs=refs,
        coverage="complete",
        limitations=[],
    )


def observe(retained):
    request = AssessmentRequest(
        scope={"assets": ["app.py"]},
        tools=["retained-report"],
        reportRefs=["art_summary"],
        credentialRefs=[],
        networkRefs=[],
        budget={"baseSeconds": 60, "maxSeconds": 120, "progressStallSeconds": 60},
    )
    return _scanner_observation(request, retained, "art_summary", NOW)


def test_scanner_comparison_ignores_report_destinations():
    baseline = compare_observations(
        observe(summary(rule="selected-config")), None, now=NOW
    )
    current = summary(rule="selected-config", findings=False)
    current.admission.output_directory = "artifacts/current"
    result = compare_observations(observe(current), baseline, now=NOW)
    assert result["findings"][0]["state"] == "absent_comparable"


@pytest.mark.parametrize("scanner", ["gitleaks", "opengrep", "trivy"])
def test_clean_rescan_covers_native_rules_without_positive_findings(scanner):
    baseline = compare_observations(observe(summary(scanner)), None, now=NOW)
    result = compare_observations(
        observe(summary(scanner, findings=False)), baseline, now=NOW
    )
    assert {row["state"] for row in result["findings"]} == {"absent_comparable"}


@pytest.mark.parametrize("change", ["version", "rules", "partial", "unexamined"])
def test_scanner_comparison_keeps_findings_without_comparable_coverage(change):
    baseline = compare_observations(observe(summary()), None, now=NOW)
    current = summary(findings=False)
    if change == "version":
        current.execution.version = "1.2.4"
    elif change == "rules":
        current.admission.rules[0].sha256 = "c" * 64
    elif change == "partial":
        current.results["scan"].coverage = "partial"
    else:
        current.results["scan"].examined_assets = []
    result = compare_observations(observe(current), baseline, now=NOW)
    assert result["findings"][0]["state"] == "not_reassessed"


def test_combined_scanner_findings_are_bounded_and_keep_retained_evidence():
    retained = summary("gitleaks", findings=False)
    for mode, row in retained.results.items():
        row.findings = [
            Finding(
                source="repository",
                tool="gitleaks",
                ruleId="native-rule",
                asset="app.py",
                nativeId=f"{mode}-{index}",
                evidenceRefs=[retained.native_refs[mode]],
            )
            for index in range(2050)
        ]
    observation = observe(retained)
    assert len(observation.findings) == 4096
    assert observation.coverage[0].status == "partial"
    assert {ref for f in observation.findings for ref in f.evidence_refs} == {
        "art_history",
        "art_worktree",
    }
    assert "combined_finding_bound_exceeded" in observation.provenance.values()


GAME = "game:coin"
BUDGET = {"baseSeconds": 60, "maxSeconds": 120, "progressStallSeconds": 60}


def game_math(**kwargs):
    coin = GameMathSpec.model_validate(
        {
            "gameRef": GAME,
            "provenance": {
                "sourceRef": "par:coin",
                "contentDigest": "sha256:" + "c" * 64,
                "approvalRef": "filing-1",
            },
            "game": {
                "kind": "outcome-table",
                "outcomes": [
                    {"id": "heads", "probability": "1/2", "multiplier": "2"},
                    {"id": "tails", "probability": "1/2", "multiplier": "0"},
                ],
            },
            "wagers": [{"id": "bet", "stake": "1"}],
            "strategies": [{"id": "play", "bets": ["bet"]}],
            "settlement": {"quantum": "0.01", "rounding": "floor"},
            "declared": [{"strategy": "play", "rtp": "1"}],
            "criteria": {"tolerance": "1/50", "confidence": "99/100"},
        }
    )
    return assess_game_math(coin, **kwargs)


def observe_game(summary, assets=(GAME,)):
    request = AssessmentRequest(
        scope={"assets": list(assets)},
        tools=["retained-report"],
        reportRefs=["art_game"],
        credentialRefs=[],
        networkRefs=[],
        budget=BUDGET,
    )
    return _game_math_observation(request, summary, "art_game", NOW)


def test_game_math_violations_are_findings_and_fixes_are_comparable_absence():
    overpaid = game_math(
        settlements=[RoundRecord(strategy="play", outcome="tails", total="1")]
    )
    observation = observe_game(overpaid)
    (finding,) = observation.findings
    assert (finding.source, finding.rule_id, finding.asset) == (
        "game-math",
        "settlement",
        GAME,
    )
    assert finding.evidence_refs == ["art_game"]
    assert observation.coverage[0].status == "complete"
    assert observation.provenance["gameMathApproval"] == "filing-1"
    baseline = compare_observations(observation, None, now=NOW)
    fixed = game_math(
        settlements=[RoundRecord(strategy="play", outcome="tails", total="0")]
    )
    result = compare_observations(observe_game(fixed), baseline, now=NOW)
    assert result["findings"][0]["state"] == "absent_comparable"


@pytest.mark.parametrize("model", [False, True])
def test_theory_or_model_does_not_clear_implementation_findings(model):
    prior = game_math(
        settlements=[
            RoundRecord(strategy="play", outcome="tails", total="1"),
            RoundRecord(strategy="play", outcome="unknown", total="0"),
        ],
        simulation=SimulationRequest(source="records"),
        records=[RoundRecord(strategy="play", outcome="heads", total="2")] * 50_000,
    )
    baseline = compare_observations(observe_game(prior), None, now=NOW)
    current = game_math(
        **(
            {"simulation": SimulationRequest(source="model", samples=100_000, seed=7)}
            if model
            else {}
        )
    )
    result = compare_observations(observe_game(current), baseline, now=NOW)
    assert {row["finding"]["ruleId"] for row in result["findings"]} == {
        "settlement",
        "outcome-support",
        "simulated-rtp",
    }
    assert all(row["state"] != "absent_comparable" for row in result["findings"])


def test_recorded_run_can_clear_a_retested_implementation_rtp_finding():
    heads = RoundRecord(strategy="play", outcome="heads", total="2")
    tails = RoundRecord(strategy="play", outcome="tails", total="0")
    baseline = compare_observations(
        observe_game(
            game_math(
                simulation=SimulationRequest(source="records"),
                records=[heads] * 50_000,
            )
        ),
        None,
        now=NOW,
    )
    current = game_math(
        simulation=SimulationRequest(source="records"),
        records=[heads, tails] * 50_000,
    )
    assert current.covered
    result = compare_observations(observe_game(current), baseline, now=NOW)
    assert [row["state"] for row in result["findings"]] == ["absent_comparable"]


@pytest.mark.parametrize("stopped", [False, True])
def test_imprecise_or_stopped_game_math_stays_partial_coverage(stopped):
    summary = game_math(
        simulation=SimulationRequest(
            source="model", samples=50 if not stopped else 10**6, seed=1
        ),
        should_stop=lambda: stopped,
    )
    coverage = observe_game(summary).coverage[0]
    assert coverage.status == "partial" and not coverage.all_rules


def test_game_outside_admitted_scope_is_refused():
    with pytest.raises(ValueError, match="outside admitted"):
        observe_game(game_math(), assets=["app.py"])


RNG = "rng:deal-service"
REVIEW = {
    name: {"status": "no_concern_noted", "summary": f"{name} reviewed"}
    for name in (
        "generator",
        "seeding",
        "restartFork",
        "stateHandling",
        "predictableInputs",
    )
}
CARDS = {
    "description": "card index dealt by the implementation under test",
    "model": [{"id": f"card{i}", "probability": "1/52"} for i in range(52)],
}


def rng(sample: bytes, *, outcomes=None, review=REVIEW, claims=(), **kwargs):
    request = RngAssessmentRequest.model_validate(
        {
            "schemaVersion": "rng-assessment-request/v1",
            "subjectRef": RNG,
            "review": review,
            "diagnostics": {
                "origin": {"kind": "recorded", "description": "deal service output"},
                "bits": len(sample) * 8,
            },
            **({"outcomes": CARDS} if outcomes is not None else {}),
            "externalClaims": list(claims),
        }
    )
    return assess_rng(request, sample=sample, outcomes=outcomes, **kwargs)


def observe_rng(summary, assets=(RNG,)):
    request = AssessmentRequest(
        scope={"assets": list(assets)},
        tools=["retained-report"],
        reportRefs=["art_rng"],
        credentialRefs=[],
        networkRefs=[],
        budget=BUDGET,
    )
    return _rng_observation(request, summary, "art_rng", NOW)


def test_rng_sections_are_separate_coverage_and_claims_stay_provenance():
    data = fixture_sample("biased", 1, 12_500)
    summary = rng(
        data,
        outcomes=[f"card{value % 52}" for value in data],
        review={**REVIEW, "generator": {"status": "concern", "summary": "LCG"}},
        claims=[
            {
                "claim": "cryptographic_strength",
                "verifier": "specialist",
                "reportRef": "report:7",
            }
        ],
    )
    observation = observe_rng(summary)
    rules = {(f.tool, f.rule_id) for f in observation.findings}
    assert {
        ("rng-review", "review:generator"),
        ("rng-diagnostics", "raw-bits:frequency"),
        ("rng-outcomes", "mapped-outcome:distribution"),
    } <= rules
    assert {f.evidence_refs[0] for f in observation.findings} == {"art_rng"}
    assert [row.tool for row in observation.coverage] == [
        "rng-review",
        "rng-diagnostics",
        "rng-outcomes",
    ]
    # A battery or outcome check never covers an implementation review rule.
    review, raw, mapped = observation.coverage
    assert not set(raw.rule_ids) & set(review.rule_ids)
    assert not set(mapped.rule_ids) & set(review.rule_ids)
    assert observation.provenance["rngCryptographicStrength"] == "externally_reported"
    assert observation.provenance["rngCertification"] == "not_performed"


def test_rng_battery_pass_never_promotes_assurance_or_review_coverage():
    summary = rng(fixture_sample("mt19937", 2, 25_000), review=None)
    observation = observe_rng(summary)
    assert observation.findings == []
    (raw,) = observation.coverage
    assert (raw.tool, raw.status) == ("rng-diagnostics", "complete")
    assert observation.provenance["rngCryptographicStrength"] == "not_established"
    assert observation.provenance["rngGameFairness"] == "not_established"


def test_rng_incomplete_run_stays_partial_coverage():
    summary = rng(fixture_sample("mt19937", 2, 25_000), should_stop=lambda: True)
    raw = observe_rng(summary).coverage[1]
    assert (raw.status, raw.rule_ids, raw.all_rules) == ("partial", [], False)


def test_rng_retest_clears_an_anomaly_only_for_the_same_origin():
    baseline = compare_observations(
        observe_rng(rng(fixture_sample("biased", 1, 12_500))), None, now=NOW
    )
    retest = rng(fixture_sample("sha256-counter", 1, 12_500))
    result = compare_observations(observe_rng(retest), baseline, now=NOW)
    assert {row["state"] for row in result["findings"]} == {"absent_comparable"}

    fixture_run = assess_rng(
        RngAssessmentRequest.model_validate(
            {
                "schemaVersion": "rng-assessment-request/v1",
                "subjectRef": RNG,
                "review": REVIEW,
                "diagnostics": {
                    "origin": {
                        "kind": "fixture",
                        "fixture": {"name": "sha256-counter", "seed": 1},
                    },
                    "bits": 100_000,
                },
            }
        )
    )
    result = compare_observations(observe_rng(fixture_run), baseline, now=NOW)
    assert {row["state"] for row in result["findings"]} == {"not_reassessed"}


def test_rng_subject_outside_admitted_scope_is_refused():
    with pytest.raises(ValueError, match="outside admitted"):
        observe_rng(rng(fixture_sample("mt19937", 2, 1000)), assets=["app.py"])
