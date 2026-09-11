"""Unit tests for canonical assessment-verdict normalization."""

from __future__ import annotations

from moonmind.workflows.temporal.assessment_verdict import (
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


def test_verdict_from_text_recovers_github_refs() -> None:
    assert (
        verdict_from_text(
            "Assessment complete for MoonLadderStudios/MoonMind#4175: FULLY_IMPLEMENTED"
        )
        == "FULLY_IMPLEMENTED"
    )
    assert verdict_from_text("Assessment complete for #4175: NOT_IMPLEMENTED") == (
        "NOT_IMPLEMENTED"
    )


def test_verdict_from_mapping_checks_aliases() -> None:
    assert (
        verdict_from_mapping({"assessmentVerdict": "NOT_IMPLEMENTED"})
        == "NOT_IMPLEMENTED"
    )
    assert verdict_from_mapping({"verdict": "blocked"}) == "BLOCKED"
    assert verdict_from_mapping({}) == ""


def test_requirements_alone_never_imply_completion() -> None:
    # Skill authority: unanimous met requirements without an explicit verdict
    # must NOT manufacture FULLY_IMPLEMENTED natively. The portable assessment
    # Skill owns completion decisions; native only recovers explicit statements.
    verdict, provenance, _ = normalize_assessment_payload(
        {
            "summary": "Epic fully implemented",
            "requirements": [{"status": "met"}, {"status": "met"}],
        }
    )
    assert verdict == ""
    assert provenance == "unavailable"


def test_normalize_payload_prefers_declared() -> None:
    verdict, provenance, _ = normalize_assessment_payload(
        {"verdict": "BLOCKED", "requirements": [{"status": "met"}]}
    )
    assert (verdict, provenance) == ("BLOCKED", "declared")


def test_normalize_payload_recovers_explicit_text() -> None:
    verdict, provenance, _ = normalize_assessment_payload(
        {"summary": "## Verdict: PARTIALLY_IMPLEMENTED"},
    )
    assert (verdict, provenance) == (
        "PARTIALLY_IMPLEMENTED",
        "text_recovered",
    )


def test_normalize_payload_unavailable_when_no_signal() -> None:
    assert normalize_assessment_payload({"summary": "no verdict here"})[0] == ""
