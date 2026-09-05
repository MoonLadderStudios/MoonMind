"""Step review Temporal Activity.

Reports whether review evidence is available for a workflow step.
Registered as ``step.review`` in the activity catalog.
"""

from __future__ import annotations

import logging
from typing import Any, Mapping

from moonmind.workflows.skills.approval_policy import (
    ReviewRequest,
    ReviewVerdict,
)

logger = logging.getLogger(__name__)

async def step_review_activity(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Return an explicit unavailable outcome until a reviewer is configured.

    A successful step execution is not review evidence. The worker has no
    reviewer implementation bound to this activity, so it cannot approve a gate
    or infer that reexecuting the completed step will repair the missing review.

    Parameters
    ----------
    payload:
        Serialized ``ReviewRequest`` dict.

    Returns
    -------
    dict
        Serialized ``ReviewVerdict`` dict.
    """
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
        previous_feedback=(
            str(payload["previous_feedback"])
            if payload.get("previous_feedback") is not None
            else None
        ),
    )

    logger.info(
        "step.review unavailable: node=%s attempt=%d tool=%s",
        request.node_id,
        request.review_attempt,
        request.tool_name,
    )

    verdict = ReviewVerdict(
        verdict="NO_DETERMINATION",
        confidence=0.0,
        feedback=(
            "Review unavailable: no reviewer implementation is configured for "
            "step.review. Preserve the completed step's outputs and obtain "
            "review evidence before approving advancement."
        ),
        issues=({
            "severity": "warning",
            "description": "Reviewer unavailable; no review was performed.",
            "code": "reviewer_unavailable",
        },),
        recommended_next_action="needs_human",
        recoverable_in_current_runtime=False,
    )

    return verdict.to_payload()

__all__ = ["step_review_activity"]
