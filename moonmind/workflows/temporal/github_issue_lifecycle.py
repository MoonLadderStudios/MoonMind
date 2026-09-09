"""Shared GitHub issue lifecycle evaluator and transition boundary.

Single policy entrypoint for GitHub issue lifecycle state interpretation and
label mutations (design: docs/Workflows/GitHubIssueStatusStateMachineDesign.md,
sections 2, 3, and 8.1). Deterministic and side-effect-free: no network I/O.
Trusted Activities/services perform reads/writes; this module decides what the
reads mean and what mutations are allowed.

Canonical open states:
  Available        open issue with no MoonMind lifecycle status label
  In progress      ``status: in-progress``
  Recovery needed  ``status: recovery-needed``
  Code review      ``status: code-review``
  Needs attention  ``status: needs-attention``
Closed (GitHub issue state) always excludes selection. ``status: done`` is
qualified terminal presentation only: an open issue carrying it is inconsistent
and never treated as available.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

STATUS_IN_PROGRESS = "status: in-progress"
STATUS_RECOVERY_NEEDED = "status: recovery-needed"
STATUS_CODE_REVIEW = "status: code-review"
STATUS_NEEDS_ATTENTION = "status: needs-attention"
STATUS_DONE = "status: done"

#: Canonical open-issue lifecycle labels (exact canonical spellings).
CANONICAL_OPEN_LABELS = frozenset(
    {
        STATUS_IN_PROGRESS,
        STATUS_RECOVERY_NEEDED,
        STATUS_CODE_REVIEW,
        STATUS_NEEDS_ATTENTION,
    }
)

#: All MoonMind-owned lifecycle labels, including qualified Done presentation.
CANONICAL_LIFECYCLE_LABELS = frozenset({*CANONICAL_OPEN_LABELS, STATUS_DONE})

#: Explicitly retained history only: never emitted on the new path, never
#: required for availability, and never removed destructively by transitions.
LEGACY_TODO_LABEL = "status: todo"

#: Settled lifecycle states returned by :func:`interpret_issue`.
SETTLED_AVAILABLE = "available"
SETTLED_IN_PROGRESS = "in_progress"
SETTLED_RECOVERY_NEEDED = "recovery_needed"
SETTLED_CODE_REVIEW = "code_review"
SETTLED_NEEDS_ATTENTION = "needs_attention"
SETTLED_CLOSED = "closed"
SETTLED_BLOCKED_MIXED = "blocked_mixed"
SETTLED_BLOCKED_UNKNOWN = "blocked_unknown"
SETTLED_BLOCKED_OPEN_DONE = "blocked_open_done"

ELIGIBLE_SETTLED_STATES = frozenset({SETTLED_AVAILABLE, SETTLED_RECOVERY_NEEDED})

#: Transition targets (``to_*`` names used by :func:`plan_transition`).
TO_IN_PROGRESS = "to_in_progress"
TO_CODE_REVIEW = "to_code_review"
TO_RECOVERY_NEEDED = "to_recovery_needed"
TO_NEEDS_ATTENTION = "to_needs_attention"
TO_AVAILABLE = "to_available"
TO_CLOSED = "to_closed"

#: Mutation outcomes distinguished after read-back (design section 8.1).
OUTCOME_APPLIED = "applied"
OUTCOME_ALREADY_APPLIED = "already_applied"
OUTCOME_INCOMPLETE = "incomplete"
OUTCOME_DENIED = "denied"
OUTCOME_UNKNOWN = "outcome_unknown"

_STATUS_PREFIX_RE = re.compile(r"^status\s*[:/_-]*\s*(.*)$")


def _normalize_label_name(value: Any) -> str:
    return str(value or "").strip().lower()


def normalize_lifecycle_label(name: Any) -> str | None:
    """Return the canonical lifecycle label for *name*, else ``None``.

    Only exact canonical spellings (case-insensitive, surrounding whitespace
    ignored) map to a lifecycle label. Ordinary classification labels return
    ``None`` and stay independent of this state machine.
    """
    normalized = _normalize_label_name(name)
    if not normalized:
        return None
    for canonical in CANONICAL_LIFECYCLE_LABELS:
        if normalized == canonical:
            return canonical
    if normalized == LEGACY_TODO_LABEL:
        return LEGACY_TODO_LABEL
    return None


def is_workflow_status_like(name: Any) -> bool:
    """Return True for unrecognized ``status:*`` workflow-status values.

    These require classification and block admission rather than normalizing
    optimistically to Available.
    """
    normalized = _normalize_label_name(name)
    if not normalized:
        return False
    if normalize_lifecycle_label(name) is not None:
        return False
    if normalized in {"todo", "ready", "claiming"}:
        return True
    if normalized.startswith("status:"):
        return True
    match = _STATUS_PREFIX_RE.match(normalized)
    if match and match.group(1):
        return True
    return False


def _label_names(labels: Any) -> list[str]:
    if not isinstance(labels, Sequence) or isinstance(labels, (str, bytes, bytearray)):
        return []
    names: list[str] = []
    for item in labels:
        if isinstance(item, Mapping):
            name = str(item.get("name") or "").strip()
        else:
            name = str(item or "").strip()
        if name:
            names.append(name)
    return names


@dataclass(frozen=True)
class LifecycleInterpretation:
    """Settled interpretation of one GitHub issue's lifecycle state."""

    github_state: str
    canonical_present: frozenset[str] = frozenset()
    unknown_status_labels: tuple[str, ...] = ()
    done_present: bool = False
    legacy_todo_present: bool = False
    settled: str = SETTLED_AVAILABLE
    blocked_reason: str = ""
    eligible_for_implement: bool = False
    eligible_for_continuation: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "githubState": self.github_state,
            "canonicalPresent": sorted(self.canonical_present),
            "unknownStatusLabels": list(self.unknown_status_labels),
            "donePresent": self.done_present,
            "legacyTodoPresent": self.legacy_todo_present,
            "settled": self.settled,
            "blockedReason": self.blocked_reason,
            "eligibleForImplement": self.eligible_for_implement,
            "eligibleForContinuation": self.eligible_for_continuation,
        }


