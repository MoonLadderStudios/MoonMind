"""Pure, fail-closed resolver classification."""

from __future__ import annotations

from .models import CanonicalPullRequestSnapshot, ResolverAction, ResolverDecision


_REMEDIATIONS = {
    "actionable_comments": "fix-comments",
    "ci_failures": "fix-ci",
    "merge_conflicts": "fix-merge-conflicts",
}


def _decision(
    classification: str,
    reason_code: str,
    action: ResolverAction,
    *,
    terminal_status: str | None = None,
) -> ResolverDecision:
    return ResolverDecision(
        classification=classification,
        reason_code=reason_code,
        action=action,
        remediation_skill=_REMEDIATIONS.get(classification),
        terminal_status=terminal_status,
    )


def classify_snapshot(
    snapshot: CanonicalPullRequestSnapshot,
    *,
    known_ci_failures_precede_degraded: bool = True,
) -> ResolverDecision:
    if snapshot.merged:
        return _decision(
            "already_merged",
            "already_merged",
            ResolverAction.PUBLISH_TERMINAL,
            terminal_status="already_merged",
        )
    if not snapshot.open:
        return _decision(
            "manual_review",
            "pull_request_closed",
            ResolverAction.STOP_MANUAL_REVIEW,
            terminal_status="manual_review",
        )
    if snapshot.draft:
        return _decision("manual_review", "draft", ResolverAction.STOP_MANUAL_REVIEW)
    if not snapshot.publish_available:
        return _decision(
            "manual_review", "publish_unavailable", ResolverAction.STOP_MANUAL_REVIEW
        )
    if snapshot.merge_conflict:
        return _decision(
            "merge_conflicts", "merge_conflicts", ResolverAction.RUN_REMEDIATION
        )
    if not snapshot.comments_available:
        return _decision(
            "manual_review", "comments_unavailable", ResolverAction.STOP_MANUAL_REVIEW
        )
    if not snapshot.comment_policy_enforced:
        return _decision(
            "manual_review",
            "comment_policy_not_enforced",
            ResolverAction.STOP_MANUAL_REVIEW,
        )
    if snapshot.checks_degraded and (
        not snapshot.checks_failed or not known_ci_failures_precede_degraded
    ):
        return _decision(
            "manual_review", "ci_signal_degraded", ResolverAction.STOP_MANUAL_REVIEW
        )
    if snapshot.unknown_blocker:
        return _decision(
            "manual_review", "unknown_blocker", ResolverAction.STOP_MANUAL_REVIEW
        )
    if snapshot.malformed:
        return _decision(
            "manual_review", "snapshot_malformed", ResolverAction.STOP_MANUAL_REVIEW
        )
    if snapshot.actionable_comments:
        return _decision(
            "actionable_comments", "actionable_comments", ResolverAction.RUN_REMEDIATION
        )
    if snapshot.checks_failed:
        return _decision("ci_failures", "ci_failures", ResolverAction.RUN_REMEDIATION)
    if snapshot.automated_review_pending:
        return _decision("review_grace", "review_grace", ResolverAction.WAIT)
    if snapshot.checks_signal_available and not snapshot.checks_complete:
        return _decision("ci_running", "ci_running", ResolverAction.WAIT)
    if snapshot.mergeability_unknown:
        return _decision(
            "mergeability_transient", "external_state_transient", ResolverAction.WAIT
        )
    if not snapshot.checks_complete:
        return _decision("ci_running", "ci_running", ResolverAction.WAIT)
    if snapshot.checks_passing:
        return _decision(
            "ready_to_merge", "ready_to_merge", ResolverAction.ATTEMPT_MERGE
        )
    return _decision(
        "manual_review", "unknown_blocker", ResolverAction.STOP_MANUAL_REVIEW
    )
