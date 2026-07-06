"""Review gate contracts and utilities.

Data models for the step approval policy system:
- ``ReviewRequest``: input to the ``step.review`` activity.
- ``ReviewVerdict``: structured output from the review activity.
- Feedback builders for injecting review feedback into step inputs.
- Prompt builder for constructing the LLM review prompt.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping

from moonmind.workflows.skills.tool_plan_contracts import (
    ContractValidationError,
    REVIEW_VERDICTS,
)

_RECOMMENDED_NEXT_ACTIONS = {
    "advance",
    "reattempt_current_step",
    "needs_human",
    "blocked",
}

_VERDICT_SYNONYMS = {
    "PASS": "FULLY_IMPLEMENTED",
    "FAIL": "ADDITIONAL_WORK_NEEDED",
    "INCONCLUSIVE": "NO_DETERMINATION",
}


def recommended_next_actions() -> tuple[str, ...]:
    """Canonical ``recommendedNextAction`` vocabulary for gate payloads."""
    return tuple(sorted(_RECOMMENDED_NEXT_ACTIONS))


def recommended_next_action_for_verdict(
    verdict: Any,
    *,
    recoverable_in_current_runtime: bool = False,
) -> str | None:
    """Return the runtime-owned next action for a canonical gate verdict."""

    normalized = str(verdict or "").strip().upper()
    normalized = _VERDICT_SYNONYMS.get(normalized, normalized)
    if normalized == "FULLY_IMPLEMENTED":
        return "advance"
    if normalized == "ADDITIONAL_WORK_NEEDED":
        return "reattempt_current_step"
    if normalized == "NO_DETERMINATION":
        return (
            "reattempt_current_step"
            if recoverable_in_current_runtime
            else "needs_human"
        )
    if normalized in {"BLOCKED", "FAILED_UNRECOVERABLE"}:
        return "blocked"
    return None


def step_gate_contract_violations(payload: Mapping[str, Any]) -> list[str]:
    """Return the contract violations that force a fail-closed downgrade.

    A non-empty result means ``parse_step_gate_result`` will mark the payload
    invalid/degraded and downgrade its verdict to ``NO_DETERMINATION``.
    """
    violations: list[str] = []
    raw_verdict = str(payload.get("verdict") or "").strip()
    if not raw_verdict:
        violations.append(
            "verdict is missing from the structured gate payload"
        )
    else:
        normalized = raw_verdict.upper()
        normalized = _VERDICT_SYNONYMS.get(normalized, normalized)
        if normalized not in REVIEW_VERDICTS:
            violations.append(
                f"verdict {raw_verdict!r} is not one of "
                f"{sorted(REVIEW_VERDICTS)}"
            )
    if bool(payload.get("invalid")):
        violations.append("payload was flagged invalid by its producer")
    if bool(payload.get("degraded")):
        violations.append("payload was flagged degraded by its producer")
    recommended_next_action = payload.get("recommendedNextAction")
    if recommended_next_action is None:
        recommended_next_action = payload.get("recommended_next_action")
    if recommended_next_action is not None:
        if not isinstance(recommended_next_action, str):
            violations.append(
                "recommendedNextAction must be a string, got "
                f"{type(recommended_next_action).__name__}"
            )
        else:
            recommended_text = recommended_next_action.strip()
            if recommended_text not in _RECOMMENDED_NEXT_ACTIONS:
                violations.append(
                    f"recommendedNextAction {recommended_text!r} is not one of "
                    f"{sorted(_RECOMMENDED_NEXT_ACTIONS)}"
                )
    return violations

@dataclass(frozen=True, slots=True)
class ReviewRequest:
    """Input envelope for the ``step.review`` activity."""

    node_id: str
    step_index: int
    total_steps: int
    review_attempt: int
    tool_name: str
    tool_type: str
    inputs: Mapping[str, Any]
    execution_result: Mapping[str, Any]
    workflow_context: Mapping[str, Any]
    reviewer_model: str = "default"
    review_timeout_seconds: int = 120
    previous_feedback: str | None = None

    def __post_init__(self) -> None:
        if not self.node_id:
            raise ContractValidationError(
                "invalid_review_request", "node_id cannot be blank"
            )
        if self.step_index < 1:
            raise ContractValidationError(
                "invalid_review_request", "step_index must be >= 1"
            )
        if self.review_attempt < 1:
            raise ContractValidationError(
                "invalid_review_request", "review_attempt must be >= 1"
            )

    def to_payload(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "step_index": self.step_index,
            "total_steps": self.total_steps,
            "review_attempt": self.review_attempt,
            "tool_name": self.tool_name,
            "tool_type": self.tool_type,
            "inputs": dict(self.inputs),
            "execution_result": dict(self.execution_result),
            "workflow_context": dict(self.workflow_context),
            "reviewer_model": self.reviewer_model,
            "review_timeout_seconds": self.review_timeout_seconds,
            "previous_feedback": self.previous_feedback,
        }

@dataclass(frozen=True, slots=True)
class ReviewVerdict:
    """Structured output from the ``step.review`` activity."""

    verdict: str
    confidence: float | str = 0.0
    feedback: str | None = None
    issues: tuple[Mapping[str, Any], ...] = ()
    validated_refs: Mapping[str, Any] | None = None
    invalidated_refs: tuple[str, ...] = ()
    remaining_work_ref: str | None = None
    blocking_evidence_refs: tuple[str, ...] = ()
    recommended_next_action: str | None = None
    target_logical_step_id: str | None = None
    workspace_policy_recommendation: str | None = None
    recoverable_in_current_runtime: bool = False
    invalid: bool = False
    degraded: bool = False
    downgrade_reason: str | None = None

    def __post_init__(self) -> None:
        if self.verdict not in REVIEW_VERDICTS:
            raise ContractValidationError(
                "invalid_review_verdict",
                f"verdict must be one of {sorted(REVIEW_VERDICTS)}",
            )
        if isinstance(self.confidence, (int, float)) and not isinstance(
            self.confidence, bool
        ):
            if not (0.0 <= float(self.confidence) <= 1.0):
                raise ContractValidationError(
                    "invalid_review_verdict",
                    "numeric confidence must be between 0.0 and 1.0",
                )
        elif str(self.confidence).strip().lower() not in {"low", "medium", "high"}:
            raise ContractValidationError(
                "invalid_review_verdict",
                "confidence must be numeric 0.0-1.0 or low|medium|high",
            )
        if (
            self.recommended_next_action is not None
            and self.recommended_next_action not in _RECOMMENDED_NEXT_ACTIONS
        ):
            raise ContractValidationError(
                "invalid_review_verdict",
                "recommendedNextAction must be one of "
                f"{sorted(_RECOMMENDED_NEXT_ACTIONS)}",
            )

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "verdict": self.verdict,
            "confidence": self.confidence,
            "feedback": self.feedback,
            "issues": [dict(issue) for issue in self.issues],
            "recoverableInCurrentRuntime": self.recoverable_in_current_runtime,
            "invalid": self.invalid,
            "degraded": self.degraded,
        }
        if self.validated_refs:
            payload["validatedRefs"] = dict(self.validated_refs)
        if self.invalidated_refs:
            payload["invalidatedRefs"] = list(self.invalidated_refs)
        if self.remaining_work_ref:
            payload["remainingWorkRef"] = self.remaining_work_ref
        if self.blocking_evidence_refs:
            payload["blockingEvidenceRefs"] = list(self.blocking_evidence_refs)
        if self.recommended_next_action:
            payload["recommendedNextAction"] = self.recommended_next_action
        if self.target_logical_step_id:
            payload["targetLogicalStepId"] = self.target_logical_step_id
        if self.workspace_policy_recommendation:
            payload["workspacePolicyRecommendation"] = (
                self.workspace_policy_recommendation
            )
        if self.downgrade_reason:
            payload["downgradeReason"] = self.downgrade_reason
        return payload

    def to_gate_result(self) -> "StepGateResult":
        return StepGateResult(
            verdict=self.verdict,
            confidence=self.confidence,
            feedback=self.feedback,
            issues=self.issues,
            validated_refs=self.validated_refs,
            invalidated_refs=self.invalidated_refs,
            remaining_work_ref=self.remaining_work_ref,
            blocking_evidence_refs=self.blocking_evidence_refs,
            recommended_next_action=self.recommended_next_action,
            target_logical_step_id=self.target_logical_step_id,
            workspace_policy_recommendation=self.workspace_policy_recommendation,
            recoverable_in_current_runtime=self.recoverable_in_current_runtime,
            invalid=self.invalid,
            degraded=self.degraded,
            downgrade_reason=self.downgrade_reason,
        )


@dataclass(frozen=True, slots=True)
class StepGateResult:
    """Canonical typed gate result artifact payload."""

    verdict: str
    confidence: float | str = 0.0
    feedback: str | None = None
    issues: tuple[Mapping[str, Any], ...] = ()
    validated_refs: Mapping[str, Any] | None = None
    invalidated_refs: tuple[str, ...] = ()
    remaining_work_ref: str | None = None
    blocking_evidence_refs: tuple[str, ...] = ()
    recommended_next_action: str | None = None
    target_logical_step_id: str | None = None
    workspace_policy_recommendation: str | None = None
    recoverable_in_current_runtime: bool = False
    invalid: bool = False
    degraded: bool = False
    downgrade_reason: str | None = None
    schema_version: str = "v1"

    def __post_init__(self) -> None:
        if self.schema_version != "v1":
            raise ContractValidationError(
                "invalid_step_gate_result",
                "schemaVersion must be v1",
            )
        if self.verdict not in REVIEW_VERDICTS:
            raise ContractValidationError(
                "invalid_step_gate_result",
                f"verdict must be one of {sorted(REVIEW_VERDICTS)}",
            )
        if isinstance(self.confidence, (int, float)) and not isinstance(
            self.confidence, bool
        ):
            if not (0.0 <= float(self.confidence) <= 1.0):
                raise ContractValidationError(
                    "invalid_step_gate_result",
                    "numeric confidence must be between 0.0 and 1.0",
                )
        elif str(self.confidence).strip().lower() not in {"low", "medium", "high"}:
            raise ContractValidationError(
                "invalid_step_gate_result",
                "confidence must be numeric 0.0-1.0 or low|medium|high",
            )
        if (
            self.recommended_next_action is not None
            and self.recommended_next_action not in _RECOMMENDED_NEXT_ACTIONS
        ):
            raise ContractValidationError(
                "invalid_step_gate_result",
                "recommendedNextAction must be one of "
                f"{sorted(_RECOMMENDED_NEXT_ACTIONS)}",
            )

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schemaVersion": self.schema_version,
            "verdict": self.verdict,
            "confidence": self.confidence,
            "feedback": self.feedback,
            "issues": [dict(issue) for issue in self.issues],
            "recoverableInCurrentRuntime": self.recoverable_in_current_runtime,
            "invalid": self.invalid,
            "degraded": self.degraded,
        }
        if self.validated_refs:
            payload["validatedRefs"] = dict(self.validated_refs)
        else:
            payload["validatedRefs"] = {}
        if self.invalidated_refs:
            payload["invalidatedRefs"] = list(self.invalidated_refs)
        else:
            payload["invalidatedRefs"] = []
        if self.remaining_work_ref:
            payload["remainingWorkRef"] = self.remaining_work_ref
        if self.blocking_evidence_refs:
            payload["blockingEvidenceRefs"] = list(self.blocking_evidence_refs)
        else:
            payload["blockingEvidenceRefs"] = []
        if self.recommended_next_action:
            payload["recommendedNextAction"] = self.recommended_next_action
        if self.target_logical_step_id:
            payload["targetLogicalStepId"] = self.target_logical_step_id
        if self.workspace_policy_recommendation:
            payload["workspacePolicyRecommendation"] = (
                self.workspace_policy_recommendation
            )
        if self.downgrade_reason:
            payload["downgradeReason"] = self.downgrade_reason
        return payload

    def to_review_verdict(self) -> ReviewVerdict:
        return ReviewVerdict(
            verdict=self.verdict,
            confidence=self.confidence,
            feedback=self.feedback,
            issues=self.issues,
            validated_refs=self.validated_refs,
            invalidated_refs=self.invalidated_refs,
            remaining_work_ref=self.remaining_work_ref,
            blocking_evidence_refs=self.blocking_evidence_refs,
            recommended_next_action=self.recommended_next_action,
            target_logical_step_id=self.target_logical_step_id,
            workspace_policy_recommendation=self.workspace_policy_recommendation,
            recoverable_in_current_runtime=self.recoverable_in_current_runtime,
            invalid=self.invalid,
            degraded=self.degraded,
            downgrade_reason=self.downgrade_reason,
        )

def parse_review_verdict(payload: Mapping[str, Any]) -> ReviewVerdict:
    """Parse an LLM response payload into a ``ReviewVerdict``."""
    return parse_step_gate_result(payload).to_review_verdict()


def parse_step_gate_result(payload: Mapping[str, Any]) -> StepGateResult:
    """Normalize activity output into the canonical gate result contract."""
    declared_verdict = str(payload.get("verdict") or "").strip()
    verdict_raw = (declared_verdict or "INCONCLUSIVE").upper()
    verdict_raw = _VERDICT_SYNONYMS.get(verdict_raw, verdict_raw)
    invalid = bool(payload.get("invalid"))
    degraded = bool(payload.get("degraded"))
    if verdict_raw not in REVIEW_VERDICTS:
        verdict_raw = "NO_DETERMINATION"
        invalid = True
        degraded = True
    elif not declared_verdict:
        invalid = True
        degraded = True

    confidence_raw = payload.get("confidence")
    confidence: float | str
    if isinstance(confidence_raw, str) and confidence_raw.strip().lower() in {
        "low",
        "medium",
        "high",
    }:
        confidence = confidence_raw.strip().lower()
    else:
        try:
            confidence = float(confidence_raw or 0.0)
            confidence = max(0.0, min(1.0, confidence))
        except (TypeError, ValueError):
            confidence = 0.0

    feedback = payload.get("feedback")
    if isinstance(feedback, str):
        feedback = feedback.strip() or None
    else:
        feedback = None

    issues_raw = payload.get("issues")
    issues: tuple[Mapping[str, Any], ...] = ()
    if isinstance(issues_raw, list):
        issues = tuple(
            dict(issue) for issue in issues_raw if isinstance(issue, dict)
        )
    validated_refs = payload.get("validatedRefs") or payload.get("validated_refs")
    invalidated_refs = payload.get("invalidatedRefs") or payload.get(
        "invalidated_refs"
    )
    blocking_refs = payload.get("blockingEvidenceRefs") or payload.get(
        "blocking_evidence_refs"
    )

    recommended_next_action_value = payload.get("recommendedNextAction")
    if recommended_next_action_value is None:
        recommended_next_action_value = payload.get("recommended_next_action")
    recommended_next_action = _optional_text(recommended_next_action_value)
    if recommended_next_action_value is not None and (
        not isinstance(recommended_next_action_value, str)
        or recommended_next_action not in _RECOMMENDED_NEXT_ACTIONS
    ):
        # Fail closed gracefully on an unrecognized recommended action instead
        # of raising a hard ContractValidationError from StepGateResult.
        invalid = True
        degraded = True
    downgrade_reason: str | None = None
    if invalid or degraded:
        # An invalid/degraded gate result must not retain a passing verdict:
        # downstream branching keys on ``verdict`` alone (see run.py), so a
        # malformed/degraded result that still says FULLY_IMPLEMENTED would
        # otherwise approve publication. Downgrade the verdict and force a
        # blocking action so the gate fails closed.
        violations = step_gate_contract_violations(payload)
        detail = (
            "; ".join(violations)
            if violations
            else "gate payload failed contract validation"
        )
        if verdict_raw != "NO_DETERMINATION":
            downgrade_reason = (
                f"declared verdict {verdict_raw} was downgraded to "
                f"NO_DETERMINATION because the structured gate payload failed "
                f"contract validation: {detail}"
            )
        else:
            downgrade_reason = (
                f"structured gate payload failed contract validation: {detail}"
            )
        verdict_raw = "NO_DETERMINATION"
        recommended_next_action = "blocked"
    elif not recommended_next_action and verdict_raw == "FULLY_IMPLEMENTED":
        recommended_next_action = "advance"
    elif not recommended_next_action and verdict_raw == "ADDITIONAL_WORK_NEEDED":
        recommended_next_action = "reattempt_current_step"
    elif not recommended_next_action and verdict_raw in {
        "BLOCKED",
        "FAILED_UNRECOVERABLE",
    }:
        recommended_next_action = "blocked"

    return StepGateResult(
        verdict=verdict_raw,
        confidence=confidence,
        feedback=feedback,
        issues=issues,
        validated_refs=dict(validated_refs)
        if isinstance(validated_refs, Mapping)
        else None,
        invalidated_refs=tuple(
            str(ref).strip()
            for ref in invalidated_refs
            if isinstance(ref, str) and ref.strip()
        )
        if isinstance(invalidated_refs, list)
        else (),
        remaining_work_ref=_optional_text(
            payload.get("remainingWorkRef") or payload.get("remaining_work_ref")
        ),
        blocking_evidence_refs=tuple(
            str(ref).strip()
            for ref in blocking_refs
            if isinstance(ref, str) and ref.strip()
        )
        if isinstance(blocking_refs, list)
        else (),
        recommended_next_action=recommended_next_action,
        target_logical_step_id=_optional_text(
            payload.get("targetLogicalStepId") or payload.get("target_logical_step_id")
        ),
        workspace_policy_recommendation=_optional_text(
            payload.get("workspacePolicyRecommendation")
            or payload.get("workspace_policy_recommendation")
        ),
        recoverable_in_current_runtime=bool(
            payload.get("recoverableInCurrentRuntime")
            or payload.get("recoverable_in_current_runtime")
        ),
        invalid=invalid,
        degraded=degraded,
        downgrade_reason=downgrade_reason,
    )


def _optional_text(value: Any) -> str | None:
    if isinstance(value, str):
        return value.strip() or None
    return None

def build_feedback_input(
    original_inputs: Mapping[str, Any],
    attempt: int,
    feedback: str,
    issues: tuple[Mapping[str, Any], ...] = (),
) -> dict[str, Any]:
    """Inject ``_review_feedback`` into skill step inputs for retry."""
    merged = dict(original_inputs)
    merged["_review_feedback"] = {
        "attempt": attempt,
        "feedback": feedback,
        "issues": [dict(issue) for issue in issues],
    }
    return merged

def build_feedback_instruction(
    original_instruction: str,
    attempt: int,
    feedback: str,
) -> str:
    """Append review feedback to an agent_runtime instruction string."""
    feedback_block = (
        f"\n\n---\n"
        f"REVIEW FEEDBACK (attempt {attempt}): "
        f"The previous execution did not fully succeed.\n"
        f"{feedback}\n"
        f"Please address the above issues in this attempt."
    )
    return original_instruction + feedback_block

_REVIEW_PROMPT_TEMPLATE = """\
You are a code review agent for MoonMind. Your job is to evaluate whether a \
workflow step achieved its intended outcome.

