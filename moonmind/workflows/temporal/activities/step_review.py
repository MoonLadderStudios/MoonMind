"""Step review Temporal Activity.

Reports whether review evidence is available for a workflow step.
Registered as ``step.review`` in the activity catalog.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Mapping

from moonmind.workflows.skills.approval_policy import (
    ReviewRequest,
    ReviewVerdict,
    build_review_prompt,
    parse_step_gate_result,
)

logger = logging.getLogger(__name__)

async def step_review_activity(payload: Mapping[str, Any], *, reviewer: Any = None) -> dict[str, Any]:
    """Execute the configured reviewer and fail closed on unavailable evidence."""
    if reviewer is None:
        return _unavailable("reviewer_unavailable", "no reviewer implementation is configured")
    try:
        request = ReviewRequest(
            node_id=str(payload.get("node_id") or ""),
            step_index=int(payload.get("step_index") or 1),
            total_steps=int(payload.get("total_steps") or 1),
            review_attempt=int(payload.get("review_attempt") or 1),
            tool_name=str(payload.get("tool_name") or ""),
            tool_type=str(payload.get("tool_type") or "skill"),
            inputs=payload.get("inputs") if isinstance(payload.get("inputs"), dict) else {},
            execution_result=(
                payload.get("execution_result")
                if isinstance(payload.get("execution_result"), dict)
                else {}
            ),
            workflow_context=(
                payload.get("workflow_context")
                if isinstance(payload.get("workflow_context"), dict)
                else {}
            ),
            reviewer_model=str(payload.get("reviewer_model", "default")),
            review_timeout_seconds=int(payload.get("review_timeout_seconds", 120)),
            previous_feedback=(
                str(payload["previous_feedback"])
                if payload.get("previous_feedback") is not None
                else None
            ),
        )
        if request.review_timeout_seconds <= 0:
            raise ValueError("review timeout must be positive")
        prompt = build_review_prompt(request)
        if len(prompt.encode("utf-8")) > 256_000:
            return _unavailable("review_evidence_too_large", "review input exceeds the bounded evidence budget")
        async with asyncio.timeout(request.review_timeout_seconds):
            text = await reviewer.review(
                prompt=prompt, model=request.reviewer_model,
                timeout=request.review_timeout_seconds,
            )
        decoded = json.loads(text)
        if not isinstance(decoded, dict):
            raise ValueError("reviewer result must be an object")
        gate = parse_step_gate_result(decoded)
        return gate.to_payload()
    except (TimeoutError, asyncio.TimeoutError):
        return _unavailable("reviewer_timeout", "configured reviewer timed out")
    except Exception:
        # Provider exception strings can contain request bodies and credentials.
        # Do not persist or log them in a workflow outcome.
        return _unavailable("reviewer_unavailable", "configured reviewer failed or returned malformed evidence")


def _unavailable(code: str, reason: str) -> dict[str, Any]:
    return ReviewVerdict(
        verdict="NO_DETERMINATION", confidence=0.0,
        feedback=f"Review unavailable: {reason}. Preserve completed outputs and obtain review evidence before advancement.",
        issues=({"severity": "warning", "description": reason, "code": code},),
        recommended_next_action="needs_human", recoverable_in_current_runtime=False,
    ).to_payload()


__all__ = ["step_review_activity"]