def interpret_issue(issue: Mapping[str, Any]) -> LifecycleInterpretation:
    """Interpret one GitHub issue mapping into a settled lifecycle state.

    Accepts trusted issue payloads with ``state`` (``open``/``closed``) and
    ``labels`` (list of names or ``{"name": ...}`` mappings). Never treats
    closed-not-planned as success and never treats open ``status: done`` as
    available.
    """
    github_state = str(issue.get("state") or "").strip().lower()
    names = _label_names(issue.get("labels"))
    canonical: set[str] = set()
    unknown: list[str] = []
    done_present = False
    legacy_todo = False
    for name in names:
        mapped = normalize_lifecycle_label(name)
        if mapped in CANONICAL_OPEN_LABELS:
            canonical.add(mapped)
        elif mapped == STATUS_DONE:
            done_present = True
        elif mapped == LEGACY_TODO_LABEL:
            legacy_todo = True
        elif is_workflow_status_like(name):
            unknown.append(name.strip())

    if github_state == "closed":
        return LifecycleInterpretation(
            github_state="closed",
            canonical_present=frozenset(canonical),
            unknown_status_labels=tuple(unknown),
            done_present=done_present,
            legacy_todo_present=legacy_todo,
            settled=SETTLED_CLOSED,
            blocked_reason="Issue is closed; closed issues are excluded from selection.",
            eligible_for_implement=False,
            eligible_for_continuation=False,
        )

    if unknown:
        return LifecycleInterpretation(
            github_state="open",
            canonical_present=frozenset(canonical),
            unknown_status_labels=tuple(unknown),
            done_present=done_present,
            legacy_todo_present=legacy_todo,
            settled=SETTLED_BLOCKED_UNKNOWN,
            blocked_reason=(
                "Unrecognized workflow-status value requires classification; "
                "not silently admitted."
            ),
            eligible_for_implement=False,
            eligible_for_continuation=False,
        )
    if done_present:
        return LifecycleInterpretation(
            github_state="open",
            canonical_present=frozenset(canonical),
            unknown_status_labels=tuple(unknown),
            done_present=True,
            legacy_todo_present=legacy_todo,
            settled=SETTLED_BLOCKED_OPEN_DONE,
            blocked_reason=(
                "Open issue carrying status: done is inconsistent; "
                "not treated as available."
            ),
            eligible_for_implement=False,
            eligible_for_continuation=False,
        )
    if len(canonical) > 1:
        return LifecycleInterpretation(
            github_state="open",
            canonical_present=frozenset(canonical),
            unknown_status_labels=tuple(unknown),
            done_present=False,
            legacy_todo_present=legacy_todo,
            settled=SETTLED_BLOCKED_MIXED,
            blocked_reason=(
                "Multiple canonical status labels block admission "
                "pending reconciliation."
            ),
            eligible_for_implement=False,
            eligible_for_continuation=False,
        )
    if not canonical:
        return LifecycleInterpretation(
            github_state="open",
            canonical_present=frozenset(),
            unknown_status_labels=tuple(unknown),
            done_present=False,
            legacy_todo_present=legacy_todo,
            settled=SETTLED_AVAILABLE,
            blocked_reason="",
            eligible_for_implement=True,
            eligible_for_continuation=False,
        )
    (only,) = canonical
    settled = {
        STATUS_IN_PROGRESS: SETTLED_IN_PROGRESS,
        STATUS_RECOVERY_NEEDED: SETTLED_RECOVERY_NEEDED,
        STATUS_CODE_REVIEW: SETTLED_CODE_REVIEW,
        STATUS_NEEDS_ATTENTION: SETTLED_NEEDS_ATTENTION,
    }[only]
    if settled == SETTLED_RECOVERY_NEEDED:
        return LifecycleInterpretation(
            github_state="open",
            canonical_present=frozenset(canonical),
            unknown_status_labels=tuple(unknown),
            done_present=False,
            legacy_todo_present=legacy_todo,
            settled=settled,
            blocked_reason="",
            eligible_for_implement=False,
            eligible_for_continuation=True,
        )
    blocked_reason = {
        SETTLED_IN_PROGRESS: "An attempt is already in progress; excluded from new implementation.",
        SETTLED_CODE_REVIEW: "A verified implementation is in review; excluded from ordinary implementation search.",
        SETTLED_NEEDS_ATTENTION: "Attention is required before automatic implementation.",
    }[settled]
    return LifecycleInterpretation(
        github_state="open",
        canonical_present=frozenset(canonical),
        unknown_status_labels=tuple(unknown),
        done_present=False,
        legacy_todo_present=legacy_todo,
        settled=settled,
        blocked_reason=blocked_reason,
        eligible_for_implement=False,
        eligible_for_continuation=False,
    )


