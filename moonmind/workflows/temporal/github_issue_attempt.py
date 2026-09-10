"""Portable per-attempt GitHub issue handoffs and cross-deployment retry history.

Single policy entrypoint for MoonLadderStudios/MoonMind#4177 (design:
docs/Workflows/GitHubIssueStatusStateMachineDesign.md, sections 4 and 5.3).
Deterministic and side-effect-free except for the explicit trusted-boundary
``publish_attempt_handoff`` orchestration, which performs GitHub reads/writes
only through an injected service object (``GitHubService`` in production,
fakes in tests).

Contract summary:

* Every issue-work attempt owns exactly one identifiable GitHub issue comment.
  The comment carries a stable machine marker (the attempt ID) plus a readable
  summary and one versioned, bounded JSON metadata block. GitHub is the shared
  handoff surface: another independent deployment reconstructs remaining work
  and retry restrictions from GitHub-visible comments alone.
* A GitHub account name never substitutes for deployment identity. The stable
  installation ID comes from one canonical source (explicit input or
  ``MOONMIND_INSTALLATION_ID``); there are no competing aliases or silent
  hostname/username fallbacks, so the value persists unchanged through
  restart/retry. A copied machine marker never authenticates itself: provenance
  is validated against a caller-supplied trusted-poster set at read time.
* Issue prose and arbitrary comments are untrusted reference content. Only the
  versioned metadata block of a provenance-validated comment drives admission
  and retry decisions. Unsupported formats and inconsistent/missing referenced
  history are rejected explicitly rather than inferred as a fresh start.
* Production consumption (no parallel reimplementation): admission consumes
  the portable retry budget through ``attempt_evidence_blocks_admission`` in
  ``github_issue_lifecycle`` (which honors ``linkedAttempts``/``retryPolicy``
  context via :func:`derive_retry_state`); progress and release publication
  run through ``attempt_handoff_activities.publish_attempt_progress`` /
  ``publish_attempt_release``, which supply the production ``GitHubService``,
  the canonical installation identity, and the caller-owned trusted-poster
  allow-list. Workflows never call the GitHub comment endpoints for attempt
  state directly.
* Operators provision one stable value per deployment as
  ``MOONMIND_INSTALLATION_ID`` (see ``.env-template``); there are no
  competing aliases or silent fallbacks.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

#: Single supported metadata schema. Unknown versions fail closed.
ATTEMPT_COMMENT_FORMAT_VERSION = "moonmind.github_issue_attempt.v1"

#: Stable per-comment machine marker. Reconciliation keys creation retries on
#: this marker before repeating effects (design section 4.2).
ATTEMPT_MARKER_PREFIX = "<!-- moonmind-github-attempt:"
ATTEMPT_MARKER_SUFFIX = " -->"

#: Bounded comment size. GitHub issue comments support far more, but the
#: handoff is a compact portable record, not another workflow database.
MAX_COMMENT_CHARS = 8000

#: Minimum seconds between routine progress updates for one attempt.
#: Activity/outcome changes and explicit force bypass coalescing.
PROGRESS_COALESCE_SECONDS = 300

#: Per-field bounds applied before the total-size check.
_MAX_REPORT_CHARS = 500
_MAX_VERIFICATION_CHARS = 1000
_MAX_STOP_EVIDENCE_CHARS = 500
_MAX_REQUIREMENTS = 20
_MAX_REQUIREMENT_CHARS = 200
_MAX_REF_CHARS = 300

#: Attempt activity vocabulary (design section 4.1). No equivalent label
#: families are introduced: these values live only inside attempt comments.
ACTIVITY_PREPARING = "preparing"
ACTIVITY_ACTIVE = "active"
ACTIVITY_AWAITING_REVIEW = "awaiting-review"
ACTIVITY_RELEASING = "releasing"
ACTIVITY_RELEASED = "released"
ACTIVITY_ATTENTION = "attention"

ACTIVITIES = frozenset(
    {
        ACTIVITY_PREPARING,
        ACTIVITY_ACTIVE,
        ACTIVITY_AWAITING_REVIEW,
        ACTIVITY_RELEASING,
        ACTIVITY_RELEASED,
        ACTIVITY_ATTENTION,
    }
)

ACTIVITY_MEANINGS: dict[str, str] = {
    ACTIVITY_PREPARING: "The attempt announced ownership and is assessing or preparing work; it has no verified output yet.",
    ACTIVITY_ACTIVE: "The attempt is editing, verifying, or repairing work under its own ownership.",
    ACTIVITY_AWAITING_REVIEW: "The attempt published a verified implementation and the remaining review/merge journey owns next action.",
    ACTIVITY_RELEASING: "The attempt recorded a proposed disposition and is settling writers, mutations, preservation, and labels.",
    ACTIVITY_RELEASED: "The attempt completed release: writers stopped, mutations settled, preservation verified or explicitly absent, and the label outcome observed. A released attempt performs no further issue-state or PR writes.",
    ACTIVITY_ATTENTION: "The attempt requires operator intervention before any automatic continuation.",
}

#: Bounded next-action vocabulary for the portable handoff.
NEXT_ACTIONS = frozenset(
    {
        "fresh-retry",
        "continue-implementation",
        "verify",
        "continue-review",
        "finalize-status",
        "obtain-operator-attention",
    }
)

#: Bounded terminal outcome vocabulary. Intermediate (non-terminal) handoffs
#: use ``"pending"``.
OUTCOMES = frozenset(
    {
        "pending",
        "completed",
        "failed",
        "cancelled",
        "blocked",
        "no-work",
    }
)

#: Exact machine-block sentinel emitted by :func:`render_attempt_comment`.
#: Extraction anchors to this sentinel so untrusted report/verification prose
#: cannot inject a competing JSON fence earlier in the comment body.
_METADATA_SENTINEL = f"<!-- {ATTEMPT_COMMENT_FORMAT_VERSION} metadata"

_ATTEMPT_ID_RE = re.compile(r"^att_[0-9a-f]{24}$")
_INSTALLATION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,127}$")
_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_PR_URL_RE = re.compile(r"^https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/pull/([1-9]\d*)$")
_BRANCH_RE = re.compile(r"^[A-Za-z0-9_./-]{1,200}$")


def _string(value: Any) -> str:
    return str(value or "").strip() if not isinstance(value, bool) else ""


def _truncate(text: str, limit: int) -> str:
    text = str(text or "")
    if len(text) <= limit:
        return text
    return text[: max(0, limit - len("[truncated]"))] + "[truncated]"


# ---------------------------------------------------------------------------
# Requirement 1: stable installation identity + attempt identity
# ---------------------------------------------------------------------------


def resolve_installation_id(
    explicit: Any = None,
    *,
    env: Mapping[str, str] | None = None,
) -> tuple[str, str]:
    """Resolve the one canonical stable installation identity.

    Returns ``(installation_id, error)``: exactly one is non-empty. The value
    comes from the explicit input or ``MOONMIND_INSTALLATION_ID`` only. There
    is intentionally no hostname, username, or generated fallback: silent
    fallbacks would create competing aliases that diverge across restarts and
    would let two devices sharing one GitHub account blend into each other.
    Operators persist one value per deployment (environment or file-backed
    environment) so restarts and retries reuse it unchanged.
    """

    candidate = _string(explicit)
    if not candidate and env is None:
        candidate = str(os.environ.get("MOONMIND_INSTALLATION_ID") or "").strip()
    elif not candidate and env is not None:
        candidate = str(env.get("MOONMIND_INSTALLATION_ID") or "").strip()
    if not candidate:
        return "", "MOONMIND_INSTALLATION_ID is not configured; set one stable value per deployment."
    if not _INSTALLATION_ID_RE.fullmatch(candidate):
        return "", f"Invalid installation ID {candidate!r}: use 3-128 [A-Za-z0-9._-] characters starting alphanumeric."
    return candidate, ""


def new_attempt_id() -> str:
    """Return a fresh globally unique attempt ID."""
    return f"att_{secrets.token_hex(12)}"


def build_attempt_identity(
    *,
    repository: str,
    issue_number: int,
    workflow_id: str,
    run_id: str,
    installation_id: str,
    attempt_id: str = "",
) -> tuple[dict[str, Any], str]:
    """Bind one attempt ID to the exact repository/issue and workflow/run.

    Returns ``(identity, error)``. The attempt ID itself is opaque randomness;
    the binding lives in the validated metadata fields so any deployment can
    check that a comment belongs to the issue it is reading.
    """

    repo = _string(repository)
    workflow = _string(workflow_id)
    run = _string(run_id)
    deployment = _string(installation_id)
    candidate = _string(attempt_id) or new_attempt_id()
    if not _REPO_RE.fullmatch(repo):
        return {}, f"Invalid repository {repo!r}: expected owner/name."
    if type(issue_number) is not int or issue_number <= 0:
        return {}, f"Invalid issue number {issue_number!r}."
    if not workflow:
        return {}, "Missing workflow identity for the attempt."
    if not run:
        return {}, "Missing run identity for the attempt."
    if not _INSTALLATION_ID_RE.fullmatch(deployment):
        return {}, "Invalid installation identity for the attempt."
    if not _ATTEMPT_ID_RE.fullmatch(candidate):
        return {}, f"Invalid attempt ID {candidate!r}."
    return (
        {
            "attemptId": candidate,
            "deploymentId": deployment,
            "repository": repo,
            "issueNumber": issue_number,
            "workflowId": _truncate(workflow, _MAX_REF_CHARS),
            "runId": _truncate(run, _MAX_REF_CHARS),
        },
        "",
    )


# ---------------------------------------------------------------------------
# Requirement 2: versioned, bounded comment representation
# ---------------------------------------------------------------------------


@dataclass
class AttemptHandoff:
    """Portable per-attempt handoff (design section 4.1, required fields)."""

    attempt_id: str = ""
    deployment_id: str = ""
    repository: str = ""
    issue_number: int = 0
    workflow_id: str = ""
    run_id: str = ""
    # Lineage: predecessor attempt/comment when continuing previous work.
    predecessor_attempt_id: str = ""
    predecessor_comment_id: int = 0
    # Activity + last report.
    activity: str = ACTIVITY_PREPARING
    last_report: str = ""
    update_seq: int = 1
    last_activity_at: str = ""
    # Stop evidence for all writers + pending publication disposition.
    writers_stopped: bool = False
    stop_evidence: str = ""
    pending_disposition: str = ""
    # Preserved work: exact PR/head/base or saved branch/SHA.
    pr_url: str = ""
    pr_head_sha: str = ""
    pr_base: str = ""
    saved_branch: str = ""
    saved_sha: str = ""
    # Result: outcome, met/unmet requirements, verification summary.
    outcome: str = "pending"
    met_requirements: tuple[str, ...] = ()
    remaining_requirements: tuple[str, ...] = ()
    verification_summary: str = ""
    next_action: str = "continue-implementation"
    # Retry eligibility: history, allowance, cooldown, operator hold.
    failed_attempt_refs: tuple[str, ...] = ()
    no_progress_attempt_refs: tuple[str, ...] = ()
    internal_retries: int = 0
    cooldown_until: str = ""
    operator_hold: bool = False
    operator_hold_reason: str = ""
    policy_lineage: str = ""
    authorized_reset: Mapping[str, Any] | None = None
    # Optional diagnostics only: never the sole recoverability evidence.
    local_workflow_ref: str = ""
    # Redaction tombstone marker (design section 4.2).
    tombstone: bool = False
    successor_attempt_id: str = ""

    def to_metadata(self) -> dict[str, Any]:
        metadata: dict[str, Any] = {
            "formatVersion": ATTEMPT_COMMENT_FORMAT_VERSION,
            "attemptId": self.attempt_id,
            "deploymentId": self.deployment_id,
            "repository": self.repository,
            "issueNumber": self.issue_number,
            "workflowId": self.workflow_id,
            "runId": self.run_id,
            "activity": self.activity,
            "lastReport": self.last_report,
            "updateSeq": self.update_seq,
            "writersStopped": self.writers_stopped,
            "pendingDisposition": self.pending_disposition,
            "outcome": self.outcome,
            "metRequirements": list(self.met_requirements),
            "remainingRequirements": list(self.remaining_requirements),
            "verificationSummary": self.verification_summary,
            "nextAction": self.next_action,
            "retryHistory": {
                "failedAttempts": list(self.failed_attempt_refs),
                "noProgressAttempts": list(self.no_progress_attempt_refs),
                "internalRetries": self.internal_retries,
                "cooldownUntil": self.cooldown_until,
                "operatorHold": self.operator_hold,
                "policyLineage": self.policy_lineage,
            },
        }
        if self.predecessor_attempt_id:
            metadata["predecessorAttemptId"] = self.predecessor_attempt_id
        if self.predecessor_comment_id:
            metadata["predecessorCommentId"] = self.predecessor_comment_id
        if self.last_activity_at:
            metadata["lastActivityAt"] = self.last_activity_at
        if self.stop_evidence:
            metadata["stopEvidence"] = self.stop_evidence
        if self.pr_url:
            metadata["preservedWork"] = {
                "prUrl": self.pr_url,
                "prHeadSha": self.pr_head_sha,
                "prBase": self.pr_base,
            }
        if self.saved_branch or self.saved_sha:
            saved = metadata.setdefault("preservedWork", {})
            if self.saved_branch:
                saved["savedBranch"] = self.saved_branch
            if self.saved_sha:
                saved["savedSha"] = self.saved_sha
        if self.operator_hold_reason:
            metadata["retryHistory"]["operatorHoldReason"] = self.operator_hold_reason
        if self.authorized_reset:
            metadata["retryHistory"]["authorizedReset"] = dict(self.authorized_reset)
        if self.local_workflow_ref:
            metadata["localWorkflowRef"] = self.local_workflow_ref
        if self.tombstone:
            metadata["tombstone"] = True
        if self.successor_attempt_id:
            metadata["successorAttemptId"] = self.successor_attempt_id
        return metadata


def handoff_from_metadata(metadata: Mapping[str, Any]) -> AttemptHandoff:
    """Rebuild a handoff from validated metadata (same logical attempt)."""
    retry = metadata.get("retryHistory") if isinstance(metadata.get("retryHistory"), Mapping) else {}
    preserved = metadata.get("preservedWork") if isinstance(metadata.get("preservedWork"), Mapping) else {}
    retry = retry or {}
    preserved = preserved or {}
    authorized_reset = retry.get("authorizedReset")
    return AttemptHandoff(
        attempt_id=_string(metadata.get("attemptId")),
        deployment_id=_string(metadata.get("deploymentId")),
        repository=_string(metadata.get("repository")),
        issue_number=metadata.get("issueNumber") if type(metadata.get("issueNumber")) is int else 0,
        workflow_id=_string(metadata.get("workflowId")),
        run_id=_string(metadata.get("runId")),
        predecessor_attempt_id=_string(metadata.get("predecessorAttemptId")),
        predecessor_comment_id=metadata.get("predecessorCommentId") if type(metadata.get("predecessorCommentId")) is int else 0,
        activity=_string(metadata.get("activity")),
        last_report=_string(metadata.get("lastReport")),
        update_seq=metadata.get("updateSeq") if type(metadata.get("updateSeq")) is int else 1,
        last_activity_at=_string(metadata.get("lastActivityAt")),
        writers_stopped=bool(metadata.get("writersStopped")),
        stop_evidence=_string(metadata.get("stopEvidence")),
        pending_disposition=_string(metadata.get("pendingDisposition")),
        pr_url=_string(preserved.get("prUrl")),
        pr_head_sha=_string(preserved.get("prHeadSha")),
        pr_base=_string(preserved.get("prBase")),
        saved_branch=_string(preserved.get("savedBranch")),
        saved_sha=_string(preserved.get("savedSha")),
        outcome=_string(metadata.get("outcome")) or "pending",
        met_requirements=tuple(str(item) for item in (metadata.get("metRequirements") or []) if str(item).strip()),
        remaining_requirements=tuple(str(item) for item in (metadata.get("remainingRequirements") or []) if str(item).strip()),
        verification_summary=_string(metadata.get("verificationSummary")),
        next_action=_string(metadata.get("nextAction")) or "continue-implementation",
        failed_attempt_refs=tuple(str(item) for item in (retry.get("failedAttempts") or []) if str(item).strip()),
        no_progress_attempt_refs=tuple(str(item) for item in (retry.get("noProgressAttempts") or []) if str(item).strip()),
        internal_retries=retry.get("internalRetries") if type(retry.get("internalRetries")) is int else 0,
        cooldown_until=_string(retry.get("cooldownUntil")),
        operator_hold=bool(retry.get("operatorHold")),
        operator_hold_reason=_string(retry.get("operatorHoldReason")),
        policy_lineage=_string(retry.get("policyLineage")),
        authorized_reset=dict(authorized_reset) if isinstance(authorized_reset, Mapping) else None,
        local_workflow_ref=_string(metadata.get("localWorkflowRef")),
        tombstone=bool(metadata.get("tombstone")),
        successor_attempt_id=_string(metadata.get("successorAttemptId")),
    )


def _metadata_digest(metadata: Mapping[str, Any]) -> str:
    serialized = json.dumps(metadata, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def render_attempt_comment(handoff: AttemptHandoff) -> tuple[str, str]:
    """Render the one bounded comment body for an attempt.

    Returns ``(body, error)``. The body is redacted and outbound-scanned
    before it is returned: secret-like content blocks rendering with an
    explicit error instead of producing a postable comment. Oversized content
    is truncated at field bounds first; content still exceeding the total
    bound is rejected rather than silently dropped.
    """
    from moonmind.utils.logging import redact_sensitive_text

    metadata = handoff.to_metadata()
    # Bound every free-text field before serialization so the comment stays a
    # compact portable record.
    metadata["lastReport"] = _truncate(metadata.get("lastReport", ""), _MAX_REPORT_CHARS)
    metadata["verificationSummary"] = _truncate(metadata.get("verificationSummary", ""), _MAX_VERIFICATION_CHARS)
    if metadata.get("stopEvidence"):
        metadata["stopEvidence"] = _truncate(metadata["stopEvidence"], _MAX_STOP_EVIDENCE_CHARS)
    for key in ("metRequirements", "remainingRequirements"):
        items = [str(item) for item in (metadata.get(key) or [])]
        metadata[key] = [_truncate(item, _MAX_REQUIREMENT_CHARS) for item in items[:_MAX_REQUIREMENTS]]
    if metadata.get("pendingDisposition"):
        metadata["pendingDisposition"] = _truncate(metadata["pendingDisposition"], _MAX_REPORT_CHARS)

    try:
        metadata_json = json.dumps(metadata, indent=2, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError) as exc:
        return "", f"Attempt metadata is not serializable: {exc.__class__.__name__}."
    summary_lines = _readable_summary(handoff, metadata)
    marker = f"{ATTEMPT_MARKER_PREFIX} {handoff.attempt_id} v1{ATTEMPT_MARKER_SUFFIX}"
    body = (
        f"{marker}\n{summary_lines}\n\n"
        f"<!-- {ATTEMPT_COMMENT_FORMAT_VERSION} metadata (machine-readable, do not edit) -->\n"
        f"```json\n{metadata_json}\n```\n"
    )
    # Scan the raw body first: secret-like content blocks the comment with an
    # explicit error instead of being laundered through redaction into a
    # postable body. The posted body is still redacted below as defense in
    # depth so scanner-missed shapes are scrubbed as well.
    blocked, block_detail = _scan_comment_body(body)
    if blocked:
        return "", f"Attempt comment blocked by outbound scan: {block_detail}."
    body = redact_sensitive_text(body)
    if len(body) > MAX_COMMENT_CHARS:
        return "", (
            f"Attempt comment exceeds the {MAX_COMMENT_CHARS}-character bound "
            f"({len(body)} characters); shorten reports and requirement lists."
        )
    field_error = _validate_handoff_shapes(handoff)
    if field_error:
        return "", field_error
    return body, ""


def _readable_summary(handoff: AttemptHandoff, metadata: Mapping[str, Any]) -> str:
    meaning = ACTIVITY_MEANINGS.get(handoff.activity, "Unknown activity.")
    lines = [
        f"## MoonMind attempt `{handoff.attempt_id}` — {handoff.activity}",
        "",
        f"Deployment `{handoff.deployment_id}` works {handoff.repository}#{handoff.issue_number} "
        f"(workflow `{handoff.workflow_id}`, run `{handoff.run_id}`).",
        f"Activity: **{handoff.activity}** — {meaning}",
    ]
    if handoff.predecessor_attempt_id:
        lines.append(
            f"Continues attempt `{handoff.predecessor_attempt_id}`"
            + (f" (comment {handoff.predecessor_comment_id})" if handoff.predecessor_comment_id else "")
            + "."
        )
    if handoff.last_report:
        lines.append(f"Last report: {_truncate(handoff.last_report, _MAX_REPORT_CHARS)}")
    lines.append(f"Writers stopped: {'yes' if handoff.writers_stopped else 'no'}.")
    if handoff.pending_disposition:
        lines.append(f"Pending disposition: {_truncate(handoff.pending_disposition, _MAX_REPORT_CHARS)}")
    preserved = metadata.get("preservedWork") if isinstance(metadata.get("preservedWork"), Mapping) else {}
    if preserved:
        parts = []
        if preserved.get("prUrl"):
            parts.append(f"PR {preserved.get('prUrl')} @ {preserved.get('prHeadSha') or 'unknown head'} (base {preserved.get('prBase') or 'unknown'})")
        if preserved.get("savedBranch"):
            parts.append(f"saved {preserved.get('savedBranch')}@{preserved.get('savedSha') or 'unknown'}")
        if parts:
            lines.append("Preserved work: " + "; ".join(parts) + ".")
    lines.append(f"Outcome: **{handoff.outcome}**; next action: **{handoff.next_action}**.")
    # Render from the already-truncated metadata values: the raw handoff may
    # carry unbounded requirement text that would push the final body past the
    # total bound even though the bounded metadata itself fits.
    _bounded_remaining = metadata.get("remainingRequirements")
    if isinstance(_bounded_remaining, list) and _bounded_remaining:
        lines.append("Remaining: " + "; ".join(str(item) for item in _bounded_remaining[:_MAX_REQUIREMENTS]) + ".")
    elif handoff.remaining_requirements:
        lines.append("Remaining: " + "; ".join(list(handoff.remaining_requirements)[:_MAX_REQUIREMENTS]) + ".")
    if handoff.verification_summary:
        lines.append(f"Verification: {_truncate(handoff.verification_summary, _MAX_VERIFICATION_CHARS)}")
    retry_bits = []
    if handoff.failed_attempt_refs or handoff.no_progress_attempt_refs:
        retry_bits.append(
            f"{len(handoff.failed_attempt_refs)} failed / {len(handoff.no_progress_attempt_refs)} no-progress linked attempts"
        )
    if handoff.cooldown_until:
        retry_bits.append(f"cooldown until {handoff.cooldown_until}")
    if handoff.operator_hold:
        retry_bits.append("operator hold active")
    if retry_bits:
        lines.append("Retry: " + "; ".join(retry_bits) + ".")
    if handoff.tombstone:
        lines.append(
            "Redaction tombstone: content withheld; lineage fields above remain authoritative. "
            + (f"Successor: `{handoff.successor_attempt_id}`." if handoff.successor_attempt_id else "No successor recorded.")
        )
    return "\n".join(lines)


def render_tombstone(
    *,
    attempt_id: str,
    deployment_id: str,
    repository: str,
    issue_number: int,
    failed_attempt_refs: Sequence[str] = (),
    no_progress_attempt_refs: Sequence[str] = (),
    policy_lineage: str = "",
    successor_attempt_id: str = "",
) -> tuple[str, str]:
    """Render a redaction tombstone retaining lineage (design section 4.2)."""
    handoff = AttemptHandoff(
        attempt_id=_string(attempt_id),
        deployment_id=_string(deployment_id),
        repository=_string(repository),
        issue_number=issue_number,
        workflow_id="redacted",
        run_id="redacted",
        activity=ACTIVITY_ATTENTION,
        last_report="Content redacted; lineage fields remain authoritative.",
        writers_stopped=True,
        outcome="blocked",
        next_action="obtain-operator-attention",
        failed_attempt_refs=tuple(failed_attempt_refs),
        no_progress_attempt_refs=tuple(no_progress_attempt_refs),
        policy_lineage=_string(policy_lineage),
        tombstone=True,
        successor_attempt_id=_string(successor_attempt_id),
    )
    return render_attempt_comment(handoff)


def _validate_handoff_shapes(handoff: AttemptHandoff) -> str:
    if not _ATTEMPT_ID_RE.fullmatch(handoff.attempt_id):
        return f"Invalid attempt ID {handoff.attempt_id!r}."
    if not _INSTALLATION_ID_RE.fullmatch(handoff.deployment_id):
        return "Invalid deployment ID for the attempt."
    if not _REPO_RE.fullmatch(handoff.repository):
        return f"Invalid repository {handoff.repository!r}."
    if type(handoff.issue_number) is not int or handoff.issue_number <= 0:
        return "Invalid issue number for the attempt."
    if handoff.activity not in ACTIVITIES:
        return f"Unsupported activity {handoff.activity!r}; use one of {sorted(ACTIVITIES)} without label-family aliases."
    if handoff.outcome not in OUTCOMES:
        return f"Unsupported outcome {handoff.outcome!r}."
    if handoff.next_action not in NEXT_ACTIONS:
        return f"Unsupported next action {handoff.next_action!r}."
    if handoff.predecessor_attempt_id and not _ATTEMPT_ID_RE.fullmatch(handoff.predecessor_attempt_id):
        return f"Invalid predecessor attempt ID {handoff.predecessor_attempt_id!r}."
    if handoff.pr_url and not _PR_URL_RE.fullmatch(handoff.pr_url):
        return f"Invalid preserved PR URL {handoff.pr_url!r}."
    for sha_value, label in ((handoff.pr_head_sha, "PR head"), (handoff.saved_sha, "saved commit")):
        if sha_value and not _SHA_RE.fullmatch(sha_value.strip().lower()):
            return f"Invalid {label} SHA {sha_value!r}: expected a 40-character hex commit SHA."
    if handoff.saved_branch and not _BRANCH_RE.fullmatch(handoff.saved_branch):
        return f"Invalid saved branch {handoff.saved_branch!r}."
    if handoff.successor_attempt_id and not _ATTEMPT_ID_RE.fullmatch(handoff.successor_attempt_id):
        return f"Invalid successor attempt ID {handoff.successor_attempt_id!r}."
    return ""


def _scan_comment_body(body: str) -> tuple[bool, str]:
    """Scan a rendered comment; returns ``(blocked, redacted_detail)``."""
    from moonmind.security import OutboundBundleItem, scan_outbound_bundle
    from moonmind.utils.logging import redact_sensitive_text

    try:
        result = scan_outbound_bundle(
            [OutboundBundleItem(location="attempt.comment", content=body)],
            high_security_mode=True,
        )
    except Exception as exc:  # noqa: BLE001 - scanner failure must fail closed
        return True, f"scanner unavailable ({exc.__class__.__name__})"
    if not result.allowed:
        categories = sorted({finding.category for finding in result.findings})
        detail = redact_sensitive_text("; ".join(result.sanitized_diagnostics) or ",".join(categories))
        return True, detail or "secret-like content detected"
    return False, ""


def redacted_error_detail(raw: Any) -> tuple[str, str]:
    """Render a structured-error detail safe for comments and error payloads.

    Returns ``(safe_detail, error)``. Secret-like content is never echoed: a
    blocked input yields an explicit placeholder plus the redacted categories.
    """
    from moonmind.utils.logging import redact_sensitive_text

    text = str(raw or "").strip()
    if not text:
        return "No detail available.", ""
    blocked, block_detail = _scan_comment_body(text)
    if blocked:
        return f"Error detail withheld by outbound scan ({block_detail}).", ""
    return _truncate(redact_sensitive_text(text), _MAX_REPORT_CHARS), ""


# ---------------------------------------------------------------------------
# Requirement 3: provenance + schema + lineage validation
# ---------------------------------------------------------------------------


def extract_attempt_metadata(body: Any) -> tuple[dict[str, Any] | None, str]:
    """Extract the versioned metadata block from one comment body.

    Returns ``(metadata, error)``. A comment without the machine marker yields
    ``(None, "")``: it is ordinary discussion, not a parse failure. Marker
    present but metadata missing/unparseable yields an explicit error.
    """
    text = str(body or "")
    if ATTEMPT_MARKER_PREFIX not in text:
        return None, ""
    marker_match = re.search(
        r"<!--\s*moonmind-github-attempt:\s*(att_[0-9a-f]{24})\s+v1\s*-->",
        text,
    )
    if not marker_match:
        return None, "unsupported_format: attempt marker is present but malformed"
    # Anchor to the dedicated machine-block sentinel: free-text fields such as
    # lastReport or verificationSummary are untrusted and may themselves
    # contain JSON fences. Only the fence following the exact sentinel is the
    # authoritative metadata block.
    sentinel_idx = text.find(_METADATA_SENTINEL)
    if sentinel_idx == -1:
        return None, "unsupported_format: attempt marker without the versioned metadata block"
    match = re.search(r"```json\s*(\{.*?\})\s*```", text[sentinel_idx:], re.DOTALL)
    if not match:
        return None, "unsupported_format: attempt marker without a machine-readable JSON block"
    try:
        metadata = json.loads(match.group(1))
    except (json.JSONDecodeError, ValueError):
        return None, "unsupported_format: attempt metadata JSON is malformed"
    if not isinstance(metadata, Mapping):
        return None, "unsupported_format: attempt metadata is not an object"
    metadata = dict(metadata)
    # The marker binds the comment to one attempt ID; a mismatched embedded
    # ID is conflicting-copy evidence, surfaced to the caller.
    if metadata.get("attemptId") and metadata["attemptId"] != marker_match.group(1):
        return None, f"conflicting_marker: marker {marker_match.group(1)} disagrees with embedded {metadata.get('attemptId')!r}"
    metadata.setdefault("attemptId", marker_match.group(1))
    return metadata, ""


def validate_attempt_handoff(
    metadata: Mapping[str, Any],
    *,
    repository: str,
    issue_number: int,
    trusted_posters: Sequence[str],
    author_login: str,
) -> dict[str, Any]:
    """Validate one extracted handoff against provenance, schema, and lineage.

    A copied machine marker is not authentication: the comment author's login
    must exactly match the caller-supplied trusted-poster set. Issue prose is
    never consulted; only this metadata block is validated.
    """

    def _deny(code: str, summary: str) -> dict[str, Any]:
        return {"allowed": False, "reasonCode": code, "summary": summary, "metadata": dict(metadata)}

    trusted = {str(login).strip().lower() for login in trusted_posters if str(login).strip()}
    author = str(author_login or "").strip().lower()
    # Shared GitHub usernames do not separate mutually adversarial devices:
    # trust requires an explicit per-deployment poster allow-list match, never
    # the mere presence of a machine marker.
    if not author or author not in trusted:
        return _deny(
            "untrusted_poster",
            "Comment poster is not in the trusted poster set; a copied machine marker is not authentication.",
        )
    version = _string(metadata.get("formatVersion"))
    if version != ATTEMPT_COMMENT_FORMAT_VERSION:
        return _deny(
            "unsupported_version",
            f"Unsupported attempt format {version!r}; expected {ATTEMPT_COMMENT_FORMAT_VERSION}.",
        )
    if _string(metadata.get("repository")).lower() != _string(repository).lower() or metadata.get("issueNumber") != issue_number:
        return _deny(
            "issue_identity_mismatch",
            f"Attempt targets {metadata.get('repository')}#{metadata.get('issueNumber')}, not {repository}#{issue_number}.",
        )
    if not _ATTEMPT_ID_RE.fullmatch(_string(metadata.get("attemptId"))):
        return _deny("unsupported_format", "Attempt metadata lacks a valid attemptId.")
    if not _INSTALLATION_ID_RE.fullmatch(_string(metadata.get("deploymentId"))):
        return _deny("unsupported_format", "Attempt metadata lacks a valid deploymentId.")
    activity = _string(metadata.get("activity"))
    if activity not in ACTIVITIES:
        return _deny(
            "unsupported_format",
            f"Unsupported activity {activity!r}; use one of {sorted(ACTIVITIES)} without label-family aliases.",
        )
    if _string(metadata.get("outcome")) not in OUTCOMES:
        return _deny("unsupported_format", "Attempt metadata carries an unsupported outcome.")
    if _string(metadata.get("nextAction")) not in NEXT_ACTIONS:
        return _deny("unsupported_format", "Attempt metadata carries an unsupported next action.")
    # Authority-sensitive fields must carry their declared JSON types.
    # Coercion after validation (bool("false") is True) would let a
    # provenance-valid malformed comment flip stop/sequence semantics, so
    # mistyped fields fail closed here instead of being coerced later.
    if "writersStopped" in metadata and not isinstance(metadata.get("writersStopped"), bool):
        return _deny("unsupported_format", "Attempt metadata writersStopped must be a boolean.")
    if "updateSeq" in metadata and (
        not isinstance(metadata.get("updateSeq"), int) or isinstance(metadata.get("updateSeq"), bool)
    ):
        return _deny("unsupported_format", "Attempt metadata updateSeq must be an integer.")
    for _collection_key in ("metRequirements", "remainingRequirements"):
        _collection = metadata.get(_collection_key)
        if _collection is not None and (
            not isinstance(_collection, list) or not all(isinstance(item, str) for item in _collection)
        ):
            return _deny("unsupported_format", f"Attempt metadata {_collection_key} must be a list of strings.")
    _retry_shape = metadata.get("retryHistory")
    if isinstance(_retry_shape, Mapping) and "operatorHold" in _retry_shape and not isinstance(
        _retry_shape.get("operatorHold"), bool
    ):
        return _deny("unsupported_format", "Attempt metadata retryHistory.operatorHold must be a boolean.")
    predecessor = _string(metadata.get("predecessorAttemptId"))
    if predecessor and not _ATTEMPT_ID_RE.fullmatch(predecessor):
        return _deny("invalid_predecessor", f"Predecessor attempt ID {predecessor!r} is malformed.")
    preserved = metadata.get("preservedWork")
    if preserved is not None:
        if not isinstance(preserved, Mapping):
            return _deny("invalid_reference", "preservedWork must be an object when present.")
        pr_url = _string(preserved.get("prUrl"))
        if pr_url:
            pr_match = _PR_URL_RE.fullmatch(pr_url)
            if not pr_match:
                return _deny("invalid_reference", f"Preserved PR URL {pr_url!r} is not a valid GitHub PR URL.")
            if pr_match.group(1).lower() != repository.split("/")[0].lower() or pr_match.group(2).lower() != repository.split("/")[-1].lower():
                return _deny("invalid_reference", f"Preserved PR {pr_url!r} belongs to a different repository than {repository}.")
        for sha_value, label in ((preserved.get("prHeadSha"), "PR head"), (preserved.get("savedSha"), "saved commit")):
            if _string(sha_value) and not _SHA_RE.fullmatch(_string(sha_value).lower()):
                return _deny("invalid_reference", f"{label} SHA {_string(sha_value)!r} is not a 40-character hex commit SHA.")
        if _string(preserved.get("savedBranch")) and not _BRANCH_RE.fullmatch(_string(preserved.get("savedBranch"))):
            return _deny("invalid_reference", "Saved branch name is malformed.")
    retry = metadata.get("retryHistory")
    if retry is not None and not isinstance(retry, Mapping):
        return _deny("unsupported_format", "retryHistory must be an object when present.")
    return {
        "allowed": True,
        "reasonCode": "allowed",
        "summary": f"Validated attempt {metadata.get('attemptId')} ({activity}) for {repository}#{issue_number}.",
        "metadata": dict(metadata),
    }


def check_predecessor_available(
    metadata: Mapping[str, Any],
    known_attempt_ids: Sequence[str],
) -> dict[str, Any]:
    """Require referenced predecessors to resolve; never infer a fresh start."""
    predecessor = _string(metadata.get("predecessorAttemptId"))
    if not predecessor:
        return {"ok": True, "reasonCode": "no_predecessor", "summary": "No predecessor claimed."}
    known = {_string(item) for item in known_attempt_ids if _string(item)}
    if predecessor not in known:
        return {
            "ok": False,
            "reasonCode": "missing_predecessor",
            "summary": (
                f"Predecessor attempt {predecessor} is not in the observed GitHub history; "
                "the continuation is blocked rather than started fresh."
            ),
        }
    return {"ok": True, "reasonCode": "predecessor_observed", "summary": f"Predecessor {predecessor} observed."}


def is_stale_local_update(*, local_seq: int, remote_seq: int) -> bool:
    """Detect out-of-order local updates: a local seq at/below the published
    remote seq for the same attempt must not overwrite newer remote state."""
    return int(local_seq) <= int(remote_seq)


# ---------------------------------------------------------------------------
# Requirement 4: serialized per-attempt writes + reconciliation
# ---------------------------------------------------------------------------


def reconcile_attempt_comments(
    comments: Sequence[Mapping[str, Any]],
    *,
    attempt_id: str,
) -> dict[str, Any]:
    """Reconcile uncertain creation by the stable attempt marker.

    ``comments`` are GitHub-visible ``{"id": int, "body": str}`` mappings.
    Duplicate same-ID comments are one logical attempt, never extra retry
    allowance: identical copies reconcile to the canonical (lowest-ID) comment
    while conflicting copies require attention instead of last-timestamp-wins.
    """

    matches: list[dict[str, Any]] = []
    malformed = 0
    for comment in comments:
        if not isinstance(comment, Mapping):
            continue
        body = str(comment.get("body") or "")
        if f"{ATTEMPT_MARKER_PREFIX} {attempt_id} v1" not in body:
            continue
        metadata, error = extract_attempt_metadata(body)
        if metadata is None:
            malformed += 1
            continue
        try:
            comment_id = int(comment.get("id"))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
        matches.append({"id": comment_id, "metadata": metadata, "digest": _metadata_digest(metadata)})
    if not matches:
        if malformed:
            return {
                "action": "attention",
                "reasonCode": "malformed_marker",
                "summary": "Comments carry the attempt marker but no parseable metadata; attention required.",
                "commentId": None,
            }
        return {
            "action": "create",
            "reasonCode": "no_existing_comment",
            "summary": "No comment carries this attempt marker; creation is safe after this read.",
            "commentId": None,
        }
    if len(matches) == 1:
        return {
            "action": "update",
            "reasonCode": "own_comment_found",
            "summary": f"Attempt owns comment {matches[0]['id']}; update that comment only.",
            "commentId": matches[0]["id"],
        }
    digests = {match["digest"] for match in matches}
    if len(digests) == 1:
        canonical = min(match["id"] for match in matches)
        return {
            "action": "update",
            "reasonCode": "duplicate_copies_one_attempt",
            "summary": (
                f"{len(matches)} identical copies represent one logical attempt; "
                f"update canonical comment {canonical} only."
            ),
            "commentId": canonical,
        }
    return {
        "action": "attention",
        "reasonCode": "conflicting_copies",
        "summary": (
            f"{len(matches)} same-ID comments conflict; reconciliation required, "
            "not last-timestamp-wins. No write is authorized."
        ),
        "commentId": None,
    }


def should_publish_progress(
    *,
    last_publish_ts: float | None,
    now_ts: float,
    activity_changed: bool = False,
    outcome_changed: bool = False,
    force: bool = False,
) -> bool:
    """Coalesce routine progress reports within bounded rate limits."""
    if force or activity_changed or outcome_changed:
        return True
    if last_publish_ts is None:
        return True
    try:
        return (float(now_ts) - float(last_publish_ts)) >= PROGRESS_COALESCE_SECONDS
    except (TypeError, ValueError):
        return True


# ---------------------------------------------------------------------------
# Requirement 5: portable retry history (this issue owns the calculation)
# ---------------------------------------------------------------------------


def _referenced_attempt_ids(entry: Mapping[str, Any], *keys: str) -> set[str]:
    """Collect well-formed attempt IDs retained inside one chain entry.

    ``AttemptHandoff`` serializes accumulated ``retryHistory.failedAttempts``
    and ``retryHistory.noProgressAttempts`` (including in redaction
    tombstones), so the references an entry carries are part of the portable
    retry budget, not free text.
    """
    refs: set[str] = set()
    candidates: list[Any] = []
    for key in keys:
        candidates.append(entry.get(key))
    retry_history = entry.get("retryHistory")
    if isinstance(retry_history, Mapping):
        for key in keys:
            candidates.append(retry_history.get(key))
    for candidate in candidates:
        if isinstance(candidate, (list, tuple)):
            for item in candidate:
                if isinstance(item, str) and _ATTEMPT_ID_RE.fullmatch(item.strip()):
                    refs.add(item.strip())
    return refs


def _count_retry_evidence(entries: Sequence[Mapping[str, Any]]) -> tuple[int, int]:
    """Count failures and no-progress signals as a union of direct and retained evidence.

    Direct per-entry outcomes and retained ``failedAttempts`` /
    ``noProgressAttempts`` references are unioned by attempt ID so a current
    handoff carrying retained history cannot silently reset the budget, while
    visible chain entries are never double-counted.
    """
    failed_ids: set[str] = set()
    no_progress_ids: set[str] = set()
    for index, entry in enumerate(entries):
        attempt_id = _string(entry.get("attemptId")) or f"__entry_{index}"
        if _string(entry.get("outcome")) in {"failed", "blocked"} or bool(entry.get("failed")):
            failed_ids.add(attempt_id)
        if bool(entry.get("noProgress")):
            no_progress_ids.add(attempt_id)
        failed_ids |= _referenced_attempt_ids(entry, "failedAttempts", "failed_attempt_refs", "failedAttemptRefs")
        no_progress_ids |= _referenced_attempt_ids(
            entry, "noProgressAttempts", "no_progress_attempt_refs", "noProgressAttemptRefs"
        )
    # References that resolve to a visible chain entry already counted above
    # add no extra allowance either way; the union keeps them counted once.
    return len(failed_ids), len(no_progress_ids)


def derive_retry_state(
    linked_attempts: Sequence[Mapping[str, Any]],
    *,
    policy: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Derive effective retry allowance, cooldown, and hold from GitHub evidence.

    Only the predecessor-linked chain counts: new workflow IDs, device
    (deployment) changes, and label clearing never reset history. Internal
    retries (``internalRetries`` inside one attempt) never create new issue
    attempts. Missing or incompatible policy lineage blocks automatic recovery.
    Simultaneous races (duplicate/conflicting copies or forked successors of
    one predecessor) make the exact global count unknowable: the result is
    then explicitly approximate with no claimed remaining allowance.
    """

    policy = dict(policy or {})
    max_attempts = policy.get("maxAttempts", 3)
    try:
        max_attempts = int(max_attempts)
    except (TypeError, ValueError):
        return _retry_blocked("invalid_policy", "Retry policy maxAttempts is not an integer.")
    expected_lineage = _string(policy.get("lineageRef") or policy.get("lineage_ref"))
    chain: list[Mapping[str, Any]] = [entry for entry in linked_attempts if isinstance(entry, Mapping)]
    # A lineage gap (successor referencing a missing predecessor) is unknown
    # prior work, never a clean slate: block automatic recovery explicitly.
    if any(
        bool(entry.get("lineageGap")) or _string(entry.get("missingPredecessor"))
        for entry in chain
    ):
        return _retry_blocked(
            "lineage_gap",
            "Linked history contains a lineage gap; unknown prior work blocks automatic recovery.",
        )
    failures, no_progress = _count_retry_evidence(chain)
    operator_hold = False
    hold_reason = ""
    cooldown_until = ""
    reset_index = -1
    for index, entry in enumerate(chain):
        retry_history = entry.get("retryHistory") if isinstance(entry.get("retryHistory"), Mapping) else {}
        lineage = _string(entry.get("policyLineage")) or _string(retry_history.get("policyLineage"))
        if expected_lineage and lineage and lineage != expected_lineage:
            return _retry_blocked(
                "incompatible_policy_lineage",
                f"Attempt {entry.get('attemptId')} carries incompatible policy lineage {lineage!r}; automatic recovery is blocked.",
            )
        if not _string(lineage) and chain and expected_lineage:
            return _retry_blocked(
                "missing_policy_lineage",
                "Linked history is missing policy lineage; automatic recovery is blocked rather than assumed fresh.",
            )
        if bool(entry.get("operatorHold")) or bool(retry_history.get("operatorHold")):
            operator_hold = True
            hold_reason = _string(retry_history.get("operatorHoldReason")) or _string(entry.get("operatorHoldReason")) or "operator hold recorded in portable handoff"
        candidate_cooldown = _string(retry_history.get("cooldownUntil")) or _string(entry.get("cooldownUntil"))
        if candidate_cooldown > cooldown_until:
            cooldown_until = candidate_cooldown
        reset = retry_history.get("authorizedReset") or entry.get("authorizedReset")
        if isinstance(reset, Mapping) and _string(reset.get("resetBy")) and _string(reset.get("resetReason")) and _string(reset.get("resetAt")):
            reset_index = index
    if reset_index >= 0:
        failures, no_progress = _count_retry_evidence(chain[reset_index + 1 :])
        if not any(
            bool(entry.get("operatorHold"))
            or bool(((entry.get("retryHistory") or {}) if isinstance(entry.get("retryHistory"), Mapping) else {}).get("operatorHold"))
            for entry in chain[reset_index + 1 :]
        ):
            operator_hold = False
            hold_reason = ""
    # Forked successors of one predecessor (or duplicate/conflicting markers
    # reported by the caller) mean the exact global count cannot be claimed.
    successor_counts: dict[str, int] = {}
    for entry in chain:
        predecessor = _string(entry.get("predecessorAttemptId"))
        if predecessor:
            successor_counts[predecessor] = successor_counts.get(predecessor, 0) + 1
    raced = any(count > 1 for count in successor_counts.values()) or bool(policy.get("raceObserved"))
    attempts_observed = len(chain)
    if raced:
        return {
            "blocked": True,
            "reasonCode": "race_approximate",
            "summary": "Simultaneous duplicate starts observed; no exact global retry count is claimed.",
            "attemptsObserved": attempts_observed,
            "failuresRetained": failures,
            "noProgressRetained": no_progress,
            "remainingAllowance": None,
            "cooldownUntil": cooldown_until,
            "operatorHold": operator_hold,
            "operatorHoldReason": hold_reason,
            "approximate": True,
        }
    remaining: int | None = max(0, max_attempts - failures)
    if operator_hold:
        return {
            "blocked": True,
            "reasonCode": "operator_hold",
            "summary": f"Operator hold active: {hold_reason or 'manual intervention required'}. No automatic replacement is scheduled.",
            "attemptsObserved": attempts_observed,
            "failuresRetained": failures,
            "noProgressRetained": no_progress,
            "remainingAllowance": remaining,
            "cooldownUntil": cooldown_until,
            "operatorHold": True,
            "operatorHoldReason": hold_reason,
            "approximate": False,
        }
    if remaining is not None and remaining <= 0:
        return {
            "blocked": True,
            "reasonCode": "retry_budget_exhausted",
            "summary": f"Retry budget exhausted ({failures} linked failures against allowance {max_attempts}); attention required.",
            "attemptsObserved": attempts_observed,
            "failuresRetained": failures,
            "noProgressRetained": no_progress,
            "remainingAllowance": 0,
            "cooldownUntil": cooldown_until,
            "operatorHold": False,
            "operatorHoldReason": "",
            "approximate": False,
        }
    return {
        "blocked": False,
        "reasonCode": "retry_allowed",
        "summary": f"{remaining} of {max_attempts} automatic attempts remain from portable GitHub history.",
        "attemptsObserved": attempts_observed,
        "failuresRetained": failures,
        "noProgressRetained": no_progress,
        "remainingAllowance": remaining,
        "cooldownUntil": cooldown_until,
        "operatorHold": False,
        "operatorHoldReason": "",
        "approximate": False,
    }


