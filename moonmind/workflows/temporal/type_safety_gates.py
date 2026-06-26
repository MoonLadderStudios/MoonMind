from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

FindingStatus = Literal["pass", "fail"]
ChangeKind = Literal["workflow", "activity", "signal", "update", "query", "continue_as_new"]
SafetyMode = Literal["additive", "replay_tested", "in_flight_tested", "versioned_cutover"]
TemporalPatternKind = Literal[
    "raw_dict_activity_payload",
    "public_raw_dict_handler",
    "generic_action_envelope",
    "provider_shaped_workflow_result",
    "untyped_status_leak",
    "nested_raw_bytes",
    "large_workflow_history_state",
    "typed_request_model",
    "compact_artifact_ref",
    "canonical_workflow_result",
]

class _GateModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

class ReviewGateFinding(_GateModel):
    rule_id: str = Field(alias="ruleId")
    status: FindingStatus
    target: str
    message: str
    remediation: str | None = None
    evidence_ref: str | None = Field(default=None, alias="evidenceRef")

    @field_validator("rule_id", "target", "message", "remediation", "evidence_ref")
    @classmethod
    def _require_non_empty_text(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("must not be empty")
        return value

    @model_validator(mode="after")
    def _failed_findings_require_remediation(self) -> ReviewGateFinding:
        if self.status == "fail" and not (self.remediation and self.remediation.strip()):
            raise ValueError("failed findings require remediation")
        return self

class CompatibilityEvidence(_GateModel):
    change_kind: ChangeKind = Field(alias="changeKind")
    safety_mode: SafetyMode | None = Field(default=None, alias="safetyMode")
    evidence_ref: str | None = Field(default=None, alias="evidenceRef")
    non_additive_reason: str | None = Field(default=None, alias="nonAdditiveReason")
    callers_tolerate_unknown: bool = Field(default=False, alias="callersTolerateUnknown")
    target: str | None = None

    @field_validator("evidence_ref", "non_additive_reason", "target")
    @classmethod
    def _require_non_empty_optional_text(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("must not be empty")
        return value

class EscapeHatchJustification(_GateModel):
    target: str
    reason: str
    boundary_only: bool = Field(alias="boundaryOnly")
    transitional: bool
    semantic_risk: bool = Field(alias="semanticRisk")

    @field_validator("target", "reason")
    @classmethod
    def _require_non_empty_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be empty")
        return value

class TemporalPatternCase(_GateModel):
    pattern: TemporalPatternKind
    target: str
    expected_rule_id: str = Field(alias="expectedRuleId")

    @field_validator("target", "expected_rule_id")
    @classmethod
    def _require_non_empty_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be empty")
        return value

_ANTI_PATTERN_RULES: dict[str, tuple[str, str, str]] = {
    "raw_dict_activity_payload": (
        "TEMPORAL-ANTI-001",
        "Raw dictionary activity payloads are not allowed at workflow call sites.",
        "Use a named request model and typed execution boundary.",
    ),
    "public_raw_dict_handler": (
        "TEMPORAL-ANTI-002",
        "Public workflow handlers must not expose raw dictionary contracts.",
        "Replace the handler payload with a closed request or signal model.",
    ),
    "generic_action_envelope": (
        "TEMPORAL-ANTI-003",
        "Generic action envelopes are not allowed for new public Temporal controls.",
        "Use a specific typed command model for the public control.",
    ),
    "provider_shaped_workflow_result": (
        "TEMPORAL-ANTI-004",
        "Provider-shaped top-level activity results must not leak into workflow-facing contracts.",
        "Normalize provider output into a canonical workflow-facing result model.",
    ),
    "untyped_status_leak": (
        "TEMPORAL-ANTI-005",
        "Untyped provider status values must not bypass a closed workflow-facing model.",
        "Map provider statuses into the canonical status model and fail fast for unsupported values.",
    ),
    "nested_raw_bytes": (
        "TEMPORAL-ANTI-006",
        "Nested raw bytes must not be stored directly in workflow history.",
        "Store bytes outside workflow history and pass a compact artifact or storage reference.",
    ),
    "large_workflow_history_state": (
        "TEMPORAL-ANTI-007",
        "Large conversational state must not be stored directly in workflow history.",
        "Persist large state as artifacts or durable records and keep workflow state compact.",
    ),
}
_SAFE_PATTERNS = {
    "typed_request_model",
    "compact_artifact_ref",
    "canonical_workflow_result",
}

def evaluate_compatibility_evidence(evidence: CompatibilityEvidence) -> ReviewGateFinding:
    target = evidence.target or f"{evidence.change_kind}:compatibility"

    if evidence.safety_mode is None:
        return ReviewGateFinding(
            ruleId="TEMPORAL-COMPAT-001",
            status="fail",
            target=target,
            message=(
                "Compatibility-sensitive Temporal contract changes require replay, "
                "in-flight, or cutover evidence."
            ),
            remediation=(
                "Add replay or in-flight regression coverage, or document explicit "
                "versioned cutover notes for the reviewed Temporal boundary."
            ),
        )

    if evidence.safety_mode == "additive":
        if evidence.evidence_ref and evidence.callers_tolerate_unknown:
            return ReviewGateFinding(
                ruleId="TEMPORAL-COMPAT-001",
                status="pass",
                target=target,
                message="Additive Temporal contract evolution includes compatibility evidence.",
                evidenceRef=evidence.evidence_ref,
            )
        return ReviewGateFinding(
            ruleId="TEMPORAL-COMPAT-001",
            status="fail",
            target=target,
            message=(
                "Additive Temporal contract changes must include evidence and tolerate "
                "unknown future values where applicable."
            ),
            remediation=(
                "Provide a concrete evidence reference and confirm callers and handlers "
                "tolerate optional additions or widened values."
            ),
            evidenceRef=evidence.evidence_ref,
        )

    if evidence.safety_mode == "versioned_cutover":
        if not (evidence.evidence_ref and evidence.non_additive_reason):
            return ReviewGateFinding(
                ruleId="TEMPORAL-COMPAT-002",
                status="fail",
                target=target,
                message="Unsafe non-additive Temporal changes require a cutover plan and reason.",
                remediation=(
                    "Document the non-additive reason and reference a migration or versioned "
                    "cutover plan that preserves running workflow safety."
                ),
                evidenceRef=evidence.evidence_ref,
            )
        return ReviewGateFinding(
            ruleId="TEMPORAL-COMPAT-002",
            status="pass",
            target=target,
            message="Non-additive Temporal contract change includes explicit cutover evidence.",
            evidenceRef=evidence.evidence_ref,
        )

    if not evidence.evidence_ref:
        return ReviewGateFinding(
            ruleId="TEMPORAL-COMPAT-001",
            status="fail",
            target=target,
            message=f"{evidence.safety_mode} Temporal changes require a concrete evidence reference.",
            remediation="Reference the replay, in-flight, schema, or boundary test evidence.",
        )

    return ReviewGateFinding(
        ruleId="TEMPORAL-COMPAT-001",
        status="pass",
        target=target,
        message=f"Temporal contract change is covered by {evidence.safety_mode} evidence.",
        evidenceRef=evidence.evidence_ref,
    )

def evaluate_temporal_pattern_case(case: TemporalPatternCase) -> ReviewGateFinding:
    if case.pattern in _SAFE_PATTERNS:
        return ReviewGateFinding(
            ruleId=case.expected_rule_id,
            status="pass",
            target=case.target,
            message="Temporal contract shape is typed, compact, and workflow-safe.",
        )

    rule_id, message, remediation = _ANTI_PATTERN_RULES[case.pattern]
    return ReviewGateFinding(
        ruleId=rule_id,
        status="fail",
        target=case.target,
        message=message,
        remediation=remediation,
    )

def evaluate_escape_hatch(justification: EscapeHatchJustification) -> ReviewGateFinding:
    failures: list[str] = []
    if not justification.boundary_only:
        failures.append("boundary-only")
    if not justification.transitional:
        failures.append("transitional")
    if justification.semantic_risk:
        failures.append("no semantic-risk")
    if not justification.reason.strip():
        failures.append("compatibility reason")

    if failures:
        missing = ", ".join(failures)
        return ReviewGateFinding(
            ruleId="TEMPORAL-ESCAPE-001",
            status="fail",
            target=justification.target,
            message=f"Escape hatch is not acceptable without {missing} documentation.",
            remediation=(
                "Constrain the escape hatch to the public boundary, mark it transitional, "
                "justify the replay or in-flight compatibility need, and avoid semantic risk."
            ),
        )

    return ReviewGateFinding(
        ruleId="TEMPORAL-ESCAPE-001",
        status="pass",
        target=justification.target,
        message="Escape hatch is transitional, boundary-only, and compatibility-justified.",
    )

def build_self_check_findings() -> list[ReviewGateFinding]:
    findings: list[ReviewGateFinding] = [
        evaluate_compatibility_evidence(
            CompatibilityEvidence(
                changeKind="workflow",
                safetyMode=None,
                target="fixture:missing-compatibility-evidence",
            )
        ),
        evaluate_compatibility_evidence(
            CompatibilityEvidence(
                changeKind="activity",
                safetyMode="additive",
                evidenceRef="tests/unit/workflows/temporal/test_temporal_type_safety_gates.py",
                callersTolerateUnknown=True,
                target="fixture:safe-additive-change",
            )
        ),
    ]

    for pattern in (
        "raw_dict_activity_payload",
        "public_raw_dict_handler",
        "generic_action_envelope",
        "provider_shaped_workflow_result",
        "untyped_status_leak",
        "nested_raw_bytes",
        "large_workflow_history_state",
    ):
        rule_id = _ANTI_PATTERN_RULES[pattern][0]
        findings.append(
            evaluate_temporal_pattern_case(
                TemporalPatternCase(
                    pattern=pattern,
                    target=f"fixture:{pattern}",
                    expectedRuleId=rule_id,
                )
            )
        )

    findings.extend(
        [
            evaluate_escape_hatch(
                EscapeHatchJustification(
                    target="fixture:invalid-escape-hatch",
                    reason="live workflow replay compatibility",
                    boundaryOnly=False,
                    transitional=True,
                    semanticRisk=False,
                )
            ),
            evaluate_escape_hatch(
                EscapeHatchJustification(
                    target="fixture:valid-escape-hatch",
                    reason="live workflow replay compatibility",
                    boundaryOnly=True,
                    transitional=True,
                    semanticRisk=False,
                )
            ),
        ]
    )
    return findings
