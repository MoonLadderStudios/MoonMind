"""Review gate contracts and utilities.

Data models for the step review gate system:
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
    confidence: float = 0.0
    feedback: str | None = None
    issues: tuple[Mapping[str, Any], ...] = ()

    def __post_init__(self) -> None:
        if self.verdict not in REVIEW_VERDICTS:
            raise ContractValidationError(
                "invalid_review_verdict",
                f"verdict must be one of {sorted(REVIEW_VERDICTS)}",
            )
        if not (0.0 <= self.confidence <= 1.0):
            raise ContractValidationError(
                "invalid_review_verdict",
                "confidence must be between 0.0 and 1.0",
            )

    def to_payload(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "confidence": self.confidence,
            "feedback": self.feedback,
            "issues": [dict(issue) for issue in self.issues],
        }


def parse_review_verdict(payload: Mapping[str, Any]) -> ReviewVerdict:
    """Parse an LLM response payload into a ``ReviewVerdict``."""
    verdict_raw = str(payload.get("verdict") or "INCONCLUSIVE").strip().upper()
    if verdict_raw not in REVIEW_VERDICTS:
        verdict_raw = "INCONCLUSIVE"

    confidence_raw = payload.get("confidence")
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

    return ReviewVerdict(
        verdict=verdict_raw,
        confidence=confidence,
        feedback=feedback,
        issues=issues,
    )


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
  "verdict": "PASS" | "FAIL" | "INCONCLUSIVE",
  "confidence": <0.0-1.0>,
  "feedback": "<explanation if FAIL>",
  "issues": [{{"severity": "error|warning", "description": "...", "evidence": "..."}}]
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
