"""One shared advisory admission/guard boundary for an exact GitHub issue.

Single policy entrypoint for MoonLadderStudios/MoonMind#4178 (design:
docs/Workflows/GitHubIssueStatusStateMachineDesign.md, sections 5.1, 5.2,
8.2, and 9). Deterministic and side-effect-free: no network I/O. Trusted
Activities/services perform reads/writes; this module decides what the reads
mean for one pinned repository/issue identity.

Contract summary (advisory, not a distributed lock):

* Search-selected work, explicit implementation/orchestration, retries, and
  operator continuations invoke the SAME :func:`admit_exact_issue` boundary
  with pinned identities (repository, issue number, workflow/run,
  installation/attempt, entrypoint). Discovery results and local caches are
  not admission authority.
* Eligible Available or Recovery-needed work announces a stable attempt and
  applies in-progress BEFORE expensive assessment/implementation, then
  re-reads before launching work.
* The selected issue + predecessor persist exactly once with the workflow's
  trusted input context; retry/replay/recovery cannot silently substitute a
  different issue.
* Observed competing preparing/active attempts, operator holds, or
  contradictory evidence quiesce this attempt through the existing runtime
  boundary: stop shared publication, preserve output, report attention. Never
  pick a timestamp winner and never clear another attempt's in-progress.
* Paused/reconnected resumes and pre-mutation checks revalidate shared
  evidence at trusted launch/publication boundaries. Released/superseded
  attempts perform no new shared mutation.
* Internal remediation and PR-review/merge children share the controlling
  attempt. Review waits are not disappeared owners. Existing-PR repair needs
  an admitted route plus an exact PR target.

This module reuses (never duplicates) the portable Skill-adjacent semantics
owned elsewhere: lifecycle interpretation in ``github_issue_lifecycle`` and
attempt handoff / retry lineage in ``github_issue_attempt``. It adds only the
shared exact-issue admission composition those modules do not own.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from moonmind.workflows.temporal import github_issue_attempt as _attempt
from moonmind.workflows.temporal.github_issue_lifecycle import (
    SETTLED_AVAILABLE,
    SETTLED_BLOCKED_MIXED,
    SETTLED_BLOCKED_OPEN_DONE,
    SETTLED_BLOCKED_UNKNOWN,
    SETTLED_CLOSED,
    SETTLED_CODE_REVIEW,
    SETTLED_IN_PROGRESS,
    SETTLED_NEEDS_ATTENTION,
    SETTLED_RECOVERY_NEEDED,
    TO_IN_PROGRESS,
    interpret_issue,
    plan_label_mutation,
    plan_transition,
)

#: Entrypoints that share this ONE admission boundary (acceptance A). The
#: separate search-preset issue owns candidate ranking; this issue owns the
#: exact-issue admission/guard contract used by every entrypoint below.
ENTRYPOINT_SEARCH = "search"
ENTRYPOINT_EXPLICIT = "explicit"
ENTRYPOINT_ORCHESTRATION = "orchestration"
ENTRYPOINT_CONTINUATION = "continuation"
ENTRYPOINT_RETRY = "retry"

ENTRYPOINTS = frozenset(
    {
        ENTRYPOINT_SEARCH,
        ENTRYPOINT_EXPLICIT,
        ENTRYPOINT_ORCHESTRATION,
        ENTRYPOINT_CONTINUATION,
        ENTRYPOINT_RETRY,
    }
)

#: Explicit documentation of the remaining unfenced check-to-write race
#: (design sections 8.1-8.2). A read immediately before pushing/merging cannot
#: eliminate every pause-between-check-and-write race; GitHub label/comment
#: operations provide no conditional ownership acquisition. Revalidation
#: rejects observable stale work but never claims to fence a delayed external
#: request. Tests assert this limitation instead of claiming exclusivity.
UNFENCED_CHECK_TO_WRITE_RACE_NOTE = (
    "Unfenced check-to-write race remains: GitHub labels/comments offer no "
    "transactional compare-and-swap, so two devices may both announce and a "
    "delayed label/PR mutation may still race a later read. Revalidation at "
    "resume and pre-mutation boundaries rejects observable stale work; it "
    "does not fence every delayed external request. Unknown push/merge "
    "outcomes block automatic release/takeover until resolved."
)


def _string(value: Any) -> str:
    if isinstance(value, bool):
        return ""
    return str(value or "").strip()


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
class AdmissionRequest:
    """Pinned identity + trusted evidence for one exact-issue admission check."""

    repository: str = ""
    issue_number: int = 0
    workflow_id: str = ""
    run_id: str = ""
    installation_id: str = ""
    attempt_id: str = ""
    entrypoint: str = ENTRYPOINT_EXPLICIT
    predecessor_attempt_id: str = ""


@dataclass(frozen=True)
class AdmissionDecision:
    """Shared typed decision for one exact-issue admission check."""

    allowed: bool
    reason_code: str
    summary: str
    settled: str = ""
    entrypoint: str = ""
    issue_ref: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "reasonCode": self.reason_code,
            "summary": self.summary,
            "settled": self.settled,
            "entrypoint": self.entrypoint,
            "issueRef": self.issue_ref,
        }


def _deny(
    *,
    reason_code: str,
    summary: str,
    settled: str = "",
    entrypoint: str = "",
    issue_ref: str = "",
) -> AdmissionDecision:
    return AdmissionDecision(
        allowed=False,
        reason_code=reason_code,
        summary=summary,
        settled=settled,
        entrypoint=entrypoint,
        issue_ref=issue_ref,
    )


def admit_exact_issue(
    request: AdmissionRequest,
    *,
    issue: Mapping[str, Any] | None,
    attempt_context: Mapping[str, Any] | None = None,
    blockers: Sequence[Mapping[str, Any]] | None = None,
    pr_identities: Sequence[Mapping[str, Any]] | None = None,
    retry_policy: Mapping[str, Any] | None = None,
    reads_complete: Mapping[str, Any] | None = None,
    active_attempt_comments: Sequence[Mapping[str, Any]] | None = None,
    trusted_posters: Sequence[str] | None = None,
) -> AdmissionDecision:
    """Decide admission for one exact pinned repository/issue identity.

    Every entrypoint (search, explicit, orchestration, continuation, retry)
    calls this same function with its pinned identity. Discovery results and
    local caches are not consulted here: callers pass trusted reads only.

    ``reads_complete`` carries per-read completeness (``labels``,
    ``comments``, ``prs``, ``blockers``, ``retryPolicy`` booleans). Incomplete
    pagination or failed reads are unknown evidence and block admission; they
    are never treated as an empty owner set.

    ``active_attempt_comments`` are validated competing handoff metadata dicts
    observed for this issue (preparing/active, not released). Any observed
    contender blocks shared mutation admission for this attempt.
    """
    repository = _string(request.repository)
    entrypoint = _string(request.entrypoint) or ENTRYPOINT_EXPLICIT
    issue_ref = f"{repository}#{request.issue_number}" if repository and request.issue_number else ""
    if entrypoint not in ENTRYPOINTS:
        return _deny(
            reason_code="unknown_entrypoint",
            summary=f"Unknown admission entrypoint {request.entrypoint!r}; "
            "search, explicit, orchestration, continuation, and retry share one boundary.",
            entrypoint=entrypoint,
            issue_ref=issue_ref,
        )
    if not repository or type(request.issue_number) is not int or request.issue_number <= 0:
        return _deny(
            reason_code="unpinned_identity",
            summary="Admission requires a pinned owner/repository#number identity.",
            entrypoint=entrypoint,
            issue_ref=issue_ref,
        )
    reads = dict(reads_complete or {})
    for key in ("labels", "comments", "prs", "blockers", "retryPolicy"):
        if key in reads and not reads[key]:
            return _deny(
                reason_code="read_failure",
                summary=(
                    f"GitHub {key} evidence is incomplete or unreadable for {issue_ref or 'the pinned issue'}; "
                    "unknown evidence blocks admission rather than inferring an empty owner set."
                ),
                entrypoint=entrypoint,
                issue_ref=issue_ref,
            )
    if issue is None:
        return _deny(
            reason_code="read_failure",
            summary=f"Trusted issue read for {issue_ref or 'the pinned issue'} failed; admission is blocked.",
            entrypoint=entrypoint,
            issue_ref=issue_ref,
        )
    interpretation = interpret_issue(
        {"state": issue.get("state", "open"), "labels": _label_names(issue.get("labels"))}
    )
    settled = interpretation.settled
    if settled == SETTLED_CLOSED:
        return _deny(
            reason_code="closed_terminal",
            summary=f"{issue_ref or 'Issue'} is closed; closed issues are excluded from admission.",
            settled=settled,
            entrypoint=entrypoint,
            issue_ref=issue_ref,
        )
    if settled in {SETTLED_BLOCKED_MIXED, SETTLED_BLOCKED_OPEN_DONE}:
        return _deny(
            reason_code="mixed_state",
            summary=f"{issue_ref or 'Issue'} carries a mixed/inconsistent label combination; "
            "reconciliation is required before admission.",
            settled=settled,
            entrypoint=entrypoint,
            issue_ref=issue_ref,
        )
    if settled == SETTLED_BLOCKED_UNKNOWN:
        return _deny(
            reason_code="unknown_format",
            summary=f"{issue_ref or 'Issue'} carries an unrecognized workflow-status value; "
            "classification is required rather than silent admission.",
            settled=settled,
            entrypoint=entrypoint,
            issue_ref=issue_ref,
        )
    if settled == SETTLED_NEEDS_ATTENTION:
        return _deny(
            reason_code="operator_hold",
            summary=f"{issue_ref or 'Issue'} requires attention; authorized resolution is required.",
            settled=settled,
            entrypoint=entrypoint,
            issue_ref=issue_ref,
        )
    if settled in {SETTLED_IN_PROGRESS, SETTLED_CODE_REVIEW}:
        # A missing label plus an active comment, and a manual in-progress
        # label without a trusted owner, both block: the label is respected
        # and never age-cleared, and unresolved attempt evidence always wins
        # over a missing label.
        if settled == SETTLED_IN_PROGRESS and _has_active_attempt_evidence(
            attempt_context, active_attempt_comments
        ):
            return _deny(
                reason_code="active_attempt_conflict",
                summary=f"{issue_ref or 'Issue'} shows an active attempt; "
                "a missing label never overrides unresolved attempt evidence.",
                settled=settled,
                entrypoint=entrypoint,
                issue_ref=issue_ref,
            )
        if settled == SETTLED_IN_PROGRESS:
            return _deny(
                reason_code="manual_in_progress_without_trusted_owner",
                summary=f"{issue_ref or 'Issue'} carries a manual in-progress label with no trusted owner; "
                "the label is respected and not age-cleared.",
                settled=settled,
                entrypoint=entrypoint,
                issue_ref=issue_ref,
            )
        return _deny(
            reason_code="active_attempt_conflict",
            summary=f"{issue_ref or 'Issue'} is already owned ({settled}); "
            "excluded from new independent admission.",
            settled=settled,
            entrypoint=entrypoint,
            issue_ref=issue_ref,
        )
    # Only Available (fresh) and Recovery-needed (continuation) remain.
    if settled not in {SETTLED_AVAILABLE, SETTLED_RECOVERY_NEEDED}:
        return _deny(
            reason_code="unknown_format",
            summary=f"{issue_ref or 'Issue'} is in an unrecognized settled state {settled!r}.",
            settled=settled,
            entrypoint=entrypoint,
            issue_ref=issue_ref,
        )
    # Missing label plus an active comment blocks even when labels look free.
    if settled == SETTLED_AVAILABLE and _has_active_attempt_evidence(
        attempt_context, active_attempt_comments
    ):
        return _deny(
            reason_code="missing_label_with_active_attempt",
            summary=f"{issue_ref or 'Issue'} has no status label but unresolved active-attempt "
            "evidence contradicts availability; no fresh work starts on the assumption it is unowned.",
            settled=settled,
            entrypoint=entrypoint,
            issue_ref=issue_ref,
        )
    if blockers:
        known = [item for item in blockers if isinstance(item, Mapping)]
        if known:
            return _deny(
                reason_code="blocked_prerequisite",
                summary=f"{issue_ref or 'Issue'} has {len(known)} unresolved blocker(s); "
                "prerequisites apply before admission.",
                settled=settled,
                entrypoint=entrypoint,
                issue_ref=issue_ref,
            )
    pr_list = [item for item in (pr_identities or []) if isinstance(item, Mapping)]
    if any(_string(item.get("ambiguous")) in {"1", "true", "yes"} or item.get("ambiguous") is True for item in pr_list):
        return _deny(
            reason_code="ambiguous_pr_identity",
            summary=f"{issue_ref or 'Issue'} has multiple competing or ambiguous PR identities; "
            "no silent canonical selection is made.",
            settled=settled,
            entrypoint=entrypoint,
            issue_ref=issue_ref,
        )
    distinct_prs = {
        _string(
            item.get("prUrl")
            or item.get("pull_request_url")
            or item.get("pullRequestUrl")
            or item.get("url")
            or item.get("number")
            or item.get("id")
        ).strip().lower()
        for item in pr_list
    }
    distinct_prs.discard("")
    if len(distinct_prs) > 1:
        return _deny(
            reason_code="ambiguous_pr_identity",
            summary=f"{issue_ref or 'Issue'} has multiple distinct open PR identities; "
            "no silent canonical selection is made.",
            settled=settled,
            entrypoint=entrypoint,
            issue_ref=issue_ref,
        )
    linked = _linked_attempts(attempt_context)
    if linked or (retry_policy is not None):
        retry_state = _attempt.derive_retry_state(
            linked, policy=dict(retry_policy) if isinstance(retry_policy, Mapping) else None
        )
        if retry_state.get("blocked"):
            code = str(retry_state.get("reasonCode") or "retry_exhausted")
            if code in {"operator_hold", "retry_budget_exhausted", "race_approximate",
                        "lineage_gap", "incompatible_policy_lineage", "missing_policy_lineage"}:
                reason = "operator_hold" if code == "operator_hold" else (
                    "retry_exhausted" if code in {"retry_budget_exhausted", "race_approximate"} else code
                )
                return _deny(
                    reason_code=reason,
                    summary=str(retry_state.get("summary") or "Retry policy blocks automatic admission."),
                    settled=settled,
                    entrypoint=entrypoint,
                    issue_ref=issue_ref,
                )
            return _deny(
                reason_code="retry_exhausted",
                summary=str(retry_state.get("summary") or "Retry policy blocks automatic admission."),
                settled=settled,
                entrypoint=entrypoint,
                issue_ref=issue_ref,
            )
    _ = trusted_posters
    return AdmissionDecision(
        allowed=True,
        reason_code="admitted",
        summary=f"{issue_ref or 'Issue'} admitted for {entrypoint} as {settled}.",
        settled=settled,
        entrypoint=entrypoint,
        issue_ref=issue_ref,
    )


def _linked_attempts(attempt_context: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    context = attempt_context or {}
    for key in ("linkedAttempts", "linked_attempts"):
        linked = context.get(key)
        if isinstance(linked, Sequence) and not isinstance(linked, (str, bytes, bytearray)):
            return [dict(item) for item in linked if isinstance(item, Mapping)]
    return []


def _has_active_attempt_evidence(
    attempt_context: Mapping[str, Any] | None,
    active_attempt_comments: Sequence[Mapping[str, Any]] | None,
) -> bool:
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
    for comment in active_attempt_comments or []:
        if not isinstance(comment, Mapping):
            continue
        activity = _string(comment.get("activity")).lower()
        if activity in {"preparing", "active", "awaiting-review", "awaiting_review", "releasing"}:
            if comment.get("released") is True:
                continue
            return True
    return False


def admit_for_entrypoint(
    entrypoint: str,
    *,
    repository: str,
    issue_number: int,
    workflow_id: str = "",
    run_id: str = "",
    installation_id: str = "",
    attempt_id: str = "",
    predecessor_attempt_id: str = "",
    issue: Mapping[str, Any] | None,
    attempt_context: Mapping[str, Any] | None = None,
    blockers: Sequence[Mapping[str, Any]] | None = None,
    pr_identities: Sequence[Mapping[str, Any]] | None = None,
    retry_policy: Mapping[str, Any] | None = None,
    reads_complete: Mapping[str, Any] | None = None,
    active_attempt_comments: Sequence[Mapping[str, Any]] | None = None,
    trusted_posters: Sequence[str] | None = None,
) -> AdmissionDecision:
    """Invoke the SAME admission boundary for one named entrypoint.

    Acceptance A: search, explicit, orchestration, continuation, and retry all
    route through :func:`admit_exact_issue` with pinned identities. There are
    no preset-name exemptions.
    """
    return admit_exact_issue(
        AdmissionRequest(
            repository=repository,
            issue_number=issue_number,
            workflow_id=workflow_id,
            run_id=run_id,
            installation_id=installation_id,
            attempt_id=attempt_id,
            entrypoint=entrypoint,
            predecessor_attempt_id=predecessor_attempt_id,
        ),
        issue=issue,
        attempt_context=attempt_context,
        blockers=blockers,
        pr_identities=pr_identities,
        retry_policy=retry_policy,
        reads_complete=reads_complete,
        active_attempt_comments=active_attempt_comments,
        trusted_posters=trusted_posters,
    )


def announce_before_assessment(
    *,
    settled: str,
    repository: str,
    issue_number: int,
    attempt_id: str,
    current_labels: Sequence[Any] | None = None,
    reason: str = "",
    predecessor_stopped: Any = None,
    handoff_usable: Any = None,
) -> dict[str, Any]:
    """Plan the advisory claim (attempt announcement + in-progress) for Req 2.

    For eligible Available or Recovery-needed work only: announce a stable
    attempt and apply in-progress BEFORE expensive assessment/implementation.
    Callers re-read after the announcement and immediately before launching
    work. Claiming for assessment never authorizes changing blocked code or
    bypassing completion gates. Recovery-needed claims require the caller's
    validated recovery handoff (predecessor_stopped plus handoff_usable);
    fabricated defaults are never substituted.
    """
    issue_ref = f"{repository}#{issue_number}"
    if settled == SETTLED_AVAILABLE:
        decision = plan_transition(
            from_settled=settled,
            to_target=TO_IN_PROGRESS,
            evidence={"admission_passed": True, "prior_work_inspected": True},
            reason=reason or f"Advisory claim for {issue_ref} before assessment",
        )
    elif settled == SETTLED_RECOVERY_NEEDED:
        if predecessor_stopped is not True or handoff_usable is not True:
            return {
                "planned": False,
                "reasonCode": "recovery_handoff_missing",
                "summary": (
                    f"{issue_ref} is recovery-needed but the validated recovery "
                    "handoff (predecessor_stopped plus handoff_usable) is absent; "
                    "the transition is denied until the caller supplies observed handoff evidence."
                ),
                "transition": None,
                "mutation": None,
            }
        decision = plan_transition(
            from_settled=settled,
            to_target=TO_IN_PROGRESS,
            evidence={
                "predecessor_stopped": True,
                "handoff_usable": True,
                "admission_passed": True,
            },
            reason=reason or f"Advisory continuation claim for {issue_ref} before assessment",
        )
    else:
        return {
            "planned": False,
            "reasonCode": "ineligible_state",
            "summary": f"{issue_ref} in state {settled} is not eligible for an advisory claim.",
            "transition": None,
            "mutation": None,
        }
    if not decision.allowed:
        return {
            "planned": False,
            "reasonCode": decision.reason_code,
            "summary": decision.summary,
            "transition": decision.to_dict(),
            "mutation": None,
        }
    mutation = plan_label_mutation(
        from_settled=settled, to_target=TO_IN_PROGRESS, current_labels=current_labels
    )
    return {
        "planned": True,
        "reasonCode": "claim_planned",
        "summary": (
            f"Announce attempt {attempt_id} for {issue_ref} and apply in-progress "
            "before assessment; re-read after the announcement and before launching work."
        ),
        "transition": decision.to_dict(),
        "mutation": mutation.to_dict(),
    }


def persist_admission_identity(
    trusted_input_context: Mapping[str, Any] | None,
    previously_persisted: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Persist the selected issue + predecessor exactly once (Req 3).

    Activity retry, workflow replay, and failed-step recovery cannot rerun
    search and silently substitute a different issue: when a persisted
    identity already exists, a differing candidate is rejected instead of
    overwriting the admitted identity.
    """
    current = dict(trusted_input_context or {})
    previous = dict(previously_persisted or {})
    repo = _string(current.get("repository")).lower()
    number = current.get("issueNumber", current.get("issue_number", current.get("number")))
    predecessor = _string(
        current.get("predecessorAttemptId") or current.get("predecessor_attempt_id")
    )
    if not repo or type(number) is not int or number <= 0:
        # Accept string numbers from workflow payloads without widening the
        # pinned-identity contract: coerce here, keep admission strict.
        try:
            number = int(str(number).strip())
        except (TypeError, ValueError):
            number = 0
    if not repo or type(number) is not int or number <= 0:
        return {
            "persisted": False,
            "reasonCode": "unpinned_identity",
            "summary": "Admission identity requires a pinned repository and issue number.",
            "identity": {},
        }
    identity = {"repository": _string(current.get("repository")), "issueNumber": number}
    if predecessor:
        identity["predecessorAttemptId"] = predecessor
    if not previous:
        return {
            "persisted": True,
            "reasonCode": "persisted_once",
            "summary": f"Persisted admitted identity {identity['repository']}#{number} exactly once.",
            "identity": identity,
        }
    prev_repo = _string(previous.get("repository")).lower()
    prev_number = previous.get("issueNumber", previous.get("issue_number", previous.get("number")))
    try:
        prev_number = int(str(prev_number).strip())
    except (TypeError, ValueError):
        prev_number = 0
    if prev_repo != repo or prev_number != number:
        return {
            "persisted": False,
            "reasonCode": "substitution_denied",
            "summary": (
                f"Retry/replay candidate {identity['repository']}#{number} differs from "
                f"persisted {previous.get('repository')}#{prev_number}; "
                "re-running search cannot silently substitute a different issue."
            ),
            "identity": dict(previous),
        }
    prev_predecessor = _string(
        previous.get("predecessorAttemptId") or previous.get("predecessor_attempt_id")
    )
    if predecessor != prev_predecessor:
        return {
            "persisted": False,
            "reasonCode": "substitution_denied",
            "summary": (
                "Replay predecessor "
                f"{predecessor or '<absent>'} differs from persisted "
                f"{prev_predecessor or '<absent>'}; continuation lineage and "
                "preserved-work ownership cannot change after first persistence."
            ),
            "identity": dict(previous),
        }
    return {
        "persisted": True,
        "reasonCode": "already_persisted",
        "summary": "Persisted identity unchanged across retry/replay.",
        "identity": dict(previous),
    }