def _retry_blocked(code: str, summary: str) -> dict[str, Any]:
    return {
        "blocked": True,
        "reasonCode": code,
        "summary": summary,
        "attemptsObserved": 0,
        "failuresRetained": 0,
        "noProgressRetained": 0,
        "remainingAllowance": None,
        "cooldownUntil": "",
        "operatorHold": False,
        "operatorHoldReason": "",
        "approximate": False,
    }


# ---------------------------------------------------------------------------
# Requirement 6: proposed release vs completed release
# ---------------------------------------------------------------------------


def evaluate_release(
    handoff: AttemptHandoff,
    *,
    writers_stopped: bool,
    mutations_settled: bool,
    preservation_verified_or_no_work: bool,
    label_outcome_observed: bool,
) -> dict[str, Any]:
    """Distinguish a proposed disposition from a completed release.

    The terminal comment may record the intended next disposition
    (``pendingDisposition``) before label changes, but ``released`` requires
    all four confirmations. Anything less stays ``releasing`` with the
    explicit missing list.
    """
    missing: list[str] = []
    if not writers_stopped:
        missing.append("writers_stopped")
    if not mutations_settled:
        missing.append("mutations_settled")
    if not preservation_verified_or_no_work:
        missing.append("preservation_verified_or_no_work")
    if not label_outcome_observed:
        missing.append("label_outcome_observed")
    if not missing:
        return {
            "released": True,
            "reasonCode": "released",
            "missing": [],
            "summary": "Attempt release completed: writers stopped, mutations settled, preservation verified or explicitly absent, and label outcome observed.",
        }
    return {
        "released": False,
        "reasonCode": "release_proposed",
        "missing": missing,
        "summary": f"Release proposed but not completed; missing: {', '.join(missing)}.",
    }


