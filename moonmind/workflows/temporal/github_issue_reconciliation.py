"""Bounded periodic reconciliation of interrupted GitHub issue handoffs.

Single policy entrypoint for MoonLadderStudios/MoonMind#4182 (design:
docs/Workflows/GitHubIssueStatusStateMachineDesign.md, sections 4.2, 6.2,
6.3, and 9). Deterministic and side-effect-free: no network I/O. Trusted
Activities/services perform GitHub reads/writes; this module decides what
the reads mean and what a periodic reconciler may repair or surface.

Contract summary:

* The reconciler first replays locally persisted pending GitHub effects,
  then performs a bounded GitHub-only scan for managed in-progress,
  attention, review-owner, and interrupted-handoff cases in repositories
  already authorized for issue automation. The scan is independent of
  Search and Implement's eligible-candidate filter, so stranded
  in-progress issues are not invisible forever (Req 1).
* Shared labels, supported attempt comments, exact PR state, and terminal
  evidence are evaluated with the same policy as normal finalization. A
  known interrupted transition is completed only when stop, preservation,
  and pending-mutation evidence is conclusive. Every retry re-reads
  current evidence and abandons obsolete transitions when a successor,
  operator hold, or contradiction is observed (Req 2).
* Silence, old timestamps, sleep, unavailable local workflow records, or
  an unreachable deployment are uncertain ownership, never release
  evidence. The reconciler surfaces needs-attention while retaining the
  old blocking status when necessary. It never age-clears manual/legacy
  in-progress labels and never authorizes takeover because another
  deployment is absent from this device's Temporal service (Req 3).
* Unknown push/PR/merge outcomes are respected: known accepted results
  are read-and-reconciled, but an unmerged PR is never proof that a
  delayed merge request cannot still complete. Unresolved external
  operations require attention, not a timed unlock (Req 4).
* Reconciliation is retry-safe across multiple independent reconcilers:
  attempt identity is reused, per-attempt comment ownership is preserved,
  repeated incident reporting is coalesced, and whole-label-set updates
  are never used. Duplicate observations may occur; no global
  exactly-once scheduler guarantee is implied (Req 5).
* Local pending-sync evidence survives worker restart and GitHub outages.
  Pages, API requests, retries, and reporting frequency are bounded with
  backoff/rate-limit handling. Exhausted or incomplete scans return
  explicit partial/unknown results, never a clean repository. New
  admission and shared mutations stop during insufficient GitHub
  connectivity (Req 6).
* The supported default maintenance path registers through the existing
  Temporal scheduling/readiness mechanisms. Routine correctness never
  depends on a hidden enable flag or a permanently paused schedule.
  Missing credentials or permissions are actionable local readiness
  failures; there is no fallback to another credential and no assumption
  that GitHub was updated (Req 7).
* Last successful reconciliation, pending issue effects, ambiguous
  owners, and actionable failures are exposed through existing
  diagnostics and projections. At least one healthy authorized deployment
  is required for progress (Req 8).

This module reuses (never duplicates) the portable semantics owned
elsewhere: lifecycle interpretation and targeted label mutations in
``github_issue_lifecycle``, writer/mutation/preservation gates in
``github_issue_finalization``, and attempt identity/metadata in
``github_issue_attempt``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

# ---------------------------------------------------------------------------
# Bounds (Req 6: bounded pages, requests, retries, reporting)
# ---------------------------------------------------------------------------

#: Maximum issue-list pages examined per repository scan.
MAX_SCAN_PAGES = 5
#: Maximum issues per issue-list page.
MAX_SCAN_PER_PAGE = 100
#: Maximum issues reconciled per run (candidates beyond the budget defer to
#: the next run and are reported as partial, never as clean).
MAX_SCAN_ISSUES = 100
#: Maximum GitHub API requests per reconciliation run, including issue
#: reads, comment reads, PR reads, and label/comment writes.
MAX_SCAN_API_REQUESTS = 100
#: Maximum comments read per issue for attempt-handoff evidence.
MAX_COMMENTS_PER_ISSUE = 100
#: Maximum reconciler observation comments posted per issue per run.
#: Coalescing normally keeps this at zero or one; the bound is a failsafe.
MAX_RECONCILER_COMMENTS_PER_ISSUE = 1
#: Maximum incident (needs-attention) comments posted per issue per run.
MAX_INCIDENTS_PER_ISSUE_PER_RUN = 1
#: Backoff schedule (seconds) between GitHub retries on rate-limit/outage.
RETRY_BACKOFF_SECONDS: tuple[int, ...] = (5, 15, 60)
#: Maximum mutation retries per issue after a re-read (re-read, then retry
#: once; a second failure defers to the next run with pending evidence).
MAX_MUTATION_RETRIES_PER_ISSUE = 1

#: Default periodic maintenance cadence (UTC cron). Hourly, with skip-on-
#: overlap so duplicate observations stay bounded without a global lock.
DEFAULT_RECONCILIATION_CRON = "17 * * * *"
DEFAULT_RECONCILIATION_TIMEZONE = "UTC"
DEFAULT_RECONCILIATION_OVERLAP_MODE = "skip"
DEFAULT_RECONCILIATION_CATCHUP_MODE = "last"

#: Machine marker prefix for reconciler observation comments. Reconciler
#: comments carry their own marker (never an attempt marker) and reference
#: the observed attempt ID in the body, so per-attempt comment ownership is
#: preserved and repeated runs coalesce on the marker instead of posting
#: unbounded duplicates (Req 5).
RECONCILER_MARKER_PREFIX = "<!-- moonmind-github-reconcile:"

# ---------------------------------------------------------------------------
# Decision vocabulary
# ---------------------------------------------------------------------------

#: Reconciler actions for one issue.
ACTION_COMPLETE = "complete_interrupted_transition"
ACTION_ATTENTION = "surface_attention"
ACTION_NO_ACTION = "no_action"
ACTION_DEFERRED_UNKNOWN = "deferred_unknown"
ACTION_ABANDONED = "abandoned_obsolete"

ACTIONS = frozenset(
    {
        ACTION_COMPLETE,
        ACTION_ATTENTION,
        ACTION_NO_ACTION,
        ACTION_DEFERRED_UNKNOWN,
        ACTION_ABANDONED,
    }
)

#: Scan-result states (Req 6: explicit partial/unknown, never false clean).
SCAN_COMPLETE = "complete"
SCAN_PARTIAL = "partial"
SCAN_UNKNOWN = "unknown"


def _string(value: Any) -> str:
    if isinstance(value, bool):
        return ""
    return str(value or "").strip()


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes"}
    return bool(value)


# ---------------------------------------------------------------------------
# Req 1: reconciliation scope (independent of the eligible-candidate filter)
# ---------------------------------------------------------------------------

#: Settled states the bounded scan examines. Available, recovery-needed
#: (with usable handoff), and closed issues stay owned by the normal
#: admission/continuation paths; the scan exists so the states below are
#: not invisible forever. Review-owner cases arrive as code_review.
SCAN_IN_SCOPE_SETTLED = frozenset(
    {
        "in_progress",
        "needs_attention",
        "code_review",
        "blocked_mixed",
        "blocked_unknown",
        "blocked_open_done",
    }
)


def is_scan_in_scope(settled: str) -> bool:
    """Return True when *settled* belongs to the reconciliation scan scope."""
    return _string(settled).lower().replace("-", "_") in SCAN_IN_SCOPE_SETTLED


def classify_issue_for_scan(issue: Mapping[str, Any]) -> dict[str, Any]:
    """Classify one issue payload as in- or out-of-scan-scope (Req 1).

    Uses the shared #4176 lifecycle interpretation, so mixed labels,
    unknown workflow-status values, and open-Done inconsistencies are
    in scope (they block admission pending reconciliation) while
    Available/Recovery-needed/Closed stay with the normal paths.
    """
    from moonmind.workflows.temporal import github_issue_lifecycle as lifecycle

    interpretation = lifecycle.interpret_issue(issue)
    in_scope = interpretation.settled in SCAN_IN_SCOPE_SETTLED
    return {
        "inScope": in_scope,
        "settled": interpretation.settled,
        "blockedReason": interpretation.blocked_reason,
        "reasonCode": "scan_in_scope" if in_scope else "owned_by_normal_path",
        "summary": (
            f"Issue is {interpretation.settled}; "
            + (
                "examined by the bounded reconciliation scan."
                if in_scope
                else "owned by the normal admission/continuation path."
            )
        ),
    }


# ---------------------------------------------------------------------------
# Evidence source distinction (acceptance: local vs remote, preserve outcome)
# ---------------------------------------------------------------------------


def classify_evidence_source(
    *,
    local_terminal_record: Mapping[str, Any] | None = None,
    remote_handoff: Mapping[str, Any] | None = None,
    local_workflow_available: bool = False,
) -> dict[str, Any]:
    """Distinguish locally confirmed terminal evidence from remote status.

    A missing local workflow record says nothing about the remote owner's
    execution (design section 6.2): the result keeps the source execution
    outcome separate from the reconciler's uncertainty. ``remote_handoff``
    is a validated attempt metadata mapping (or None when no trusted
    handoff is readable); unvalidated prose never counts as evidence.
    """
    local = dict(local_terminal_record or {})
    remote = dict(remote_handoff or {})
    locally_confirmed = bool(local) and _truthy(
        local.get("terminalRecorded", local.get("terminal_recorded"))
    )
    remote_release_claimed = bool(remote) and str(
        remote.get("activity") or ""
    ).strip().lower() in {"released", "releasing"}
    # An orphaned remote attempt is reported as unresponsive, never
    # misdescribed as a locally confirmed failure (design section 9).
    remote_unresponsive = bool(remote) and not remote_release_claimed
    if not remote and not locally_confirmed and not local_workflow_available:
        remote_unresponsive = True
    source_outcome = _string(local.get("primaryOutcome", local.get("primary_outcome")))
    return {
        "locallyConfirmed": locally_confirmed,
        "remoteUnresponsive": remote_unresponsive,
        "remoteReleaseClaimed": remote_release_claimed,
        "sourceOutcome": source_outcome,
        "sourceOutcomePreserved": bool(source_outcome),
        "summary": (
            "Locally confirmed terminal evidence."
            if locally_confirmed
            else (
                "Remote owner unresponsive or no trusted handoff readable; "
                "ownership is uncertain."
                if remote_unresponsive
                else "Remote release claimed; verifying before repair."
            )
        ),
    }


# ---------------------------------------------------------------------------
# Req 2: conclusive-repair evaluation (same policy as normal finalization)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReconciliationDecision:
    """Typed decision for one issue's reconciliation."""

    action: str
    reason_code: str
    summary: str
    from_settled: str = ""
    to_target: str = ""
    retain_labels: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "reasonCode": self.reason_code,
            "summary": self.summary,
            "fromSettled": self.from_settled,
            "toTarget": self.to_target,
            "retainLabels": list(self.retain_labels),
        }


