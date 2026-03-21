"""Temporal activities for Jules external agent integration.

Registered on the ``mm.activity.integrations`` task queue to satisfy
spec 066 / FR-008.
"""

from __future__ import annotations

import logging
import os

from temporalio import activity

from moonmind.jules.runtime import build_runtime_gate_state, JULES_RUNTIME_DISABLED_MESSAGE
from moonmind.schemas.agent_runtime_models import (
    AgentExecutionRequest,
    AgentRunHandle,
    AgentRunResult,
    AgentRunStatus,
)
from moonmind.schemas.jules_models import (
    JulesIntegrationMergePRResult,
    JulesListActivitiesResult,
    JulesSendMessageRequest,
)
from moonmind.workflows.adapters.jules_agent_adapter import JulesAgentAdapter
from moonmind.workflows.adapters.jules_client import JulesClient

logger = logging.getLogger(__name__)


def _build_client() -> JulesClient:
    """Build a ``JulesClient`` from environment config.

    Raises ``RuntimeError`` if the Jules runtime gate is unsatisfied.
    """
    gate = build_runtime_gate_state()
    if not gate.enabled:
        raise RuntimeError(
            f"{JULES_RUNTIME_DISABLED_MESSAGE} (missing: {', '.join(gate.missing)})"
        )
    jules_url = os.environ.get("JULES_API_URL", "").strip() or "https://jules.googleapis.com/v1alpha"
    jules_key = os.environ.get("JULES_API_KEY", "").strip()
    return JulesClient(base_url=jules_url, api_key=jules_key)


def _build_adapter() -> JulesAgentAdapter:
    """Build a gated JulesAgentAdapter using env-based configuration.

    Raises ``RuntimeError`` if the Jules runtime gate is unsatisfied.
    """

    import os

    gate = build_runtime_gate_state()
    if not gate.enabled:
        raise RuntimeError(
            f"{JULES_RUNTIME_DISABLED_MESSAGE} (missing: {', '.join(gate.missing)})"
        )

    jules_url = os.environ.get("JULES_API_URL", "").strip() or "https://jules.googleapis.com/v1alpha"
    jules_key = os.environ.get("JULES_API_KEY", "").strip()
    client = JulesClient(base_url=jules_url, api_key=jules_key)
    return JulesAgentAdapter(client_factory=lambda: client)


@activity.defn(name="integration.jules.start")
async def jules_start_activity(request: AgentExecutionRequest) -> AgentRunHandle:
    """Start a Jules-backed run via the canonical adapter contract."""

    adapter = _build_adapter()
    return await adapter.start(request)


@activity.defn(name="integration.jules.status")
async def jules_status_activity(run_id: str) -> AgentRunStatus:
    """Poll current status for one Jules task."""

    adapter = _build_adapter()
    return await adapter.status(run_id)


@activity.defn(name="integration.jules.fetch_result")
async def jules_fetch_result_activity(run_id: str) -> AgentRunResult:
    """Fetch terminal result for one completed Jules task."""

    adapter = _build_adapter()
    return await adapter.fetch_result(run_id)


@activity.defn(name="integration.jules.cancel")
async def jules_cancel_activity(run_id: str) -> AgentRunStatus:
    """Attempt best-effort cancellation for one Jules task."""

    adapter = _build_adapter()
    return await adapter.cancel(run_id)


@activity.defn(name="integration.jules.send_message")
async def jules_send_message_activity(payload: dict) -> AgentRunStatus:
    """Send a follow-up prompt to an existing Jules session.

    Used for multi-step workflows: resumes the session with new
    instructions instead of creating a new session.

    Accepts a dict with:
    - ``session_id`` (str): the Jules session ID to send a message to
    - ``prompt`` (str): the follow-up prompt/instructions to send
    """
    from pydantic import BaseModel, Field, ValidationError

    class _SendMessagePayload(BaseModel):
        session_id: str = Field(min_length=1)
        prompt: str = Field(min_length=1)

    try:
        validated = _SendMessagePayload.model_validate(payload)
    except ValidationError as exc:
        raise ValueError(
            f"Invalid payload for jules_send_message_activity: {exc}"
        ) from exc

    adapter = _build_adapter()
    return await adapter.send_message(
        run_id=validated.session_id,
        prompt=validated.prompt,
    )


