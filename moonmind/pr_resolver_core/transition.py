"""Deterministic resolver transition policy shared by all hosts."""

from collections.abc import Mapping
from typing import Any

from .models import ResolverAction, ResolverDecision, ResolverSnapshot, ResolverState


def normalize_github_snapshot(snapshot: Mapping[str, Any]) -> ResolverSnapshot:
    """Normalize the portable/Temporal GitHub snapshot into the core contract."""

    blockers = tuple(
        item for item in snapshot.get("blockers", ()) if isinstance(item, Mapping)
    )
    kinds = {str(item.get("kind") or "").strip() for item in blockers}
    summaries = " ".join(
        str(item.get("summary") or "").strip().lower() for item in blockers
    )
    checks_complete = snapshot.get("checksComplete")
    checks_passing = snapshot.get("checksPassing")
    if "checks_failed" in kinds or (
        checks_complete is True and checks_passing is False
    ):
        checks = "failed"
    elif "checks_running" in kinds or checks_complete is False:
        checks = "running"
    elif checks_complete is True and checks_passing is True:
        checks = "passing"
    else:
        checks = "unknown"

    if "automated_review_pending" in kinds:
        comments = (
            "actionable"
            if "requested changes" in summaries or "changes requested" in summaries
            else "pending"
        )
    elif snapshot.get("ready") is True and not blockers:
        comments = "clear"
    elif blockers:
        comments = "unknown"
    else:
        comments = "clear"

    mergeable: bool | None
    if "merge_conflict" in kinds or "merge_conflicts" in kinds:
        mergeable = False
    elif snapshot.get("ready") is True:
        mergeable = True
    else:
        mergeable = None
    return ResolverSnapshot(
        merged=snapshot.get("pullRequestMerged") is True,
        closed=snapshot.get("pullRequestOpen") is False,
        draft=snapshot.get("isDraft") is True,
        mergeable=mergeable,
        checks="passing" if snapshot.get("ready") is True and checks == "unknown" else checks,
        comments=comments,
        publish_available=(
            not blockers or all(bool(item.get("retryable", True)) for item in blockers)
        ),
        head_sha=str(snapshot.get("headSha") or "").strip(),
        base_sha=str(snapshot.get("baseSha") or "").strip(),
    )


def classify_github_snapshot(snapshot: Mapping[str, Any]) -> dict[str, str]:
    """Return the stable host classification derived only from the pure core."""

    normalized = normalize_github_snapshot(snapshot)
    decision = reduce_resolver_state(
        previous_state=ResolverState(), snapshot=normalized
    )
    classification_by_reason = {
        "already_merged": "already_merged",
        "closed_without_merge": "manual_review",
        "draft": "manual_review",
        "provider_state_unavailable": "mergeability_transient",
        "comments_unavailable": "mergeability_transient",
        "ci_running": "ci_running",
        "fix-merge-conflicts": "merge_conflicts",
        "fix-ci": "ci_failures",
        "fix-comments": "actionable_comments",
        "ready_to_merge": "ready_to_merge",
    }
    if normalized.comments == "pending":
        return {"classification": "review_grace", "reasonCode": "review_grace"}
    classification = classification_by_reason.get(decision.reason_code, "manual_review")
    reason = (
        "pull_request_closed"
        if decision.reason_code == "closed_without_merge"
        else "external_state_transient"
        if decision.reason_code in {"provider_state_unavailable", "comments_unavailable"}
        else classification
        if classification in {"merge_conflicts", "ci_failures", "actionable_comments"}
        else "unknown_blocker"
        if classification == "manual_review" and decision.reason_code not in {"draft"}
        else decision.reason_code
    )
    return {"classification": classification, "reasonCode": reason}


def reduce_resolver_state(
    *, previous_state: ResolverState, snapshot: ResolverSnapshot
) -> ResolverDecision:
    if snapshot.merged:
        return ResolverDecision(ResolverAction.PUBLISH_TERMINAL, "already_merged")
    if snapshot.closed:
        return ResolverDecision(ResolverAction.STOP_MANUAL_REVIEW, "closed_without_merge")
    if snapshot.draft:
        return ResolverDecision(ResolverAction.STOP_MANUAL_REVIEW, "draft")
    if not snapshot.publish_available:
        return ResolverDecision(ResolverAction.STOP_MANUAL_REVIEW, "publish_unavailable")
    if snapshot.checks == "running":
        return ResolverDecision(ResolverAction.WAIT, "ci_running")
    remediation = None
    if snapshot.mergeable is False:
        remediation = "fix-merge-conflicts"
    elif snapshot.checks == "failed":
        remediation = "fix-ci"
    elif snapshot.comments == "actionable":
        remediation = "fix-comments"
    if remediation:
        if previous_state.remediation_attempts >= previous_state.max_remediation_attempts:
            return ResolverDecision(ResolverAction.STOP_MANUAL_REVIEW, "attempts_exhausted")
        return ResolverDecision(ResolverAction.RUN_REMEDIATION, remediation, remediation)
    if snapshot.mergeable is None or snapshot.checks in {"unknown", "degraded", "unavailable"}:
        return ResolverDecision(ResolverAction.WAIT, "provider_state_unavailable")
    if snapshot.comments in {"unknown", "unavailable"}:
        return ResolverDecision(ResolverAction.WAIT, "comments_unavailable")
    return ResolverDecision(
        ResolverAction.ATTEMPT_MERGE, "ready_to_merge", merge_eligible=True
    )
