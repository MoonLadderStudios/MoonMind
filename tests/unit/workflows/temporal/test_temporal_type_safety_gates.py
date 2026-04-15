from __future__ import annotations

import pytest
from pydantic import ValidationError

from moonmind.workflows.temporal.type_safety_gates import (
    CompatibilityEvidence,
    EscapeHatchJustification,
    ReviewGateFinding,
    TemporalAntiPatternCase,
    evaluate_anti_pattern_case,
    evaluate_compatibility_evidence,
    evaluate_escape_hatch,
)


def test_compatibility_sensitive_change_requires_evidence() -> None:
    finding = evaluate_compatibility_evidence(
        CompatibilityEvidence(changeKind="workflow", safetyMode=None)
    )

    assert finding.status == "fail"
    assert finding.rule_id == "TEMPORAL-COMPAT-001"
    assert "replay, in-flight, or cutover evidence" in finding.message
    assert finding.remediation


def test_safe_additive_change_passes_with_evidence() -> None:
    finding = evaluate_compatibility_evidence(
        CompatibilityEvidence(
            changeKind="activity",
            safetyMode="additive",
            evidenceRef="tests/unit/workflows/temporal/test_activity_catalog.py",
            callersTolerateUnknown=True,
        )
    )

    assert finding.status == "pass"
    assert finding.evidence_ref == "tests/unit/workflows/temporal/test_activity_catalog.py"


def test_non_additive_change_requires_cutover_reason() -> None:
    finding = evaluate_compatibility_evidence(
        CompatibilityEvidence(
            changeKind="signal",
            safetyMode="versioned_cutover",
            evidenceRef="cutover-plan.md",
        )
    )

    assert finding.status == "fail"
    assert finding.rule_id == "TEMPORAL-COMPAT-002"
    assert "non-additive" in finding.message


@pytest.mark.parametrize(
    ("pattern", "expected_rule"),
    [
        ("raw_dict_activity_payload", "TEMPORAL-ANTI-001"),
        ("public_raw_dict_handler", "TEMPORAL-ANTI-002"),
        ("generic_action_envelope", "TEMPORAL-ANTI-003"),
        ("provider_shaped_workflow_result", "TEMPORAL-ANTI-004"),
        ("untyped_status_leak", "TEMPORAL-ANTI-005"),
        ("nested_raw_bytes", "TEMPORAL-ANTI-006"),
        ("large_workflow_history_state", "TEMPORAL-ANTI-007"),
    ],
)
def test_known_temporal_anti_patterns_fail_with_rule_specific_findings(
    pattern: str, expected_rule: str
) -> None:
    finding = evaluate_anti_pattern_case(
        TemporalAntiPatternCase(
            pattern=pattern,
            target=f"fixture:{pattern}",
            expectedRuleId=expected_rule,
            expectedOutcome="fail",
        )
    )

    assert finding.status == "fail"
    assert finding.rule_id == expected_rule
    assert finding.remediation


def test_safe_temporal_contract_case_passes() -> None:
    finding = evaluate_anti_pattern_case(
        TemporalAntiPatternCase(
            pattern="typed_request_model",
            target="fixture:typed-request",
            expectedRuleId="TEMPORAL-ANTI-SAFE",
            expectedOutcome="pass",
        )
    )

    assert finding.status == "pass"
    assert finding.rule_id == "TEMPORAL-ANTI-SAFE"


def test_escape_hatch_requires_transitional_boundary_only_justification() -> None:
    finding = evaluate_escape_hatch(
        EscapeHatchJustification(
            target="legacy-control-signal",
            reason="live workflow replay compatibility",
            boundaryOnly=False,
            transitional=True,
            semanticRisk=False,
        )
    )

    assert finding.status == "fail"
    assert finding.rule_id == "TEMPORAL-ESCAPE-001"
    assert "boundary-only" in finding.message


def test_valid_escape_hatch_passes() -> None:
    finding = evaluate_escape_hatch(
        EscapeHatchJustification(
            target="legacy-control-signal",
            reason="live workflow replay compatibility",
            boundaryOnly=True,
            transitional=True,
            semanticRisk=False,
        )
    )

    assert finding.status == "pass"
    assert finding.rule_id == "TEMPORAL-ESCAPE-001"


def test_failed_finding_requires_remediation() -> None:
    with pytest.raises(ValidationError, match="remediation"):
        ReviewGateFinding(
            ruleId="TEMPORAL-ANTI-001",
            status="fail",
            target="fixture",
            message="failed",
        )
