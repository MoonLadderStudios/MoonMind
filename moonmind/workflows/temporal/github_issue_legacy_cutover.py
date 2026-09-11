"""Conservative legacy reconciliation and coordinated deployment guidance.

Single policy entrypoint for MoonLadderStudios/MoonMind#4184 (design:
docs/Workflows/GitHubIssueStatusStateMachineDesign.md, sections 2.1, 9,
and 11). Deterministic and side-effect-free: no network I/O. Trusted
Activities/services perform GitHub reads/writes; this module decides what
legacy evidence means, what a bounded read-only assessment reports, which
repairs are allowed through the shared evidence/authorization rules, how
retained histories and pending updates behave, and when a mixed old/new
deployment may claim coordinated support.

Contract summary:

* One surviving policy owner serves every reader/writer
  (``github_issue_lifecycle``). The new path never emits or requires the
  superseded ``status: todo`` value; legacy todo labels are retained
  history only and are never bulk-cleared, and legacy work is never
  silently approved (Req 1).
* Legacy assessment is bounded, read-only, and repeatable: it reports
  exact evidence, known owner or unknown ownership, and a suggested
  action for legacy in-progress labels, old/generic start comments, open
  Done, unknown status formats, linked PRs, private-only checkpoints, and
  unsupported attempt versions. Incomplete evidence is explained, never
  inferred as no work. No timestamps-only release, broad label removal,
  PR deletion, or absent-metadata-means-clean claim (Req 2).
* Repairs run only through the shared evidence/authorization rules
  (``plan_transition`` guards plus conclusive stop/preservation
  evidence). Human labels, old comments, useful PRs, and retained
  attempt history are preserved. Conclusively stopped work may receive a
  portable handoff and appropriate state; ambiguous cases stay blocked
  for an explicit operator decision. Reopened issues are reassessed,
  never auto-freshened and never resurrected from old labels (Req 3).
* Retained-history behavior is explicit per payload family: old Activity
  inputs, tool results, preset expansions, and pending finalization
  operations either pass representative replay/compatibility handling or
  follow a tested drainage path. New executions stop writing obsolete
  shapes; frozen workflow inputs are never reinterpreted and no
  permanent alias stack is kept (Req 4).
* Coordinated release pauses/drains nonparticipating old
  claimers/publishers while installing matching code, preset
  definitions, and label/permission readiness. An unknown-format check
  in new code cannot constrain old code that ignores it, so mixed
  old/new behavior is documented and tested as unqualified until old
  bypasses stop and all participating devices conform (Req 5).
* Canonical lifecycle, preset, publishing, remediation/checkpoint, and
  operator guidance name the no-status default, recovery-vs-review
  routing, private-only limitations, and best-effort concurrency limits
  (Req 6). Temporary inventories, release steps, and rollback notes stay
  in issues or ``docs/tmp/``.
* Upgrade/rollback rehearsal uses fixtures: a rollback must not restart
  an old selector against active new-format work without a controlled
  hold/drain, and fixtures prove labels, comments, code, artifacts, and
  unrelated deployment settings survive (Req 7).

This module reuses (never duplicates) the portable semantics owned
elsewhere: lifecycle interpretation and label mutation plans in
``github_issue_lifecycle``, attempt identity/metadata/versioning in
``github_issue_attempt``, preserved-work routing in
``github_issue_continuation``, terminal disposition in
``github_issue_finalization``, interruption handling in
``github_issue_reconciliation``, and operator attention in
``github_issue_recovery_surface``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from moonmind.workflows.temporal import github_issue_attempt as _attempt
from moonmind.workflows.temporal import github_issue_attempts as _canonical_attempt
from moonmind.workflows.temporal import github_issue_lifecycle as lifecycle

#: Single surviving policy owner for every issue-status reader/writer.
SURVIVING_POLICY_OWNER = "moonmind.workflows.temporal.github_issue_lifecycle"

#: Supported attempt format consumed by this cutover (mirrors the attempt module).
SUPPORTED_ATTEMPT_VERSION = _attempt.ATTEMPT_COMMENT_FORMAT_VERSION

#: Bounds for the read-only legacy assessment (Req 2: bounded, repeatable).
MAX_COMMENTS_ASSESSED = 100
MAX_PRS_ASSESSED = 20
MAX_FINDINGS = 25
MAX_EVIDENCE_CHARS = 500

#: Reopened sentinel: GitHub exposes ``timeline``/``events`` at the trusted
#: boundary; callers pass the already-read ``was_reopened`` flag so this
#: module stays side-effect-free.
REOPENED_FLAG_KEYS = ("was_reopened", "wasReopened", "reopened")

# ---------------------------------------------------------------------------
# Req 1: caller / persisted-contract inventory naming the surviving owner
# ---------------------------------------------------------------------------

#: Inventory entry: {area, path, role, survivingPolicy}.
#: ``role`` is reader, writer, or contract. Every entry routes through the
#: surviving policy owner; the new path never requires ``status: todo``.
CALLER_INVENTORY: tuple[dict[str, str], ...] = (
    {"area": "lifecycle interpretation", "path": "moonmind/workflows/temporal/github_issue_lifecycle.py", "role": "policy-owner", "survivingPolicy": SURVIVING_POLICY_OWNER},
    {"area": "advisory admission", "path": "moonmind/workflows/temporal/github_issue_admission.py", "role": "reader", "survivingPolicy": SURVIVING_POLICY_OWNER},
    {"area": "candidate search", "path": "moonmind/workflows/temporal/github_issue_search.py", "role": "reader", "survivingPolicy": SURVIVING_POLICY_OWNER},
    {"area": "preserved-work continuation", "path": "moonmind/workflows/temporal/github_issue_continuation.py", "role": "reader", "survivingPolicy": SURVIVING_POLICY_OWNER},
    {"area": "terminal finalization", "path": "moonmind/workflows/temporal/github_issue_finalization.py", "role": "writer", "survivingPolicy": SURVIVING_POLICY_OWNER},
    {"area": "interrupted-handoff reconciliation", "path": "moonmind/workflows/temporal/github_issue_reconciliation.py", "role": "writer", "survivingPolicy": SURVIVING_POLICY_OWNER},
    {"area": "recovery/operator surface", "path": "moonmind/workflows/temporal/github_issue_recovery_surface.py", "role": "reader", "survivingPolicy": SURVIVING_POLICY_OWNER},
    {"area": "attempt handoff publication", "path": "moonmind/workflows/temporal/activities/attempt_handoff_activities.py", "role": "writer", "survivingPolicy": SURVIVING_POLICY_OWNER},
    {"area": "finalization activities", "path": "moonmind/workflows/temporal/activities/github_issue_finalization_activities.py", "role": "writer", "survivingPolicy": SURVIVING_POLICY_OWNER},
    {"area": "reconciliation activities", "path": "moonmind/workflows/temporal/activities/github_issue_reconciliation_activities.py", "role": "writer", "survivingPolicy": SURVIVING_POLICY_OWNER},
    {"area": "legacy cutover assessment activities", "path": "moonmind/workflows/temporal/activities/github_issue_legacy_cutover_activities.py", "role": "reader", "survivingPolicy": SURVIVING_POLICY_OWNER},
    {"area": "legacy cutover activity bindings", "path": "moonmind/workflows/temporal/activity_runtime.py:github_issue_assess_legacy", "role": "reader", "survivingPolicy": SURVIVING_POLICY_OWNER},
    {"area": "legacy cutover repair planning", "path": "moonmind/workflows/temporal/activity_runtime.py:github_issue_plan_legacy_repair", "role": "writer", "survivingPolicy": SURVIVING_POLICY_OWNER},
    {"area": "new-path status emission", "path": "moonmind/workflows/temporal/story_output_tools.py", "role": "writer", "survivingPolicy": SURVIVING_POLICY_OWNER},
    {"area": "seeded preset: search-and-implement", "path": "api_service/data/presets/github-issue-search-and-implement.yaml", "role": "contract", "survivingPolicy": SURVIVING_POLICY_OWNER},
    {"area": "seeded preset: implement", "path": "api_service/data/presets/github-issue-implement.yaml", "role": "contract", "survivingPolicy": SURVIVING_POLICY_OWNER},
    {"area": "seeded preset: orchestrate", "path": "api_service/data/presets/github-issue-orchestrate.yaml", "role": "contract", "survivingPolicy": SURVIVING_POLICY_OWNER},
    {"area": "seeded preset: breakdown-implement", "path": "api_service/data/presets/github-issue-breakdown-implement.yaml", "role": "contract", "survivingPolicy": SURVIVING_POLICY_OWNER},
    {"area": "seeded preset: breakdown-orchestrate", "path": "api_service/data/presets/github-issue-breakdown-orchestrate.yaml", "role": "contract", "survivingPolicy": SURVIVING_POLICY_OWNER},
    {"area": "recurring schedule: reconciliation", "path": "moonmind/workflows/temporal/github_issue_reconciliation.py:DEFAULT_RECONCILIATION_CRON", "role": "contract", "survivingPolicy": SURVIVING_POLICY_OWNER},
    {"area": "explicit issue path: implement", "path": "api_service/data/presets/github-issue-implement.yaml:explicit-issue", "role": "contract", "survivingPolicy": SURVIVING_POLICY_OWNER},
    {"area": "PR handoff boundary", "path": "moonmind/workflows/temporal/github_issue_continuation.py:route_continuation", "role": "contract", "survivingPolicy": SURVIVING_POLICY_OWNER},
    {"area": "runtime publication guard", "path": "moonmind/workflows/temporal/github_issue_continuation.py:check_publication_scope", "role": "contract", "survivingPolicy": SURVIVING_POLICY_OWNER},
    {"area": "pending GitHub effects", "path": "moonmind/workflows/temporal/github_issue_finalization.py:record_pending_sync", "role": "contract", "survivingPolicy": SURVIVING_POLICY_OWNER},
)


def verify_new_path_todo_free(
    new_path_status_values: Sequence[Any] | None = None,
) -> dict[str, Any]:
    """Verify the new path neither emits nor requires ``status: todo``.

    Callers pass the status values a new execution would emit/require
    (defaults to the canonical emission set). Legacy todo labels found on
    old issues are retained history, not approval: they never satisfy
    this check and never authorize admission by themselves.
    """
    values = list(new_path_status_values) if new_path_status_values is not None else sorted(
        lifecycle.CANONICAL_OPEN_LABELS | {lifecycle.STATUS_DONE}
    )
    offenders = [
        str(value)
        for value in values
        if str(value or "").strip().lower() == lifecycle.LEGACY_TODO_LABEL
    ]
    if offenders:
        return {
            "todoFree": False,
            "reasonCode": "todo_on_new_path",
            "summary": "New path must not emit or require status: todo; legacy todo stays retained history only.",
            "offenders": offenders,
            "survivingPolicyOwner": SURVIVING_POLICY_OWNER,
        }
    return {
        "todoFree": True,
        "reasonCode": "todo_free",
        "summary": f"New path is todo-free under {SURVIVING_POLICY_OWNER}; legacy todo labels remain retained history only.",
        "offenders": [],
        "survivingPolicyOwner": SURVIVING_POLICY_OWNER,
    }


# ---------------------------------------------------------------------------
# Req 2: bounded read-only legacy assessment
# ---------------------------------------------------------------------------

FINDING_MANUAL_STATUS = "manual_in_progress_no_handoff"
FINDING_GENERIC_START = "generic_historical_start_comment"
FINDING_MISSING_HISTORY = "missing_history"
FINDING_CONTRADICTORY_HISTORY = "contradictory_history"
FINDING_OPEN_DONE = "open_done"
FINDING_REOPENED = "reopened_reassess"
FINDING_PARTIAL_PR = "partial_pr"
FINDING_UNSUPPORTED_VERSION = "unsupported_attempt_version"
FINDING_UNKNOWN_STATUS = "unknown_status_format"
FINDING_LEGACY_TODO = "legacy_todo_retained"
FINDING_PRIVATE_ONLY = "private_only_checkpoint"
FINDING_LINKED_PRS = "linked_prs_ambiguous"
FINDING_INTERRUPTED_RELEASE = "interrupted_release"

ACTION_NO_ACTION = "no_action"
ACTION_ASSESS_PRIOR_WORK = "assess_prior_work"
ACTION_CONTINUE_PR = "continue_pr"
ACTION_REQUEST_OPERATOR = "request_operator_decision"
ACTION_RECONCILE_LABELS = "reconcile_labels"
ACTION_VERIFY_COMPLETION = "verify_completion"
ACTION_ADOPT_WITH_LINEAGE = "adopt_with_lineage"
ACTION_DRAIN_PENDING = "drain_pending"
ACTION_HOLD_RELEASE = "hold_release"

_GENERIC_START_PATTERNS = (
    "started working",
    "taking this",
    "i'll take",
    "working on this",
    "/start",
    "claimed",
)


def _truncate(text: Any, limit: int = MAX_EVIDENCE_CHARS) -> str:
    value = str(text or "")
    return value if len(value) <= limit else value[:limit] + "…"


def _comment_texts(comments: Sequence[Mapping[str, Any]] | None) -> list[str]:
    out: list[str] = []
    for comment in list(comments or [])[:MAX_COMMENTS_ASSESSED]:
        if isinstance(comment, Mapping):
            body = str(comment.get("body") or "")
            if body:
                out.append(body)
    return out


def _canonical_record(
    *,
    repository: str,
    issue_number: int,
    body: str,
    author: str,
    trusted_posters: Sequence[str],
) -> tuple[str, dict[str, Any] | None]:
    """Try the canonical production handoff format first.

    Returns (outcome, record) where outcome is "validated", "unsupported",
    or "no_marker" (caller falls through to the legacy singular format).
    The canonical format written by ``github_issue_attempts`` is authoritative;
    the singular ``github_issue_attempt`` format is retained only for actual
    legacy comments.
    """
    try:
        parsed = _canonical_attempt.parse_attempt_comment(
            body, author_login=author
        )
    except Exception:  # noqa: BLE001 - a parse crash is never clean evidence
        return "unsupported", {
            "metadata": {},
            "author": author,
            "decision": {
                "allowed": False,
                "reasonCode": "invalid_metadata",
                "summary": "Canonical attempt handoff is unparseable; rejected rather than inferred.",
            },
        }
    if parsed.status == "no_marker":
        return "no_marker", None
    if parsed.status == "ok" and parsed.handoff is not None:
        try:
            validation = _canonical_attempt.validate_attempt_handoff(
                parsed,
                expected_repository=repository,
                expected_issue_number=int(issue_number),
                trusted_posters=list(trusted_posters),
            )
        except Exception:  # noqa: BLE001 - validation crash fails closed
            return "unsupported", {
                "metadata": {},
                "author": author,
                "decision": {
                    "allowed": False,
                    "reasonCode": "invalid_metadata",
                    "summary": "Canonical attempt validation is inconclusive; rejected rather than inferred.",
                },
            }
        metadata = dict(parsed.handoff.to_dict())
        decision = {
            "allowed": bool(validation.valid),
            "reasonCode": str(validation.reason_code or "unusable"),
            "summary": str(validation.summary or "Canonical attempt handoff validated."),
        }
        record = {"metadata": metadata, "author": author, "decision": decision}
        return ("validated" if validation.valid else "unsupported"), record
    # Marker present but the machine block is missing, malformed, versioned
    # differently, or inconsistent: explicit unsupported evidence, never clean.
    reason = str(getattr(parsed, "reason_code", None) or parsed.status or "invalid_metadata")
    if reason not in {"unsupported_version", "missing_metadata", "invalid_metadata", "inconsistent_metadata",
                      "invalid_marker", "unparseable"}:
        reason = "invalid_metadata"
    code = "unsupported_version" if reason == "unsupported_version" else reason
    metadata: dict[str, Any] = {}
    handoff = getattr(parsed, "handoff", None)
    if handoff is not None and hasattr(handoff, "to_dict"):
        try:
            metadata = dict(handoff.to_dict())
        except Exception:  # noqa: BLE001 - keep the rejection, drop the payload
            metadata = {}
    return "unsupported", {
        "metadata": metadata,
        "author": author,
        "decision": {
            "allowed": False,
            "reasonCode": code,
            "summary": str(getattr(parsed, "summary", None) or "Canonical attempt handoff is unusable; rejected rather than inferred."),
        },
    }


def _parse_validated_attempts(
    *,
    repository: str,
    issue_number: int,
    comments: Sequence[Mapping[str, Any]] | None,
    trusted_posters: Sequence[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    """Return (validated, unsupported, generic_start_bodies) from comment history."""
    validated: list[dict[str, Any]] = []
    unsupported: list[dict[str, Any]] = []
    generic: list[str] = []
    for comment in list(comments or [])[:MAX_COMMENTS_ASSESSED]:
        if not isinstance(comment, Mapping):
            continue
        body = str(comment.get("body") or "")
        author = str(comment.get("author_login", comment.get("author") or ""))
        if not body:
            continue
        outcome, record = _canonical_record(
            repository=repository,
            issue_number=issue_number,
            body=body,
            author=author,
            trusted_posters=list(trusted_posters),
        )
        if outcome in {"validated", "unsupported"} and record is not None:
            if outcome == "validated":
                validated.append(record)
            else:
                unsupported.append(record)
            continue
        metadata, _error = _attempt.extract_attempt_metadata(body)
        if metadata is None:
            lowered = body.lower()
            if any(pattern in lowered for pattern in _GENERIC_START_PATTERNS):
                generic.append(body)
            continue
        decision = _attempt.validate_attempt_handoff(
            metadata,
            repository=repository,
            issue_number=issue_number,
            trusted_posters=list(trusted_posters),
            author_login=author,
        )
        record = {"metadata": dict(metadata), "author": author, "decision": decision}
        if decision.get("allowed"):
            validated.append(record)
        else:
            unsupported.append(record)
    return validated, unsupported, generic


@dataclass(frozen=True)
class LegacyFinding:
    """One bounded, non-destructive legacy observation."""

    finding: str
    evidence: str
    owner: str
    suggested_action: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "finding": self.finding,
            "evidence": self.evidence,
            "owner": self.owner,
            "suggestedAction": self.suggested_action,
        }


@dataclass
class LegacyAssessment:
    """Bounded read-only assessment of one issue's legacy evidence."""

    repository: str
    issue_number: int
    settled: str
    complete: bool
    completeness_note: str
    findings: list[LegacyFinding] = field(default_factory=list)
    preserved: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "repository": self.repository,
            "issueNumber": self.issue_number,
            "settled": self.settled,
            "complete": self.complete,
            "completenessNote": self.completeness_note,
            "findings": [finding.to_dict() for finding in self.findings],
            "preserved": list(self.preserved),
        }


