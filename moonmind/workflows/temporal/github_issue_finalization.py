"""Failed-attempt finalization with safe release and preserved-work handoffs.

Single policy entrypoint for MoonLadderStudios/MoonMind#4179 (design:
docs/Workflows/GitHubIssueStatusStateMachineDesign.md, sections 3, 4.2,
and 6). Deterministic and side-effect-free: no network I/O. Trusted
Activities/services perform GitHub reads/writes; this module decides what
the reads mean and what a failed/canceled controlling attempt may publish.

Contract summary:

* Finalization attaches to the durable controlling workflow boundary only:
  success, terminal failure, exhausted retries, blocked outcomes, and
  intentional cancellation release the controlling attempt. Internal step
  failures, remediation iterations, and legitimate external review waits
  keep the same attempt active (Req 1, via ``github_issue_admission``
  child-attempt propagation: those events never release).
* Release requires confirmed writer stop, settled push/PR/merge outcomes,
  and preserved authoritative output under the admitted save/publication
  policy before any label release. Agent messages, workflow timestamps,
  cleanup requests, and unmerged-PR presence alone are insufficient (Req 2).
* The proposed terminal comment carries preserved PR/branch revision,
  accepted and remaining requirements, primary outcome, retry history, and
  next action. Destination labels apply through the #4176 lifecycle
  boundary, are read back, and only then is the comment finalized as
  released. Incomplete delivery stays durable and visible; failed GitHub
  reporting never discards the only workspace/evidence (Req 3).
* Disposition is evidence-driven (Req 4); objective state stays separate
  from execution result so verified work is never re-bought on a reporting
  failure (Req 5).
* Abrupt termination, disconnected devices, and unknown mutation responses
  are potentially unfinalized with recoverable local pending-sync, never
  automatic release. Retries reread shared state and never knowingly
  overwrite an observed successor (Req 6).
* Both PR-only handoff and parent-owned PR-and-merge completion integrate
  here; review-owner failure routes to the missing repair/review/
  finalization phase, cancellation stays an explicit hold, and failure
  cleanup never introduces merge authority (Req 7).

This module reuses (never duplicates) the portable semantics owned
elsewhere: lifecycle transitions in ``github_issue_lifecycle``, attempt
release gating in ``github_issue_attempt``, and admission/child ownership
in ``github_issue_admission``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence


def _string(value: Any) -> str:
    if isinstance(value, bool):
        return ""
    return str(value or "").strip()


def _truthy(value: Any) -> bool:
    if value is True:
        return True
    if isinstance(value, str):
        return bool(value.strip())
    return bool(value) and value is not False and value is not None


# ---------------------------------------------------------------------------
# Req 1: controlling-attempt finalization boundary
# ---------------------------------------------------------------------------

#: Controlling terminal outcomes that release the attempt at the durable
#: execution boundary (normalized lowercase, underscores).
CONTROLLING_TERMINAL_OUTCOMES = frozenset(
    {
        "success",
        "succeeded",
        "completed",
        "failed",
        "terminal_failure",
        "exhausted_retries",
        "retry_budget_exhausted",
        "blocked",
        "blocked_outcome",
        "cancelled",
        "canceled",
        "intentional_cancellation",
    }
)

#: Events that never release the controlling attempt: the same attempt stays
#: active through internal retries, remediation, and legitimate review waits.
NON_RELEASING_EVENTS = frozenset(
    {
        "internal_retry",
        "internal_step_failure",
        "step_failure",
        "remediation_iteration",
        "remediation_retry",
        "review_wait",
        "external_review_wait",
        "awaiting_review",
        "child_failure",
        "activity_retry",
    }
)


def normalize_execution_event(value: Any) -> str:
    """Normalize an execution event/outcome onto the controlling vocabulary."""
    return _string(value).lower().replace("-", "_").replace(" ", "_")


def should_finalize_controlling_attempt(event: Any) -> dict[str, Any]:
    """Decide whether *event* releases the controlling attempt (Req 1).

    Returns ``{"finalize": bool, "reasonCode": str, "summary": str}``.
    """
    normalized = normalize_execution_event(event)
    if normalized in NON_RELEASING_EVENTS:
        return {
            "finalize": False,
            "reasonCode": "internal_event_retains_attempt",
            "summary": (
                f"Event {normalized or '<empty>'} is an internal step, remediation "
                "iteration, or legitimate review wait; the controlling attempt stays active."
            ),
        }
    if normalized in CONTROLLING_TERMINAL_OUTCOMES:
        return {
            "finalize": True,
            "reasonCode": "controlling_terminal",
            "summary": (
                f"Event {normalized} ends the controlling attempt at the durable "
                "execution boundary; terminal finalization applies."
            ),
        }
    return {
        "finalize": False,
        "reasonCode": "unknown_event_no_release",
        "summary": (
            f"Event {normalized or '<empty>'} is not a recognized controlling terminal "
            "outcome; no release is authorized without an explicit decision."
        ),
    }


#: Abrupt/disconnect vocabulary from the durable execution boundary that maps
#: onto the Req 6 potentially-unfinalized triggers instead of automatic
#: release. Termination and timeout are never silent release evidence.
ABRUPT_OUTCOME_ALIASES = frozenset(
    {
        "abrupt_termination",
        "unknown_mutation",
        "terminated",
        "termination",
        "timed_out",
        "timeout",
        "disconnected",
        "disconnected_device",
        "connection_lost",
    }
)


def execution_event_for_controlling_outcome(outcome: Any) -> dict[str, Any]:
    """Map a durable controlling-outcome value onto a finalizer event (Req 1).

    The controlling workflow boundary (preset terminal handler, run-workflow
    terminal state) reports outcomes in execution vocabulary; this thin
    adapter routes each value to exactly one finalizer path without changing
    the release semantics owned by :func:`should_finalize_controlling_attempt`
    and :func:`classify_potentially_unfinalized`:

    * controlling terminal outcomes (success, terminal failure, exhausted
      retries, blocked outcomes, intentional cancellation) -> ``finalize``;
    * internal step failures, remediation iterations, review waits ->
      ``retain`` (the same attempt stays active);
    * abrupt termination, timeout, disconnect vocabulary ->
      ``potentially_unfinalized`` (never automatic release);
    * anything else (unknown, blank, new values) -> ``no_release``.

    Returns ``{"event": str, "action": str, "summary": str}``.
    """
    normalized = normalize_execution_event(outcome)
    if normalized in ABRUPT_OUTCOME_ALIASES:
        if normalized in UNFINALIZED_TRIGGERS:
            canonical = normalized
        elif "disconnect" in normalized or "connection" in normalized:
            canonical = "disconnected_device"
        else:
            canonical = "abrupt_termination"
        return {
            "event": canonical,
            "action": "potentially_unfinalized",
            "summary": (
                f"Outcome {normalized or '<empty>'} is abrupt termination, timeout, or "
                "disconnect evidence: potentially unfinalized with recoverable "
                "local pending synchronization, never automatic release."
            ),
        }
    gate = should_finalize_controlling_attempt(normalized)
    if gate["finalize"]:
        return {
            "event": normalized,
            "action": "finalize",
            "summary": str(gate["summary"]),
        }
    if normalized in NON_RELEASING_EVENTS:
        return {
            "event": normalized,
            "action": "retain",
            "summary": str(gate["summary"]),
        }
    return {
        "event": normalized,
        "action": "no_release",
        "summary": str(gate["summary"]),
    }


# ---------------------------------------------------------------------------
# Req 2: writer stop + mutation settlement + preservation
# ---------------------------------------------------------------------------

#: Supported writer-stop confirmation methods (through the runtime boundary).
SUPPORTED_STOP_METHODS = frozenset(
    {
        "runtime_quiescence",
        "process_wait",
        "activity_poll_stopped",
        "device_disabled",
        "operator_confirmed_stop",
    }
)

#: Insufficient stop proofs that never authorize release on their own.
INSUFFICIENT_STOP_PROOFS = frozenset(
    {
        "agent_message_only",
        "workflow_timestamp_only",
        "cleanup_request_only",
        "unmerged_pr_only",
    }
)

#: Settled mutation outcomes (per push/PR/merge channel).
SETTLED_MUTATION_OUTCOMES = frozenset({"confirmed", "absent_na", "verified_absent"})
UNKNOWN_MUTATION_OUTCOMES = frozenset({"unknown", "pending", "lost_response", ""})

#: Admitted save/publication methods for preserved output.
ADMITTED_SAVE_METHODS = frozenset(
    {
        "pr_head_verified",
        "saved_branch_verified",
        "explicit_no_work",
        "local_only_owner_recovery",
    }
)


def confirm_writer_stop(evidence: Mapping[str, Any] | None) -> dict[str, Any]:
    """Confirm all associated writers stopped via the supported boundary."""
    data = dict(evidence or {})
    method = _string(data.get("stopMethod") or data.get("stop_method")).lower().replace("-", "_").replace(" ", "_")
    proof = _string(data.get("stopProof") or data.get("stop_proof")).lower().replace("-", "_").replace(" ", "_")
    if proof in INSUFFICIENT_STOP_PROOFS:
        return {
            "stopped": False,
            "reasonCode": "insufficient_stop_proof",
            "summary": (
                f"Stop proof {proof} is insufficient: a returned agent message, workflow "
                "timestamp, cleanup request, or currently unmerged PR is not stop evidence."
            ),
        }
    if not _truthy(data.get("writersStopped", data.get("writers_stopped"))):
        return {
            "stopped": False,
            "reasonCode": "writers_running",
            "summary": "Writers have not confirmed stop; release is blocked.",
        }
    if not _string(data.get("stopEvidence", data.get("stop_evidence"))):
        return {
            "stopped": False,
            "reasonCode": "stop_evidence_missing",
            "summary": "Writer stop is claimed without stop evidence; release is blocked.",
        }
    if method and method not in SUPPORTED_STOP_METHODS:
        return {
            "stopped": False,
            "reasonCode": "unsupported_stop_method",
            "summary": f"Stop method {method!r} is not a supported runtime-boundary confirmation.",
        }
    if not method:
        return {
            "stopped": False,
            "reasonCode": "stop_method_missing",
            "summary": "Writer stop lacks a supported boundary method; release is blocked.",
        }
    return {
        "stopped": True,
        "reasonCode": "writers_stopped",
        "summary": f"Writers stopped via {method} with recorded stop evidence.",
    }


def settle_shared_mutations(evidence: Mapping[str, Any] | None) -> dict[str, Any]:
    """Settle outstanding push/PR/merge outcomes before release."""
    data = dict(evidence or {})
    channels = (
        ("pushOutcome", "push_outcome", "push"),
        ("prOutcome", "pr_outcome", "pr"),
        ("mergeOutcome", "merge_outcome", "merge"),
    )
    unknown: list[str] = []
    for camel, snake, label in channels:
        raw = _string(data.get(camel, data.get(snake))).lower().replace("-", "_").replace(" ", "_")
        if not raw:
            # An absent channel (e.g. no merge attempted in a PR-only handoff)
            # is settled as absent only when explicitly declared.
            declared_absent = data.get(f"{snake}_absent", data.get(f"{camel}Absent"))
            if declared_absent is True:
                continue
            unknown.append(f"{label}:missing")
        elif raw in UNKNOWN_MUTATION_OUTCOMES:
            unknown.append(f"{label}:{raw or 'unknown'}")
        elif raw not in SETTLED_MUTATION_OUTCOMES:
            unknown.append(f"{label}:{raw}")
    if unknown:
        return {
            "settled": False,
            "reasonCode": "mutations_unsettled",
            "summary": (
                "Outstanding push/PR/merge outcomes are unknown: "
                + ", ".join(unknown)
                + ". Unknown outcomes block automatic release/takeover until resolved."
            ),
            "unknown": unknown,
        }
    return {
        "settled": True,
        "reasonCode": "mutations_settled",
        "summary": "Push/PR/merge outcomes are settled (confirmed or explicitly absent).",
        "unknown": [],
    }


def preserve_authoritative_output(evidence: Mapping[str, Any] | None) -> dict[str, Any]:
    """Preserve authoritative output under the admitted save policy."""
    data = dict(evidence or {})
    method = _string(data.get("saveMethod") or data.get("save_method")).lower().replace("-", "_").replace(" ", "_")
    if not method:
        return {
            "preserved": False,
            "reasonCode": "preservation_missing",
            "summary": "No admitted save method recorded; preservation is unverified.",
            "workspaceRetained": True,
        }
    if method not in ADMITTED_SAVE_METHODS:
        return {
            "preserved": False,
            "reasonCode": "preservation_unadmitted",
            "summary": f"Save method {method!r} is outside the admitted save/publication policy.",
            "workspaceRetained": True,
        }
    if method == "explicit_no_work":
        if _truthy(data.get("trustworthyNoWork", data.get("trustworthy_no_work"))):
            return {
                "preserved": True,
                "reasonCode": "explicit_no_work",
                "summary": "Trustworthy no-work recorded; nothing to preserve.",
                "workspaceRetained": False,
            }
        return {
            "preserved": False,
            "reasonCode": "no_work_untrusted",
            "summary": "No-work claim is untrusted; preservation is unverified.",
            "workspaceRetained": True,
        }
    if method == "local_only_owner_recovery":
        return {
            "preserved": True,
            "reasonCode": "local_only_owner_recovery",
            "summary": (
                "Work is preserved local-only for owner recovery; portable "
                "recovery is not promised and the workspace is retained."
            ),
            "workspaceRetained": True,
        }
    revision = _string(data.get("revision") or data.get("savedSha") or data.get("saved_sha") or data.get("prHeadSha") or data.get("pr_head_sha"))
    ref = _string(data.get("prUrl") or data.get("pr_url") or data.get("savedBranch") or data.get("saved_branch"))
    if not ref or not revision:
        return {
            "preserved": False,
            "reasonCode": "preservation_incomplete",
            "summary": "Admitted save lacks the preserved revision reference (PR/branch plus head SHA).",
            "workspaceRetained": True,
        }
    if not _truthy(data.get("preservationVerified", data.get("preservation_verified"))):
        return {
            "preserved": False,
            "reasonCode": "preservation_unverified",
            "summary": "Preserved revision is recorded but not verified; release is blocked.",
            "workspaceRetained": True,
        }
    return {
        "preserved": True,
        "reasonCode": "preservation_verified",
        "summary": f"Authoritative output preserved via {method} at {ref}@{revision}.",
        "workspaceRetained": False,
    }


# ---------------------------------------------------------------------------
# Req 4: evidence-driven disposition
# ---------------------------------------------------------------------------

#: Disposition targets expressed as lifecycle transition targets (#4176).
DISPOSITION_RECOVERY_NEEDED = "to_recovery_needed"
DISPOSITION_AVAILABLE = "to_available"
DISPOSITION_CODE_REVIEW = "to_code_review"
DISPOSITION_CLOSED = "to_closed"
DISPOSITION_NEEDS_ATTENTION = "to_needs_attention"


def choose_disposition(evidence: Mapping[str, Any] | None) -> dict[str, Any]:
    """Choose the terminal disposition from evidence (Req 4).

    Precedence (first match wins):
    intentional cancellation/hold -> needs_attention (hold, never auto-rerun);
    unsafe recovery / exhausted budget / unresolved decision -> needs_attention;
    verified objective satisfaction -> closed;
    fulfilled implementation awaiting normal review -> code_review;
    safe unfinished portable work -> recovery_needed;
    trustworthy no-work plus allowed fresh retry -> available.
    """
    data = dict(evidence or {})
    if _truthy(data.get("intentionalCancellation", data.get("intentional_cancellation"))) or _truthy(
        data.get("operatorHold", data.get("operator_hold"))
    ):
        return {
            "disposition": DISPOSITION_NEEDS_ATTENTION,
            "reasonCode": "cancellation_hold",
            "summary": "Intentional cancellation/hold: explicit hold with no automatic replacement work.",
            "schedulesReplacement": False,
        }
    if (
        _truthy(data.get("unsafeRecovery", data.get("unsafe_recovery")))
        or _truthy(data.get("budgetExhausted", data.get("budget_exhausted")))
        or _truthy(data.get("unresolvedDecision", data.get("unresolved_decision")))
    ):
        return {
            "disposition": DISPOSITION_NEEDS_ATTENTION,
            "reasonCode": "unsafe_or_exhausted",
            "summary": "Unsafe recovery, exhausted budget, or unresolved decision requires attention.",
            "schedulesReplacement": False,
        }
    if _truthy(data.get("completionVerified", data.get("completion_verified"))):
        return {
            "disposition": DISPOSITION_CLOSED,
            "reasonCode": "objective_satisfied",
            "summary": "Objective verified satisfied under the existing completion/closure policy.",
            "schedulesReplacement": False,
        }
    if _truthy(data.get("gatesSatisfied", data.get("gates_satisfied"))) and _truthy(
        data.get("prUrlVerified", data.get("pr_url_verified"))
    ):
        return {
            "disposition": DISPOSITION_CODE_REVIEW,
            "reasonCode": "awaiting_review",
            "summary": "Fulfilled implementation awaiting normal review becomes code-review.",
            "schedulesReplacement": False,
        }
    if _truthy(data.get("portableWorkSafe", data.get("portable_work_safe"))) and _truthy(
        data.get("preservationVerified", data.get("preservation_verified"))
    ):
        return {
            "disposition": DISPOSITION_RECOVERY_NEEDED,
            "reasonCode": "safe_unfinished_portable",
            "summary": "Safe unfinished portable work becomes recovery-needed.",
            "schedulesReplacement": False,
        }
    if _truthy(data.get("trustworthyNoWork", data.get("trustworthy_no_work"))) and _truthy(
        data.get("freshRetryAllowed", data.get("fresh_retry_allowed"))
    ):
        return {
            "disposition": DISPOSITION_AVAILABLE,
            "reasonCode": "safe_no_work_retry",
            "summary": "Trustworthy no-work plus allowed fresh retry becomes Available with retained history.",
            "schedulesReplacement": False,
        }
    return {
        "disposition": DISPOSITION_NEEDS_ATTENTION,
        "reasonCode": "default_attention",
        "summary": "Evidence does not establish a safe next action; attention required.",
        "schedulesReplacement": False,
    }


# ---------------------------------------------------------------------------
# Req 3: proposed terminal comment
# ---------------------------------------------------------------------------

MAX_COMMENT_REQUIREMENTS = 10


def render_terminal_comment(
    *,
    repository: str = "",
    issue_number: int = 0,
    attempt_id: str = "",
    primary_outcome: str = "",
    pr_url: Any = "",
    pr_head_sha: Any = "",
    pr_base: Any = "",
    saved_branch: Any = "",
    saved_sha: Any = "",
    met_requirements: Sequence[Any] | None = None,
    remaining_requirements: Sequence[Any] | None = None,
    retry_history: Any = "",
    next_action: str = "",
    disposition: str = "",
) -> str:
    """Render the proposed terminal comment body (Req 3).

    Pure rendering: GitHub writes stay at the trusted Activity boundary.
    """
    repo = _string(repository)
    issue_ref = f"{repo}#{issue_number}" if repo and issue_number else f"issue #{issue_number or '?'}"
    lines = [
        f"## MoonMind terminal handoff for {issue_ref}",
        "",
        f"Attempt `{_string(attempt_id) or 'unknown'}` ended with primary outcome **{_string(primary_outcome) or 'unknown'}**.",
        f"Proposed disposition: **{_string(disposition) or 'undecided'}**.",
    ]
    preserved: list[str] = []
    if _string(pr_url):
        preserved.append(
            f"PR {_string(pr_url)} @ {_string(pr_head_sha) or 'unknown head'} (base {_string(pr_base) or 'unknown'})"
        )
    if _string(saved_branch) or _string(saved_sha):
        preserved.append(f"saved {_string(saved_branch) or '?'}@{_string(saved_sha) or 'unknown'}")
    lines.append("Preserved work: " + ("; ".join(preserved) + "." if preserved else "none recorded."))
    met = [_string(item) for item in (met_requirements or []) if _string(item)][:MAX_COMMENT_REQUIREMENTS]
    remaining = [_string(item) for item in (remaining_requirements or []) if _string(item)][:MAX_COMMENT_REQUIREMENTS]
    lines.append("Accepted requirements: " + ("; ".join(met) + "." if met else "none recorded."))
    lines.append("Remaining requirements: " + ("; ".join(remaining) + "." if remaining else "none recorded."))
    lines.append(f"Retry history: {_string(retry_history) or 'none recorded'}.")
    lines.append(f"Next action: **{_string(next_action) or 'undecided'}**.")
    lines.append("")
    lines.append("Incomplete delivery remains durable and visible for reconciliation.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Req 5: objective state vs execution result
# ---------------------------------------------------------------------------

AUXILIARY_FAILURE_KINDS = frozenset({"preservation", "status_synchronization", "publication", "cleanup"})


def separate_objective_from_execution(
    *,
    execution_outcome: str = "",
    objective_verified: bool = False,
    auxiliary_failures: Sequence[Any] | None = None,
) -> dict[str, Any]:
    """Keep objective state separate from execution result (Req 5).

    A verified implementation/merge is never re-bought because final
    reporting failed; auxiliary failures are distinguished, not merged into
    the primary outcome.
    """
    aux = [_string(item).lower().replace("-", "_") for item in (auxiliary_failures or []) if _string(item)]
    unknown_aux = [item for item in aux if item not in AUXILIARY_FAILURE_KINDS]
    if objective_verified:
        return {
            "rebuyImplementation": False,
            "reasonCode": "objective_preserved",
            "summary": (
                f"Objective already verified; execution outcome {execution_outcome or 'unknown'} "
                "does not re-buy implementation. Remaining work is "
                + (", ".join(aux) if aux else "final reporting")
                + "."
            ),
            "auxiliaryFailures": aux,
            "unknownAuxiliaryKinds": unknown_aux,
        }
    return {
        "rebuyImplementation": True,
        "reasonCode": "objective_unverified",
        "summary": (
            f"Objective unverified and execution outcome is {execution_outcome or 'unknown'}; "
            "remaining requirements stay open."
        ),
        "auxiliaryFailures": aux,
        "unknownAuxiliaryKinds": unknown_aux,
    }


def partial_pr_justifies_code_review(*, gates_satisfied: bool, pr_url_verified: bool) -> dict[str, Any]:
    """A partial PR alone never justifies code-review (Req 5)."""
    if gates_satisfied and pr_url_verified:
        return {
            "allowed": True,
            "reasonCode": "gates_satisfied",
            "summary": "Controlling gates satisfied with a verified PR; code-review is justified.",
        }
    return {
        "allowed": False,
        "reasonCode": "partial_pr_insufficient",
        "summary": "A partial PR alone does not justify code-review when controlling gates are unsatisfied.",
    }


# ---------------------------------------------------------------------------
# Req 6: abrupt termination / disconnect / unknown mutation
# ---------------------------------------------------------------------------

UNFINALIZED_TRIGGERS = frozenset({"abrupt_termination", "disconnected_device", "unknown_mutation"})


def classify_potentially_unfinalized(evidence: Mapping[str, Any] | None) -> dict[str, Any]:
    """Treat abrupt/disconnect/unknown-mutation as potentially unfinalized."""
    data = dict(evidence or {})
    triggers = {normalize_execution_event(item) for item in (
        data.get("triggers") if isinstance(data.get("triggers"), Sequence) and not isinstance(data.get("triggers"), (str, bytes)) else [data.get("trigger")]
    ) if _string(item)}
    hit = sorted(triggers & {t.replace("-", "_") for t in UNFINALIZED_TRIGGERS} | triggers & UNFINALIZED_TRIGGERS)
    if hit or _truthy(data.get("unknownMutation", data.get("unknown_mutation"))):
        return {
            "potentiallyUnfinalized": True,
            "reasonCode": "potentially_unfinalized",
            "summary": (
                "Abrupt termination, disconnected device, or unknown mutation response: "
                "potentially unfinalized, not automatic release."
            ),
            "triggers": hit,
        }
    if _truthy(data.get("githubUnavailable", data.get("github_unavailable"))):
        return {
            "potentiallyUnfinalized": True,
            "reasonCode": "github_unavailable",
            "summary": "GitHub unavailable: recoverable local pending synchronization recorded.",
            "triggers": ["github_unavailable"],
        }
    return {
        "potentiallyUnfinalized": False,
        "reasonCode": "no_unfinalized_trigger",
        "summary": "No abrupt-termination, disconnect, or unknown-mutation trigger observed.",
        "triggers": [],
    }


def record_pending_sync(*, reason: str = "", workspace_retained: bool = True) -> dict[str, Any]:
    """Record recoverable local pending synchronization (Req 6)."""
    return {
        "pendingSync": True,
        "reasonCode": "pending_sync",
        "summary": (
            f"Recoverable local pending synchronization recorded ({_string(reason) or 'GitHub unavailable'}). "
            "Retry rereads current shared state before acting."
        ),
        "workspaceRetained": bool(workspace_retained),
    }


def should_abandon_for_successor(*, intended_from_settled: str, observed_settled: str) -> dict[str, Any]:
    """Never knowingly overwrite an observed successor's status (Req 6).

    Thin adapter over the #4176 ``should_abandon_retry`` boundary.
    """
    from moonmind.workflows.temporal import github_issue_lifecycle as _lifecycle

    observed = _lifecycle.interpret_issue(
        {"state": "open", "labels": [_settled_to_label(observed_settled)] if _settled_to_label(observed_settled) else []}
    )
    # When the observed settled string is already a lifecycle settled state,
    # interpret directly instead of round-tripping through a label.
    if not _settled_to_label(observed_settled) and _string(observed_settled) in {
        _lifecycle.SETTLED_AVAILABLE,
        _lifecycle.SETTLED_IN_PROGRESS,
        _lifecycle.SETTLED_RECOVERY_NEEDED,
        _lifecycle.SETTLED_CODE_REVIEW,
        _lifecycle.SETTLED_NEEDS_ATTENTION,
        _lifecycle.SETTLED_CLOSED,
        _lifecycle.SETTLED_BLOCKED_MIXED,
        _lifecycle.SETTLED_BLOCKED_UNKNOWN,
        _lifecycle.SETTLED_BLOCKED_OPEN_DONE,
    }:
        from dataclasses import replace as _replace

        observed = _replace(observed, settled=_string(observed_settled))
    abandon, reason = _lifecycle.should_abandon_retry(
        intended_from_settled=intended_from_settled, observed=observed
    )
    return {"abandon": bool(abandon), "reason": reason}


def _settled_to_label(settled: str) -> str:
    mapping = {
        "in_progress": "status: in-progress",
        "recovery_needed": "status: recovery-needed",
        "code_review": "status: code-review",
        "needs_attention": "status: needs-attention",
    }
    return mapping.get(_string(settled), "")


# ---------------------------------------------------------------------------
# Req 7: PR-only vs parent PR-and-merge completion
# ---------------------------------------------------------------------------

COMPLETION_PR_ONLY = "pr_only_handoff"
COMPLETION_PARENT_PR_AND_MERGE = "parent_pr_and_merge"


def route_completion_handoff(
    *,
    completion_mode: str = "",
    review_owner_ended: bool = False,
    cancellation_hold: bool = False,
) -> dict[str, Any]:
    """Integrate PR-only and parent PR-and-merge completion (Req 7)."""
    mode = _string(completion_mode).lower().replace("-", "_").replace(" ", "_")
    if cancellation_hold:
        return {
            "route": "explicit_hold",
            "reasonCode": "cancellation_hold",
            "summary": "Cancellation remains an explicit hold; no replacement work scheduled.",
            "mergeAuthorized": False,
        }
    if mode in {"pr_only", COMPLETION_PR_ONLY}:
        if review_owner_ended:
            return {
                "route": "missing_finalization_phase",
                "reasonCode": "review_owner_failure",
                "summary": "Review-owner failure routes to the missing repair/review/finalization phase.",
                "mergeAuthorized": False,
            }
        return {
            "route": COMPLETION_PR_ONLY,
            "reasonCode": "pr_only",
            "summary": "PR-only handoff: review/merge journey owns remaining work.",
            "mergeAuthorized": False,
        }
    if mode in {"parent_pr_and_merge", COMPLETION_PARENT_PR_AND_MERGE, "parent"}:
        if review_owner_ended:
            return {
                "route": "missing_finalization_phase",
                "reasonCode": "review_owner_failure",
                "summary": "Review-owner failure routes to the missing repair/review/finalization phase.",
                "mergeAuthorized": False,
            }
        return {
            "route": COMPLETION_PARENT_PR_AND_MERGE,
            "reasonCode": "parent_pr_and_merge",
            "summary": "Parent-owned PR-and-merge completion under the existing merge contract.",
            "mergeAuthorized": True,
        }
    return {
        "route": "needs_decision",
        "reasonCode": "unknown_completion_mode",
        "summary": f"Unknown completion mode {completion_mode!r}; no merge authority granted in failure cleanup.",
        "mergeAuthorized": False,
    }


# ---------------------------------------------------------------------------
# Composition: plan one failed-attempt finalization (Reqs 1-7)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FinalizationPlan:
    """Typed plan for one failed-attempt finalization."""

    releasable: bool
    reason_code: str
    summary: str
    disposition: str = ""
    transition: dict[str, Any] | None = None
    mutation: dict[str, Any] | None = None
    terminal_comment: str = ""
    pending_sync: dict[str, Any] | None = None
    workspace_retained: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "releasable": self.releasable,
            "reasonCode": self.reason_code,
            "summary": self.summary,
            "disposition": self.disposition,
            "transition": self.transition,
            "mutation": self.mutation,
            "terminalComment": self.terminal_comment,
            "pendingSync": self.pending_sync,
            "workspaceRetained": self.workspace_retained,
        }


def plan_failed_attempt_finalization(
    *,
    repository: str = "",
    issue_number: int = 0,
    from_settled: str = "in_progress",
    execution_event: Any = "",
    writer_evidence: Mapping[str, Any] | None = None,
    mutation_evidence: Mapping[str, Any] | None = None,
    preservation_evidence: Mapping[str, Any] | None = None,
    disposition_evidence: Mapping[str, Any] | None = None,
    current_labels: Sequence[Any] | None = None,
    reason: str = "",
    attempt_id: str = "",
    primary_outcome: str = "",
    met_requirements: Sequence[Any] | None = None,
    remaining_requirements: Sequence[Any] | None = None,
    retry_history: Any = "",
    next_action: str = "",
) -> FinalizationPlan:
    """Compose writer/mutation/preservation/disposition into a release plan.

    Returns a non-releasable plan with ``workspaceRetained=True`` whenever
    any gate fails: failed reporting never discards the only workspace.
    """
    from moonmind.workflows.temporal import github_issue_lifecycle as _lifecycle

    unfinalized = classify_potentially_unfinalized(
        {"trigger": execution_event, "githubUnavailable": (disposition_evidence or {}).get("githubUnavailable", (disposition_evidence or {}).get("github_unavailable"))}
    )
    if unfinalized["potentiallyUnfinalized"] and (
        normalize_execution_event(execution_event) in UNFINALIZED_TRIGGERS
        or _truthy((mutation_evidence or {}).get("unknownMutation", (mutation_evidence or {}).get("unknown_mutation")))
    ):
        pending = record_pending_sync(reason=str(unfinalized["summary"]))
        return FinalizationPlan(
            releasable=False,
            reason_code="potentially_unfinalized",
            summary=str(unfinalized["summary"]),
            pending_sync=pending,
            workspace_retained=True,
        )
    gate = should_finalize_controlling_attempt(execution_event)
    if not gate["finalize"]:
        return FinalizationPlan(
            releasable=False,
            reason_code=str(gate["reasonCode"]),
            summary=str(gate["summary"]),
            workspace_retained=True,
        )
    writer = confirm_writer_stop(writer_evidence)
    if not writer["stopped"]:
        return FinalizationPlan(
            releasable=False, reason_code=str(writer["reasonCode"]), summary=str(writer["summary"]), workspace_retained=True
        )
    mutations = settle_shared_mutations(mutation_evidence)
    if not mutations["settled"]:
        return FinalizationPlan(
            releasable=False,
            reason_code=str(mutations["reasonCode"]),
            summary=str(mutations["summary"]),
            workspace_retained=True,
        )
    preserved = preserve_authoritative_output(preservation_evidence)
    if not preserved["preserved"]:
        return FinalizationPlan(
            releasable=False,
            reason_code=str(preserved["reasonCode"]),
            summary=str(preserved["summary"]),
            workspace_retained=True,
        )
    if _truthy((disposition_evidence or {}).get("githubUnavailable", (disposition_evidence or {}).get("github_unavailable"))):
        pending = record_pending_sync(reason="GitHub unavailable during finalization")
        return FinalizationPlan(
            releasable=False,
            reason_code="github_unavailable",
            summary=str(pending["summary"]),
            pending_sync=pending,
            workspace_retained=True,
        )
    disposition = choose_disposition(disposition_evidence)
    target = str(disposition["disposition"])
    evidence = _transition_evidence_for_disposition(
        target, writer_evidence=writer_evidence, disposition_evidence=disposition_evidence
    )
    decision = _lifecycle.plan_transition(
        from_settled=from_settled, to_target=target, evidence=evidence, reason=reason or str(disposition["summary"])
    )
    if not decision.allowed:
        return FinalizationPlan(
            releasable=False,
            reason_code=str(decision.reason_code),
            summary=str(decision.summary),
            disposition=target,
            transition=decision.to_dict(),
            workspace_retained=True,
        )
    mutation = _lifecycle.plan_label_mutation(
        from_settled=from_settled, to_target=target, current_labels=current_labels
    )
    comment = render_terminal_comment(
        repository=repository,
        issue_number=issue_number,
        attempt_id=attempt_id,
        primary_outcome=primary_outcome or normalize_execution_event(execution_event),
        pr_url=(preservation_evidence or {}).get("prUrl", (preservation_evidence or {}).get("pr_url")),
        pr_head_sha=(preservation_evidence or {}).get("prHeadSha", (preservation_evidence or {}).get("pr_head_sha")),
        pr_base=(preservation_evidence or {}).get("prBase", (preservation_evidence or {}).get("pr_base")),
        saved_branch=(preservation_evidence or {}).get("savedBranch", (preservation_evidence or {}).get("saved_branch")),
        saved_sha=(preservation_evidence or {}).get("savedSha", (preservation_evidence or {}).get("saved_sha")),
        met_requirements=met_requirements,
        remaining_requirements=remaining_requirements,
        retry_history=retry_history,
        next_action=next_action or str(disposition["reasonCode"]),
        disposition=target,
    )
    return FinalizationPlan(
        releasable=True,
        reason_code="finalization_planned",
        summary=f"Failed-attempt finalization planned to {target} with proposed terminal comment.",
        disposition=target,
        transition=decision.to_dict(),
        mutation=mutation.to_dict(),
        terminal_comment=comment,
        workspace_retained=bool(preserved.get("workspaceRetained", False)),
    )


def _transition_evidence_for_disposition(
    target: str,
    *,
    writer_evidence: Mapping[str, Any] | None = None,
    disposition_evidence: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Map disposition evidence onto #4176 transition guard keys."""
    _ = writer_evidence
    data = dict(disposition_evidence or {})
    if target == DISPOSITION_RECOVERY_NEEDED:
        return {
            "writers_stopped": True,
            "handoff_published": _truthy(data.get("handoffPublished", data.get("handoff_published", True))),
        }
    if target == DISPOSITION_AVAILABLE:
        return {
            "writers_stopped": True,
            "terminal_proof": _truthy(data.get("terminalProof", data.get("terminal_proof", True))),
        }
    if target == DISPOSITION_CODE_REVIEW:
        return {
            "gates_satisfied": _truthy(data.get("gatesSatisfied", data.get("gates_satisfied"))),
            "pr_url_verified": _truthy(data.get("prUrlVerified", data.get("pr_url_verified"))),
        }
    if target == DISPOSITION_CLOSED:
        return {"completion_verified": _truthy(data.get("completionVerified", data.get("completion_verified")))}
    if target == DISPOSITION_NEEDS_ATTENTION:
        return {"blocking_reason": _string(data.get("blockingReason", data.get("blocking_reason"))) or "terminal attention required"}
    return {}