def _conclusive_terminal_evidence(
    *,
    writer_evidence: Mapping[str, Any] | None,
    mutation_evidence: Mapping[str, Any] | None,
    preservation_evidence: Mapping[str, Any] | None,
    disposition: str,
) -> tuple[bool, str]:
    """Require conclusive stop/preservation/mutation evidence (Req 2).

    Reuses the #4179 finalization gates so the reconciler applies exactly
    the same policy as normal finalization. Insufficient stop proofs
    (agent messages, timestamps, cleanup requests, unmerged-PR presence),
    unknown mutation outcomes, and unverified preservation all fail here.
    """
    from moonmind.workflows.temporal import github_issue_finalization as finalization

    if not _string(disposition):
        return False, "no proposed disposition recorded"
    writer = finalization.confirm_writer_stop(writer_evidence)
    if not writer["stopped"]:
        return False, f"writer stop inconclusive: {writer['reasonCode']}"
    mutations = finalization.settle_shared_mutations(mutation_evidence)
    if not mutations["settled"]:
        return False, f"mutations unsettled: {mutations['reasonCode']}"
    preserved = finalization.preserve_authoritative_output(preservation_evidence)
    if not preserved["preserved"]:
        return False, f"preservation unverified: {preserved['reasonCode']}"
    return True, "stop, preservation, and mutation evidence are conclusive"