def is_terminal_released(handoff: AttemptHandoff) -> bool:
    """Return True when the attempt must not resume publication or cleanup.

    A released attempt performs no further issue-state or PR writes, even when
    its device reconnects: a resumed old process rereads GitHub and stops.
    """
    return handoff.activity == ACTIVITY_RELEASED


# ---------------------------------------------------------------------------
# Trusted-boundary publish path (Activity/adapter orchestration)
# ---------------------------------------------------------------------------


def _comment_poster_login(comment: Any) -> str:
    """Return the normalized GitHub login that authored one listed comment."""
    if not isinstance(comment, Mapping):
        return ""
    user = comment.get("user")
    login = user.get("login") if isinstance(user, Mapping) else None
    return str(login or "").strip().lower()


async def publish_attempt_handoff(
    *,
    service: Any,
    repository: str,
    issue_number: int,
    handoff: AttemptHandoff,
    last_publish_ts: float | None = None,
    now_ts: float | None = None,
    force: bool = False,
    trusted_posters: Sequence[str] | None = None,
    prior_activity: str | None = None,
    prior_outcome: str | None = None,
) -> dict[str, Any]:
    """Create or update the attempt's own comment through the trusted adapter.

    The real Activity/adapter path: renders the bounded, redacted comment,
    reconciles uncertain creation by the stable attempt marker, updates only
    the attempt's own comment, coalesces routine progress, and refuses to
    overwrite another attempt's comment. A lost create/update response
    surfaces ``outcome_unknown`` with a reconcile-by-marker instruction
    instead of blindly repeating effects.

    ``trusted_posters`` is the caller-supplied provenance allow-list: when
    provided, only comments authored by those logins participate in
    reconciliation, so a copied machine marker from any other poster can
    neither force ``conflicting_copies`` nor steer the write decision. A
    released handoff may always perform its initial publication (creation);
    the no-more-writes rule applies once the remote comment is already
    released or names a successor. Stale local updates (local ``updateSeq``
    at/below the published remote seq with divergent content) are refused
    instead of overwriting newer terminal evidence.
    """
    import time as _time

    shape_error = _validate_handoff_shapes(handoff)
    if shape_error:
        return {"ok": False, "reasonCode": "invalid_handoff", "summary": shape_error, "commentId": None}
    if _string(handoff.repository).lower() != _string(repository).lower() or handoff.issue_number != issue_number:
        return {
            "ok": False,
            "reasonCode": "issue_identity_mismatch",
            "summary": f"Handoff targets {handoff.repository}#{handoff.issue_number}, not {repository}#{issue_number}.",
            "commentId": None,
        }
    body, render_error = render_attempt_comment(handoff)
    if render_error:
        code = "comment_blocked_by_scan" if "outbound scan" in render_error else "comment_render_failed"
        return {"ok": False, "reasonCode": code, "summary": render_error, "commentId": None}
    trusted: set[str] | None = None
    if trusted_posters is not None:
        trusted = {str(login).strip().lower() for login in trusted_posters if str(login).strip()}
        if not trusted:
            return {
                "ok": False,
                "reasonCode": "invalid_trusted_posters",
                "summary": "trusted_posters must be a non-empty allow-list of GitHub logins.",
                "commentId": None,
            }
    current_ts = float(now_ts) if now_ts is not None else _time.time()
    listed = await service.list_issue_comments(repo=repository, issue_number=issue_number)
    if not listed.get("ok"):
        if listed.get("reasonCode") == "outcome_unknown":
            return {
                "ok": False,
                "reasonCode": "outcome_unknown",
                "summary": "Issue comments unreadable; no local-only claim is made and no write was attempted.",
                "commentId": None,
            }
        return {
            "ok": False,
            "reasonCode": str(listed.get("reasonCode") or "list_failed"),
            "summary": str(listed.get("summary") or "Issue comment listing failed."),
            "commentId": None,
        }
    comments = listed.get("comments") or []
    if not isinstance(comments, list):
        return {"ok": False, "reasonCode": "malformed_list", "summary": "Issue comment listing returned malformed evidence.", "commentId": None}
    ignored_untrusted = 0
    if trusted is not None:
        provenanced: list[Any] = []
        for comment in comments:
            if isinstance(comment, Mapping) and _comment_poster_login(comment) in trusted:
                provenanced.append(comment)
            elif isinstance(comment, Mapping):
                ignored_untrusted += 1
        comments = provenanced
    decision = reconcile_attempt_comments(comments, attempt_id=handoff.attempt_id)
    if decision["action"] == "attention":
        return {
            "ok": False,
            "reasonCode": decision["reasonCode"],
            "summary": decision["summary"],
            "commentId": None,
        }
    local_meta, _ = extract_attempt_metadata(body)
    local_digest = _metadata_digest(local_meta) if local_meta is not None else ""
    if decision["action"] == "update":
        assert decision["commentId"] is not None
        remote_body = ""
        for comment in comments:
            if not isinstance(comment, Mapping):
                continue
            try:
                candidate_id = int(comment.get("id"))  # type: ignore[arg-type]
            except (TypeError, ValueError):
                continue
            if candidate_id == int(decision["commentId"]):
                remote_body = str(comment.get("body") or "")
                break
        remote_meta, remote_error = extract_attempt_metadata(remote_body)
        if remote_meta is None:
            return {
                "ok": False,
                "reasonCode": "malformed_remote" if remote_error else "outcome_unknown",
                "summary": remote_error or "Own attempt comment is unreadable; no overwrite is authorized.",
                "commentId": decision["commentId"],
            }
        remote_digest = _metadata_digest(remote_meta)
        remote_seq = remote_meta.get("updateSeq")
        if not isinstance(remote_seq, int) or isinstance(remote_seq, bool):
            return {
                "ok": False,
                "reasonCode": "malformed_remote",
                "summary": "Own attempt comment carries a mistyped updateSeq; no overwrite is authorized.",
                "commentId": decision["commentId"],
            }
        if _string(remote_meta.get("activity")) == ACTIVITY_RELEASED:
            if remote_digest == local_digest:
                return {
                    "ok": True,
                    "reasonCode": "already_released",
                    "summary": f"Attempt comment {decision['commentId']} is already released; no further write performed.",
                    "commentId": decision["commentId"],
                }
            return {
                "ok": False,
                "reasonCode": "terminal_released_no_resume",
                "summary": "Remote attempt comment is already released; a resumed process must not overwrite terminal evidence.",
                "commentId": decision["commentId"],
            }
        if _string(remote_meta.get("successorAttemptId")) and remote_digest != local_digest:
            return {
                "ok": False,
                "reasonCode": "successor_recorded",
                "summary": "Remote attempt comment names a successor; a resumed process must not overwrite superseded state.",
                "commentId": decision["commentId"],
            }
        if remote_digest != local_digest and is_stale_local_update(
            local_seq=handoff.update_seq, remote_seq=int(remote_seq)
        ):
            return {
                "ok": False,
                "reasonCode": "stale_local_update",
                "summary": (
                    f"Local updateSeq {handoff.update_seq} does not advance published remote seq {remote_seq}; "
                    "a resumed old process must not overwrite newer remote state."
                ),
                "commentId": decision["commentId"],
            }
        if prior_activity is not None:
            activity_changed = bool(force) or (prior_activity != handoff.activity)
        else:
            activity_changed = bool(force) or (_string(remote_meta.get("activity")) != handoff.activity)
        if prior_outcome is not None:
            outcome_changed = prior_outcome != handoff.outcome
        else:
            outcome_changed = _string(remote_meta.get("outcome")) != handoff.outcome
        if not should_publish_progress(
            last_publish_ts=last_publish_ts,
            now_ts=current_ts,
            activity_changed=activity_changed,
            outcome_changed=outcome_changed,
            force=force,
        ):
            return {
                "ok": True,
                "reasonCode": "coalesced",
                "summary": "Routine progress coalesced within the bounded rate limit; no GitHub write performed.",
                "commentId": None,
            }
        updated = await service.update_issue_comment(
            repo=repository,
            comment_id=int(decision["commentId"]),
            body=body,
        )
        if updated.get("ok"):
            summary = f"Updated attempt comment {decision['commentId']}."
            if ignored_untrusted:
                summary += f" Ignored {ignored_untrusted} untrusted same-marker copies."
            return {
                "ok": True,
                "reasonCode": "updated",
                "summary": summary,
                "commentId": decision["commentId"],
            }
        if updated.get("reasonCode") == "outcome_unknown":
            return {
                "ok": False,
                "reasonCode": "outcome_unknown",
                "summary": "Comment update response lost; reconcile by the stable attempt marker before retrying effects.",
                "commentId": decision["commentId"],
            }
        return {
            "ok": False,
            "reasonCode": str(updated.get("reasonCode") or "update_failed"),
            "summary": str(updated.get("summary") or "Comment update failed."),
            "commentId": decision["commentId"],
        }
    # Create path: initial publication (including the first released terminal
    # state) is always authorized; only routine re-publication coalesces.
    if prior_activity is not None:
        create_activity_changed = bool(force) or (prior_activity != handoff.activity)
    else:
        create_activity_changed = bool(force)
    create_outcome_changed = (prior_outcome != handoff.outcome) if prior_outcome is not None else False
    if not should_publish_progress(
        last_publish_ts=last_publish_ts,
        now_ts=current_ts,
        activity_changed=create_activity_changed,
        outcome_changed=create_outcome_changed,
        force=force,
    ):
        return {
            "ok": True,
            "reasonCode": "coalesced",
            "summary": "Routine progress coalesced within the bounded rate limit; no GitHub write performed.",
            "commentId": None,
        }
    created = await service.create_issue_comment(repo=repository, issue_number=issue_number, body=body)
    if created.get("ok"):
        summary = f"Created attempt comment {created.get('commentId')}."
        if ignored_untrusted:
            summary += f" Ignored {ignored_untrusted} untrusted same-marker copies."
        return {
            "ok": True,
            "reasonCode": "created",
            "summary": summary,
            "commentId": created.get("commentId"),
        }
    if created.get("reasonCode") == "outcome_unknown":
        return {
            "ok": False,
            "reasonCode": "outcome_unknown",
            "summary": "Comment create response lost; reconcile by the stable attempt marker before retrying effects.",
            "commentId": None,
        }
    return {
        "ok": False,
        "reasonCode": str(created.get("reasonCode") or "create_failed"),
        "summary": str(created.get("summary") or "Comment creation failed."),
        "commentId": None,
    }


