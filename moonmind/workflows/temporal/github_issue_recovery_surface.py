"""Server-derived GitHub issue recovery projection and safe operator actions.

Implements the Workflow Detail / issue-centric surface for
MoonLadderStudios/MoonMind#4183 (design:
docs/Workflows/GitHubIssueStatusStateMachineDesign.md, section 9).

Scope contract (extends, never replaces, existing stores):

* Consumes validated GitHub evidence and local execution facts produced by
  the dependency issues (#4176 labels, #4177 attempt handoffs,
  #4178 admission, #4179 finalization, #4180 continuation routing,
  #4182 reconciliation). This module adds no second result store, no
  separate recovery dashboard authority, and no React lifecycle state
  engine -- it projects a bounded, server-derived context for display.
* UI state is a projection of validated GitHub evidence plus local
  execution facts, never a cross-device ownership database. A missing
  local workflow record says nothing about a remote owner's execution.
* Coordinates with #4020: workflow results remain authoritative; this
  module only derives lineage, attention, and action availability from
  them.

Deterministic and side-effect-free: no network I/O. Trusted
Activity/service boundaries perform GitHub reads/writes; this module
decides what the evidence means, which attention category applies, which
actions are available or blocked (and why), and whether a submitted
operator action passes server-side revalidation.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from moonmind.workflows.temporal import github_issue_lifecycle as lifecycle

# ---------------------------------------------------------------------------
# Canonical vocabularies (issue #4183 required work items 2, 3, 6)
# ---------------------------------------------------------------------------

#: Persistent needs-attention categories. A remote workflow that is merely
#: missing locally must never be labelled a confirmed local failure.
ATTENTION_CONFIRMED_LOCAL_FAILURE = "confirmed_local_failure"
ATTENTION_UNRESPONSIVE_REMOTE_OWNER = "unresponsive_remote_owner"
ATTENTION_CANCELLATION_OR_HOLD = "deliberate_cancellation_hold"
ATTENTION_EXHAUSTED_RETRIES = "exhausted_retries"
ATTENTION_UNKNOWN_PUBLICATION = "unknown_publication_result"
ATTENTION_PRIVATE_ONLY_WORK = "private_only_saved_work"

ATTENTION_CATEGORIES = (
    ATTENTION_CONFIRMED_LOCAL_FAILURE,
    ATTENTION_UNRESPONSIVE_REMOTE_OWNER,
    ATTENTION_CANCELLATION_OR_HOLD,
    ATTENTION_EXHAUSTED_RETRIES,
    ATTENTION_UNKNOWN_PUBLICATION,
    ATTENTION_PRIVATE_ONLY_WORK,
)

ATTENTION_CATEGORY_LABELS = {
    ATTENTION_CONFIRMED_LOCAL_FAILURE: "Confirmed local failure",
    ATTENTION_UNRESPONSIVE_REMOTE_OWNER: "Unresponsive remote owner",
    ATTENTION_CANCELLATION_OR_HOLD: "Deliberate cancellation / hold",
    ATTENTION_EXHAUSTED_RETRIES: "Exhausted retries",
    ATTENTION_UNKNOWN_PUBLICATION: "Unknown publication result",
    ATTENTION_PRIVATE_ONLY_WORK: "Private-only saved work",
}

#: Supported operator actions, offered through existing authorized
#: execution/control APIs (required work item 3).
ACTION_CONTINUE = "continue_work"
ACTION_HOLD = "hold_processing"
ACTION_ACKNOWLEDGE = "acknowledge_incident"
ACTION_RESOLVE_CONFLICT = "resolve_conflict"
ACTION_AUTHORIZE_RETRY = "authorize_retry"
ACTION_ABANDON = "abandon_work"

OPERATOR_ACTIONS = (
    ACTION_CONTINUE,
    ACTION_HOLD,
    ACTION_ACKNOWLEDGE,
    ACTION_RESOLVE_CONFLICT,
    ACTION_AUTHORIZE_RETRY,
    ACTION_ABANDON,
)

#: Continue variants derived from #4180 (required work item 6). Never
#: silently downgrade to a fresh source retry.
CONTINUE_EXACT_RESUME = "exact_checkpoint_resume"
CONTINUE_CODE_SEEDED = "code_seeded_continuation"
CONTINUE_VERIFY_ONLY = "verification_only"
CONTINUE_PUBLISH_FINALIZE = "publication_status_finalization"

CONTINUE_VARIANTS = (
    CONTINUE_EXACT_RESUME,
    CONTINUE_CODE_SEEDED,
    CONTINUE_VERIFY_ONLY,
    CONTINUE_PUBLISH_FINALIZE,
)

CONTINUE_VARIANT_LABELS = {
    CONTINUE_EXACT_RESUME: "Exact checkpoint resume",
    CONTINUE_CODE_SEEDED: "Code-seeded continuation",
    CONTINUE_VERIFY_ONLY: "Verification only",
    CONTINUE_PUBLISH_FINALIZE: "Publication / status finalization",
}

#: Sync states for the GitHub-visible projection (required work item 7).
SYNC_FRESH = "fresh"
SYNC_PENDING = "pending"
SYNC_UNKNOWN = "unknown"
SYNC_STALE = "stale"


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _string(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def _truthy(value: Any) -> bool:
    if value is True:
        return True
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes"}
    return False


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _issue_url(repository: str, issue_number: int) -> str:
    repository = _string(repository).strip("/")
    return f"https://github.com/{repository}/issues/{int(issue_number)}"


# ---------------------------------------------------------------------------
# Projection (required work item 1)
# ---------------------------------------------------------------------------


def build_issue_lifecycle_context(
    *,
    repository: str,
    issue_number: int,
    issue: Mapping[str, Any] | None = None,
    current_attempt: Mapping[str, Any] | None = None,
    predecessor_attempts: Sequence[Mapping[str, Any]] | None = None,
    deployment_id: str | None = None,
    preserved_pr: Mapping[str, Any] | None = None,
    retry_state: Mapping[str, Any] | None = None,
    operator_hold: Mapping[str, Any] | None = None,
    sync_state: Mapping[str, Any] | None = None,
    local_facts: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the bounded server-derived lifecycle projection for one issue.

    All inputs are already-validated GitHub evidence (issue, attempt
    handoffs, PR objects) plus local execution facts. Unknown / missing
    evidence is reported as ``unknown`` -- never inferred as available.
    """
    issue_map = _mapping(issue)
    current = _mapping(current_attempt)
    predecessors = [dict(p) for p in (predecessor_attempts or []) if isinstance(p, Mapping)]
    pr = _mapping(preserved_pr)
    retry = _mapping(retry_state)
    hold = _mapping(operator_hold)
    sync = _mapping(sync_state)
    local = _mapping(local_facts)

    interpretation = lifecycle.interpret_issue(dict(issue_map) if issue_map else {})
    settled = interpretation.settled
    issue_state = _string(issue_map.get("state") or issue_map.get("github_state") or "open") or "open"

    github_outage = _truthy(sync.get("github_unavailable")) or _truthy(sync.get("github_outage"))
    pending_sync = bool(sync.get("pending_effects")) or _truthy(sync.get("has_pending_effects"))
    if github_outage:
        sync_status = SYNC_UNKNOWN
    elif pending_sync:
        sync_status = SYNC_PENDING
    elif _truthy(sync.get("stale")):
        sync_status = SYNC_STALE
    else:
        sync_status = SYNC_FRESH

    evidence_freshness = SYNC_UNKNOWN if github_outage else _string(sync.get("evidence_freshness") or sync_status) or sync_status
    evidence_completeness = _string(sync.get("evidence_completeness") or ("partial" if pending_sync or github_outage else "complete"))

    remaining_requirements = list(current.get("remaining_requirements") or current.get("remainingRequirements") or [])
    if not remaining_requirements and isinstance(current.get("result"), Mapping):
        remaining_requirements = list(current["result"].get("unmet_requirements") or [])

    prior_failures = [dict(p) for p in predecessors if _string(p.get("result") or p.get("outcome")) in {"failed", "failure", "error", "exhausted", "cancelled", "canceled"} or _truthy(p.get("failed"))]
    # A later launch must not erase prior failure evidence: surface every
    # predecessor failure even when the current attempt is active.
    failure_history = [
        {
            "attempt_id": _string(p.get("attempt_id") or p.get("attemptId")),
            "result": _string(p.get("result") or p.get("outcome") or "unknown"),
            "summary": _string(p.get("verification_summary") or p.get("summary"))[:500],
        }
        for p in predecessors
    ]

    recovery_phase = _string(current.get("recovery_phase") or current.get("recoveryPhase") or local.get("recovery_phase"))
    if not recovery_phase:
        if settled == lifecycle.SETTLED_RECOVERY_NEEDED:
            recovery_phase = "continuation_ready"
        elif settled == lifecycle.SETTLED_NEEDS_ATTENTION:
            recovery_phase = "attention_required"
        elif settled == lifecycle.SETTLED_CODE_REVIEW:
            recovery_phase = "awaiting_review"
        elif settled == lifecycle.SETTLED_IN_PROGRESS:
            recovery_phase = "attempt_active"
        else:
            recovery_phase = "none"

    retry_allowance = retry.get("remaining") if "remaining" in retry else retry.get("remaining_allowance")
    retry_cooldown = _string(retry.get("cooldown_until") or retry.get("cooldownUntil"))
    retry_blocked = _truthy(retry.get("blocked"))
    retry_block_reason = _string(retry.get("block_reason") or retry.get("blockReason"))

    on_hold = _truthy(hold.get("active")) or settled == lifecycle.SETTLED_NEEDS_ATTENTION and _truthy(hold.get("hold_intent"))
    acknowledged = _truthy(hold.get("acknowledged")) or _truthy(current.get("acknowledged"))

    competing_refs = list(current.get("competing_prs") or current.get("competingPrs") or [])
    for p in predecessors:
        for ref in p.get("competing_prs") or p.get("competingPrs") or []:
            if ref not in competing_refs:
                competing_refs.append(ref)

    context: dict[str, Any] = {
        "schema": "moonmind.github_issue_recovery_surface.v1",
        "issue_ref": f"{_string(repository) or _string(issue_map.get('repository'))}#{int(issue_number)}",
        "issue_url": _string(issue_map.get("html_url") or issue_map.get("url")) or _issue_url(repository or _string(issue_map.get("repository")), issue_number),
        "issue_state": issue_state,
        "settled_lifecycle_state": settled,
        "current_attempt": dict(current) if current else None,
        "predecessor_attempts": predecessors,
        "originating_deployment_id": _string(current.get("deployment_id") or current.get("deploymentId") or deployment_id),
        "preserved_pr": dict(pr) if pr else None,
        "preserved_revision": _string(pr.get("head_sha") or pr.get("headSha") or current.get("preserved_head_sha")),
        "remaining_requirements": remaining_requirements,
        "recovery_phase": recovery_phase,
        "retry_allowance_remaining": retry_allowance,
        "retry_cooldown_until": retry_cooldown,
        "retry_blocked": retry_blocked,
        "retry_block_reason": retry_block_reason,
        "operator_hold": {
            "active": on_hold,
            "acknowledged": acknowledged,
            "reason": _string(hold.get("reason")),
            "recorded_by": _string(hold.get("recorded_by") or hold.get("recordedBy")),
        },
        "evidence_freshness": evidence_freshness,
        "evidence_completeness": evidence_completeness,
        "sync_status": sync_status,
        "pending_sync_errors": list(sync.get("pending_errors") or sync.get("pendingErrors") or []),
        "failure_history": failure_history,
        "prior_failure_count": len(prior_failures),
        "competing_refs_preserved": competing_refs,
        "local_execution": dict(local) if local else {},
        "projection_note": "Server-derived projection of validated GitHub evidence and local execution facts; not a cross-device ownership database.",
    }
    context["attention_category"] = classify_attention(context)
    context["recovery_availability"] = describe_recovery_availability(context)
    return context


