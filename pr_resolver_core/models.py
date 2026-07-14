"""Pure resolver models with no provider, host, or persistence dependencies."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class ResolverAction(str, Enum):
    READ_SNAPSHOT = "read_snapshot"
    WAIT = "wait"
    RUN_REMEDIATION = "run_remediation"
    ATTEMPT_MERGE = "attempt_merge"
    PUBLISH_TERMINAL = "publish_terminal"
    STOP_MANUAL_REVIEW = "stop_manual_review"


@dataclass(frozen=True, slots=True)
class CanonicalPullRequestSnapshot:
    repository: str = ""
    pr_number: int | None = None
    pr_url: str = ""
    head_sha: str = ""
    base_sha: str = ""
    merged: bool = False
    open: bool = True
    draft: bool = False
    merge_conflict: bool = False
    mergeability_unknown: bool = False
    checks_complete: bool = False
    checks_passing: bool = False
    checks_failed: bool = False
    checks_degraded: bool = False
    checks_signal_available: bool = False
    actionable_comments: bool = False
    comments_available: bool = True
    comment_policy_enforced: bool = True
    automated_review_pending: bool = False
    publish_available: bool = True
    malformed: bool = False
    unknown_blocker: bool = False
    blocker_kinds: tuple[str, ...] = ()
    blocker_summaries: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ResolverDecision:
    classification: str
    reason_code: str
    action: ResolverAction
    remediation_skill: str | None = None
    terminal_status: str | None = None


@dataclass(frozen=True, slots=True)
class ResolverPolicy:
    max_finalize_attempts: int = 60
    max_remediations_per_type: int = 2
    max_identical_blockers_without_progress: int = 2


@dataclass(frozen=True, slots=True)
class ResolverState:
    finalize_attempts: int = 0
    remediation_counts: tuple[tuple[str, int], ...] = ()
    identical_blocker_count: int = 0
    last_progress_signature: str | None = None

    def remediation_count(self, skill: str) -> int:
        return dict(self.remediation_counts).get(skill, 0)


@dataclass(frozen=True, slots=True)
class ResolverEvent:
    kind: str
    progress_signature: str | None = None


@dataclass(frozen=True, slots=True)
class ResolverTransition:
    state: ResolverState
    decision: ResolverDecision
    action: ResolverAction
    metadata: dict[str, str | int | bool | None] = field(default_factory=dict)