def assess_legacy_issue(
    *,
    repository: str,
    issue_number: int,
    issue: Mapping[str, Any] | None = None,
    comments: Sequence[Mapping[str, Any]] | None = None,
    prs: Sequence[Mapping[str, Any]] | None = None,
    checkpoints: Sequence[Mapping[str, Any]] | None = None,
    trusted_posters: Sequence[str] = (),
    issue_flags: Mapping[str, Any] | None = None,
) -> LegacyAssessment:
    """Assess one issue's legacy evidence without mutating anything.

    Inputs are already-read GitHub evidence (issue payload, comment bodies
    with poster logins, PR states, checkpoint descriptors, reopened flag).
    Absent metadata is reported as incomplete evidence, never as proof
    that no work exists. At most ``MAX_COMMENTS_ASSESSED`` comments and
    ``MAX_PRS_ASSESSED`` PRs are examined; overflow is reported as an
    explicit incomplete-evidence note rather than a clean result.
    """
    issue_mapping = dict(issue or {})
    comment_list = list(comments or [])[:MAX_COMMENTS_ASSESSED]
    raw_prs = list(prs or [])
    pr_list = raw_prs[:MAX_PRS_ASSESSED]
    comments_truncated = isinstance(comments, Sequence) and not isinstance(comments, (str, bytes)) and len(list(comments or [])) > MAX_COMMENTS_ASSESSED
    prs_truncated = isinstance(prs, Sequence) and not isinstance(prs, (str, bytes)) and len(raw_prs) > MAX_PRS_ASSESSED
    interpretation = lifecycle.interpret_issue(issue_mapping)
    findings: list[LegacyFinding] = []
    preserved = ["human labels", "old comments", "useful PRs", "retained attempt history"]

    def _add(finding: str, evidence: str, owner: str, action: str) -> None:
        if len(findings) < MAX_FINDINGS:
            findings.append(LegacyFinding(finding, _truncate(evidence), owner, action))

    validated, unsupported, generic_starts = _parse_validated_attempts(
        repository=repository,
        issue_number=issue_number,
        comments=comment_list,
        trusted_posters=list(trusted_posters),
    )
    known_owner = validated[-1]["metadata"].get("deploymentId") if validated else ""
    if len(validated) >= 2:
        owners = {str(record["metadata"].get("deploymentId") or "") for record in validated}
        owners.discard("")
        unresolved = [
            record for record in validated
            if not record["metadata"].get("writersStopped")
        ]
        unresolved_owners = {str(record["metadata"].get("deploymentId") or "") for record in unresolved}
        unresolved_owners.discard("")
        if len(owners) > 1 and len(unresolved_owners) > 1:
            _add(
                FINDING_CONTRADICTORY_HISTORY,
                f"Multiple unresolved validated attempts from distinct deployments ({sorted(unresolved_owners)}); competing ownership requires reconciliation, not last-comment-wins.",
                "unknown",
                ACTION_REQUEST_OPERATOR,
            )

    if interpretation.settled == lifecycle.SETTLED_BLOCKED_OPEN_DONE:
        _add(FINDING_OPEN_DONE, "Open issue carries status: done; inconsistent, not available.", "unknown", ACTION_RECONCILE_LABELS)
    if interpretation.unknown_status_labels:
        _add(
            FINDING_UNKNOWN_STATUS,
            f"Unrecognized workflow-status values require classification: {', '.join(interpretation.unknown_status_labels)}.",
            "unknown",
            ACTION_REQUEST_OPERATOR,
        )
    if interpretation.legacy_todo_present:
        _add(
            FINDING_LEGACY_TODO,
            "Legacy status: todo present as retained history only; not emitted or required on the new path and not bulk-cleared.",
            "unknown",
            ACTION_NO_ACTION,
        )
    if (
        interpretation.settled == lifecycle.SETTLED_IN_PROGRESS
        and not validated
        and not unsupported
        and not generic_starts
    ):
        _add(
            FINDING_MANUAL_STATUS,
            "Manual status: in-progress with no trusted attempt handoff; label is respected and not age-cleared.",
            "unknown",
            ACTION_REQUEST_OPERATOR,
        )
    for body in generic_starts:
        _add(
            FINDING_GENERIC_START,
            f"Generic historical start comment without a versioned handoff: {_truncate(body, 200)}",
            "unknown",
            ACTION_ASSESS_PRIOR_WORK,
        )
    if unsupported:
        for record in unsupported:
            code = str(record["decision"].get("reasonCode") or "unsupported")
            if code in {"unsupported_version"}:
                _add(
                    FINDING_UNSUPPORTED_VERSION,
                    f"Unsupported attempt format from @{record['author'] or 'unknown'}: {record['decision'].get('summary')}. No silent admission or destructive normalization.",
                    str(record["metadata"].get("deploymentId") or "unknown"),
                    ACTION_REQUEST_OPERATOR,
                )
            else:
                _add(
                    FINDING_CONTRADICTORY_HISTORY,
                    f"Contradictory/unusable attempt evidence from @{record['author'] or 'unknown'} ({code}): {record['decision'].get('summary')}.",
                    str(record["metadata"].get("deploymentId") or "unknown"),
                    ACTION_REQUEST_OPERATOR,
                )
    if not validated and not unsupported and not generic_starts:
        _add(
            FINDING_MISSING_HISTORY,
            "No observable attempt comments. Absent metadata does not prove no work exists; treat as no observable history.",
            "unknown",
            ACTION_ASSESS_PRIOR_WORK,
        )
    flags = dict(issue_flags or {})
    reopened = any(bool(flags.get(key)) for key in REOPENED_FLAG_KEYS)
    if reopened:
        _add(
            FINDING_REOPENED,
            "Issue was reopened after prior work; reassess from current evidence rather than resurrecting old labels or declaring it automatically fresh.",
            str(known_owner or "unknown"),
            ACTION_ASSESS_PRIOR_WORK,
        )
    open_prs = [pr for pr in pr_list if isinstance(pr, Mapping) and str(pr.get("state") or "").lower() == "open"]
    merged_prs = [pr for pr in pr_list if isinstance(pr, Mapping) and str(pr.get("state") or "").lower() == "merged"]
    if len(open_prs) > 1:
        _add(
            FINDING_LINKED_PRS,
            f"Multiple open linked PRs ({len(open_prs)}); no silent canonical selection, reopen, merge, or overwrite.",
            str(known_owner or "unknown"),
            ACTION_REQUEST_OPERATOR,
        )
    elif len(open_prs) == 1:
        pr = open_prs[0]
        _add(
            FINDING_PARTIAL_PR,
            f"Linked PR {pr.get('url') or pr.get('number') or 'unknown'} at head {pr.get('head_sha') or 'unknown'} is partial work until gates verify it; default is to finish the existing PR, not merge to transfer work.",
            str(known_owner or "unknown"),
            ACTION_CONTINUE_PR,
        )
    if merged_prs and interpretation.github_state == "open":
        _add(
            FINDING_PARTIAL_PR,
            "PR merged but issue still open; perform missing finalization, not reimplementation.",
            str(known_owner or "unknown"),
            ACTION_VERIFY_COMPLETION,
        )
    for checkpoint in list(checkpoints or []):
        if not isinstance(checkpoint, Mapping):
            continue
        scope = str(checkpoint.get("scope") or checkpoint.get("save_method") or "")
        if scope in {"local-only", "private", "local_only"} or checkpoint.get("private_only"):
            _add(
                FINDING_PRIVATE_ONLY,
                f"Private-only checkpoint {checkpoint.get('id') or checkpoint.get('ref') or 'unknown'} is not a cross-device recovery point; require owner recovery or an authorized portable handoff.",
                str(checkpoint.get("owner") or known_owner or "unknown"),
                ACTION_REQUEST_OPERATOR,
            )
    if validated and interpretation.settled == lifecycle.SETTLED_IN_PROGRESS:
        last = validated[-1]["metadata"]
        if not last.get("writersStopped"):
            _add(
                FINDING_INTERRUPTED_RELEASE,
                f"Latest validated attempt {last.get('attemptId')} has not recorded a terminal release; another deployment may finish bookkeeping only on conclusive stop/mutation evidence.",
                str(last.get("deploymentId") or "unknown"),
                ACTION_HOLD_RELEASE,
            )
    complete = not comments_truncated and not prs_truncated and len(findings) < MAX_FINDINGS
    note = "Assessment examined all supplied evidence."
    if comments_truncated:
        complete = False
        note = f"Comment history exceeded the {MAX_COMMENTS_ASSESSED}-comment bound; result is partial, never a clean repository."
    elif prs_truncated:
        complete = False
        note = f"Linked PR history exceeded the {MAX_PRS_ASSESSED}-PR bound; result is partial, never a clean repository."
    elif len(findings) >= MAX_FINDINGS:
        complete = False
        note = f"Finding budget ({MAX_FINDINGS}) exhausted; remaining evidence deferred to the next bounded run."
    return LegacyAssessment(
        repository=repository,
        issue_number=issue_number,
        settled=interpretation.settled,
        complete=complete,
        completeness_note=note,
        findings=findings,
        preserved=preserved,
    )