def attempt_evidence_blocks_admission(attempt_context: Mapping[str, Any] | None) -> bool:
    """Return True when supplied validated attempt context blocks admission.

    A missing in-progress label can never override supplied unresolved
    active-attempt evidence: selectors honor unresolved attempt evidence even
    when the label is missing (design section 8.1).
    """
    context = attempt_context or {}
    for key in (
        "hasUnresolvedActiveAttempt",
        "has_unresolved_active_attempt",
        "unresolvedActiveAttempt",
        "unresolved_active_attempt",
        "activeAttemptUnresolved",
        "active_attempt_unresolved",
    ):
        value = context.get(key)
        if value is True or (isinstance(value, str) and value.strip().lower() in {"1", "true", "yes"}):
            return True
    return False


def is_selectable_candidate(
    issue: Mapping[str, Any],
    attempt_context: Mapping[str, Any] | None = None,
) -> tuple[bool, LifecycleInterpretation]:
    """Return ``(selectable, interpretation)`` for Search and Implement admission.

    Only Available issues are fresh-implementation candidates and only
    Recovery-needed issues are continuation candidates, and neither is
    selectable while unresolved active-attempt evidence contradicts the label.
    """
    interpretation = interpret_issue(issue)
    if interpretation.settled not in ELIGIBLE_SETTLED_STATES:
        return False, interpretation
    if attempt_evidence_blocks_admission(attempt_context):
        return False, interpretation
    return True, interpretation