def decide_issue_reconciliation(
    *,
    issue: Mapping[str, Any],
    intended_from_settled: str = "",
    intended_to_target: str = "",
    writer_evidence: Mapping[str, Any] | None = None,
    mutation_evidence: Mapping[str, Any] | None = None,
    preservation_evidence: Mapping[str, Any] | None = None,
    proposed_disposition: str = "",
    trusted_handoff_present: bool = False,
    manual_in_progress: bool = False,
    successor_observed_settled: str = "",
    operator_hold: bool = False,
    contradiction_observed: bool = False,
    pr_state: Mapping[str, Any] | None = None,
    local_terminal_record: Mapping[str, Any] | None = None,
    remote_handoff: Mapping[str, Any] | None = None,
    local_workflow_available: bool = False,
) -> ReconciliationDecision:
    """Decide one issue's reconciliation from current GitHub evidence.

    Precedence (first match wins):

    * observed successor, operator hold, or contradiction -> abandoned
      (re-read before each retry; obsolete transitions never replay);
    * inconclusive stop/preservation/mutation evidence, manual/legacy
      labels, unresponsive owners, unknown external outcomes, or mixed
      labels without conclusive repair evidence -> surface_attention
      (retaining the old blocking status) or deferred_unknown when the
      reads themselves are insufficient;
    * conclusive evidence for a known interrupted transition ->
      complete_interrupted_transition via the targeted lifecycle boundary;
    * otherwise -> no_action.
    """
    from moonmind.workflows.temporal import github_issue_lifecycle as lifecycle

    interpretation = lifecycle.interpret_issue(issue)
    from_settled = interpretation.settled

    # Abandonment first: a newer attempt, operator hold, closure, or
    # conflicting evidence invalidates old cleanup intentions (Req 2).
    if _string(successor_observed_settled):
        observed = lifecycle.interpret_issue(
            {"state": "open", "labels": []},
        )
        from dataclasses import replace as _replace

        observed = _replace(observed, settled=_string(successor_observed_settled))
        anchor = _string(intended_from_settled) or from_settled
        abandon, abandon_reason = lifecycle.should_abandon_retry(
            intended_from_settled=anchor, observed=observed
        )
        if abandon:
            return ReconciliationDecision(
                action=ACTION_ABANDONED,
                reason_code="successor_or_hold_observed",
                summary=f"Reconciliation abandoned: {abandon_reason}.",
                from_settled=from_settled,
            )
    elif operator_hold or contradiction_observed:
        anchor = _string(intended_from_settled) or from_settled
        hold_like = "needs_attention" if operator_hold else from_settled
        observed = lifecycle.interpret_issue({"state": "open", "labels": []})
        from dataclasses import replace as _replace

        observed = _replace(observed, settled=hold_like)
        abandon, abandon_reason = lifecycle.should_abandon_retry(
            intended_from_settled=anchor, observed=observed
        )
        if abandon or operator_hold or contradiction_observed:
            return ReconciliationDecision(
                action=ACTION_ABANDONED if abandon else ACTION_ATTENTION,
                reason_code=(
                    "operator_hold" if operator_hold else "contradiction_observed"
                ),
                summary=(
                    abandon_reason
                    or (
                        "Operator hold requires resolution before repair."
                        if operator_hold
                        else "Contradictory evidence requires attention, not repair."
                    )
                ),
                from_settled=from_settled,
                retain_labels=tuple(sorted(interpretation.canonical_present)),
            )
    else:
        anchor = _string(intended_from_settled) or from_settled
        if anchor and anchor != from_settled and from_settled in {
            lifecycle.SETTLED_IN_PROGRESS,
            lifecycle.SETTLED_CODE_REVIEW,
            lifecycle.SETTLED_RECOVERY_NEEDED,
            lifecycle.SETTLED_AVAILABLE,
            lifecycle.SETTLED_NEEDS_ATTENTION,
            lifecycle.SETTLED_CLOSED,
            lifecycle.SETTLED_BLOCKED_MIXED,
            lifecycle.SETTLED_BLOCKED_UNKNOWN,
            lifecycle.SETTLED_BLOCKED_OPEN_DONE,
        }:
            observed = lifecycle.interpret_issue({"state": "open", "labels": []})
            from dataclasses import replace as _replace

            observed = _replace(observed, settled=from_settled)
            abandon, abandon_reason = lifecycle.should_abandon_retry(
                intended_from_settled=anchor, observed=observed
            )
            if abandon:
                return ReconciliationDecision(
                    action=ACTION_ABANDONED,
                    reason_code="successor_observed",
                    summary=f"Reconciliation abandoned: {abandon_reason}.",
                    from_settled=from_settled,
                )

    # Manual/legacy in-progress labels: respected and surfaced, never
    # age-cleared (Req 3, design section 10).
    if manual_in_progress or (
        from_settled == lifecycle.SETTLED_IN_PROGRESS and not trusted_handoff_present
    ):
        return ReconciliationDecision(
            action=ACTION_ATTENTION,
            reason_code="manual_in_progress_surfaced",
            summary=(
                "Manual or legacy in-progress label with no trusted attempt "
                "handoff: surfaced for attention and retained, never age-cleared."
            ),
            from_settled=from_settled,
            retain_labels=tuple(sorted(interpretation.canonical_present)),
        )

    # Unrecognized workflow-status values or open-Done inconsistencies need
    # classification: surface attention, never silent admission or
    # destructive normalization (Req 3).
    if from_settled in {
        lifecycle.SETTLED_BLOCKED_UNKNOWN,
        lifecycle.SETTLED_BLOCKED_OPEN_DONE,
    }:
        # A conclusive interrupted transition may still repair a blocked
        # combination (design section 10); otherwise it stays surfaced.
        if _string(intended_to_target) and _string(proposed_disposition):
            conclusive, _ = _conclusive_terminal_evidence(
                writer_evidence=writer_evidence,
                mutation_evidence=mutation_evidence,
                preservation_evidence=preservation_evidence,
                disposition=proposed_disposition,
            )
            if conclusive and not _unknown_external_block(pr_state):
                return _complete_decision(
                    from_settled=from_settled,
                    to_target=_string(intended_to_target),
                    writer_evidence=writer_evidence,
                    proposed_disposition=proposed_disposition,
                )
        return ReconciliationDecision(
            action=ACTION_ATTENTION,
            reason_code="classification_required",
            summary=(
                "Unrecognized workflow-status value or open-Done inconsistency "
                "requires classification; surfaced without destructive normalization."
            ),
            from_settled=from_settled,
            retain_labels=tuple(sorted(interpretation.canonical_present)),
        )

    # Mixed labels block until repaired; conclusive handoff evidence permits
    # reconciliation, otherwise they stay surfaced (Req 2, design §10).
    if from_settled == lifecycle.SETTLED_BLOCKED_MIXED:
        if _string(intended_to_target) and _string(proposed_disposition):
            conclusive, detail = _conclusive_terminal_evidence(
                writer_evidence=writer_evidence,
                mutation_evidence=mutation_evidence,
                preservation_evidence=preservation_evidence,
                disposition=proposed_disposition,
            )
            if conclusive and not _unknown_external_block(pr_state):
                return _complete_decision(
                    from_settled=from_settled,
                    to_target=_string(intended_to_target),
                    writer_evidence=writer_evidence,
                    proposed_disposition=proposed_disposition,
                )
            return ReconciliationDecision(
                action=ACTION_ATTENTION,
                reason_code="mixed_labels_inconclusive",
                summary=f"Mixed labels block admission until repaired: {detail}.",
                from_settled=from_settled,
                retain_labels=tuple(sorted(interpretation.canonical_present)),
            )
        return ReconciliationDecision(
            action=ACTION_ATTENTION,
            reason_code="mixed_labels_blocked",
            summary="Mixed labels block admission pending reconciliation from evidence.",
            from_settled=from_settled,
            retain_labels=tuple(sorted(interpretation.canonical_present)),
        )

    # Uncertain ownership: silence, old timestamps, sleep, unavailable
    # local records, or unreachable deployments never authorize release or
    # takeover (Req 3). Surface needs-attention, retaining the old status.
    source = classify_evidence_source(
        local_terminal_record=local_terminal_record,
        remote_handoff=remote_handoff,
        local_workflow_available=local_workflow_available,
    )
    if source["remoteUnresponsive"] and not source["locallyConfirmed"]:
        writer = (writer_evidence or {})
        if not _truthy(writer.get("writersStopped", writer.get("writers_stopped"))):
            return ReconciliationDecision(
                action=ACTION_ATTENTION,
                reason_code="unresponsive_owner_uncertain",
                summary=(
                    "Owner responsiveness is uncertain (silence, sleep, missing "
                    "local record, or unreachable deployment): needs-attention "
                    "is surfaced while the old blocking status is retained; "
                    "no takeover authorized."
                ),
                from_settled=from_settled,
                retain_labels=tuple(sorted(interpretation.canonical_present)),
            )

    # Unknown external outcomes block repair: an unmerged PR never proves a
    # delayed merge cannot still complete (Req 4).
    if _unknown_external_block(pr_state) or _unknown_mutations(mutation_evidence):
        return ReconciliationDecision(
            action=ACTION_ATTENTION,
            reason_code="unknown_external_outcome",
            summary=(
                "Push/PR/merge outcome is unknown: attention required, "
                "not a timed unlock. An unmerged PR does not prove a delayed "
                "merge request cannot still complete."
            ),
            from_settled=from_settled,
            retain_labels=tuple(sorted(interpretation.canonical_present)),
        )

    # Known interrupted transition with conclusive evidence: complete it
    # without repeating implementation (Req 2). The source execution
    # outcome is preserved separately (acceptance: evidence distinction).
    if _string(intended_to_target) and _string(proposed_disposition):
        conclusive, detail = _conclusive_terminal_evidence(
            writer_evidence=writer_evidence,
            mutation_evidence=mutation_evidence,
            preservation_evidence=preservation_evidence,
            disposition=proposed_disposition,
        )
        if conclusive:
            return _complete_decision(
                from_settled=from_settled,
                to_target=_string(intended_to_target),
                writer_evidence=writer_evidence,
                proposed_disposition=proposed_disposition,
            )
        return ReconciliationDecision(
            action=ACTION_ATTENTION,
            reason_code="interrupted_transition_inconclusive",
            summary=f"Interrupted transition lacks conclusive evidence: {detail}.",
            from_settled=from_settled,
            retain_labels=tuple(sorted(interpretation.canonical_present)),
        )

    # In-scope states with no actionable evidence stay visible but
    # untouched; out-of-scope states are owned by the normal paths.
    if from_settled in {
        "in_progress",
        "needs_attention",
        "code_review",
    }:
        return ReconciliationDecision(
            action=ACTION_NO_ACTION,
            reason_code="no_interrupted_transition",
            summary=(
                f"Issue is {from_settled} with no known interrupted transition "
                "or actionable evidence; left for the owning attempt or operator."
            ),
            from_settled=from_settled,
        )
    return ReconciliationDecision(
        action=ACTION_NO_ACTION,
        reason_code="owned_by_normal_path",
        summary=f"Issue is {from_settled}; owned by the normal admission path.",
        from_settled=from_settled,
    )