def collect_linked_chain(
    validated_handoffs: Sequence[Mapping[str, Any]],
    *,
    head_attempt_id: str,
) -> list[dict[str, Any]]:
    """Order validated handoffs oldest-first along the predecessor chain.

    Device B reconstructs A's remaining work from GitHub alone: starting at
    the head attempt, follow ``predecessorAttemptId`` links through the
    validated set. A lineage gap (successor referencing a missing
    predecessor) stops the chain with an explicit marker instead of inferring
    a clean slate.
    """
    by_id = {str(entry.get("attemptId")): dict(entry) for entry in validated_handoffs if str(entry.get("attemptId"))}
    chain: list[dict[str, Any]] = []
    seen: set[str] = set()
    current: str | None = _string(head_attempt_id)
    gap = ""
    while current and current not in seen:
        seen.add(current)
        entry = by_id.get(current)
        if entry is None:
            gap = current
            break
        chain.append(entry)
        current = _string(entry.get("predecessorAttemptId")) or None
    chain.reverse()
    if gap:
        chain.append({"attemptId": gap, "lineageGap": True, "missingPredecessor": gap})
    return chain


__all__ = [
    "ACTIVITIES",
    "ACTIVITY_ACTIVE",
    "ACTIVITY_ATTENTION",
    "ACTIVITY_AWAITING_REVIEW",
    "ACTIVITY_MEANINGS",
    "ACTIVITY_PREPARING",
    "ACTIVITY_RELEASED",
    "ACTIVITY_RELEASING",
    "ATTEMPT_COMMENT_FORMAT_VERSION",
    "ATTEMPT_MARKER_PREFIX",
    "ATTEMPT_MARKER_SUFFIX",
    "MAX_COMMENT_CHARS",
    "NEXT_ACTIONS",
    "OUTCOMES",
    "PROGRESS_COALESCE_SECONDS",
    "AttemptHandoff",
    "build_attempt_identity",
    "check_predecessor_available",
    "collect_linked_chain",
    "derive_retry_state",
    "evaluate_release",
    "extract_attempt_metadata",
    "handoff_from_metadata",
    "is_stale_local_update",
    "is_terminal_released",
    "new_attempt_id",
    "publish_attempt_handoff",
    "reconcile_attempt_comments",
    "redacted_error_detail",
    "render_attempt_comment",
    "render_tombstone",
    "resolve_installation_id",
    "should_publish_progress",
    "validate_attempt_handoff",
]