_SETTLED_TO_LABEL: dict[str, str | None] = {
    SETTLED_AVAILABLE: None,
    SETTLED_IN_PROGRESS: STATUS_IN_PROGRESS,
    SETTLED_RECOVERY_NEEDED: STATUS_RECOVERY_NEEDED,
    SETTLED_CODE_REVIEW: STATUS_CODE_REVIEW,
    SETTLED_NEEDS_ATTENTION: STATUS_NEEDS_ATTENTION,
    SETTLED_CLOSED: None,
}

_TARGET_TO_LABEL: dict[str, str | None] = {
    TO_IN_PROGRESS: STATUS_IN_PROGRESS,
    TO_CODE_REVIEW: STATUS_CODE_REVIEW,
    TO_RECOVERY_NEEDED: STATUS_RECOVERY_NEEDED,
    TO_NEEDS_ATTENTION: STATUS_NEEDS_ATTENTION,
    TO_AVAILABLE: None,
    TO_CLOSED: None,
}

_TARGET_FROM_CLOSED = {TO_CLOSED}

# (from_settled, to_target) -> required evidence keys. Every transition also
# requires an explicit non-empty reason. No timeout alone authorizes
# in_progress -> available or recovery_needed: those require writers_stopped
# plus terminal_proof / handoff_published evidence. Every move to Available
# (including needs_attention -> available) requires supplied terminal_proof.
_TRANSITION_REQUIREMENTS: dict[tuple[str, str], tuple[str, ...]] = {
    (SETTLED_AVAILABLE, TO_IN_PROGRESS): ("admission_passed", "prior_work_inspected"),
    (SETTLED_AVAILABLE, TO_CODE_REVIEW): ("gates_satisfied", "pr_url_verified"),
    (SETTLED_AVAILABLE, TO_CLOSED): ("completion_verified",),
    (SETTLED_RECOVERY_NEEDED, TO_IN_PROGRESS): (
        "predecessor_stopped",
        "handoff_usable",
        "admission_passed",
    ),
    (SETTLED_IN_PROGRESS, TO_IN_PROGRESS): ("same_attempt_active",),
    (SETTLED_IN_PROGRESS, TO_CODE_REVIEW): ("gates_satisfied", "pr_url_verified"),
    (SETTLED_IN_PROGRESS, TO_RECOVERY_NEEDED): ("writers_stopped", "handoff_published"),
    (SETTLED_IN_PROGRESS, TO_AVAILABLE): ("writers_stopped", "terminal_proof"),
    (SETTLED_IN_PROGRESS, TO_NEEDS_ATTENTION): ("blocking_reason",),
    (SETTLED_IN_PROGRESS, TO_CLOSED): ("completion_verified",),
    (SETTLED_CODE_REVIEW, TO_CLOSED): ("completion_verified",),
    (SETTLED_CODE_REVIEW, TO_IN_PROGRESS): ("admitted_repair",),
    (SETTLED_CODE_REVIEW, TO_RECOVERY_NEEDED): ("owner_ended", "next_action_recorded"),
    (SETTLED_CODE_REVIEW, TO_NEEDS_ATTENTION): ("owner_ended", "next_action_recorded"),
    (SETTLED_AVAILABLE, TO_NEEDS_ATTENTION): ("hold_intent",),
    (SETTLED_RECOVERY_NEEDED, TO_NEEDS_ATTENTION): ("hold_intent",),
    (SETTLED_NEEDS_ATTENTION, TO_AVAILABLE): (
        "authorized_resolution",
        "preserved_work_disposition",
        "terminal_proof",
    ),
    (SETTLED_NEEDS_ATTENTION, TO_RECOVERY_NEEDED): (
        "authorized_resolution",
        "preserved_work_disposition",
    ),
    (SETTLED_NEEDS_ATTENTION, TO_CODE_REVIEW): (
        "authorized_resolution",
        "preserved_work_disposition",
    ),
    (SETTLED_NEEDS_ATTENTION, TO_CLOSED): (
        "authorized_resolution",
        "preserved_work_disposition",
    ),
    (SETTLED_NEEDS_ATTENTION, TO_IN_PROGRESS): ("authorized_resolution",),
}


def _truthy_evidence(value: Any) -> bool:
    if value is True:
        return True
    if isinstance(value, str):
        return bool(value.strip())
    return bool(value) and value is not False and value is not None