def _complete_decision(
    *,
    from_settled: str,
    to_target: str,
    writer_evidence: Mapping[str, Any] | None,
    proposed_disposition: str,
) -> ReconciliationDecision:
    """Build a repair decision gated on the shared transition policy."""
    from moonmind.workflows.temporal import github_issue_lifecycle as lifecycle

    _ = writer_evidence
    if from_settled in {
        lifecycle.SETTLED_BLOCKED_MIXED,
        lifecycle.SETTLED_BLOCKED_UNKNOWN,
        lifecycle.SETTLED_BLOCKED_OPEN_DONE,
    }:
        # Blocked combinations never pass the shared admission guard by
        # design; conclusive terminal evidence (already verified by the
        # caller) authorizes the reconciler to finish the recorded
        # destination and drop the extraneous blockers instead.
        blocked = plan_blocked_repair_mutation(canonical_present=(), to_target=to_target)
        if not blocked["allowed"]:
            return ReconciliationDecision(
                action=ACTION_ATTENTION,
                reason_code="repair_guard_denied",
                summary=str(blocked["summary"]),
                from_settled=from_settled,
                to_target=to_target,
            )
        return ReconciliationDecision(
            action=ACTION_COMPLETE,
            reason_code="conclusive_repair",
            summary=(
                f"Conclusive terminal evidence permits repairing blocked labels "
                f"{from_settled} -> {to_target} without repeating implementation."
            ),
            from_settled=from_settled,
            to_target=to_target,
        )
    decision = lifecycle.plan_transition(
        from_settled=from_settled,
        to_target=to_target,
        evidence=_transition_evidence_for_target(to_target, proposed_disposition),
        reason=f"Reconciling interrupted transition to {to_target}: {proposed_disposition}",
    )
    if not decision.allowed:
        return ReconciliationDecision(
            action=ACTION_ATTENTION,
            reason_code="repair_guard_denied",
            summary=f"Interrupted transition denied by shared guard: {decision.summary}",
            from_settled=from_settled,
            to_target=to_target,
        )
    return ReconciliationDecision(
        action=ACTION_COMPLETE,
        reason_code="conclusive_repair",
        summary=(
            f"Conclusive terminal evidence permits completing the interrupted "
            f"transition {from_settled} -> {to_target} without repeating implementation."
        ),
        from_settled=from_settled,
        to_target=to_target,
    )