# ---------------------------------------------------------------------------
# Attention classification (required work item 2)
# ---------------------------------------------------------------------------


def classify_attention(context: Mapping[str, Any]) -> str | None:
    """Return the persistent needs-attention category for *context*.

    Returns ``None`` when no attention is required. Acknowledgment alone
    never clears the category (required work item 5): only an explicit
    resolution does.
    """
    sync_status = _string(context.get("sync_status"))
    local = _mapping(context.get("local_execution"))
    current = _mapping(context.get("current_attempt"))
    hold = _mapping(context.get("operator_hold"))
    pr = _mapping(context.get("preserved_pr"))

    save_method = _string(pr.get("save_method") or current.get("save_method"))
    if save_method in {"local_only", "private_checkpoint", "private-only"}:
        return ATTENTION_PRIVATE_ONLY_WORK
    if _truthy(local.get("has_local_failure")) or _truthy(current.get("local_failure_confirmed")):
        # A confirmed local failure is only reported when this deployment
        # actually observed the failure -- never because a remote workflow
        # is missing locally.
        if _truthy(local.get("observed_locally")) or _truthy(current.get("local_failure_confirmed")):
            return ATTENTION_CONFIRMED_LOCAL_FAILURE
    if sync_status in {SYNC_UNKNOWN, SYNC_PENDING} and _truthy(current.get("publication_unknown") or local.get("publication_unknown")):
        return ATTENTION_UNKNOWN_PUBLICATION
    if _truthy(context.get("retry_blocked")) and "exhaust" in _string(context.get("retry_block_reason")).lower():
        return ATTENTION_EXHAUSTED_RETRIES
    if _truthy(hold.get("active")) or _truthy(current.get("cancelled")) or _truthy(current.get("canceled")):
        return ATTENTION_CANCELLATION_OR_HOLD
    if _truthy(current.get("owner_unresponsive")) or _truthy(local.get("owner_unresponsive")):
        return ATTENTION_UNRESPONSIVE_REMOTE_OWNER
    if _string(context.get("settled_lifecycle_state")) == lifecycle.SETTLED_NEEDS_ATTENTION:
        # Default attention bucket when the lifecycle already requires
        # intervention but no narrower evidence matched.
        if _truthy(hold.get("active")):
            return ATTENTION_CANCELLATION_OR_HOLD
        return ATTENTION_UNRESPONSIVE_REMOTE_OWNER
    return None