@dataclass(frozen=True)
class TransitionDecision:
    """Shared typed decision for one lifecycle transition attempt."""

    allowed: bool
    from_settled: str
    to_target: str
    reason: str = ""
    reason_code: str = ""
    required_evidence: tuple[str, ...] = ()
    missing_evidence: tuple[str, ...] = ()
    summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "fromSettled": self.from_settled,
            "toTarget": self.to_target,
            "reason": self.reason,
            "reasonCode": self.reason_code,
            "requiredEvidence": list(self.required_evidence),
            "missingEvidence": list(self.missing_evidence),
            "summary": self.summary,
        }


def plan_transition(
    *,
    from_settled: str,
    to_target: str,
    evidence: Mapping[str, Any] | None = None,
    reason: str = "",
) -> TransitionDecision:
    """Decide one lifecycle transition as a shared typed decision.

    GitHub issue state stays distinct from Temporal execution state: callers
    pass GitHub-visible evidence only. Blocking outcomes (mixed labels, unknown
    workflow-status values, unsupported evidence, open-Done inconsistencies)
    deny admission rather than normalizing optimistically.
    """
    evidence_mapping = dict(evidence or {})
    cleaned_reason = str(reason or "").strip()
    if from_settled == SETTLED_CLOSED and to_target not in _TARGET_FROM_CLOSED:
        return TransitionDecision(
            allowed=False,
            from_settled=from_settled,
            to_target=to_target,
            reason=cleaned_reason,
            reason_code="closed_terminal",
            summary="Closed issues require human or authorized-policy reopen before reassessment.",
        )
    if from_settled in {
        SETTLED_BLOCKED_MIXED,
        SETTLED_BLOCKED_UNKNOWN,
        SETTLED_BLOCKED_OPEN_DONE,
    }:
        return TransitionDecision(
            allowed=False,
            from_settled=from_settled,
            to_target=to_target,
            reason=cleaned_reason,
            reason_code="reconciliation_required",
            summary=(
                "Mixed labels, unknown workflow-status values, or open-Done "
                "inconsistencies block admission pending reconciliation."
            ),
        )
    required = _TRANSITION_REQUIREMENTS.get((from_settled, to_target))
    if required is None:
        return TransitionDecision(
            allowed=False,
            from_settled=from_settled,
            to_target=to_target,
            reason=cleaned_reason,
            reason_code="unsupported_transition",
            summary=f"Transition {from_settled} -> {to_target} requires an explicit decision under the existing authority contracts.",
        )
    missing = tuple(key for key in required if not _truthy_evidence(evidence_mapping.get(key)))
    if not cleaned_reason:
        missing = (*missing, "reason")
    if missing:
        return TransitionDecision(
            allowed=False,
            from_settled=from_settled,
            to_target=to_target,
            reason=cleaned_reason,
            reason_code="missing_guard",
            required_evidence=required,
            missing_evidence=missing,
            summary=f"Transition {from_settled} -> {to_target} is missing required guard evidence: {', '.join(missing)}.",
        )
    if to_target == TO_AVAILABLE and not _truthy_evidence(evidence_mapping.get("terminal_proof")):
        return TransitionDecision(
            allowed=False,
            from_settled=from_settled,
            to_target=to_target,
            reason=cleaned_reason,
            reason_code="missing_guard",
            required_evidence=required,
            missing_evidence=("terminal_proof",),
            summary="Moving to Available requires supplied terminal/handoff proof, not an unconditional remove-label action.",
        )
    return TransitionDecision(
        allowed=True,
        from_settled=from_settled,
        to_target=to_target,
        reason=cleaned_reason,
        reason_code="allowed",
        required_evidence=required,
        missing_evidence=(),
        summary=f"Transition {from_settled} -> {to_target} satisfies all guards.",
    )


@dataclass(frozen=True)
class LabelMutationPlan:
    """Targeted add/remove plan. Destination is added before old blockers."""

    labels_to_add: tuple[str, ...] = ()
    labels_to_remove: tuple[str, ...] = ()
    close_issue: bool = False

    def ordered_operations(self) -> list[tuple[str, str]]:
        """Return ``("add"|"remove", label)`` ops in add-before-remove order."""
        ops = [("add", label) for label in self.labels_to_add]
        ops.extend(("remove", label) for label in self.labels_to_remove)
        return ops

    def to_dict(self) -> dict[str, Any]:
        return {
            "labelsToAdd": list(self.labels_to_add),
            "labelsToRemove": list(self.labels_to_remove),
            "closeIssue": self.close_issue,
        }


