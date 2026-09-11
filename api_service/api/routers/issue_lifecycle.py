"""GitHub issue recovery-status projection and operator-action API (#4183).

Extends existing workflow results (owned by #4020 and the execution
lifecycle APIs) with a bounded, server-derived issue-lifecycle
projection for Workflow Detail plus server-side revalidation of the
supported operator actions. Adds no second result store and no separate
recovery dashboard authority: the request supplies already-validated
GitHub evidence and local execution facts, and the server derives
attention, availability, and action eligibility from them.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from api_service.auth_providers import get_current_user
from api_service.db.models import User
from moonmind.workflows.temporal import github_issue_recovery_surface as surface

router = APIRouter(prefix="/api/v1/executions/issue-lifecycle", tags=["issue-lifecycle"])

OperatorActionName = Literal[
    "continue_work",
    "hold_processing",
    "acknowledge_incident",
    "resolve_conflict",
    "authorize_retry",
    "abandon_work",
]


class LifecycleContextRequest(BaseModel):
    repository: str = Field(..., description="GitHub repository e.g. 'o/r'.")
    issue_number: int = Field(..., description="GitHub issue number.")
    issue: dict[str, Any] | None = None
    current_attempt: dict[str, Any] | None = None
    predecessor_attempts: list[dict[str, Any]] = Field(default_factory=list)
    deployment_id: str | None = None
    preserved_pr: dict[str, Any] | None = None
    retry_state: dict[str, Any] | None = None
    operator_hold: dict[str, Any] | None = None
    sync_state: dict[str, Any] | None = None
    local_facts: dict[str, Any] | None = None


class OperatorActionRequest(BaseModel):
    action: OperatorActionName
    repository: str
    issue_number: int
    request: dict[str, Any] = Field(default_factory=dict)
    live_issue: dict[str, Any] | None = None
    live_attempt: dict[str, Any] | None = None
    live_pr: dict[str, Any] | None = None
    seen_idempotency_keys: list[str] = Field(default_factory=list)
    stop_proof: dict[str, Any] | None = None
    retry_policy: dict[str, Any] | None = None
    # Audit fields recorded into the portable handoff on success.
    reason: str = ""
    work_disposition: str | None = None
    retry_budget_reset: bool = False
    continue_variant: str | None = None


def _permissions_for_user(user: User | None) -> dict[str, bool]:
    is_admin = bool(user is not None and getattr(user, "is_superuser", False))
    return {
        "can_continue": is_admin,
        "can_hold": user is not None,
        "can_retry_reset": is_admin,
        "can_abandon": is_admin,
        "can_resolve": is_admin,
    }


@router.post("/context")
async def project_lifecycle_context(
    payload: LifecycleContextRequest,
    user: User | None = Depends(get_current_user()),
) -> dict[str, Any]:
    """Derive the bounded issue-lifecycle projection for Workflow Detail."""
    context = surface.build_issue_lifecycle_context(
        repository=payload.repository,
        issue_number=payload.issue_number,
        issue=payload.issue,
        current_attempt=payload.current_attempt,
        predecessor_attempts=payload.predecessor_attempts,
        deployment_id=payload.deployment_id,
        preserved_pr=payload.preserved_pr,
        retry_state=payload.retry_state,
        operator_hold=payload.operator_hold,
        sync_state=payload.sync_state,
        local_facts=payload.local_facts,
    )
    permissions = _permissions_for_user(user)
    return {
        "context": context,
        "attention_category": context.get("attention_category"),
        "recovery_availability": context.get("recovery_availability"),
        "actions": surface.available_actions(context, permissions=permissions),
        "continue_variants": list(surface.CONTINUE_VARIANTS),
    }


@router.post("/actions/validate")
async def validate_operator_action(
    payload: OperatorActionRequest,
    user: User | None = Depends(get_current_user()),
) -> dict[str, Any]:
    """Server-side revalidation of one operator action submission.

    Stale UI eligibility never grants a release: permission, staleness,
    duplicate, wrong-issue, conflicting-PR, unknown-writer, stop-proof,
    publication-intent, and retry-policy checks run on the server. On
    success an auditable portable-handoff record is returned (to be
    published through the existing authorized GitHub write boundary by
    the caller); during a GitHub outage the decision reports
    pending/unknown rather than a false successful remote update.
    """
    permissions = _permissions_for_user(user)
    merged_request = {
        **(payload.request or {}),
        "issue_number": payload.request.get("issue_number", payload.issue_number)
        if isinstance(payload.request, dict)
        else payload.issue_number,
    }
    verdict = surface.validate_operator_action(
        action=payload.action,
        request=merged_request,
        live_issue=payload.live_issue,
        live_attempt=payload.live_attempt,
        live_pr=payload.live_pr,
        permissions=permissions,
        seen_idempotency_keys=payload.seen_idempotency_keys,
        stop_proof=payload.stop_proof,
        retry_policy=payload.retry_policy,
    )
    if not verdict.get("allowed"):
        return {"allowed": False, "verdict": verdict, "decision": None}
    operator_identity = {
        "id": getattr(user, "id", None) or getattr(user, "email", None) or "authenticated-operator",
    }
    live_issue = payload.live_issue or {}
    previous_state = str(
        (live_issue.get("settled_lifecycle_state") if isinstance(live_issue, dict) else "")
        or (payload.live_attempt or {}).get("recovery_phase", "")
        or "unknown"
    )
    decision = surface.record_operator_decision(
        action=payload.action,
        operator=operator_identity,
        previous_state=previous_state,
        reason=payload.reason,
        work_disposition=payload.work_disposition,
        retry_budget_reset=payload.retry_budget_reset,
        continue_variant=payload.continue_variant,
        competing_refs=((payload.live_attempt or {}).get("competing_prs") or []),
    )
    comment = surface.render_next_action_comment(
        context=surface.build_issue_lifecycle_context(
            repository=payload.repository,
            issue_number=payload.issue_number,
            issue=payload.live_issue,
            current_attempt=payload.live_attempt,
            preserved_pr=payload.live_pr,
            sync_state={"github_unavailable": False} if payload.live_issue else {"github_unavailable": True},
        ),
        decision=decision,
    )
    return {"allowed": True, "verdict": verdict, "decision": decision, "comment": comment}