def contender_quiesce_decision(
    *,
    own_attempt_id: str,
    observed_contenders: Sequence[Mapping[str, Any]] | None,
    operator_hold: bool = False,
    contradictory_evidence: bool = False,
) -> dict[str, Any]:
    """Decide self-quiesce on observed conflicts (Req 4, acceptance C).

    When another preparing/active attempt, an operator hold, or contradictory
    evidence is observed: stop shared publication and self-quiesce through the
    existing runtime boundary, preserve this attempt's output within its
    admitted policy, and report attention. Never select a timestamp winner and
    never remove another attempt's in-progress status. A search may consider
    another candidate only after its own abandoned announcement and writers
    are conclusively settled.
    """
    contenders = [dict(item) for item in (observed_contenders or []) if isinstance(item, Mapping)]
    others = [item for item in contenders if _string(item.get("attemptId")) != _string(own_attempt_id)]
    active_others = [
        item
        for item in others
        if _string(item.get("activity")).lower()
        in {"preparing", "active", "awaiting-review", "awaiting_review", "releasing"}
        and item.get("released") is not True
    ]
    if operator_hold or contradictory_evidence or active_others:
        detail = (
            "operator hold observed"
            if operator_hold
            else "contradictory evidence observed"
            if contradictory_evidence
            else f"{len(active_others)} competing attempt(s) observed"
        )
        return {
            "quiesce": True,
            "reasonCode": "contender_observed",
            "summary": (
                f"Attempt {own_attempt_id or 'unknown'} quiesces: {detail}. "
                "Shared publication stops; output is preserved within the admitted policy; "
                "no timestamp winner is selected and no other attempt's in-progress status is cleared."
            ),
            "stopSharedPublication": True,
            "preserveOutput": True,
            "clearOtherInProgress": False,
            "contenders": [_string(item.get("attemptId")) for item in active_others],
            "recandidateAllowed": False,
        }
    return {
        "quiesce": False,
        "reasonCode": "no_contender",
        "summary": "No competing attempt, hold, or contradiction observed.",
        "stopSharedPublication": False,
        "preserveOutput": False,
        "clearOtherInProgress": False,
        "contenders": [],
        "recandidateAllowed": False,
    }


