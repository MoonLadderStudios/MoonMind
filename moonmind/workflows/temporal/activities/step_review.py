"""Step review Temporal Activity.

Evaluates a workflow step's output against its input aims using an LLM.
Registered as ``step.review`` in the activity catalog.
"""

from __future__ import annotations

import logging
from typing import Any, Mapping

from moonmind.workflows.skills.approval_policy import (
    ReviewRequest,
    parse_review_verdict,
)

logger = logging.getLogger(__name__)


async def step_review_activity(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Execute a step review via LLM.

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
        tool_version=str(payload.get("tool_version") or ""),
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

    # TODO: Wire LLM call using build_review_prompt(request) once
    # the mm.activity.llm fleet integration is available.
    logger.info(
        "step.review: node=%s attempt=%d tool=%s",
        request.node_id,
        request.review_attempt,
        request.tool_name,
    )

    # ----- LLM call placeholder -----
    # In production, this calls the LLM fleet via mm.activity.llm or an
    # equivalent routing mechanism.  For now we return PASS so the gate
    # is non-blocking until the LLM integration is wired.
    verdict = parse_review_verdict({
        "verdict": "PASS",
        "confidence": 1.0,
        "feedback": None,
        "issues": [],
    })

    return verdict.to_payload()


__all__ = ["step_review_activity"]