def describe_recovery_availability(context: Mapping[str, Any]) -> dict[str, Any]:
    """Explain why recovery is available or blocked for Workflow Detail."""
    settled = _string(context.get("settled_lifecycle_state"))
    sync_status = _string(context.get("sync_status"))
    attention = context.get("attention_category")
    hold = _mapping(context.get("operator_hold"))

    if sync_status == SYNC_UNKNOWN:
        return {"available": False, "reason": "github_unavailable", "detail": "GitHub is unreachable; showing pending/unknown rather than a false remote state. Reread live GitHub state before acting."}
    if _truthy(hold.get("active")):
        return {"available": False, "reason": "operator_hold", "detail": "Operator hold is active. Acknowledgment alone does not release it; resolve the hold explicitly."}
    if attention == ATTENTION_PRIVATE_ONLY_WORK:
        return {"available": False, "reason": "private_only_work", "detail": "Work exists only in another device's private storage. Owner recovery or an authorized portable handoff is required; Resume is not offered."}
    if attention == ATTENTION_UNRESPONSIVE_REMOTE_OWNER:
        return {"available": False, "reason": "unresponsive_owner", "detail": "The owning deployment is unresponsive. Another deployment surfaces attention without releasing on age alone; conclusive stop evidence or an authorized operator resolution is required."}
    if attention == ATTENTION_EXHAUSTED_RETRIES:
        return {"available": False, "reason": "retries_exhausted", "detail": "Automatic retry allowance is exhausted. An explicit operator retry reset is required."}
    if attention == ATTENTION_UNKNOWN_PUBLICATION:
        return {"available": False, "reason": "publication_unknown", "detail": "A prior push/label/PR outcome is unknown (possibly a lost response). Reconcile the observed result before repeating effects."}
    if settled == lifecycle.SETTLED_RECOVERY_NEEDED:
        return {"available": True, "reason": "continuation_ready", "detail": "A safe continuation handoff is available from the preserved work."}
    if settled == lifecycle.SETTLED_AVAILABLE:
        return {"available": True, "reason": "fresh_retry_eligible", "detail": "No blocking lifecycle state; fresh retry is eligible subject to retry policy."}
    if settled == lifecycle.SETTLED_CODE_REVIEW:
        return {"available": False, "reason": "awaiting_review", "detail": "Implementation is published for review; ordinary implementation search does not duplicate it."}
    return {"available": False, "reason": "blocked", "detail": f"Lifecycle state '{settled or 'unknown'}' does not offer automatic recovery."}


