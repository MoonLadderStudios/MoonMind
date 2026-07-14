"""Host-neutral resolver state reducer."""

from __future__ import annotations

from .models import (
    CanonicalPullRequestSnapshot,
    ResolverAction,
    ResolverEvent,
    ResolverPolicy,
    ResolverState,
    ResolverTransition,
)
from .classify import classify_snapshot


def reduce_resolver_state(
    *,
    previous_state: ResolverState,
    snapshot: CanonicalPullRequestSnapshot,
    policy: ResolverPolicy,
    event: ResolverEvent,
    known_ci_failures_precede_degraded: bool = True,
) -> ResolverTransition:
    decision = classify_snapshot(
        snapshot,
        known_ci_failures_precede_degraded=known_ci_failures_precede_degraded,
    )
    state = previous_state
    metadata: dict[str, str | int | bool | None] = {}

    if decision.action is ResolverAction.ATTEMPT_MERGE:
        if previous_state.finalize_attempts >= policy.max_finalize_attempts:
            decision = decision.__class__(
                classification="manual_review",
                reason_code="finalize_budget_exhausted",
                action=ResolverAction.STOP_MANUAL_REVIEW,
                terminal_status="manual_review",
            )
        else:
            state = ResolverState(
                finalize_attempts=previous_state.finalize_attempts + 1,
                remediation_counts=previous_state.remediation_counts,
                identical_blocker_count=previous_state.identical_blocker_count,
                last_progress_signature=previous_state.last_progress_signature,
            )
    elif decision.action is ResolverAction.RUN_REMEDIATION:
        skill = decision.remediation_skill or ""
        signature = event.progress_signature
        identical = (
            previous_state.identical_blocker_count + 1
            if signature and signature == previous_state.last_progress_signature
            else 0
        )
        current_count = previous_state.remediation_count(skill)
        if (
            identical >= policy.max_identical_blockers_without_progress
            or current_count >= policy.max_remediations_per_type
        ):
            decision = decision.__class__(
                classification="manual_review",
                reason_code="repeated_blocker_without_progress",
                action=ResolverAction.STOP_MANUAL_REVIEW,
                terminal_status="manual_review",
            )
        else:
            counts = dict(previous_state.remediation_counts)
            counts[skill] = current_count + 1
            state = ResolverState(
                finalize_attempts=previous_state.finalize_attempts,
                remediation_counts=tuple(sorted(counts.items())),
                identical_blocker_count=identical,
                last_progress_signature=signature,
            )
            metadata["remediationSkill"] = skill
            metadata["remediationAttempt"] = counts[skill]

    return ResolverTransition(
        state=state,
        decision=decision,
        action=decision.action,
        metadata=metadata,
    )