## Step Information
- Tool: {tool_name}
- Step {step_index} of {total_steps} in plan "{plan_title}"

## Step Inputs (what the step was asked to do)
{json_inputs}

## Step Outputs (what the step produced)
{json_result}

## Previous Feedback (if retrying)
{previous_feedback}

## Your Task
Evaluate whether the step output satisfies the aims described in the inputs.

Respond with JSON:
{{
  "verdict": "FULLY_IMPLEMENTED" | "ADDITIONAL_WORK_NEEDED" | "NO_DETERMINATION" | "BLOCKED" | "FAILED_UNRECOVERABLE",
  "confidence": "low" | "medium" | "high",
  "feedback": "<explanation when more work, no determination, blocked, or failed>",
  "issues": [{{"severity": "error|warning", "description": "...", "evidence": "..."}}],
  "validatedRefs": {{"diffRef": "<artifact ref>", "testReportRef": "<artifact ref>"}},
  "invalidatedRefs": [],
  "remainingWorkRef": "<artifact ref if available>",
  "blockingEvidenceRefs": [],
  "recommendedNextAction": "advance | reattempt_current_step | needs_human | blocked",
  "targetLogicalStepId": "<logical step id when reattempting>",
  "workspacePolicyRecommendation": "<workspace policy recommendation>",
  "recoverableInCurrentRuntime": false
}}
"""

def build_review_prompt(request: ReviewRequest) -> str:
    """Construct the LLM review prompt from a ``ReviewRequest``."""

    plan_title = str(
        request.workflow_context.get("plan_title", "Untitled")
    )

    return _REVIEW_PROMPT_TEMPLATE.format(
        tool_name=request.tool_name,
        step_index=request.step_index,
        total_steps=request.total_steps,
        plan_title=plan_title,
        json_inputs=json.dumps(dict(request.inputs), indent=2, default=str),
        json_result=json.dumps(
            dict(request.execution_result), indent=2, default=str
        ),
        previous_feedback=request.previous_feedback or "N/A",
    )

__all__ = [
    "ReviewRequest",
    "ReviewVerdict",
    "StepGateResult",
    "build_feedback_input",
    "build_feedback_instruction",
    "build_review_prompt",
    "parse_step_gate_result",
    "parse_review_verdict",
    "recommended_next_actions",
    "step_gate_contract_violations",
]
