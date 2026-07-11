"""Deterministic resolver transition policy shared by all hosts."""

from .models import ResolverAction, ResolverDecision, ResolverSnapshot, ResolverState


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
    if snapshot.mergeable is None or snapshot.checks in {"unknown", "degraded", "unavailable"}:
        return ResolverDecision(ResolverAction.WAIT, "provider_state_unavailable")
    if snapshot.comments in {"unknown", "unavailable"}:
        return ResolverDecision(ResolverAction.WAIT, "comments_unavailable")
    if snapshot.checks == "running":
        return ResolverDecision(ResolverAction.WAIT, "ci_running")
    remediation = None
    if not snapshot.mergeable:
        remediation = "fix-merge-conflicts"
    elif snapshot.checks == "failed":
        remediation = "fix-ci"
    elif snapshot.comments == "actionable":
        remediation = "fix-comments"
    if remediation:
        if previous_state.remediation_attempts >= previous_state.max_remediation_attempts:
            return ResolverDecision(ResolverAction.STOP_MANUAL_REVIEW, "attempts_exhausted")
        return ResolverDecision(ResolverAction.RUN_REMEDIATION, remediation, remediation)
    return ResolverDecision(
        ResolverAction.ATTEMPT_MERGE, "ready_to_merge", merge_eligible=True
    )