# ---------------------------------------------------------------------------
# Req 3: repairs only through the shared evidence/authorization rules
# ---------------------------------------------------------------------------

CONCLUSIVELY_STOPPED_DISPOSITIONS = frozenset({"completed", "stopped_confirmed", "abandoned_authorized"})


def _is_verified_operator_decision(decision: Mapping[str, Any]) -> bool:
    """Return True only for an authenticated operator record, never a bare boolean.

    The owning API boundary records decisions via
    ``github_issue_recovery_surface.record_operator_decision`` (schema
    ``moonmind.github_issue_operator_decision.v1``) with an authenticated
    operator identity, action, and reason. A caller-supplied
    ``{"authorized_resolution": True}`` alone manufactures lifecycle
    authorization without that provenance and must not grant authority.
    """
    if not bool(decision.get("authorized_resolution")):
        return False
    operator_id = (
        decision.get("operator")
        or decision.get("authorized_by")
        or decision.get("operator_login")
    )
    if isinstance(operator_id, Mapping):
        operator_id = (
            operator_id.get("id")
            or operator_id.get("login")
            or operator_id.get("email")
        )
    action = decision.get("action") or decision.get("authorized_action")
    reason_text = decision.get("reason")
    schema_ok = (
        str(decision.get("schema") or "")
        == "moonmind.github_issue_operator_decision.v1"
    )
    ref_ok = any(
        str(decision.get(key) or "").strip()
        for key in (
            "approval_reference",
            "decision_record_id",
            "record_id",
            "decision_id",
            "approval_id",
        )
    )
    return bool(
        str(operator_id or "").strip()
        and str(action or "").strip()
        and str(reason_text or "").strip()
        and (schema_ok or ref_ok)
    )