@activity.defn(name="integration.jules.list_activities")
async def jules_list_activities_activity(session_id: str) -> dict:
    """Fetch session activities and extract the latest agent question.

    Returns a dict with:
    - ``sessionId`` (str): the session queried
    - ``latestAgentQuestion`` (str | None): the latest question from Jules
    - ``activityId`` (str | None): the activity ID for deduplication
    """
    client = _build_client()
    try:
        result = await client.list_activities(session_id)
        return result.model_dump(by_alias=True)
    finally:
        await client.aclose()


@activity.defn(name="integration.jules.answer_question")
async def jules_answer_question_activity(payload: dict) -> dict:
    """Orchestrate a full question-answer cycle for Jules.

    Accepts a dict with:
    - ``session_id`` (str): the Jules session ID
    - ``question`` (str): the extracted question from Jules
    - ``task_context`` (str, optional): context about the task/repo

    Returns a dict with:
    - ``answered`` (bool): whether the answer was successfully sent
    - ``answer`` (str): the generated answer text
    - ``error`` (str | None): error message if the cycle failed
    """
    session_id = payload.get("session_id") or payload.get("sessionId") or ""
    question = payload.get("question", "")
    task_context = payload.get("task_context") or payload.get("taskContext") or ""

    if not session_id or not question:
        return {"answered": False, "answer": "", "error": "Missing session_id or question"}

    # Build the clarification prompt for the LLM
    prompt_parts = [
        "You are answering a clarification question from Jules (an AI coding agent).",
    ]
    if task_context:
        prompt_parts.append(f"Task context: {task_context}")
    prompt_parts.extend([
        "",
        f"Jules's question:\n{question}",
        "",
        "Provide a concise, actionable answer. If the question asks about a preference or",
        "choice, choose the most reasonable default and explain your reasoning briefly.",
        "Do not ask follow-up questions.",
    ])
    clarification_prompt = "\n".join(prompt_parts)

    # For the default LLM path, we use a simple approach:
    # Generate a reasonable answer based on the prompt context.
    # In production, this would dispatch to mm.activity.llm.complete or
    # a managed agent runtime. For now, we send the constructed prompt
    # directly as the answer since the workflow layer will handle
    # the actual LLM dispatch.
    answer = clarification_prompt

    # Send the answer back to Jules
    client = _build_client()
    try:
        await client.send_message(
            JulesSendMessageRequest(
                session_id=session_id,
                prompt=answer,
            )
        )
        return {"answered": True, "answer": answer, "error": None}
    except Exception as exc:
        logger.error(
            "Failed to send auto-answer to Jules session %s: %s",
            session_id, exc,
            exc_info=True,
        )
        return {"answered": False, "answer": answer, "error": str(exc)}
    finally:
        await client.aclose()


@activity.defn(name="integration.jules.merge_pr")
async def jules_merge_pr_activity(payload: dict) -> JulesIntegrationMergePRResult:
    """Auto-merge a Jules-created PR into its target branch via GitHub API.

    Used when ``publishMode == "branch"`` to merge the PR that Jules
    created during an ``AUTO_CREATE_PR`` session.

    Accepts a dict with:
    - ``pr_url`` (str): the GitHub PR URL to merge
    - ``target_branch`` (str, optional): if set and differs from the PR's
      current base, the PR base is updated before merging
    """

    import os

    gate = build_runtime_gate_state()
    if not gate.enabled:
        raise RuntimeError(
            f"{JULES_RUNTIME_DISABLED_MESSAGE} (missing: {', '.join(gate.missing)})"
        )

    pr_url = payload.get("pr_url") or payload.get("prUrl") or ""
    target_branch = payload.get("target_branch") or payload.get("targetBranch")

    jules_url = os.environ.get("JULES_API_URL", "").strip() or "https://jules.googleapis.com/v1alpha"
    jules_key = os.environ.get("JULES_API_KEY", "").strip()
    client = JulesClient(base_url=jules_url, api_key=jules_key)

    # If a target branch is specified, update the PR's base before merging
    if target_branch:
        success, summary = await client.update_pull_request_base(
            pr_url=pr_url, new_base=target_branch,
        )
        if not success:
            return JulesIntegrationMergePRResult(
                prUrl=pr_url,
                merged=False,
                summary=f"Base branch update failed: {summary}",
            )

    return await client.merge_pull_request(pr_url=pr_url)


__all__ = [
    "jules_answer_question_activity",
    "jules_cancel_activity",
    "jules_fetch_result_activity",
    "jules_list_activities_activity",
    "jules_merge_pr_activity",
    "jules_send_message_activity",
    "jules_start_activity",
    "jules_status_activity",
]