def _transition_evidence_for_target(target: str, disposition: str) -> dict[str, Any]:
    """Map a conclusive disposition onto #4176 transition guard keys."""
    if target == "to_recovery_needed":
        return {"writers_stopped": True, "handoff_published": True}
    if target == "to_available":
        return {"writers_stopped": True, "terminal_proof": True}
    if target == "to_code_review":
        return {"gates_satisfied": True, "pr_url_verified": True}
    if target == "to_closed":
        return {"completion_verified": True}
    if target == "to_needs_attention":
        return {"blocking_reason": _string(disposition) or "reconciliation attention"}
    if target == "to_in_progress":
        return {"same_attempt_active": True}
    return {}


def _unknown_mutations(mutation_evidence: Mapping[str, Any] | None) -> bool:
    from moonmind.workflows.temporal import github_issue_finalization as finalization

    if not mutation_evidence:
        return False
    settled = finalization.settle_shared_mutations(mutation_evidence)
    return not settled["settled"]


def _unknown_external_block(pr_state: Mapping[str, Any] | None) -> bool:
    """Return True when PR/merge state is unknown or unresolved (Req 4)."""
    if not pr_state:
        return False
    data = dict(pr_state)
    outcome = _string(data.get("mergeOutcome", data.get("merge_outcome"))).lower()
    if outcome in {"unknown", "pending", "lost_response", ""} and _truthy(
        data.get("mergeRequested", data.get("merge_requested"))
    ):
        return True
    # A PR that merely appears unmerged (open, mergeable unknown) never
    # proves a delayed merge cannot still complete.
    if _truthy(data.get("mergeRequested", data.get("merge_requested"))) and not _truthy(
        data.get("mergeOutcomeKnown", data.get("merge_outcome_known"))
    ):
        return True
    return False


def plan_repair_mutation(
    *,
    from_settled: str,
    to_target: str,
    current_labels: Sequence[Any] | None = None,
    proposed_disposition: str = "",
    reason: str = "",
) -> dict[str, Any]:
    """Plan targeted label ops for a conclusive interrupted transition.

    Never replaces the whole label set: only MoonMind-owned lifecycle
    labels are added/removed, destination first (Req 5, design 8.1).
    Blocked combinations are repaired through
    :func:`plan_blocked_repair_mutation` since they never pass the shared
    admission guard by design.
    """
    from moonmind.workflows.temporal import github_issue_lifecycle as lifecycle

    if from_settled in {
        lifecycle.SETTLED_BLOCKED_MIXED,
        lifecycle.SETTLED_BLOCKED_UNKNOWN,
        lifecycle.SETTLED_BLOCKED_OPEN_DONE,
    }:
        names = [
            str(item.get("name") if isinstance(item, Mapping) else item or "").strip()
            for item in (current_labels or [])
        ]
        canonical = [
            name
            for name in names
            if lifecycle.normalize_lifecycle_label(name) in lifecycle.CANONICAL_OPEN_LABELS
            or lifecycle.is_workflow_status_like(name)
        ]
        return plan_blocked_repair_mutation(canonical_present=canonical, to_target=to_target)
    decision = lifecycle.plan_transition(
        from_settled=from_settled,
        to_target=to_target,
        evidence=_transition_evidence_for_target(to_target, proposed_disposition),
        reason=reason or f"Reconciling interrupted transition to {to_target}",
    )
    if not decision.allowed:
        return {
            "allowed": False,
            "transition": decision.to_dict(),
            "mutation": None,
            "summary": decision.summary,
        }
    mutation = lifecycle.plan_label_mutation(
        from_settled=from_settled,
        to_target=to_target,
        current_labels=current_labels,
    )
    return {
        "allowed": True,
        "transition": decision.to_dict(),
        "mutation": mutation.to_dict(),
        "summary": f"Targeted repair planned: {decision.summary}",
    }


def plan_blocked_repair_mutation(
    *,
    canonical_present: Sequence[str],
    to_target: str,
) -> dict[str, Any]:
    """Plan targeted label ops repairing a blocked label combination.

    Mixed labels, unknown workflow-status values, and open-Done
    inconsistencies never pass the shared admission guard by design, so the
    reconciler finishes the recorded destination directly: the destination
    status is added first (when absent) and every other canonical status
    label is removed. Only MoonMind-owned lifecycle labels are touched;
    unrelated and ordinary labels are never replaced (Req 5, design 8.1).
    Callers must only invoke this with conclusive terminal evidence for a
    known interrupted transition (enforced by
    :func:`decide_issue_reconciliation`).
    """
    from moonmind.workflows.temporal import github_issue_lifecycle as lifecycle

    targets = {
        lifecycle.TO_IN_PROGRESS,
        lifecycle.TO_CODE_REVIEW,
        lifecycle.TO_RECOVERY_NEEDED,
        lifecycle.TO_NEEDS_ATTENTION,
        lifecycle.TO_AVAILABLE,
        lifecycle.TO_CLOSED,
    }
    if _string(to_target) not in targets:
        return {
            "allowed": False,
            "transition": None,
            "mutation": None,
            "summary": f"Blocked repair target {to_target!r} requires an explicit decision.",
        }
    destination = {
        lifecycle.TO_IN_PROGRESS: lifecycle.STATUS_IN_PROGRESS,
        lifecycle.TO_CODE_REVIEW: lifecycle.STATUS_CODE_REVIEW,
        lifecycle.TO_RECOVERY_NEEDED: lifecycle.STATUS_RECOVERY_NEEDED,
        lifecycle.TO_NEEDS_ATTENTION: lifecycle.STATUS_NEEDS_ATTENTION,
        lifecycle.TO_AVAILABLE: None,
        lifecycle.TO_CLOSED: lifecycle.STATUS_DONE,
    }[_string(to_target)]
    present = sorted({str(name).strip() for name in canonical_present if str(name).strip()})
    lowered = {name.lower() for name in present}
    to_add: list[str] = []
    if destination is not None and destination.lower() not in lowered:
        to_add.append(destination)
    to_remove = [name for name in present if destination is None or name.lower() != destination.lower()]
    return {
        "allowed": True,
        "transition": {
            "allowed": True,
            "fromSettled": "blocked",
            "toTarget": _string(to_target),
            "reasonCode": "conclusive_repair",
            "summary": f"Blocked labels repaired to {_string(to_target)} from conclusive terminal evidence.",
        },
        "mutation": {
            "labelsToAdd": to_add,
            "labelsToRemove": to_remove,
            "closeIssue": _string(to_target) == lifecycle.TO_CLOSED,
        },
        "summary": (
            f"Targeted blocked-label repair to {_string(to_target)}: "
            f"add {to_add or 'nothing'}, remove {to_remove or 'nothing'}."
        ),
    }