def recandidate_after_abandon(
    *, own_announcement_abandoned: bool, writers_settled: bool
) -> dict[str, Any]:
    """Gate search recandidacy after this attempt's own contender loss."""
    if own_announcement_abandoned and writers_settled:
        return {
            "allowed": True,
            "reasonCode": "recandidate_allowed",
            "summary": "Own abandoned announcement and writers are conclusively settled; "
            "a search may consider another candidate.",
        }
    return {
        "allowed": False,
        "reasonCode": "recandidate_blocked",
        "summary": "Another candidate may be considered only after this attempt's own "
        "abandoned announcement and writers are conclusively settled.",
    }


def revalidate_for_mutation(
    *,
    own_attempt_id: str,
    observed: Mapping[str, Any] | None,
    known_successor_attempt_id: str = "",
    released: bool = False,
    superseded: bool = False,
) -> dict[str, Any]:
    """Revalidate shared evidence before code/PR mutations (Req 5).

    A known released or superseded attempt cannot replay publication or stale
    cleanup. Call at trusted launch/publication boundaries, not only as agent
    instructions. Returns ``allowed=False`` to stop new shared effects.
    """
    observed_mapping = dict(observed or {})
    settled = _string(observed_mapping.get("settled"))
    if released or superseded or _string(observed_mapping.get("activity")) == "released":
        return {
            "allowed": False,
            "reasonCode": "released_or_superseded",
            "summary": (
                f"Attempt {own_attempt_id or 'unknown'} is released/superseded; "
                "no new shared mutation is authorized."
            ),
            "raceNote": UNFENCED_CHECK_TO_WRITE_RACE_NOTE,
        }
    if known_successor_attempt_id:
        return {
            "allowed": False,
            "reasonCode": "known_successor",
            "summary": (
                f"Attempt {own_attempt_id or 'unknown'} observed successor "
                f"{known_successor_attempt_id}; reconnection stops new shared effects."
            ),
            "raceNote": UNFENCED_CHECK_TO_WRITE_RACE_NOTE,
        }
    if settled in {SETTLED_BLOCKED_MIXED, SETTLED_BLOCKED_UNKNOWN, SETTLED_BLOCKED_OPEN_DONE}:
        return {
            "allowed": False,
            "reasonCode": "conflicting_evidence",
            "summary": "Conflicting shared evidence blocks new mutations pending reconciliation.",
            "raceNote": UNFENCED_CHECK_TO_WRITE_RACE_NOTE,
        }
    if settled == SETTLED_NEEDS_ATTENTION:
        return {
            "allowed": False,
            "reasonCode": "operator_hold",
            "summary": "Operator hold blocks new shared mutations.",
            "raceNote": UNFENCED_CHECK_TO_WRITE_RACE_NOTE,
        }
    return {
        "allowed": True,
        "reasonCode": "revalidated",
        "summary": "Shared evidence revalidated; mutation may proceed under the admitted policy. "
        + UNFENCED_CHECK_TO_WRITE_RACE_NOTE,
        "raceNote": UNFENCED_CHECK_TO_WRITE_RACE_NOTE,
    }