def plan_label_mutation(
    *,
    from_settled: str,
    to_target: str,
    current_labels: Sequence[Any] | None = None,
) -> LabelMutationPlan:
    """Plan targeted additions/removals of MoonMind-owned status labels.

    Never replaces the complete label list: unrelated and ordinary labels are
    untouched. Transitions add the destination status before removing an old
    blocking status. Moving to Available has no destination label.
    """
    destination = _TARGET_TO_LABEL.get(to_target)
    origin = _SETTLED_TO_LABEL.get(from_settled)
    names = _label_names(current_labels)
    present = {_normalize_label_name(name) for name in names}
    to_add: list[str] = []
    to_remove: list[str] = []
    if destination is not None and destination.lower() not in present:
        to_add.append(destination)
    if origin is not None and to_target != TO_CLOSED:
        if origin.lower() in present and (destination is None or origin.lower() != destination.lower()):
            to_remove.append(origin)
    if to_target == TO_CLOSED and origin is not None and origin.lower() in present:
        # Terminal Done presentation is applied by the completion policy via the
        # close path; the open-work blocker itself is released on close.
        to_remove.append(origin)
    return LabelMutationPlan(
        labels_to_add=tuple(to_add),
        labels_to_remove=tuple(to_remove),
        close_issue=to_target == TO_CLOSED,
    )


@dataclass(frozen=True)
class MutationOutcome:
    """Read-back classification of one transition attempt."""

    outcome: str
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"outcome": self.outcome, "detail": self.detail}


def classify_mutation_outcome(
    *,
    plan: LabelMutationPlan,
    read_back: Mapping[str, Any] | None,
    transport_error: str = "",
    denied: bool = False,
    denied_detail: str = "",
) -> MutationOutcome:
    """Distinguish applied, already applied, incomplete, denied, outcome-unknown."""
    if denied:
        return MutationOutcome(
            outcome=OUTCOME_DENIED,
            detail=denied_detail or "GitHub denied the label mutation.",
        )
    if not plan.labels_to_add and not plan.labels_to_remove and not plan.close_issue:
        return MutationOutcome(
            outcome=OUTCOME_ALREADY_APPLIED,
            detail="Desired state was already present; no mutation was required.",
        )
    if transport_error or read_back is None:
        return MutationOutcome(
            outcome=OUTCOME_UNKNOWN,
            detail=(
                f"Mutation result is unknown after {transport_error or 'response loss'}; "
                "reconcile by exact read-back before repeating effects."
            ),
        )
    interpretation = interpret_issue(read_back)
    expected_destination = next(
        (label for _op, label in plan.ordered_operations() if _op == "add"),
        None,
    )
    if plan.close_issue:
        if str(read_back.get("state") or "").strip().lower() == "closed":
            return MutationOutcome(outcome=OUTCOME_APPLIED, detail="Issue is closed as intended.")
        return MutationOutcome(outcome=OUTCOME_INCOMPLETE, detail="Close was not observed on read-back.")
    if expected_destination is None:
        # Moving to Available: success means no canonical open label remains.
        if interpretation.settled == SETTLED_AVAILABLE:
            if not plan.labels_to_add and not plan.labels_to_remove:
                return MutationOutcome(outcome=OUTCOME_ALREADY_APPLIED, detail="No lifecycle status remains; issue is Available.")
            return MutationOutcome(outcome=OUTCOME_APPLIED, detail="No lifecycle status remains; issue is Available.")
        if interpretation.settled in {SETTLED_BLOCKED_MIXED, SETTLED_BLOCKED_UNKNOWN, SETTLED_BLOCKED_OPEN_DONE}:
            return MutationOutcome(outcome=OUTCOME_INCOMPLETE, detail="Intermediate label combination remains ineligible.")
        return MutationOutcome(outcome=OUTCOME_INCOMPLETE, detail="Old blocking status was not removed.")
    present = {_normalize_label_name(name) for name in _label_names(read_back.get("labels"))}
    if expected_destination.lower() not in present:
        if not plan.labels_to_add and not plan.labels_to_remove:
            return MutationOutcome(outcome=OUTCOME_ALREADY_APPLIED, detail="No mutation was required.")
        return MutationOutcome(outcome=OUTCOME_INCOMPLETE, detail="Destination status was not observed on read-back.")
    removed_pending = [
        label for _op, label in plan.ordered_operations()
        if _op == "remove" and label.lower() in present
    ]
    if removed_pending:
        return MutationOutcome(
            outcome=OUTCOME_INCOMPLETE,
            detail=f"Destination added but old status remains: {', '.join(removed_pending)}.",
        )
    if not plan.labels_to_add and not plan.labels_to_remove:
        return MutationOutcome(outcome=OUTCOME_ALREADY_APPLIED, detail="Desired state was already present.")
    return MutationOutcome(outcome=OUTCOME_APPLIED, detail="Destination status observed on read-back.")


