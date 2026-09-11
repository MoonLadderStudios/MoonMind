"""Unit tests for canonical assessment-verdict normalization."""

from __future__ import annotations

from moonmind.workflows.temporal.assessment_verdict import (
    infer_verdict_from_requirements,
    normalize_assessment_payload,
    normalize_verdict,
    verdict_from_mapping,
    verdict_from_text,
)


def test_normalize_verdict_accepts_canonical() -> None:
    assert normalize_verdict("fully_implemented") == "FULLY_IMPLEMENTED"
    assert normalize_verdict("BLOCKED") == "BLOCKED"
    assert normalize_verdict("bogus") == ""


def test_verdict_from_text_recovers_assistant_verdict() -> None:
    assert (
        verdict_from_text("## Verdict: FULLY_IMPLEMENTED") == "FULLY_IMPLEMENTED"
    )
    assert verdict_from_text("verdict: partially_implemented") == (
        "PARTIALLY_IMPLEMENTED"
    )
    assert verdict_from_text("no verdict here") == ""


def test_verdict_from_mapping_checks_aliases() -> None:
    assert (
        verdict_from_mapping({"assessmentVerdict": "NOT_IMPLEMENTED"})
        == "NOT_IMPLEMENTED"
    )
    assert verdict_from_mapping({"verdict": "blocked"}) == "BLOCKED"
    assert verdict_from_mapping({}) == ""


def test_infer_all_met_implies_fully() -> None:
    verdict, _ = infer_verdict_from_requirements(
        [{"status": "met"}, {"status": "met"}],
        summary="Epic fully implemented",
    )
    assert verdict == "FULLY_IMPLEMENTED"


def test_infer_mixed_implies_partially_safe_bias() -> None:
    verdict, _ = infer_verdict_from_requirements(
        [{"status": "met"}, {"status": "not_met"}]
    )
    assert verdict == "PARTIALLY_IMPLEMENTED"


def test_infer_empty_unavailable() -> None:
    assert infer_verdict_from_requirements([])[0] == ""
    assert infer_verdict_from_requirements(None)[0] == ""  # type: ignore[arg-type]


def test_normalize_payload_prefers_declared() -> None:
    verdict, provenance, _ = normalize_assessment_payload(
        {"verdict": "BLOCKED", "requirements": [{"status": "met"}]}
    )
    assert (verdict, provenance) == ("BLOCKED", "declared")


def test_normalize_payload_recovers_requirements() -> None:
    verdict, provenance, _ = normalize_assessment_payload(
        {
            "summary": "Epic fully implemented",
            "requirements": [{"status": "met"}, {"status": "met"}],
        }
    )
    assert (verdict, provenance) == (
        "FULLY_IMPLEMENTED",
        "requirements_inferred",
    )


def test_normalize_payload_unavailable_when_no_signal() -> None:
    assert normalize_assessment_payload({"summary": "no verdict here"})[0] == ""