# ---------------------------------------------------------------------------
# Action availability (required work item 3 + item 6)
# ---------------------------------------------------------------------------


def available_actions(
    context: Mapping[str, Any],
    *,
    permissions: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """List operator actions with eligibility and blocked reasons.

    ``permissions`` carries already-authorized operator capabilities
    (``can_continue``, ``can_hold``, ``can_retry_reset``, ``can_abandon``,
    ``can_resolve``). Missing capabilities disable the action in the UI,
    but the server revalidates on submission regardless.
    """
    perms = _mapping(permissions)
    availability = describe_recovery_availability(context)
    settled = _string(context.get("settled_lifecycle_state"))
    sync_status = _string(context.get("sync_status"))
    hold = _mapping(context.get("operator_hold"))
    competing = list(context.get("competing_refs_preserved") or [])
    pr = _mapping(context.get("preserved_pr"))
    save_method = _string(pr.get("save_method"))

    def _entry(action: str, *, enabled: bool, reason: str = "", continue_variant: str | None = None) -> dict[str, Any]:
        entry: dict[str, Any] = {"action": action, "enabled": enabled}
        if reason:
            entry["disabled_reason"] = reason
        if continue_variant:
            entry["continue_variant"] = continue_variant
        return entry

    github_down = sync_status == SYNC_UNKNOWN
    actions: list[dict[str, Any]] = []

    # Continue: variant derived from preserved-work evidence (#4180).
    continue_variant = infer_continue_variant(context)
    if save_method in {"local_only", "private_checkpoint", "private-only"}:
        actions.append(_entry(ACTION_CONTINUE, enabled=False, reason="private_only_work"))
    elif github_down:
        actions.append(_entry(ACTION_CONTINUE, enabled=False, reason="github_unavailable"))
    elif not availability.get("available") and settled not in {lifecycle.SETTLED_RECOVERY_NEEDED, lifecycle.SETTLED_AVAILABLE}:
        actions.append(_entry(ACTION_CONTINUE, enabled=False, reason=_string(availability.get("reason")) or "blocked"))
    elif not _truthy(perms.get("can_continue", True)):
        actions.append(_entry(ACTION_CONTINUE, enabled=False, reason="unauthorized"))
    else:
        actions.append(_entry(ACTION_CONTINUE, enabled=True, continue_variant=continue_variant))

    # Hold: always offerable except during outage (cannot record intent).
    if github_down:
        actions.append(_entry(ACTION_HOLD, enabled=False, reason="github_unavailable"))
    elif not _truthy(perms.get("can_hold", True)):
        actions.append(_entry(ACTION_HOLD, enabled=False, reason="unauthorized"))
    else:
        actions.append(_entry(ACTION_HOLD, enabled=True))

    # Acknowledge: never releases the hold (item 5).
    if not _truthy(perms.get("can_hold", True)):
        actions.append(_entry(ACTION_ACKNOWLEDGE, enabled=False, reason="unauthorized"))
    else:
        actions.append(_entry(ACTION_ACKNOWLEDGE, enabled=True))

    # Resolve conflict: only meaningful with competing refs preserved.
    if not competing:
        actions.append(_entry(ACTION_RESOLVE_CONFLICT, enabled=False, reason="no_competing_refs"))
    elif github_down:
        actions.append(_entry(ACTION_RESOLVE_CONFLICT, enabled=False, reason="github_unavailable"))
    elif not _truthy(perms.get("can_resolve", True)):
        actions.append(_entry(ACTION_RESOLVE_CONFLICT, enabled=False, reason="unauthorized"))
    else:
        actions.append(_entry(ACTION_RESOLVE_CONFLICT, enabled=True))

    # Authorize retry: requires exhausted/blocked budget + stop proof path.
    if github_down:
        actions.append(_entry(ACTION_AUTHORIZE_RETRY, enabled=False, reason="github_unavailable"))
    elif not _truthy(perms.get("can_retry_reset", True)):
        actions.append(_entry(ACTION_AUTHORIZE_RETRY, enabled=False, reason="unauthorized"))
    elif not (_truthy(context.get("retry_blocked")) or _truthy(hold.get("active"))):
        actions.append(_entry(ACTION_AUTHORIZE_RETRY, enabled=False, reason="retry_not_blocked"))
    else:
        actions.append(_entry(ACTION_AUTHORIZE_RETRY, enabled=True))

    # Abandon: explicit disposition with audit trail; never implicit.
    if github_down:
        actions.append(_entry(ACTION_ABANDON, enabled=False, reason="github_unavailable"))
    elif not _truthy(perms.get("can_abandon", True)):
        actions.append(_entry(ACTION_ABANDON, enabled=False, reason="unauthorized"))
    else:
        actions.append(_entry(ACTION_ABANDON, enabled=True))

    return actions


def infer_continue_variant(context: Mapping[str, Any]) -> str:
    """Derive Continue's actual behavior from preserved-work evidence."""
    current = _mapping(context.get("current_attempt"))
    pr = _mapping(context.get("preserved_pr"))
    remaining = list(context.get("remaining_requirements") or [])
    next_action = _string(current.get("next_action") or current.get("nextAction")).lower()
    save_method = _string(pr.get("save_method")).lower()
    checkpoint_complete = _truthy(current.get("checkpoint_complete")) or _truthy(pr.get("checkpoint_complete"))

    if "verif" in next_action and not remaining:
        return CONTINUE_VERIFY_ONLY
    if "publish" in next_action or "finaliz" in next_action or "status" in next_action:
        return CONTINUE_PUBLISH_FINALIZE
    if checkpoint_complete and "exact" in _string(current.get("resume_kind")).lower():
        return CONTINUE_EXACT_RESUME
    if save_method in {"pr_head_verified", "pr_head", "branch", "commit"} or _string(pr.get("pr_url") or pr.get("prUrl")):
        return CONTINUE_CODE_SEEDED
    if not remaining:
        return CONTINUE_VERIFY_ONLY
    return CONTINUE_CODE_SEEDED


# ---------------------------------------------------------------------------
# Server-side submission validation (required work item 4)
# ---------------------------------------------------------------------------


def validate_operator_action(
    *,
    action: str,
    request: Mapping[str, Any],
    live_issue: Mapping[str, Any] | None,
    live_attempt: Mapping[str, Any] | None = None,
    live_pr: Mapping[str, Any] | None = None,
    permissions: Mapping[str, Any] | None = None,
    seen_idempotency_keys: Sequence[str] | None = None,
    stop_proof: Mapping[str, Any] | None = None,
    retry_policy: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Revalidate a submitted operator action against live evidence.

    Stale UI eligibility never grants a release: every rejection below is
    enforced by the server, not only by disabled UI controls. Returns
    ``{"allowed": bool, "code": str, "message": str, ...}``.
    """
    perms = _mapping(permissions)
    req = _mapping(request)
    issue = _mapping(live_issue)
    attempt = _mapping(live_attempt)
    pr = _mapping(live_pr)
    stop = _mapping(stop_proof)

    if action not in OPERATOR_ACTIONS:
        return _deny("unknown_action", f"Unknown operator action '{action}'.")
    if not issue:
        return _deny("github_unavailable", "Live GitHub issue could not be read; showing pending/unknown rather than a false remote update.")

    capability_map = {
        ACTION_CONTINUE: "can_continue",
        ACTION_HOLD: "can_hold",
        ACTION_ACKNOWLEDGE: "can_hold",
        ACTION_RESOLVE_CONFLICT: "can_resolve",
        ACTION_AUTHORIZE_RETRY: "can_retry_reset",
        ACTION_ABANDON: "can_abandon",
    }
    if not _truthy(perms.get(capability_map[action], False)):
        return _deny("unauthorized", "Operator lacks permission for this action.")

    idempotency_key = _string(req.get("idempotency_key") or req.get("idempotencyKey"))
    if not idempotency_key:
        return _deny("missing_idempotency_key", "An idempotency key is required; repeated actions use existing durable idempotency semantics.")
    if idempotency_key in set(seen_idempotency_keys or []):
        return {"allowed": True, "code": "duplicate_idempotent_replay", "message": "Duplicate submission with a seen idempotency key; original result applies without repeating effects.", "duplicate": True}

    expected_issue = req.get("issue_number", req.get("issueNumber"))
    live_number = issue.get("number", issue.get("issue_number"))
    if expected_issue is not None and live_number is not None:
        try:
            if int(expected_issue) != int(live_number):
                return _deny("wrong_issue", "Action targets a different issue than the live record.")
        except (TypeError, ValueError):
            return _deny("wrong_issue", "Action targets a different issue than the live record.")

    expected_seq = req.get("attempt_seq", req.get("attemptSeq", req.get("expected_seq")))
    live_seq = attempt.get("seq", attempt.get("attempt_seq", attempt.get("sequence")))
    if expected_seq is not None and live_seq is not None:
        try:
            if int(expected_seq) != int(live_seq):
                return _deny("stale_attempt", "The attempt changed since the UI was rendered; reread live GitHub evidence before acting.")
        except (TypeError, ValueError):
            # Non-numeric sequence values cannot prove staleness; skip the check.
            pass

    writer_id = _string(attempt.get("attempt_id") or attempt.get("attemptId"))
    claimed_writer = _string(req.get("writer_attempt_id") or req.get("writerAttemptId"))
    if claimed_writer and writer_id and claimed_writer != writer_id:
        # Unknown-writer and conflicting-PR requests are checked here.
        if action in {ACTION_AUTHORIZE_RETRY, ACTION_CONTINUE, ACTION_RESOLVE_CONFLICT}:
            return _deny("unknown_writer", "The recorded writer does not match the claimed attempt; reread live evidence before acting.")

    expected_pr = _string(req.get("pr_url") or req.get("prUrl") or req.get("expected_pr"))
    live_pr_url = _string(pr.get("pr_url") or pr.get("prUrl") or pr.get("url"))
    if expected_pr and live_pr_url and expected_pr != live_pr_url:
        return _deny("conflicting_pr", "The preserved PR changed since the UI was rendered; resolve the competing-PR conflict explicitly.")

    if action in {ACTION_CONTINUE, ACTION_AUTHORIZE_RETRY}:
        if not _truthy(stop.get("writers_stopped")):
            if _truthy(stop.get("remote_owner")) and not _truthy(stop.get("can_stop_remote")):
                return _deny(
                    "cross_device_stop_required",
                    "This deployment cannot stop the old writer. Ask the owning deployment/device to stop its writer (or disable its schedule) and reread stop proof before retry is authorized; the block is retained.",
                )
            return _deny("writer_not_stopped", "The prior writer is not conclusively stopped; stop proof is required before continuation.")
        if _truthy(stop.get("publication_unknown")) or _truthy(pr.get("outcome_unknown")):
            return _deny("publication_unknown", "A prior push/label/PR outcome is unknown; reconcile the observed result before repeating effects.")

    if action == ACTION_AUTHORIZE_RETRY and isinstance(retry_policy, Mapping):
        if _string(retry_policy.get("manual_reset_required")).lower() not in {"", "false", "0", "no"} and not _truthy(req.get("retry_budget_reset")):
            # Policy may still allow when the request explicitly declines a
            # reset; only require the explicit field, not a reset itself.
            if "retry_budget_reset" not in req and "retryBudgetReset" not in req:
                return _deny("retry_reset_unspecified", "State whether this authorization resets the retry budget; the decision is recorded.")

    if action == ACTION_ACKNOWLEDGE:
        # Acknowledge is not resume: always allowed for permitted operators
        # but never releases the hold (enforced in record_operator_decision).
        return {"allowed": True, "code": "ok_acknowledge_no_release", "message": "Acknowledgment recorded; admission remains blocked until an explicit resolution."}

    return {"allowed": True, "code": "ok", "message": "Action passes server-side revalidation."}


def _deny(code: str, message: str) -> dict[str, Any]:
    return {"allowed": False, "code": code, "message": message}


# ---------------------------------------------------------------------------
# Auditable portable handoff (required work item 4, second half + item 5)
# ---------------------------------------------------------------------------


def record_operator_decision(
    *,
    action: str,
    operator: Mapping[str, Any],
    previous_state: str,
    reason: str,
    work_disposition: str | None = None,
    retry_budget_reset: bool = False,
    continue_variant: str | None = None,
    competing_refs: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Record an auditable operator decision for the portable handoff.

    Captures operator identity, reason, previous state, chosen work
    disposition, and any retry-budget reset. Hold is not abandonment and
    acknowledge never releases: those invariants are enforced here so the
    UI cannot misrepresent them.
    """
    op = _mapping(operator)
    identity = _string(op.get("id") or op.get("login") or op.get("email") or "unknown-operator")
    disposition = _string(work_disposition) or ("held" if action == ACTION_HOLD else ("acknowledged" if action == ACTION_ACKNOWLEDGE else ("abandoned" if action == ACTION_ABANDON else "preserved")))

    if action == ACTION_ACKNOWLEDGE:
        # Acknowledge is not resume: the hold stays active.
        hold_released = False
    elif action == ACTION_HOLD:
        hold_released = False
    else:
        hold_released = action in {ACTION_RESOLVE_CONFLICT, ACTION_AUTHORIZE_RETRY, ACTION_ABANDON, ACTION_CONTINUE}

    record: dict[str, Any] = {
        "schema": "moonmind.github_issue_operator_decision.v1",
        "action": action,
        "operator": identity,
        "reason": _string(reason)[:1000],
        "previous_state": _string(previous_state),
        "work_disposition": disposition,
        "retry_budget_reset": bool(retry_budget_reset),
        "hold_released": hold_released,
        "competing_refs_preserved": [str(r) for r in (competing_refs or [])],
    }
    if action == ACTION_CONTINUE and continue_variant:
        if continue_variant not in CONTINUE_VARIANTS:
            raise ValueError(f"Unknown continue variant '{continue_variant}'; refusing silent downgrade.")
        record["continue_variant"] = continue_variant
    if action == ACTION_ABANDON and disposition != "abandoned":
        raise ValueError("Abandonment requires an explicit 'abandoned' work disposition.")
    if action == ACTION_HOLD and disposition == "abandoned":
        raise ValueError("Hold is not abandonment: refusing to record a hold as abandoned.")
    return record


# ---------------------------------------------------------------------------
# Human-readable consistency (required work item 7)
# ---------------------------------------------------------------------------


def render_next_action_comment(
    *,
    context: Mapping[str, Any],
    decision: Mapping[str, Any] | None = None,
) -> str:
    """Render the human-readable GitHub comment body for the next action.

    Kept consistent with the local UI's recovery-availability explanation.
    During a GitHub outage the caller must surface pending/unknown instead
    of posting this as a successful remote update.
    """
    availability = describe_recovery_availability(context) if "recovery_availability" not in context else _mapping(context.get("recovery_availability"))
    attention = context.get("attention_category")
    lines = [
        f"Issue {_string(context.get('issue_ref'))} lifecycle: {_string(context.get('settled_lifecycle_state')) or 'unknown'}.",
        f"Recovery phase: {_string(context.get('recovery_phase')) or 'unknown'}.",
    ]
    if attention:
        lines.append(f"Needs attention: {ATTENTION_CATEGORY_LABELS.get(_string(attention), _string(attention))}.")
    lines.append(f"Next action: {_string(availability.get('detail')) or _string(availability.get('reason')) or 'see Workflow Detail'}.")
    failure_count = context.get("prior_failure_count") or 0
    try:
        failure_count = int(failure_count)
    except (TypeError, ValueError):
        failure_count = 0
    if failure_count:
        lines.append(f"Prior failed attempts preserved: {failure_count} (history is retained across launches).")
    if decision:
        decision_map = _mapping(decision)
        lines.append(
            f"Operator {_string(decision_map.get('operator'))} recorded '{_string(decision_map.get('action'))}': {_string(decision_map.get('reason')) or 'no reason given'}."
        )
        # Auditable portable-handoff fields so a second deployment reading
        # the GitHub comment can reconstruct the decision without a second
        # result store: previous state, work disposition, retry-budget reset,
        # and hold-release semantics.
        previous = _string(decision_map.get("previous_state"))
        disposition = _string(decision_map.get("work_disposition"))
        if previous or disposition:
            audit_bits = []
            if previous:
                audit_bits.append(f"previous state {previous}")
            if disposition:
                audit_bits.append(f"disposition {disposition}")
            if bool(decision_map.get("retry_budget_reset")):
                audit_bits.append("retry budget reset")
            audit_bits.append(
                "hold released" if _truthy(decision_map.get("hold_released")) else "hold retained"
            )
            lines.append(f"Decision record: {'; '.join(audit_bits)}.")
    return "\n".join(lines)