def should_abandon_retry(
    *,
    intended_from_settled: str,
    observed: LifecycleInterpretation,
) -> tuple[bool, str]:
    """Decide whether a retried transition is obsolete.

    Abandons on an observed successor, operator hold, or conflicting evidence.
    Never fences a delayed old request: abandonment only stops new mutations.
    """
    if observed.settled in {SETTLED_BLOCKED_MIXED, SETTLED_BLOCKED_UNKNOWN, SETTLED_BLOCKED_OPEN_DONE}:
        return True, "conflicting evidence requires reconciliation before retry"
    if observed.settled == SETTLED_NEEDS_ATTENTION:
        return True, "operator hold requires resolution before retry"
    if observed.settled == SETTLED_CLOSED:
        return True, "issue closed after the transition was planned"
    if observed.settled != intended_from_settled and observed.settled in {
        SETTLED_IN_PROGRESS,
        SETTLED_CODE_REVIEW,
        SETTLED_RECOVERY_NEEDED,
        SETTLED_AVAILABLE,
    }:
        return True, f"observed successor state {observed.settled} supersedes the planned update"
    return False, ""


# Backwards-compatible alias kept for the previous scattered helper name.
def canonical_status_labels() -> frozenset[str]:
    """Return the canonical open-issue status labels (one policy entrypoint)."""
    return CANONICAL_OPEN_LABELS


__all__ = [
    "STATUS_IN_PROGRESS",
    "STATUS_RECOVERY_NEEDED",
    "STATUS_CODE_REVIEW",
    "STATUS_NEEDS_ATTENTION",
    "STATUS_DONE",
    "CANONICAL_OPEN_LABELS",
    "CANONICAL_LIFECYCLE_LABELS",
    "LEGACY_TODO_LABEL",
    "SETTLED_AVAILABLE",
    "SETTLED_IN_PROGRESS",
    "SETTLED_RECOVERY_NEEDED",
    "SETTLED_CODE_REVIEW",
    "SETTLED_NEEDS_ATTENTION",
    "SETTLED_CLOSED",
    "SETTLED_BLOCKED_MIXED",
    "SETTLED_BLOCKED_UNKNOWN",
    "SETTLED_BLOCKED_OPEN_DONE",
    "ELIGIBLE_SETTLED_STATES",
    "TO_IN_PROGRESS",
    "TO_CODE_REVIEW",
    "TO_RECOVERY_NEEDED",
    "TO_NEEDS_ATTENTION",
    "TO_AVAILABLE",
    "TO_CLOSED",
    "OUTCOME_APPLIED",
    "OUTCOME_ALREADY_APPLIED",
    "OUTCOME_INCOMPLETE",
    "OUTCOME_DENIED",
    "OUTCOME_UNKNOWN",
    "LifecycleInterpretation",
    "TransitionDecision",
    "LabelMutationPlan",
    "MutationOutcome",
    "normalize_lifecycle_label",
    "is_workflow_status_like",
    "interpret_issue",
    "attempt_evidence_blocks_admission",
    "is_selectable_candidate",
    "plan_transition",
    "plan_label_mutation",
    "classify_mutation_outcome",
    "should_abandon_retry",
    "canonical_status_labels",
]