def should_stop_on_resume(
    *,
    own_attempt_id: str,
    released: bool = False,
    superseded: bool = False,
    known_successor_attempt_id: str = "",
    operator_hold: bool = False,
) -> dict[str, Any]:
    """Decide whether a paused/reconnected attempt must stop (Req 5)."""
    if released or superseded or known_successor_attempt_id or operator_hold:
        reason = (
            "released" if released
            else "superseded" if superseded
            else f"known successor {known_successor_attempt_id}"
            if known_successor_attempt_id
            else "operator hold"
        )
        return {
            "stop": True,
            "reasonCode": "resume_blocked",
            "summary": (
                f"Resumed attempt {own_attempt_id or 'unknown'} stops after {reason}; "
                "it performs no further issue-state or PR writes."
            ),
        }
    return {
        "stop": False,
        "reasonCode": "resume_allowed",
        "summary": "No release, successor, or hold observed; resume may revalidate before effects.",
    }


def child_attempt_context(
    controlling_attempt_id: str,
    *,
    child_kind: str,
    pr_url: str = "",
) -> dict[str, Any]:
    """Propagate the controlling attempt to remediation/review children (Req 6).

    Internal child retries share the controlling issue attempt. A legitimate
    review wait does not become a disappeared owner. Existing-PR repair needs
    an admitted route plus an exact PR target, never fresh implementation
    permission from a code-review label.
    """
    kind = _string(child_kind).lower()
    if not _string(controlling_attempt_id):
        return {
            "allowed": False,
            "reasonCode": "missing_controlling_attempt",
            "summary": "Child activity requires the controlling issue attempt.",
            "attemptId": "",
        }
    if kind in {"internal_retry", "remediation", "internal_remediation"}:
        return {
            "allowed": True,
            "reasonCode": "internal_retry_within_attempt",
            "summary": f"Internal retry stays within controlling attempt {controlling_attempt_id}.",
            "attemptId": controlling_attempt_id,
            "stayInProgress": True,
        }
    if kind in {"review", "review_wait", "pr_review", "merge"}:
        return {
            "allowed": True,
            "reasonCode": "review_child_retains_attempt",
            "summary": (
                f"Review/merge child retains controlling attempt {controlling_attempt_id}; "
                "a legitimate review wait is not a disappeared owner."
            ),
            "attemptId": controlling_attempt_id,
            "stayInProgress": True,
        }
    if kind in {"existing_pr_repair", "pr_repair", "admitted_repair"}:
        if not _string(pr_url):
            return {
                "allowed": False,
                "reasonCode": "missing_pr_target",
                "summary": "Existing-PR repair requires an admitted route and an exact PR target.",
                "attemptId": controlling_attempt_id,
            }
        return {
            "allowed": True,
            "reasonCode": "admitted_repair",
            "summary": (
                f"Existing-PR repair continues {pr_url} under controlling attempt "
                f"{controlling_attempt_id}; a code-review label alone grants no fresh permission."
            ),
            "attemptId": controlling_attempt_id,
            "prUrl": _string(pr_url),
            "stayInProgress": True,
        }
    return {
        "allowed": False,
        "reasonCode": "unknown_child_kind",
        "summary": f"Unknown child kind {child_kind!r}; no ownership handoff is granted.",
        "attemptId": controlling_attempt_id,
    }