def classify_repair_readback(
    *,
    mutation: Mapping[str, Any] | None,
    read_back: Mapping[str, Any] | None,
    to_target: str = "",
) -> dict[str, Any]:
    """Classify the read-back of a reconciler repair (Req 2/6).

    Success requires the destination status observed (or no canonical
    status remaining for Available) and every planned removal absent. Any
    other combination stays incomplete with retained pending evidence; a
    missing read-back is outcome-unknown, never assumed applied.
    """
    from moonmind.workflows.temporal import github_issue_lifecycle as lifecycle

    plan = dict(mutation or {})
    if read_back is None:
        return {
            "outcome": lifecycle.OUTCOME_UNKNOWN,
            "detail": "Repair read-back missing; result unknown, never assumed applied.",
        }
    adds = [str(label) for label in (plan.get("labelsToAdd") or []) if str(label).strip()]
    removes = [str(label) for label in (plan.get("labelsToRemove") or []) if str(label).strip()]
    raw_labels = read_back.get("labels")
    names = [
        str(item.get("name") if isinstance(item, Mapping) else item or "").strip()
        for item in (raw_labels if isinstance(raw_labels, list) else [])
    ]
    present = {name.lower() for name in names if name}
    if plan.get("closeIssue"):
        if str(read_back.get("state") or "").strip().lower() == "closed":
            return {"outcome": lifecycle.OUTCOME_APPLIED, "detail": "Issue is closed as intended."}
        return {"outcome": lifecycle.OUTCOME_INCOMPLETE, "detail": "Close was not observed on read-back."}
    destination = adds[0] if adds else None
    if destination is not None and destination.lower() not in present:
        return {"outcome": lifecycle.OUTCOME_INCOMPLETE, "detail": "Destination status was not observed on read-back."}
    remaining = [label for label in removes if label.lower() in present]
    if remaining:
        return {
            "outcome": lifecycle.OUTCOME_INCOMPLETE,
            "detail": f"Destination observed but old status remains: {', '.join(remaining)}.",
        }
    # An unrecognized workflow-status label keeps the issue blocked_unknown
    # even when the destination is present; never discard pending evidence
    # while such a blocker remains.
    blocking_unknown = [
        name
        for name in names
        if name and lifecycle.is_workflow_status_like(name) and (destination is None or name.lower() != destination.lower())
    ]
    if blocking_unknown:
        return {
            "outcome": lifecycle.OUTCOME_INCOMPLETE,
            "detail": f"Unknown workflow-status remains: {', '.join(sorted(set(blocking_unknown)))}.",
        }
    if destination is None:
        expected_settled = {
            lifecycle.TO_AVAILABLE: lifecycle.SETTLED_AVAILABLE,
            lifecycle.TO_IN_PROGRESS: lifecycle.SETTLED_IN_PROGRESS,
            lifecycle.TO_CODE_REVIEW: lifecycle.SETTLED_CODE_REVIEW,
            lifecycle.TO_RECOVERY_NEEDED: lifecycle.SETTLED_RECOVERY_NEEDED,
            lifecycle.TO_NEEDS_ATTENTION: lifecycle.SETTLED_NEEDS_ATTENTION,
            lifecycle.TO_CLOSED: lifecycle.SETTLED_CLOSED,
        }.get(_string(to_target), lifecycle.SETTLED_AVAILABLE)
        interpretation = lifecycle.interpret_issue(read_back)
        if interpretation.settled != expected_settled:
            return {"outcome": lifecycle.OUTCOME_INCOMPLETE, "detail": "Old blocking status was not removed."}
    if not adds and not removes:
        return {"outcome": lifecycle.OUTCOME_ALREADY_APPLIED, "detail": "Desired state was already present."}
    return {"outcome": lifecycle.OUTCOME_APPLIED, "detail": "Destination status observed on read-back."}


# ---------------------------------------------------------------------------
# Req 5: retry-safe reconciler identity, ownership, coalescing
# ---------------------------------------------------------------------------


def reconciler_comment_key(*, repository: str, issue_number: int, reason_code: str) -> str:
    """Return the stable marker key for one reconciler observation."""
    return f"{RECONCILER_MARKER_PREFIX} {repository}#{issue_number}:{reason_code} -->"


def render_reconciler_comment(
    *,
    repository: str,
    issue_number: int,
    reason_code: str,
    summary: str,
    observed_attempt_id: str = "",
    next_action: str = "",
) -> str:
    """Render a bounded, coalescible reconciler observation comment."""
    key = reconciler_comment_key(
        repository=repository, issue_number=issue_number, reason_code=reason_code
    )
    lines = [
        key,
        "",
        f"Reconciler observation for `{repository}#{issue_number}` ({reason_code}).",
        "",
        _string(summary)[:1000] or "No summary recorded.",
    ]
    if _string(observed_attempt_id):
        lines.append(f"Observed attempt: `{_string(observed_attempt_id)}` (ownership unchanged).")
    if _string(next_action):
        lines.append(f"Suggested next action: **{_string(next_action)}**.")
    lines.append("")
    lines.append(
        "This observation is coalesced: repeated runs reuse this comment instead of posting duplicates."
    )
    body = "\n".join(lines)
    return body[:4000]


def should_post_reconciler_comment(
    *,
    existing_bodies: Sequence[str],
    reason_code: str,
    new_body: str,
) -> dict[str, Any]:
    """Coalesce repeated incident reporting (Req 5).

    Returns ``{"post": False}`` when an existing comment already carries
    this run's marker key with equivalent content, so two independent
    reconcilers processing the same handoff do not produce unbounded
    comments. Per-attempt comments are never matched here: the reconciler
    never updates another attempt's comment.
    """
    marker_fragment = f":{reason_code} -->"
    new_normalized = " ".join(_string(new_body).split())
    for body in existing_bodies or []:
        text = str(body or "")
        if RECONCILER_MARKER_PREFIX not in text or marker_fragment not in text:
            continue
        existing_normalized = " ".join(text.split())
        # Same marker and equivalent content: coalesce (no new comment).
        if new_normalized in existing_normalized or existing_normalized in new_normalized:
            return {
                "post": False,
                "reasonCode": "coalesced_duplicate",
                "summary": "An equivalent reconciler observation already exists; no duplicate posted.",
            }
        return {
            "post": False,
            "reasonCode": "coalesced_same_incident",
            "summary": (
                "A reconciler observation for this incident already exists; "
                "no additional comment posted this run."
            ),
        }
    return {
        "post": True,
        "reasonCode": "new_incident",
        "summary": "No existing reconciler observation for this incident; one comment may be posted.",
    }