def plan_legacy_repair(
    *,
    assessment: LegacyAssessment,
    from_settled: str,
    to_target: str,
    evidence: Mapping[str, Any] | None = None,
    reason: str = "",
    operator_decision: Mapping[str, Any] | None = None,
    conclusively_stopped: bool = False,
) -> dict[str, Any]:
    """Plan one legacy repair through the shared transition guards.

    Ambiguous cases (unknown owner, contradictory history, unknown
    formats, private-only work, multiple PRs) stay blocked without a
    verified ``operator_decision`` record from the owning API boundary.
    A bare ``{"authorized_resolution": True}`` boolean never grants
    authority. Incomplete assessments stay blocked until the missing
    evidence is read. The repair origin is derived from the assessment:
    ``from_settled`` must equal ``assessment.settled``. Transitions that
    contradict the assessment's suggested actions stay blocked.
    Human labels, old comments, useful PRs, and retained attempt history
    are listed as preserved; this function never deletes them.
    Reopened issues must arrive with a fresh ``assessment``; old labels
    are never resurrected here.
    """
    if not assessment.complete:
        return {
            "allowed": False,
            "reasonCode": "incomplete_assessment",
            "summary": "Assessment is incomplete (truncated evidence or exhausted finding budget); reread the missing evidence before planning a repair.",
            "preserved": list(assessment.preserved),
            "transition": None,
        }
    if str(from_settled or "") != str(assessment.settled or ""):
        return {
            "allowed": False,
            "reasonCode": "settled_mismatch",
            "summary": f"Repair origin {from_settled!r} disagrees with assessed state {assessment.settled!r}; derive the origin from the assessment before evaluating transition evidence.",
            "preserved": list(assessment.preserved),
            "transition": None,
        }
    ambiguous = {
        FINDING_CONTRADICTORY_HISTORY,
        FINDING_UNKNOWN_STATUS,
        FINDING_UNSUPPORTED_VERSION,
        FINDING_PRIVATE_ONLY,
        FINDING_LINKED_PRS,
        FINDING_MANUAL_STATUS,
    }
    needs_operator = any(finding.finding in ambiguous for finding in assessment.findings)
    decision = dict(operator_decision or {})
    claims_authority = bool(decision.get("authorized_resolution"))
    verified_operator = _is_verified_operator_decision(decision)
    if claims_authority and not verified_operator:
        return {
            "allowed": False,
            "reasonCode": "operator_verification_required",
            "summary": "Operator resolution claims lifecycle authority without a verified decision record (authenticated operator, action, reason, and schema/approval reference); refusing to manufacture authorization from a boolean.",
            "preserved": list(assessment.preserved),
            "transition": None,
        }
    if needs_operator and not verified_operator:
        return {
            "allowed": False,
            "reasonCode": "operator_decision_required",
            "summary": "Ambiguous legacy evidence stays blocked for an explicit verified operator decision; no repair planned.",
            "preserved": list(assessment.preserved),
            "transition": None,
        }
    suggested = {finding.suggested_action for finding in assessment.findings}
    blocking_new_admission = {
        ACTION_ASSESS_PRIOR_WORK,
        ACTION_CONTINUE_PR,
        ACTION_VERIFY_COMPLETION,
        ACTION_HOLD_RELEASE,
        ACTION_RECONCILE_LABELS,
        ACTION_ADOPT_WITH_LINEAGE,
        ACTION_DRAIN_PENDING,
    }
    if (
        str(from_settled or "") == lifecycle.SETTLED_AVAILABLE
        and str(to_target or "") == lifecycle.TO_IN_PROGRESS
        and bool(suggested & blocking_new_admission)
        and not verified_operator
    ):
        return {
            "allowed": False,
            "reasonCode": "action_mismatch",
            "summary": "Assessment requires a different next action (assess prior work, continue/finish the existing PR, verify completion, hold release, or reconcile state); a fresh available -> in_progress admission would contradict its own evidence.",
            "preserved": list(assessment.preserved),
            "transition": None,
        }
    if (
        str(to_target or "") == lifecycle.TO_IN_PROGRESS
        and ACTION_HOLD_RELEASE in suggested
        and not verified_operator
    ):
        return {
            "allowed": False,
            "reasonCode": "action_mismatch",
            "summary": "Latest validated attempt has not recorded a terminal release; hold release until conclusive stop/mutation evidence or a verified operator decision.",
            "preserved": list(assessment.preserved),
            "transition": None,
        }
    merged_evidence = dict(evidence or {})
    if verified_operator:
        merged_evidence.setdefault("authorized_resolution", True)
        disposition = (
            decision.get("preserved_work_disposition")
            or decision.get("work_disposition")
        )
        if not str(disposition or "").strip():
            return {
                "allowed": False,
                "reasonCode": "operator_verification_required",
                "summary": "Verified operator decision names no preserved-work disposition; refusing to manufacture a default disposition.",
                "preserved": list(assessment.preserved),
                "transition": None,
            }
        merged_evidence.setdefault("preserved_work_disposition", disposition)
    verdict = lifecycle.plan_transition(
        from_settled=from_settled, to_target=to_target, evidence=merged_evidence, reason=reason
    )
    result: dict[str, Any] = {
        "allowed": verdict.allowed,
        "reasonCode": verdict.reason_code,
        "summary": verdict.summary,
        "preserved": list(assessment.preserved),
        "transition": verdict.to_dict(),
    }
    if verdict.allowed and conclusively_stopped:
        result["portableHandoff"] = "Conclusively stopped work may receive a portable handoff and appropriate state."
    return result