def check_delayed_mutation_fenced(*, mutation_issued_before_check: bool) -> dict[str, Any]:
    """Expose the unfenced race honestly (acceptance D).

    An injected delayed already-issued mutation is NOT falsely reported as
    fenced: when the mutation was already issued before the ownership check,
    the result reports ``fenced=False`` with the documented race note.
    """
    if mutation_issued_before_check:
        return {
            "fenced": False,
            "reasonCode": "unfenced_race",
            "summary": (
                "An already-issued delayed mutation is not fenced by a later ownership check. "
                + UNFENCED_CHECK_TO_WRITE_RACE_NOTE
            ),
        }
    return {
        "fenced": True,
        "reasonCode": "check_precedes_mutation",
        "summary": "The ownership check precedes the mutation under the admitted policy; "
        "observable successors still abandon retries via should_abandon_retry. "
        + UNFENCED_CHECK_TO_WRITE_RACE_NOTE,
    }


__all__ = [
    "ENTRYPOINT_CONTINUATION",
    "ENTRYPOINT_EXPLICIT",
    "ENTRYPOINT_ORCHESTRATION",
    "ENTRYPOINT_RETRY",
    "ENTRYPOINT_SEARCH",
    "ENTRYPOINTS",
    "UNFENCED_CHECK_TO_WRITE_RACE_NOTE",
    "AdmissionDecision",
    "AdmissionRequest",
    "admit_exact_issue",
    "admit_for_entrypoint",
    "announce_before_assessment",
    "check_delayed_mutation_fenced",
    "child_attempt_context",
    "contender_quiesce_decision",
    "persist_admission_identity",
    "recandidate_after_abandon",
    "revalidate_for_mutation",
    "should_stop_on_resume",
]