def targeted_attention_ops(
    *,
    current_labels: Sequence[Any] | None,
    attention_already_present: bool,
) -> dict[str, Any]:
    """Plan targeted needs-attention escalation retaining old status.

    Adds ``status: needs-attention`` when absent and removes nothing: the
    old writer's blocking status is retained while its stop status is
    unknown (design section 2.1). Never a whole-label-set replacement.
    """
    from moonmind.workflows.temporal import github_issue_lifecycle as lifecycle

    names = [
        str(item.get("name") if isinstance(item, Mapping) else item or "").strip()
        for item in (current_labels or [])
    ]
    present = {name.lower() for name in names if name}
    to_add: list[str] = []
    if not attention_already_present and lifecycle.STATUS_NEEDS_ATTENTION.lower() not in present:
        to_add.append(lifecycle.STATUS_NEEDS_ATTENTION)
    return {
        "labelsToAdd": to_add,
        "labelsToRemove": [],
        "closeIssue": False,
        "summary": (
            "Targeted attention escalation; old blocking status retained."
            if to_add
            else "Attention already present; no label mutation required."
        ),
    }


# ---------------------------------------------------------------------------
# Req 6: durable pending-sync evidence (pure store helpers; file I/O in Activity)
# ---------------------------------------------------------------------------


def record_pending_effect(
    store: Mapping[str, Any] | None,
    *,
    repository: str,
    issue_number: int,
    intended_from_settled: str,
    intended_to_target: str,
    proposed_disposition: str,
    reason: str = "",
    attempt_id: str = "",
) -> dict[str, Any]:
    """Record recoverable pending-sync evidence surviving worker restart.

    Pending effects are bound to their originating attempt: callers pass the
    current handoff attemptId so a successor taking ownership cannot complete
    a predecessor's stale disposition.
    """
    data = dict(store or {})
    effects = data.get("pendingEffects")
    if not isinstance(effects, list):
        effects = []
    else:
        effects = [dict(item) for item in effects if isinstance(item, Mapping)]
    key = f"{repository}#{issue_number}"
    effects = [item for item in effects if item.get("key") != key]
    effects.append(
        {
            "key": key,
            "repository": repository,
            "issueNumber": issue_number,
            "intendedFromSettled": intended_from_settled,
            "intendedToTarget": intended_to_target,
            "proposedDisposition": proposed_disposition,
            "reason": _string(reason),
            "attemptId": _string(attempt_id),
        }
    )
    data["pendingEffects"] = effects
    return data


def drop_pending_effect(
    store: Mapping[str, Any] | None, *, repository: str, issue_number: int
) -> dict[str, Any]:
    """Remove settled pending-sync evidence for one issue."""
    data = dict(store or {})
    effects = data.get("pendingEffects")
    if not isinstance(effects, list):
        return data
    key = f"{repository}#{issue_number}"
    data["pendingEffects"] = [
        dict(item) for item in effects if isinstance(item, Mapping) and item.get("key") != key
    ]
    return data


def pending_effects_for_issue(
    store: Mapping[str, Any] | None, *, repository: str, issue_number: int
) -> list[dict[str, Any]]:
    """Return pending-sync evidence recorded for one issue."""
    data = dict(store or {})
    effects = data.get("pendingEffects")
    if not isinstance(effects, list):
        return []
    key = f"{repository}#{issue_number}"
    return [dict(item) for item in effects if isinstance(item, Mapping) and item.get("key") == key]


def merge_scan_results(
    *,
    examined: int,
    repaired: int = 0,
    surfaced: int = 0,
    deferred: int = 0,
    pages_exhausted: bool = False,
    requests_exhausted: bool = False,
    transport_error: str = "",
    rate_limited: bool = False,
) -> dict[str, Any]:
    """Merge one run's scan outcome into an explicit result (Req 6).

    Exhausted pagination/request budgets, rate limits, outages, and lost
    acknowledgments produce explicit partial/unknown results, never a
    clean-repository claim.
    """
    if transport_error or rate_limited:
        return {
            "status": SCAN_UNKNOWN,
            "reasonCode": "github_connectivity_insufficient",
            "summary": (
                f"GitHub connectivity insufficient ({transport_error or 'rate limited'}): "
                f"{examined} examined, {repaired} repaired, {surfaced} surfaced. "
                "New admission and shared mutations must stop until connectivity returns."
            ),
            "admissionAllowed": False,
            "examined": examined,
            "repaired": repaired,
            "surfaced": surfaced,
            "deferred": deferred,
        }
    if pages_exhausted or requests_exhausted:
        return {
            "status": SCAN_PARTIAL,
            "reasonCode": "budget_exhausted_partial",
            "summary": (
                f"Scan budget exhausted (pages={pages_exhausted}, requests={requests_exhausted}): "
                f"{examined} examined, {repaired} repaired, {surfaced} surfaced, "
                f"{deferred} deferred. Result is partial, not clean."
            ),
            "admissionAllowed": False,
            "examined": examined,
            "repaired": repaired,
            "surfaced": surfaced,
            "deferred": deferred,
        }
    if deferred > 0:
        return {
            "status": SCAN_PARTIAL,
            "reasonCode": "deferred_partial",
            "summary": (
                f"Bounded scan examined {examined} issues with {deferred} deferred: "
                f"{repaired} repaired, {surfaced} surfaced. Result is partial, not clean."
            ),
            "admissionAllowed": False,
            "examined": examined,
            "repaired": repaired,
            "surfaced": surfaced,
            "deferred": deferred,
        }
    return {
        "status": SCAN_COMPLETE,
        "reasonCode": "scan_complete",
        "summary": (
            f"Bounded scan examined {examined} issues: {repaired} repaired, "
            f"{surfaced} surfaced, {deferred} deferred."
        ),
        "admissionAllowed": True,
        "examined": examined,
        "repaired": repaired,
        "surfaced": surfaced,
        "deferred": deferred,
    }