__all__ = [
    "ABRUPT_OUTCOME_ALIASES",
    "ADMITTED_SAVE_METHODS",
    "AUXILIARY_FAILURE_KINDS",
    "COMPLETION_PARENT_PR_AND_MERGE",
    "COMPLETION_PR_ONLY",
    "CONTROLLING_TERMINAL_OUTCOMES",
    "DISPOSITION_AVAILABLE",
    "DISPOSITION_CLOSED",
    "DISPOSITION_CODE_REVIEW",
    "DISPOSITION_NEEDS_ATTENTION",
    "DISPOSITION_RECOVERY_NEEDED",
    "INSUFFICIENT_STOP_PROOFS",
    "MAX_COMMENT_REQUIREMENTS",
    "NON_RELEASING_EVENTS",
    "SETTLED_MUTATION_OUTCOMES",
    "SUPPORTED_STOP_METHODS",
    "UNFINALIZED_TRIGGERS",
    "UNKNOWN_MUTATION_OUTCOMES",
    "FinalizationPlan",
    "choose_disposition",
    "classify_potentially_unfinalized",
    "confirm_writer_stop",
    "execution_event_for_controlling_outcome",
    "normalize_execution_event",
    "partial_pr_justifies_code_review",
    "plan_failed_attempt_finalization",
    "preserve_authoritative_output",
    "record_pending_sync",
    "render_terminal_comment",
    "route_completion_handoff",
    "separate_objective_from_execution",
    "settle_shared_mutations",
    "should_abandon_for_successor",
    "should_finalize_controlling_attempt",
]