# ---------------------------------------------------------------------------
# Req 4: retained-history behavior (replay/compatibility vs drainage)
# ---------------------------------------------------------------------------

#: Payload families whose retained-history behavior is defined here.
RETAINED_FAMILIES = ("activity_inputs", "tool_results", "preset_expansion", "pending_finalization")

#: Obsolete shapes new executions must stop writing.
OBSOLETE_SHAPES = frozenset(
    {
        "status: todo",
        "status: claiming",
        "legacy_retry_count",
        "unversioned_handoff",
        "whole_label_set_replace",
        "local_only_recovery_claim",
    }
)

#: Drainage owners per family: replay/compatibility where real consumers
#: require it, otherwise a controlled drain.
DRAINAGE_BY_FAMILY = {
    "activity_inputs": "replay",
    "tool_results": "replay",
    "preset_expansion": "drain",
    "pending_finalization": "drain",
}


def is_obsolete_shape(name: Any) -> bool:
    """Return True when *name* is an obsolete shape new executions must not write."""
    return str(name or "").strip() in OBSOLETE_SHAPES


def classify_retained_payload(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    """Classify one retained payload as replay-compatible, drain, or reject.

    Frozen workflow inputs are never reinterpreted: unknown or obsolete
    shapes are drained or rejected, not aliased into new semantics, and no
    permanent alias stack is kept.
    """
    data = dict(payload or {})
    family = str(data.get("family") or "")
    shape = str(data.get("shape") or "")
    version = str(data.get("version") or "")
    if not family or family not in RETAINED_FAMILIES:
        return {
            "disposition": "reject",
            "reasonCode": "unknown_family",
            "summary": f"Unknown retained family {family!r}; blocked rather than reinterpreted.",
            "drainage": None,
        }
    if is_obsolete_shape(shape):
        return {
            "disposition": "drain",
            "reasonCode": "obsolete_shape",
            "summary": f"Obsolete shape {shape!r} is drained, never written by new executions and never aliased.",
            "drainage": DRAINAGE_BY_FAMILY[family],
        }
    if family in {"activity_inputs", "tool_results"} and version == SUPPORTED_ATTEMPT_VERSION:
        return {
            "disposition": "replay",
            "reasonCode": "replay_compatible",
            "summary": f"{family} at the supported version passes representative replay/compatibility handling.",
            "drainage": "replay",
        }
    if family == "pending_finalization":
        return {
            "disposition": "drain",
            "reasonCode": "controlled_drain",
            "summary": "Pending finalization operations follow the tested drainage path: reread GitHub, abandon obsolete transitions, settle conclusive handoffs.",
            "drainage": "drain",
        }
    return {
        "disposition": "drain",
        "reasonCode": "version_drain",
        "summary": f"{family} at version {version!r} follows the controlled drainage path; frozen inputs are not reinterpreted.",
        "drainage": DRAINAGE_BY_FAMILY.get(family, "drain"),
    }


def drainage_plan_for_pending(pending: Sequence[Mapping[str, Any]] | None) -> dict[str, Any]:
    """Build a tested drainage plan for pending retained updates."""
    items = [dict(item) for item in list(pending or [])]
    steps: list[dict[str, Any]] = []
    for item in items:
        classification = classify_retained_payload(item)
        steps.append({"pending": item, "classification": classification})
    return {
        "steps": steps,
        "drainage": "Reread current GitHub evidence per item; complete conclusive handoffs, abandon obsolete transitions, hold ambiguous items for an operator decision.",
        "writesObsoleteShapes": False,
    }


# ---------------------------------------------------------------------------
# Req 5: mixed old/new deployment behavior (tested as unqualified)
# ---------------------------------------------------------------------------

MIXED_UNQUALIFIED_SUMMARY = (
    "Mixed old/new deployment behavior is unqualified until old bypasses stop and all participating devices conform."
)


def evaluate_mixed_deployment(devices: Sequence[Mapping[str, Any]] | None) -> dict[str, Any]:
    """Evaluate whether participating devices may claim coordinated support.

    Each device reports ``{installationId, codeVersion, oldSelectorActive,
    oldClaimerActive}``. Support is qualified only when every
    participating device runs matching conforming code, no old
    selector/claimer/publisher bypass remains active, installation IDs
    are unique, and operational defaults reconcile. An unknown-format
    check in new code cannot constrain old code that ignores the format,
    so any active old writer forces unqualified.
    """
    seen: list[dict[str, Any]] = [dict(device) for device in list(devices or [])]
    reasons: list[str] = []
    if not seen:
        return {"qualified": False, "reasonCode": "no_devices", "summary": MIXED_UNQUALIFIED_SUMMARY, "reasons": ["No participating devices reported."], "devices": []}
    versions = {str(device.get("codeVersion") or "") for device in seen}
    if len(versions) > 1:
        reasons.append(f"Devices run mismatched code versions: {sorted(versions)}.")
    ids = [str(device.get("installationId") or "") for device in seen]
    if any(not value for value in ids):
        reasons.append("A device reports no stable installation identity.")
    if len(set(ids)) != len(ids):
        reasons.append("Installation identities are not unique across devices.")
    for device in seen:
        if device.get("oldSelectorActive") or device.get("oldClaimerActive") or device.get("oldPublisherActive"):
            reasons.append(
                f"Device {device.get('installationId') or 'unknown'} still runs a nonparticipating old claimer/publisher; pause/drain it before claiming cross-device support."
            )
        if device.get("defaultsReconciled") is not True:
            reasons.append(f"Device {device.get('installationId') or 'unknown'} has unreconciled operational defaults (affirmative reconciliation required).")
    if reasons:
        return {"qualified": False, "reasonCode": "unqualified_mixed", "summary": MIXED_UNQUALIFIED_SUMMARY, "reasons": reasons, "devices": seen}
    return {"qualified": True, "reasonCode": "all_conform", "summary": "All participating devices conform; coordinated support may be claimed.", "reasons": [], "devices": seen}


# ---------------------------------------------------------------------------
# Req 7: upgrade / rollback rehearsal fixtures
# ---------------------------------------------------------------------------

UPGRADE_FIXTURE_FIELDS = ("labels", "comments", "code_refs", "artifacts", "settings", "attempt_format")


def simulate_upgrade(state: Mapping[str, Any]) -> dict[str, Any]:
    """Rehearse an upgrade: carry every fixture field forward unchanged.

    Upgrade never bulk-relabeled, deleted comments/PRs, rewrote code, or
    altered unrelated deployment settings; it installs matching code,
    preset definitions, and label/permission readiness alongside a
    hold/drain of old writers (see :func:`evaluate_mixed_deployment`).
    """
    carried = {key: state.get(key) for key in UPGRADE_FIXTURE_FIELDS}
    return {
        "upgraded": True,
        "carried": carried,
        "preservedLabels": list(state.get("labels") or []),
        "preservedComments": len(list(state.get("comments") or [])),
        "preservedCodeRefs": list(state.get("code_refs") or []),
        "preservedArtifacts": list(state.get("artifacts") or []),
        "settingsUnchanged": True,
        "summary": "Upgrade fixture preserves labels, comments, code, artifacts, and unrelated deployment settings.",
    }


def simulate_rollback(state: Mapping[str, Any], *, hold_drain: bool = False) -> dict[str, Any]:
    """Rehearse a safe rollback or forward-repair over a fixture.

    A rollback must not restart an old selector against active
    new-format work without a controlled hold/drain: when the fixture
    carries new-format attempt evidence and ``hold_drain`` is False, the
    rollback is refused. With the hold/drain engaged, labels, comments,
    code, artifacts, and unrelated settings are preserved.
    """
    has_new_format = str(state.get("attempt_format") or "") == SUPPORTED_ATTEMPT_VERSION
    if has_new_format and not hold_drain:
        return {
            "rolledBack": False,
            "reasonCode": "hold_drain_required",
            "summary": "Rollback refused: active new-format work requires a controlled hold/drain before an old selector may resume.",
            "preservedLabels": list(state.get("labels") or []),
            "preservedComments": len(list(state.get("comments") or [])),
            "settingsUnchanged": True,
        }
    return {
        "rolledBack": True,
        "reasonCode": "rollback_held" if has_new_format else "rollback_clean",
        "summary": "Rollback fixture preserves labels, comments, code, artifacts, and unrelated deployment settings under a controlled hold/drain.",
        "preservedLabels": list(state.get("labels") or []),
        "preservedComments": len(list(state.get("comments") or [])),
        "preservedCodeRefs": list(state.get("code_refs") or []),
        "preservedArtifacts": list(state.get("artifacts") or []),
        "settingsUnchanged": True,
    }


__all__ = [
    "SURVIVING_POLICY_OWNER",
    "SUPPORTED_ATTEMPT_VERSION",
    "MAX_COMMENTS_ASSESSED",
    "MAX_PRS_ASSESSED",
    "MAX_FINDINGS",
    "MAX_EVIDENCE_CHARS",
    "CALLER_INVENTORY",
    "CONCLUSIVELY_STOPPED_DISPOSITIONS",
    "RETAINED_FAMILIES",
    "OBSOLETE_SHAPES",
    "DRAINAGE_BY_FAMILY",
    "FINDING_MANUAL_STATUS",
    "FINDING_GENERIC_START",
    "FINDING_MISSING_HISTORY",
    "FINDING_CONTRADICTORY_HISTORY",
    "FINDING_OPEN_DONE",
    "FINDING_REOPENED",
    "FINDING_PARTIAL_PR",
    "FINDING_UNSUPPORTED_VERSION",
    "FINDING_UNKNOWN_STATUS",
    "FINDING_LEGACY_TODO",
    "FINDING_PRIVATE_ONLY",
    "FINDING_LINKED_PRS",
    "FINDING_INTERRUPTED_RELEASE",
    "ACTION_NO_ACTION",
    "ACTION_ASSESS_PRIOR_WORK",
    "ACTION_CONTINUE_PR",
    "ACTION_REQUEST_OPERATOR",
    "ACTION_RECONCILE_LABELS",
    "ACTION_VERIFY_COMPLETION",
    "ACTION_ADOPT_WITH_LINEAGE",
    "ACTION_DRAIN_PENDING",
    "ACTION_HOLD_RELEASE",
    "MIXED_UNQUALIFIED_SUMMARY",
    "UPGRADE_FIXTURE_FIELDS",
    "LegacyFinding",
    "LegacyAssessment",
    "verify_new_path_todo_free",
    "assess_legacy_issue",
    "plan_legacy_repair",
    "is_obsolete_shape",
    "classify_retained_payload",
    "drainage_plan_for_pending",
    "evaluate_mixed_deployment",
    "simulate_upgrade",
    "simulate_rollback",
]
