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

@dataclass(frozen=True, slots=True)
class ReviewRequest:
    """Input envelope for the ``step.review`` activity."""

    node_id: str
    step_index: int
    total_steps: int
    review_attempt: int
    tool_name: str
    tool_version: str
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
            "tool_version": self.tool_version,
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

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "verdict": self.verdict,
            "confidence": self.confidence,
            "feedback": self.feedback,
            "issues": [dict(issue) for issue in self.issues],
            "recoverableInCurrentRuntime": self.recoverable_in_current_runtime,
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
        return payload

def parse_review_verdict(payload: Mapping[str, Any]) -> ReviewVerdict:
    """Parse an LLM response payload into a ``ReviewVerdict``."""
    verdict_raw = str(payload.get("verdict") or "INCONCLUSIVE").strip().upper()
    verdict_raw = {
        "PASS": "FULLY_IMPLEMENTED",
        "FAIL": "ADDITIONAL_WORK_NEEDED",
        "INCONCLUSIVE": "NO_DETERMINATION",
    }.get(verdict_raw, verdict_raw)
    if verdict_raw not in REVIEW_VERDICTS:
        verdict_raw = "NO_DETERMINATION"

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

    return ReviewVerdict(
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
        recommended_next_action=_optional_text(
            payload.get("recommendedNextAction")
            or payload.get("recommended_next_action")
        ),
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
- Tool: {tool_name} v{tool_version}
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
        tool_version=request.tool_version,
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
    "build_feedback_input",
    "build_feedback_instruction",
    "build_review_prompt",
    "parse_review_verdict",
]
