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
from moonmind.utils.logging import redact_sensitive_text
from moonmind.workflows.adapters.github_service import GitHubService
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


def get_github_service() -> GitHubService:
    """Return the authorized GitHub write boundary for decision publication.

    Overridable via FastAPI dependency overrides in hermetic tests so no
    network access is required to exercise the submit path.
    """
    return GitHubService()


def _merged_action_request(payload: OperatorActionRequest) -> dict[str, Any]:
    request = payload.request if isinstance(payload.request, dict) else {}
    return {
        **request,
        "issue_number": request.get("issue_number", payload.issue_number),
    }


def _live_target_mismatch(payload: OperatorActionRequest) -> dict[str, Any] | None:
    """Bind the submitted target to live GitHub evidence before any side effect.

    The request supplies repository/issue_number alongside purportedly live
    evidence; without binding, any authenticated holder could forge live_issue
    and make the deployment credential post to an arbitrary repository.
    """
    live = payload.live_issue if isinstance(payload.live_issue, dict) else None
    if live is None:
        return None
    live_repo = str(live.get("repository") or "").strip()
    if live_repo and live_repo != payload.repository:
        return {"allowed": False, "code": "wrong_issue", "message": "Submitted repository does not match live issue evidence."}
    live_number = live.get("number", live.get("issue_number"))
    if live_number is not None:
        try:
            if int(live_number) != int(payload.issue_number):
                return {"allowed": False, "code": "wrong_issue", "message": "Submitted issue number does not match live issue evidence."}
        except (TypeError, ValueError):
            return {"allowed": False, "code": "wrong_issue", "message": "Submitted issue number does not match live issue evidence."}
    return None


def _decision_field_error(payload: OperatorActionRequest) -> dict[str, Any] | None:
    """Reject invalid decision fields before building the portable record."""
    variant = payload.continue_variant
    if payload.action == "continue_work" and variant is not None and variant not in surface.CONTINUE_VARIANTS:
        return {"allowed": False, "code": "invalid_continue_variant", "message": f"Unknown continue variant '{variant}'."}
    disposition = str(payload.work_disposition or "")
    if payload.action == "abandon_work" and disposition != "abandoned":
        return {"allowed": False, "code": "invalid_disposition", "message": "Abandonment requires an explicit 'abandoned' work disposition."}
    if payload.action == "hold_processing" and disposition == "abandoned":
        return {"allowed": False, "code": "invalid_disposition", "message": "Hold is not abandonment."}
    return None


def _build_allowed_decision(
    *,
    payload: OperatorActionRequest,
    user: User | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Validate (already done) then build the portable handoff + comment."""
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
    return decision, comment


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
    binding = _live_target_mismatch(payload)
    if binding is not None:
        return {"allowed": False, "verdict": binding, "decision": None}
    merged_request = _merged_action_request(payload)
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
    field_error = _decision_field_error(payload)
    if field_error is not None:
        return {"allowed": False, "verdict": field_error, "decision": None}
    try:
        decision, comment = _build_allowed_decision(payload=payload, user=user)
    except ValueError:
        return {"allowed": False, "verdict": {"allowed": False, "code": "invalid_disposition", "message": "Invalid decision fields for this action."}, "decision": None}
    return {"allowed": True, "verdict": verdict, "decision": decision, "comment": comment}


@router.post("/actions/submit")
async def submit_operator_action(
    payload: OperatorActionRequest,
    user: User | None = Depends(get_current_user()),
    github: GitHubService = Depends(get_github_service),
) -> dict[str, Any]:
    """Validate, record, and publish one operator action submission.

    The validated portable-handoff decision is published as a human-readable
    GitHub issue comment through the existing authorized GitHub write
    boundary (``GitHubService.create_issue_comment``), so the decision is
    auditable and visible to another deployment through GitHub -- no second
    result store is added. The GitHub comment is the durable
    cross-deployment record; the same portable handoff is returned for the
    caller to retain in existing workflow results (#4020).

    During a GitHub outage (or when live issue evidence is missing) the
    decision reports pending/unknown rather than a false successful remote
    update. Duplicate idempotency-key replays never repeat effects.
    """
    permissions = _permissions_for_user(user)
    binding = _live_target_mismatch(payload)
    if binding is not None:
        return {"allowed": False, "verdict": binding, "decision": None, "publication": None}
    merged_request = _merged_action_request(payload)
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
        return {"allowed": False, "verdict": verdict, "decision": None, "publication": None}
    field_error = _decision_field_error(payload)
    if field_error is not None:
        return {"allowed": False, "verdict": field_error, "decision": None, "publication": None}
    try:
        decision, comment = _build_allowed_decision(payload=payload, user=user)
    except ValueError:
        return {"allowed": False, "verdict": {"allowed": False, "code": "invalid_disposition", "message": "Invalid decision fields for this action."}, "decision": None, "publication": None}
    if verdict.get("duplicate"):
        return {
            "allowed": True,
            "verdict": verdict,
            "decision": decision,
            "comment": comment,
            "publication": {
                "attempted": False,
                "published": False,
                "status": "duplicate_suppressed",
                "reason": "duplicate_idempotent_replay",
                "detail": "Duplicate submission with a seen idempotency key; original result applies without repeating effects.",
            },
        }
    if not payload.live_issue:
        # Defense in depth: validate() already denies missing live evidence
        # as github_unavailable, so this is unreachable while that check
        # holds. Report pending/unknown rather than a false remote update.
        return {
            "allowed": True,
            "verdict": verdict,
            "decision": decision,
            "comment": comment,
            "publication": {
                "attempted": False,
                "published": False,
                "status": "pending",
                "reason": "github_unavailable",
                "detail": "Live GitHub issue could not be read; decision is recorded locally only and publication is pending/unknown rather than a false remote update.",
            },
        }
    body = redact_sensitive_text(comment)[:7500]
    try:
        created = await github.create_issue_comment(
            repo=payload.repository,
            issue_number=payload.issue_number,
            body=body,
        )
    except Exception as exc:  # noqa: BLE001 - transport failure stays pending
        return {
            "allowed": True,
            "verdict": verdict,
            "decision": decision,
            "comment": comment,
            "publication": {
                "attempted": True,
                "published": False,
                "status": "pending",
                "reason": "outcome_unknown",
                "detail": f"Issue comment create result unknown: {exc.__class__.__name__}; reconcile before repeating effects.",
            },
        }
    if not isinstance(created, dict) or not created.get("ok"):
        reason = str((created or {}).get("reasonCode") or "create_failed") if isinstance(created, dict) else "create_failed"
        if reason == "outcome_unknown":
            return {
                "allowed": True,
                "verdict": verdict,
                "decision": decision,
                "comment": comment,
                "publication": {
                    "attempted": True,
                    "published": False,
                    "status": "pending",
                    "reason": "outcome_unknown",
                    "detail": "Issue comment create result unknown; reconcile the observed result before repeating effects.",
                },
            }
        summary = str((created or {}).get("summary") or "Issue comment create failed.") if isinstance(created, dict) else "Issue comment create failed."
        return {
            "allowed": True,
            "verdict": verdict,
            "decision": decision,
            "comment": comment,
            "publication": {
                "attempted": True,
                "published": False,
                "status": "failed",
                "reason": reason,
                "detail": summary,
            },
        }
    return {
        "allowed": True,
        "verdict": verdict,
        "decision": decision,
        "comment": comment,
        "publication": {
            "attempted": True,
            "published": True,
            "status": "published",
            "reason": "created",
            "commentId": created.get("commentId"),
            "detail": str(created.get("summary") or "Created issue comment."),
        },
    }