# ---------------------------------------------------------------------------
# Req 7: supported default maintenance path + actionable readiness
# ---------------------------------------------------------------------------


def default_reconciliation_schedule() -> dict[str, Any]:
    """Return the supported default maintenance schedule descriptor.

    Registered through the existing Temporal scheduling/readiness
    mechanisms (the ``MoonMind.GitHubIssueReconcile`` workflow type and
    the ``github_issue.reconcile_handoffs`` activity binding). Routine
    correctness does not depend on a hidden enable flag: the descriptor
    is enabled and unpaused by default, with explicit overlap/skip and
    bounded catchup so duplicate observations stay bounded.
    """
    return {
        "workflowType": "MoonMind.GitHubIssueReconcile",
        "activityType": "github_issue.reconcile_handoffs",
        "cron": DEFAULT_RECONCILIATION_CRON,
        "timezone": DEFAULT_RECONCILIATION_TIMEZONE,
        "overlapMode": DEFAULT_RECONCILIATION_OVERLAP_MODE,
        "catchupMode": DEFAULT_RECONCILIATION_CATCHUP_MODE,
        "enabled": True,
        "paused": False,
        "summary": (
            "Default hourly bounded reconciliation through the existing "
            "Temporal workflow/activity boundary; no hidden enable flag and "
            "no permanently paused schedule."
        ),
    }


def check_reconciliation_readiness(
    *,
    token_available: bool,
    token_error: str = "",
    permission_ok: bool = True,
    permission_detail: str = "",
    repository_authorized: bool = True,
) -> dict[str, Any]:
    """Check local readiness before any GitHub mutation (Req 7).

    Missing credentials or permissions are actionable local readiness
    failures: no fallback to another credential, no assumption that
    GitHub was updated.
    """
    if not token_available:
        return {
            "ready": False,
            "reasonCode": "github_credentials_missing",
            "summary": (
                "GitHub credentials are not configured for reconciliation: "
                f"{_string(token_error) or 'no token resolved'}. "
                "Configure GITHUB_TOKEN or the workflow secret reference; "
                "no fallback credential is used and no GitHub update is assumed."
            ),
        }
    if not permission_ok:
        return {
            "ready": False,
            "reasonCode": "github_permissions_insufficient",
            "summary": (
                "GitHub permissions are insufficient for reconciliation: "
                f"{_string(permission_detail) or 'permission probe failed'}. "
                "Grant the required issue/label/comment permissions; no update is assumed."
            ),
        }
    if not repository_authorized:
        return {
            "ready": False,
            "reasonCode": "repository_not_authorized",
            "summary": (
                "Repository is not authorized for issue automation; "
                "reconciliation performs no reads or writes there."
            ),
        }
    return {
        "ready": True,
        "reasonCode": "ready",
        "summary": "Reconciliation readiness satisfied: credentials, permissions, and scope.",
    }


# ---------------------------------------------------------------------------
# Req 8: diagnostics and projections
# ---------------------------------------------------------------------------


def build_reconciliation_diagnostics(
    *,
    last_success_at: str = "",
    pending_effects: Sequence[Mapping[str, Any]] | None = None,
    ambiguous_owners: Sequence[Mapping[str, Any]] | None = None,
    failures: Sequence[Mapping[str, Any]] | None = None,
    scan: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Expose reconciliation state for existing diagnostics/projections.

    Reports the last successful reconciliation, pending issue effects,
    ambiguous owners, and actionable failures. Progress requires at least
    one healthy authorized deployment; while all deployments are off or
    GitHub is inaccessible, the result promises no updates.
    """
    pending = [dict(item) for item in (pending_effects or []) if isinstance(item, Mapping)]
    ambiguous = [dict(item) for item in (ambiguous_owners or []) if isinstance(item, Mapping)]
    failure_list = [dict(item) for item in (failures or []) if isinstance(item, Mapping)]
    scan_summary = dict(scan or {})
    healthy = bool(_string(last_success_at)) and not failure_list
    return {
        "lastSuccessfulReconciliation": _string(last_success_at),
        "pendingIssueEffects": pending,
        "ambiguousOwners": ambiguous,
        "actionableFailures": failure_list,
        "scan": scan_summary,
        "progressPossible": healthy or bool(pending) is False and bool(_string(last_success_at)),
        "summary": (
            f"Last successful reconciliation: {_string(last_success_at) or 'never'}. "
            f"{len(pending)} pending effects, {len(ambiguous)} ambiguous owners, "
            f"{len(failure_list)} actionable failures."
        ),
    }


__all__ = [
    "MAX_SCAN_PAGES",
    "MAX_SCAN_PER_PAGE",
    "MAX_SCAN_ISSUES",
    "MAX_SCAN_API_REQUESTS",
    "MAX_COMMENTS_PER_ISSUE",
    "MAX_RECONCILER_COMMENTS_PER_ISSUE",
    "MAX_INCIDENTS_PER_ISSUE_PER_RUN",
    "RETRY_BACKOFF_SECONDS",
    "MAX_MUTATION_RETRIES_PER_ISSUE",
    "DEFAULT_RECONCILIATION_CRON",
    "DEFAULT_RECONCILIATION_TIMEZONE",
    "DEFAULT_RECONCILIATION_OVERLAP_MODE",
    "DEFAULT_RECONCILIATION_CATCHUP_MODE",
    "RECONCILER_MARKER_PREFIX",
    "ACTION_COMPLETE",
    "ACTION_ATTENTION",
    "ACTION_NO_ACTION",
    "ACTION_DEFERRED_UNKNOWN",
    "ACTION_ABANDONED",
    "ACTIONS",
    "SCAN_COMPLETE",
    "SCAN_PARTIAL",
    "SCAN_UNKNOWN",
    "SCAN_IN_SCOPE_SETTLED",
    "ReconciliationDecision",
    "is_scan_in_scope",
    "classify_issue_for_scan",
    "classify_evidence_source",
    "decide_issue_reconciliation",
    "plan_repair_mutation",
    "plan_blocked_repair_mutation",
    "classify_repair_readback",
    "reconciler_comment_key",
    "render_reconciler_comment",
    "should_post_reconciler_comment",
    "targeted_attention_ops",
    "record_pending_effect",
    "drop_pending_effect",
    "pending_effects_for_issue",
    "merge_scan_results",
    "default_reconciliation_schedule",
    "check_reconciliation_readiness",
    "build_reconciliation_diagnostics",
]
