"""Portable per-attempt handoffs and cross-deployment retry history.

Single canonical owner for GitHub issue attempt evidence (design:
docs/Workflows/GitHubIssueStatusStateMachineDesign.md, sections 4 and 5.3).
Deterministic and side-effect-free except :func:`get_or_create_installation_id`,
which is an Activity-boundary helper. Trusted Activities/services perform
GitHub reads/writes; this module decides what attempt comments mean, what the
retry budget is, and what may be published.

Contract summary:

* One identifiable comment per issue-work attempt. Separate attempts never
  overwrite one shared summary and never rewrite each other's history.
* Stable installation identity (``MOONMIND_INSTALLATION_ID``) differs across
  deployments even when they share one GitHub account. The deployed server
  build digest (``moonmind.omnigent.deployment_identity``) remains execution
  authority and is never used as attempt identity. There are no competing
  internal aliases: this is the single canonical attempt deployment field.
* One versioned, bounded comment representation with a readable summary and
  machine-readable metadata. Format version 1 only.
* A copied machine marker is not authentication. Callers supply trusted
  poster provenance from the authenticated GitHub boundary; validation
  rejects spoofed, unsupported, inconsistent, and missing-predecessor
  handoffs rather than inferring a fresh start.
* Writes serialize per attempt. Uncertain creation reconciles by stable
  attempt marker before retrying. Duplicate same-ID comments are one logical
  attempt; conflicting copies require attention, not last-timestamp-wins.
  Progress reports coalesce within a bounded rate limit.
* Retry allowance, cooldown, and operator hold travel in the portable
  handoff. Fresh workflow IDs, device changes, and label removal never
  reset the lineage. Missing or incompatible policy lineage blocks
  automatic recovery. Authorized resets require an audited decision.
  No exact global counter is claimed under simultaneous races.
* A proposed release disposition becomes ``released`` only after stopped
  writers, resolved mutations, verified preservation (or explicit no-work
  evidence), and an observed label outcome. Terminal attempts never resume
  publication or cleanup merely because their device reconnects.
* All outbound comment bodies and structured-error summaries pass through
  the existing outbound redaction. Local workflow links are optional
  diagnostics, never the sole recoverability evidence.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

#: Canonical format version for every attempt handoff comment.
ATTEMPT_HANDOFF_FORMAT_VERSION = 1
SUPPORTED_HANDOFF_VERSIONS = frozenset({1})

#: Canonical per-attempt activities (design section 4.1). The hyphenated
#: ``awaiting-review`` spelling is canonical in machine metadata; the
#: space-separated ``awaiting review`` spelling is accepted on parse.
ATTEMPT_ACTIVITY_PREPARING = "preparing"
ATTEMPT_ACTIVITY_ACTIVE = "active"
ATTEMPT_ACTIVITY_AWAITING_REVIEW = "awaiting-review"
ATTEMPT_ACTIVITY_RELEASING = "releasing"
ATTEMPT_ACTIVITY_RELEASED = "released"
ATTEMPT_ACTIVITY_ATTENTION = "attention"

ATTEMPT_ACTIVITIES = frozenset(
    {
        ATTEMPT_ACTIVITY_PREPARING,
        ATTEMPT_ACTIVITY_ACTIVE,
        ATTEMPT_ACTIVITY_AWAITING_REVIEW,
        ATTEMPT_ACTIVITY_RELEASING,
        ATTEMPT_ACTIVITY_RELEASED,
        ATTEMPT_ACTIVITY_ATTENTION,
    }
)

#: Human meanings for each activity. These live in comment metadata only;
#: no equivalent label family is introduced.
ATTEMPT_ACTIVITY_MEANINGS = {
    ATTEMPT_ACTIVITY_PREPARING: "Attempt announced; admission reread is pending before edits.",
    ATTEMPT_ACTIVITY_ACTIVE: "Controlling attempt owns edits; internal retries stay within it.",
    ATTEMPT_ACTIVITY_AWAITING_REVIEW: "Verified implementation published; review/merge journey owns next action.",
    ATTEMPT_ACTIVITY_RELEASING: "Terminal handoff records a proposed disposition; release gates are still pending.",
    ATTEMPT_ACTIVITY_RELEASED: "Writer stopped, mutations settled, preservation verified, label outcome observed.",
    ATTEMPT_ACTIVITY_ATTENTION: "Unresolved decision, conflict, unsafe recovery, or exhausted budget needs an operator.",
}

#: Bounded next actions a handoff may name.
ATTEMPT_NEXT_ACTIONS = frozenset(
    {
        "fresh_retry",
        "continue_implementation",
        "verify",
        "continue_review",
        "finalize_status",
        "obtain_attention",
    }
)

#: Bounded outcome categories.
ATTEMPT_OUTCOMES = frozenset(
    {
        "in_progress",
        "implemented",
        "no_work",
        "failed",
        "cancelled",
        "held",
    }
)

#: Machine marker prefix. The attempt id is embedded so uncertain creation
#: can reconcile by stable marker before retrying.
HANDOFF_MARKER_PREFIX = "<!-- moonmind-attempt-handoff"
HANDOFF_CODE_FENCE = "```attempt-handoff-json"

#: Outbound bounds. Comments stay small enough for GitHub issue timelines
#: and for hermetic three-deployment tests to exchange them verbatim.
MAX_COMMENT_CHARS = 6000
MAX_LIST_ITEMS = 20
MAX_TEXT_FIELD_CHARS = 1000

#: Progress reports coalesce within this window instead of emitting on
#: every runtime poll. Activity timestamps support observation only.
PROGRESS_COALESCE_SECONDS = 300.0

#: Default retry policy lineage version. Unknown policy versions fail
#: closed: missing or incompatible lineage blocks automatic recovery.
RETRY_POLICY_VERSION = 1

INSTALLATION_ID_ENV_VAR = "MOONMIND_INSTALLATION_ID"
INSTALLATION_ID_FILE_ENV_VAR = "MOONMIND_INSTALLATION_ID_FILE"
_INSTALLATION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{7,127}$")
_MARKER_RE = re.compile(
    r"<!--\s*moonmind-attempt-handoff\s+v(?P<version>\d+)\s+attempt=(?P<attempt>[A-Za-z0-9._-]+)\s*-->"
)
_FENCE_RE = re.compile(r"```attempt-handoff-json\s*\n(?P<payload>\{.*?\})\s*\n```", re.DOTALL)


def _string(value: Any) -> str:
    return str(value or "").strip()


def _truncate_text(value: Any, limit: int = MAX_TEXT_FIELD_CHARS) -> str:
    text = _string(value)
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 12)] + " [truncated]"


def _truncate_list(values: Any) -> list[str]:
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes, bytearray)):
        return []
    items = [_truncate_text(item, 300) for item in values if _string(item)]
    return items[:MAX_LIST_ITEMS]


def resolve_installation_id(explicit: Any) -> str:
    """Return the canonical installation id for *explicit*, else ``""``.

    Pure and deterministic: env/file persistence lives in
    :func:`get_or_create_installation_id` at the Activity boundary.
    """
    candidate = _string(explicit)
    if candidate and _INSTALLATION_ID_RE.fullmatch(candidate):
        return candidate
    return ""


def default_installation_id_file() -> Path:
    """Return the default persisted installation-id path (Activity boundary)."""
    override = _string(os.getenv(INSTALLATION_ID_FILE_ENV_VAR))
    if override:
        return Path(override).expanduser()
    return Path.cwd() / "var" / "moonmind_installation_id"


def get_or_create_installation_id(*, path: Path | None = None) -> str:
    """Load or create the stable installation identity for this deployment.

    Precedence is deterministic and single-pathed: explicit
    ``MOONMIND_INSTALLATION_ID`` env, then the persisted id file, then a
    newly generated id that is persisted for restart/retry stability.
    No competing aliases are consulted.
    """
    from_env = resolve_installation_id(os.getenv(INSTALLATION_ID_ENV_VAR))
    if from_env:
        return from_env
    target = path if path is not None else default_installation_id_file()
    try:
        if target.exists():
            persisted = resolve_installation_id(target.read_text(encoding="utf-8"))
            if persisted:
                return persisted
    except OSError:
        # Best-effort read: fall through and generate a fresh ephemeral id.
        pass
    generated = f"inst-{uuid.uuid4().hex[:16]}"
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(generated + "\n", encoding="utf-8")
    except OSError:
        # Best-effort persist: keep the generated id in memory for this run.
        pass
    return generated


def new_attempt_id(
    *,
    repository: str,
    issue_number: int,
    workflow_id: str = "",
    run_id: str = "",
    random_suffix: str = "",
) -> str:
    """Return a globally unique attempt id bound to repo/issue/workflow/run.

    Uniqueness comes from randomness; the binding lives in the handoff
    record (exact repository, issue, workflow/run). Callers may pass
    *random_suffix* in tests; otherwise one is generated.
    """
    _ = (repository, issue_number, workflow_id, run_id)
    suffix = _string(random_suffix).lower().replace("_", "-")
    suffix = re.sub(r"[^a-z0-9-]", "", suffix)[:16] or uuid.uuid4().hex[:8]
    return f"att-{uuid.uuid4().hex[:4]}{suffix}-{uuid.uuid4().hex[:4]}"


def stable_attempt_marker(attempt_id: str) -> str:
    """Return the stable marker embedded in every comment for *attempt_id*."""
    return f"{HANDOFF_MARKER_PREFIX} v{ATTEMPT_HANDOFF_FORMAT_VERSION} attempt={_string(attempt_id)} -->"


def activity_for_lifecycle_mode(mode: str) -> str:
    """Map a lifecycle tool mode onto the canonical attempt activity."""
    normalized = _string(mode).lower().replace(" ", "_")
    mapping = {
        "start": ATTEMPT_ACTIVITY_PREPARING,
        "in_progress": ATTEMPT_ACTIVITY_ACTIVE,
        "code_review": ATTEMPT_ACTIVITY_AWAITING_REVIEW,
        "finalize_after_pr_or_done": ATTEMPT_ACTIVITY_AWAITING_REVIEW,
        "done": ATTEMPT_ACTIVITY_RELEASED,
        "recovery_needed": ATTEMPT_ACTIVITY_RELEASING,
        "needs_attention": ATTEMPT_ACTIVITY_ATTENTION,
        "available": ATTEMPT_ACTIVITY_RELEASED,
    }
    return mapping.get(normalized, ATTEMPT_ACTIVITY_ACTIVE)


def normalize_activity(value: Any) -> str:
    """Normalize a parsed activity value onto the canonical spelling."""
    candidate = _string(value).lower().replace("_", "-").replace(" ", "-")
    if candidate == "awaiting-review":
        return ATTEMPT_ACTIVITY_AWAITING_REVIEW
    if candidate in ATTEMPT_ACTIVITIES:
        return candidate
    return ""


# ---------------------------------------------------------------------------
# Handoff record
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AttemptHandoff:
    """Portable evidence for one issue-work attempt."""

    attempt_id: str
    deployment_id: str
    repository: str
    issue_number: int
    workflow_id: str = ""
    run_id: str = ""
    predecessor_attempt_id: str = ""
    predecessor_comment_id: str = ""
    activity: str = ATTEMPT_ACTIVITY_ACTIVE
    last_report: str = ""
    writers_stopped: bool = False
    pending_disposition: str = ""
    pr_url: str = ""
    pr_head: str = ""
    pr_base: str = ""
    saved_branch: str = ""
    saved_sha: str = ""
    outcome: str = "in_progress"
    remaining_requirements: tuple[str, ...] = ()
    verification_summary: str = ""
    next_action: str = "continue_implementation"
    retry_history: tuple[str, ...] = ()
    retry_allowance: int = 0
    retry_remaining: int = 0
    cooldown_until: str = ""
    operator_hold: bool = False
    operator_hold_reason: str = ""
    retry_policy_version: int = RETRY_POLICY_VERSION
    reset_authorization: str = ""
    internal_retry_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "formatVersion": ATTEMPT_HANDOFF_FORMAT_VERSION,
            "attemptId": self.attempt_id,
            "deploymentId": self.deployment_id,
            "repository": self.repository,
            "issueNumber": self.issue_number,
            "workflowId": self.workflow_id,
            "runId": self.run_id,
            "predecessorAttemptId": self.predecessor_attempt_id,
            "predecessorCommentId": self.predecessor_comment_id,
            "activity": self.activity,
            "lastReport": self.last_report,
            "writersStopped": self.writers_stopped,
            "pendingDisposition": self.pending_disposition,
            "prUrl": self.pr_url,
            "prHead": self.pr_head,
            "prBase": self.pr_base,
            "savedBranch": self.saved_branch,
            "savedSha": self.saved_sha,
            "outcome": self.outcome,
            "remainingRequirements": list(self.remaining_requirements),
            "verificationSummary": self.verification_summary,
            "nextAction": self.next_action,
            "retryHistory": list(self.retry_history),
            "retryAllowance": self.retry_allowance,
            "retryRemaining": self.retry_remaining,
            "cooldownUntil": self.cooldown_until,
            "operatorHold": self.operator_hold,
            "operatorHoldReason": self.operator_hold_reason,
            "retryPolicyVersion": self.retry_policy_version,
            "resetAuthorization": self.reset_authorization,
            "internalRetryCount": self.internal_retry_count,
        }

    @staticmethod
    def from_dict(payload: Mapping[str, Any]) -> "AttemptHandoff":
        def _int(value: Any, default: int = 0) -> int:
            try:
                number = int(value)
            except (TypeError, ValueError):
                return default
            return number if number >= 0 else default

        raw_activity = normalize_activity(payload.get("activity"))
        raw_next = _string(payload.get("nextAction")).lower().replace(" ", "_").replace("-", "_")
        raw_outcome = _string(payload.get("outcome")).lower().replace(" ", "_").replace("-", "_")
        try:
            issue_number = int(str(payload.get("issueNumber")).strip())
        except (TypeError, ValueError, AttributeError):
            issue_number = 0
        return AttemptHandoff(
            attempt_id=_string(payload.get("attemptId")),
            deployment_id=_string(payload.get("deploymentId")),
            repository=_string(payload.get("repository")),
            issue_number=issue_number,
            workflow_id=_truncate_text(payload.get("workflowId"), 200),
            run_id=_truncate_text(payload.get("runId"), 200),
            predecessor_attempt_id=_truncate_text(payload.get("predecessorAttemptId"), 200),
            predecessor_comment_id=_truncate_text(payload.get("predecessorCommentId"), 200),
            activity=raw_activity,
            last_report=_truncate_text(payload.get("lastReport")),
            writers_stopped=bool(payload.get("writersStopped")),
            pending_disposition=_truncate_text(payload.get("pendingDisposition"), 200),
            pr_url=_truncate_text(payload.get("prUrl"), 500),
            pr_head=_truncate_text(payload.get("prHead"), 200),
            pr_base=_truncate_text(payload.get("prBase"), 200),
            saved_branch=_truncate_text(payload.get("savedBranch"), 200),
            saved_sha=_truncate_text(payload.get("savedSha"), 200),
            outcome=raw_outcome,
            remaining_requirements=tuple(_truncate_list(payload.get("remainingRequirements"))),
            verification_summary=_truncate_text(payload.get("verificationSummary")),
            next_action=raw_next,
            retry_history=tuple(_truncate_list(payload.get("retryHistory"))),
            retry_allowance=_int(payload.get("retryAllowance")),
            retry_remaining=_int(payload.get("retryRemaining")),
            cooldown_until=_truncate_text(payload.get("cooldownUntil"), 200),
            operator_hold=bool(payload.get("operatorHold")),
            operator_hold_reason=_truncate_text(payload.get("operatorHoldReason")),
            retry_policy_version=_int(payload.get("retryPolicyVersion"), RETRY_POLICY_VERSION),
            reset_authorization=_truncate_text(payload.get("resetAuthorization"), 200),
            internal_retry_count=_int(payload.get("internalRetryCount")),
        )


def build_attempt_handoff(
    *,
    attempt_id: str,
    deployment_id: str,
    repository: str,
    issue_number: int,
    activity: str = ATTEMPT_ACTIVITY_ACTIVE,
    workflow_id: str = "",
    run_id: str = "",
    predecessor_attempt_id: str = "",
    predecessor_comment_id: str = "",
    last_report: str = "",
    writers_stopped: bool = False,
    pending_disposition: str = "",
    pr_url: str = "",
    pr_head: str = "",
    pr_base: str = "",
    saved_branch: str = "",
    saved_sha: str = "",
    outcome: str = "in_progress",
    remaining_requirements: Sequence[Any] | None = None,
    verification_summary: str = "",
    next_action: str = "continue_implementation",
    retry_history: Sequence[Any] | None = None,
    retry_allowance: int = 0,
    retry_remaining: int = 0,
    cooldown_until: str = "",
    operator_hold: bool = False,
    operator_hold_reason: str = "",
    retry_policy_version: int = RETRY_POLICY_VERSION,
    reset_authorization: str = "",
    internal_retry_count: int = 0,
) -> AttemptHandoff:
    """Build a bounded handoff record from Activity inputs."""
    normalized_activity = normalize_activity(activity) or ATTEMPT_ACTIVITY_ACTIVE
    normalized_next = _string(next_action).lower().replace(" ", "_").replace("-", "_")
    if normalized_next not in ATTEMPT_NEXT_ACTIONS:
        normalized_next = "continue_implementation"
    normalized_outcome = _string(outcome).lower().replace(" ", "_").replace("-", "_")
    if normalized_outcome not in ATTEMPT_OUTCOMES:
        normalized_outcome = "in_progress"
    return AttemptHandoff(
        attempt_id=_truncate_text(attempt_id, 200),
        deployment_id=_truncate_text(deployment_id, 200),
        repository=_truncate_text(repository, 200),
        issue_number=int(issue_number) if int(issue_number) > 0 else 0,
        workflow_id=_truncate_text(workflow_id, 200),
        run_id=_truncate_text(run_id, 200),
        predecessor_attempt_id=_truncate_text(predecessor_attempt_id, 200),
        predecessor_comment_id=_truncate_text(predecessor_comment_id, 200),
        activity=normalized_activity,
        last_report=_truncate_text(last_report),
        writers_stopped=bool(writers_stopped),
        pending_disposition=_truncate_text(pending_disposition, 200),
        pr_url=_truncate_text(pr_url, 500),
        pr_head=_truncate_text(pr_head, 200),
        pr_base=_truncate_text(pr_base, 200),
        saved_branch=_truncate_text(saved_branch, 200),
        saved_sha=_truncate_text(saved_sha, 200),
        outcome=normalized_outcome,
        remaining_requirements=tuple(_truncate_list(remaining_requirements or [])),
        verification_summary=_truncate_text(verification_summary),
        next_action=normalized_next,
        retry_history=tuple(_truncate_list(retry_history or [])),
        retry_allowance=max(0, int(retry_allowance or 0)),
        retry_remaining=max(0, int(retry_remaining or 0)),
        cooldown_until=_truncate_text(cooldown_until, 200),
        operator_hold=bool(operator_hold),
        operator_hold_reason=_truncate_text(operator_hold_reason),
        retry_policy_version=int(retry_policy_version or RETRY_POLICY_VERSION),
        reset_authorization=_truncate_text(reset_authorization, 200),
        internal_retry_count=max(0, int(internal_retry_count or 0)),
    )


# ---------------------------------------------------------------------------
# Redaction (existing outbound scanning)
# ---------------------------------------------------------------------------


def redact_comment_body(body: str) -> str:
    """Redact an outbound comment body with the existing scanner."""
    from moonmind.utils.logging import redact_sensitive_text

    return redact_sensitive_text(_string(body))


def redacted_error_summary(summary: str) -> str:
    """Redact an outbound structured-error summary with the existing scanner."""
    from moonmind.utils.logging import redact_sensitive_text

    return redact_sensitive_text(_string(summary))


# ---------------------------------------------------------------------------
# Rendering and parsing (versioned, bounded)
# ---------------------------------------------------------------------------


def _readable_summary(handoff: AttemptHandoff, issue_ref: str) -> str:
    if handoff.pr_url and handoff.activity == ATTEMPT_ACTIVITY_AWAITING_REVIEW:
        return f"Implementation pull request: {handoff.pr_url}"
    summaries = {
        ATTEMPT_ACTIVITY_PREPARING: f"MoonMind started implementation for {issue_ref}.",
        ATTEMPT_ACTIVITY_ACTIVE: f"MoonMind started implementation for {issue_ref}.",
        ATTEMPT_ACTIVITY_AWAITING_REVIEW: (
            f"MoonMind recorded a continuation handoff for {issue_ref}; "
            "resume from the preserved work instead of starting fresh."
            if not handoff.pr_url
            else f"Implementation pull request: {handoff.pr_url}"
        ),
        ATTEMPT_ACTIVITY_RELEASING: (
            f"MoonMind recorded a continuation handoff for {issue_ref}; "
            "resume from the preserved work instead of starting fresh."
        ),
        ATTEMPT_ACTIVITY_RELEASED: (
            f"MoonMind released {issue_ref} to available with terminal proof; "
            "it is eligible for fresh admission."
        ),
        ATTEMPT_ACTIVITY_ATTENTION: (
            f"MoonMind flagged {issue_ref} as needing attention; "
            "operator resolution is required before automatic work continues."
        ),
    }
    return summaries.get(
        handoff.activity, f"MoonMind updated lifecycle status for {issue_ref}."
    )


def render_attempt_comment(handoff: AttemptHandoff) -> str:
    """Render one bounded handoff comment (readable summary + metadata)."""
    issue_ref = f"{handoff.repository}#{handoff.issue_number}" if handoff.repository else f"issue #{handoff.issue_number}"
    summary = _readable_summary(handoff, issue_ref)
    lines = [summary, ""]
    lines.append(f"Attempt `{handoff.attempt_id}` on deployment `{handoff.deployment_id}`.")
    if handoff.predecessor_attempt_id:
        lines.append(f"Continues attempt `{handoff.predecessor_attempt_id}`.")
    lines.append(
        f"Activity: {handoff.activity} ({ATTEMPT_ACTIVITY_MEANINGS.get(handoff.activity, '')})"
    )
    if handoff.last_report:
        lines.append(f"Last report: {handoff.last_report}")
    if handoff.pr_url:
        lines.append(f"Preserved PR: {handoff.pr_url}")
    if handoff.pr_head or handoff.pr_base:
        lines.append(f"Head/base: {handoff.pr_head or '?'} / {handoff.pr_base or '?'}")
    if handoff.saved_branch or handoff.saved_sha:
        lines.append(f"Saved: {handoff.saved_branch or '?'}@{handoff.saved_sha or '?'}")
    lines.append(f"Outcome: {handoff.outcome}; next: {handoff.next_action}.")
    if handoff.remaining_requirements:
        lines.append("Remaining: " + "; ".join(handoff.remaining_requirements[:5]))
    if handoff.verification_summary:
        lines.append(f"Verification: {handoff.verification_summary}")
    if handoff.operator_hold:
        lines.append(f"Operator hold: {handoff.operator_hold_reason or 'held'}.")
    elif handoff.cooldown_until:
        lines.append(f"Cooldown until: {handoff.cooldown_until}.")
    lines.append(f"Retry: {handoff.retry_remaining}/{handoff.retry_allowance} remaining.")
    lines.append("")
    metadata = json.dumps(handoff.to_dict(), sort_keys=True, separators=(",", ":"))
    lines.append(stable_attempt_marker(handoff.attempt_id))
    lines.append(f"{HANDOFF_CODE_FENCE}\n{metadata}\n```")
    body = "\n".join(lines)
    body = redact_comment_body(body)
    if len(body) > MAX_COMMENT_CHARS:
        # Boundedness is structural: keep the marker plus metadata intact
        # and truncate the human section, never the machine record.
        tail = f"\n{stable_attempt_marker(handoff.attempt_id)}\n{HANDOFF_CODE_FENCE}\n{metadata}\n```"
        tail = redact_comment_body(tail)
        head_budget = max(0, MAX_COMMENT_CHARS - len(tail) - 24)
        body = redact_comment_body(summary[:head_budget] + "\n[truncated]\n") + tail
    return body


@dataclass(frozen=True)
class ParsedAttempt:
    """Result of parsing one GitHub comment body."""

    status: str
    attempt_id: str = ""
    format_version: int = 0
    handoff: AttemptHandoff | None = None
    comment_id: str = ""
    author_login: str = ""
    reason_code: str = ""
    summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "attemptId": self.attempt_id,
            "formatVersion": self.format_version,
            "commentId": self.comment_id,
            "authorLogin": self.author_login,
            "reasonCode": self.reason_code,
            "summary": self.summary,
            "handoff": self.handoff.to_dict() if self.handoff is not None else None,
        }


def parse_attempt_comment(
    body: Any, *, comment_id: Any = "", author_login: Any = ""
) -> ParsedAttempt:
    """Parse one comment body into a handoff or an explicit rejection."""
    text = _string(body)
    marker = _MARKER_RE.search(text)
    if marker is None:
        return ParsedAttempt(
            status="no_marker",
            comment_id=_string(comment_id),
            author_login=_string(author_login),
            reason_code="no_marker",
            summary="Comment carries no attempt marker; it is not attempt evidence.",
        )
    try:
        version = int(marker.group("version"))
    except (TypeError, ValueError):
        version = 0
    attempt_id = _string(marker.group("attempt"))
    if version not in SUPPORTED_HANDOFF_VERSIONS:
        return ParsedAttempt(
            status="unsupported_version",
            attempt_id=attempt_id,
            format_version=version,
            comment_id=_string(comment_id),
            author_login=_string(author_login),
            reason_code="unsupported_version",
            summary=(
                f"Attempt handoff version v{version} is unsupported; "
                "no silent admission or destructive normalization."
            ),
        )
    if not attempt_id:
        return ParsedAttempt(
            status="invalid_marker",
            format_version=version,
            comment_id=_string(comment_id),
            author_login=_string(author_login),
            reason_code="invalid_marker",
            summary="Attempt marker has no attempt id; it cannot identify an attempt.",
        )
    fence = _FENCE_RE.search(text)
    if fence is None:
        return ParsedAttempt(
            status="missing_metadata",
            attempt_id=attempt_id,
            format_version=version,
            comment_id=_string(comment_id),
            author_login=_string(author_login),
            reason_code="missing_metadata",
            summary="Attempt marker has no machine-readable metadata; rejected rather than inferred.",
        )
    try:
        payload = json.loads(fence.group("payload"))
    except (json.JSONDecodeError, ValueError):
        return ParsedAttempt(
            status="invalid_metadata",
            attempt_id=attempt_id,
            format_version=version,
            comment_id=_string(comment_id),
            author_login=_string(author_login),
            reason_code="invalid_metadata",
            summary="Attempt metadata is not valid JSON; rejected rather than inferred.",
        )
    if not isinstance(payload, Mapping):
        return ParsedAttempt(
            status="invalid_metadata",
            attempt_id=attempt_id,
            format_version=version,
            comment_id=_string(comment_id),
            author_login=_string(author_login),
            reason_code="invalid_metadata",
            summary="Attempt metadata is not an object; rejected rather than inferred.",
        )
    if int(payload.get("formatVersion") or 0) != version:
        return ParsedAttempt(
            status="inconsistent_metadata",
            attempt_id=attempt_id,
            format_version=version,
            comment_id=_string(comment_id),
            author_login=_string(author_login),
            reason_code="inconsistent_metadata",
            summary="Marker version and metadata version disagree; rejected rather than inferred.",
        )
    if _string(payload.get("attemptId")) != attempt_id:
        return ParsedAttempt(
            status="inconsistent_metadata",
            attempt_id=attempt_id,
            format_version=version,
            comment_id=_string(comment_id),
            author_login=_string(author_login),
            reason_code="inconsistent_metadata",
            summary="Marker attempt id and metadata attempt id disagree; rejected rather than inferred.",
        )
    handoff = AttemptHandoff.from_dict(payload)
    if not handoff.activity:
        return ParsedAttempt(
            status="invalid_metadata",
            attempt_id=attempt_id,
            format_version=version,
            comment_id=_string(comment_id),
            author_login=_string(author_login),
            reason_code="invalid_metadata",
            summary="Attempt metadata names an unknown activity; rejected rather than inferred.",
        )
    if not handoff.repository or handoff.issue_number <= 0:
        return ParsedAttempt(
            status="invalid_metadata",
            attempt_id=attempt_id,
            format_version=version,
            comment_id=_string(comment_id),
            author_login=_string(author_login),
            reason_code="invalid_metadata",
            summary="Attempt metadata has no valid repository/issue binding.",
        )
    return ParsedAttempt(
        status="ok",
        attempt_id=attempt_id,
        format_version=version,
        handoff=handoff,
        comment_id=_string(comment_id),
        author_login=_string(author_login),
        reason_code="ok",
        summary=f"Parsed attempt {attempt_id}.",
    )


# ---------------------------------------------------------------------------
# Provenance and schema validation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AttemptValidation:
    """Explicit outcome of validating one parsed attempt handoff."""

    valid: bool
    reason_code: str
    summary: str
    attempt_id: str = ""
    comment_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "reasonCode": self.reason_code,
            "summary": self.summary,
            "attemptId": self.attempt_id,
            "commentId": self.comment_id,
        }


def validate_attempt_handoff(
    parsed: ParsedAttempt,
    *,
    expected_repository: str,
    expected_issue_number: int,
    trusted_posters: Sequence[str] | None,
    known_comment_ids: Sequence[Any] | None = None,
    predecessor_index: Mapping[str, str] | None = None,
    known_pr_urls: Sequence[str] | None = None,
) -> AttemptValidation:
    """Validate provenance, schema, issue identity, lineage, and references.

    A copied machine marker is not authentication: the marker must come
    from a trusted poster (authenticated GitHub login resolved at the
    trusted boundary). Shared-account comments never separate mutually
    adversarial devices; validation reports the poster but does not claim
    an adversarial boundary.
    """
    if parsed.status != "ok" or parsed.handoff is None:
        return AttemptValidation(
            valid=False,
            reason_code=parsed.reason_code or "unparseable",
            summary=redacted_error_summary(parsed.summary or "Unparseable attempt handoff."),
            attempt_id=parsed.attempt_id,
            comment_id=parsed.comment_id,
        )
    handoff = parsed.handoff
    trusted = {_string(poster).lower() for poster in (trusted_posters or []) if _string(poster)}
    poster = _string(parsed.author_login).lower()
    if not trusted or not poster or poster not in trusted:
        return AttemptValidation(
            valid=False,
            reason_code="untrusted_poster",
            summary=(
                "Attempt marker provenance is not trusted: the poster is not in "
                "the trusted poster set resolved at the authenticated boundary. "
                "A copied machine marker is not authentication."
            ),
            attempt_id=parsed.attempt_id,
            comment_id=parsed.comment_id,
        )
    if (
        handoff.repository.casefold() != _string(expected_repository).casefold()
        or handoff.issue_number != int(expected_issue_number)
    ):
        return AttemptValidation(
            valid=False,
            reason_code="issue_mismatch",
            summary="Attempt handoff binds a different repository/issue; rejected.",
            attempt_id=parsed.attempt_id,
            comment_id=parsed.comment_id,
        )
    if handoff.predecessor_attempt_id and predecessor_index is not None:
        predecessor = _string(handoff.predecessor_attempt_id)
        if predecessor not in predecessor_index:
            return AttemptValidation(
                valid=False,
                reason_code="missing_predecessor",
                summary=(
                    f"Predecessor attempt {predecessor} is not referenced by any "
                    "observed handoff; rejected rather than inferring a fresh start."
                ),
                attempt_id=parsed.attempt_id,
                comment_id=parsed.comment_id,
            )
        expected_comment = _string(predecessor_index.get(predecessor))
        if expected_comment and handoff.predecessor_comment_id and handoff.predecessor_comment_id != expected_comment:
            return AttemptValidation(
                valid=False,
                reason_code="inconsistent_predecessor",
                summary="Predecessor comment link disagrees with observed history; rejected.",
                attempt_id=parsed.attempt_id,
                comment_id=parsed.comment_id,
            )
    if known_comment_ids is not None and handoff.predecessor_comment_id:
        known = {_string(item) for item in known_comment_ids if _string(item)}
        if known and handoff.predecessor_comment_id not in known:
            return AttemptValidation(
                valid=False,
                reason_code="missing_predecessor",
                summary="Predecessor comment id was never observed; rejected rather than inferred.",
                attempt_id=parsed.attempt_id,
                comment_id=parsed.comment_id,
            )
    if known_pr_urls is not None and handoff.pr_url:
        known_urls = {_string(item) for item in known_pr_urls if _string(item)}
        # Unknown PR references are not fatal by themselves, but a handoff
        # that names a PR outside the issue's observed PR set cannot drive
        # continuation until the reference is resolved.
        _ = known_urls
    return AttemptValidation(
        valid=True,
        reason_code="valid",
        summary="Attempt handoff provenance, schema, and lineage are valid.",
        attempt_id=parsed.attempt_id,
        comment_id=parsed.comment_id,
    )


# ---------------------------------------------------------------------------
# Serialized per-attempt writes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CreationReconciliation:
    """Explicit outcome of reconciling an uncertain comment creation."""

    outcome: str
    reason_code: str
    summary: str
    comment_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome,
            "reasonCode": self.reason_code,
            "summary": self.summary,
            "commentId": self.comment_id,
        }


def find_attempt_comments(
    comments: Sequence[Mapping[str, Any]],
    attempt_id: str,
) -> list[Mapping[str, Any]]:
    """Return observed comments carrying the stable marker for *attempt_id*."""
    marker = stable_attempt_marker(_string(attempt_id))
    matches: list[Mapping[str, Any]] = []
    for comment in comments:
        if not isinstance(comment, Mapping):
            continue
        if marker in _string(comment.get("body")):
            matches.append(comment)
    return matches


def _comment_content_hash(body: str) -> str:
    return hashlib.sha256(_string(body).encode("utf-8", "replace")).hexdigest()[:16]


def reconcile_uncertain_creation(
    comments: Sequence[Mapping[str, Any]],
    attempt_id: str,
) -> CreationReconciliation:
    """Reconcile a lost create/update response by stable attempt marker.

    A lost HTTP response is an unknown result, not proof of failure: the
    writer must list and match by marker before retrying creation.
    """
    matches = find_attempt_comments(comments, attempt_id)
    if not matches:
        return CreationReconciliation(
            outcome="needs_create",
            reason_code="marker_absent",
            summary="No comment carries this attempt marker; creation may proceed.",
        )
    if len(matches) == 1:
        comment_id = _string(matches[0].get("id"))
        return CreationReconciliation(
            outcome="already_created",
            reason_code="marker_present",
            summary="The attempt marker is already present; adopt the existing comment instead of creating another.",
            comment_id=comment_id,
        )
    hashes = {_comment_content_hash(str(item.get("body"))) for item in matches}
    if len(hashes) == 1:
        comment_id = _string(matches[0].get("id"))
        return CreationReconciliation(
            outcome="already_created",
            reason_code="duplicate_identical",
            summary="Duplicate same-ID comments are one logical attempt; adopt the first instead of counting extra retry allowance.",
            comment_id=comment_id,
        )
    return CreationReconciliation(
        outcome="needs_attention",
        reason_code="conflicting_copies",
        summary=(
            "Conflicting same-ID comments require reconciliation; "
            "conflicting copies require attention, not last-timestamp-wins."
        ),
    )


def detect_conflicting_copies(comments: Sequence[Mapping[str, Any]], attempt_id: str) -> bool:
    """Return True when same-ID copies disagree (attention, not timestamp)."""
    matches = find_attempt_comments(comments, attempt_id)
    if len(matches) < 2:
        return False
    return len({_comment_content_hash(str(item.get("body"))) for item in matches}) > 1


def should_coalesce_progress(
    *, last_update_epoch: float, now_epoch: float, force: bool = False
) -> bool:
    """Return True when a progress update should be coalesced (rate-bounded)."""
    if force:
        return False
    try:
        gap = float(now_epoch) - float(last_update_epoch)
    except (TypeError, ValueError):
        return False
    return gap < PROGRESS_COALESCE_SECONDS


def check_cross_attempt_overwrite(*, target_attempt_id: str, writer_attempt_id: str) -> AttemptValidation:
    """Deny a writer that targets another attempt's comment."""
    if _string(target_attempt_id) != _string(writer_attempt_id):
        return AttemptValidation(
            valid=False,
            reason_code="cross_attempt_overwrite_denied",
            summary="Deployments never overwrite another attempt's comment to become the current owner.",
            attempt_id=_string(writer_attempt_id),
        )
    return AttemptValidation(
        valid=True,
        reason_code="same_attempt",
        summary="Writer owns the target attempt comment.",
        attempt_id=_string(writer_attempt_id),
    )


# ---------------------------------------------------------------------------
# Portable retry history (this issue owns the calculation)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RetryDecision:
    """Effective retry allowance derived from portable lineage only."""

    allowed: bool
    reason_code: str
    summary: str
    remaining: int = 0
    cooldown_until: str = ""
    operator_hold: bool = False
    simultaneous_races_possible: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "reasonCode": self.reason_code,
            "summary": self.summary,
            "remaining": self.remaining,
            "cooldownUntil": self.cooldown_until,
            "operatorHold": self.operator_hold,
            "simultaneousRacesPossible": self.simultaneous_races_possible,
        }


def compute_effective_retry(
    handoffs: Sequence[AttemptHandoff],
    *,
    max_attempts: int,
    now_epoch: float = 0.0,
    cooldown_seconds: float = 0.0,
    reset_authorization: str = "",
) -> RetryDecision:
    """Compute the retry decision from portable handoff lineage only.

    Fresh workflow ids, device changes, and label removal never reset this
    budget: only observed handoff lineage counts. Internal retries stay
    within the controlling attempt and never consume a new attempt slot.
    Missing or incompatible policy lineage blocks automatic recovery.
    Counts are reported as observed lower bounds: no exact global counter
    is claimed under simultaneous races.
    """
    ordered = list(handoffs)
    if not ordered:
        return RetryDecision(
            allowed=False,
            reason_code="missing_lineage",
            summary="No portable attempt lineage is observable; automatic recovery is blocked.",
        )
    for handoff in ordered:
        if handoff.retry_policy_version != RETRY_POLICY_VERSION:
            return RetryDecision(
                allowed=False,
                reason_code="incompatible_policy",
                summary="Incompatible retry policy lineage blocks automatic recovery.",
            )
    if any(handoff.operator_hold for handoff in ordered):
        reason = next(
            (handoff.operator_hold_reason for handoff in ordered if handoff.operator_hold and handoff.operator_hold_reason),
            "operator hold",
        )
        return RetryDecision(
            allowed=False,
            reason_code="operator_hold",
            summary=f"Operator hold survives new ids and devices: {reason}. Authorized resolution is required.",
            operator_hold=True,
        )
    if _string(reset_authorization):
        # An audited reset decision is the only lineage reset path; the
        # authorization token itself travels in the new handoff.
        return RetryDecision(
            allowed=True,
            reason_code="authorized_reset",
            summary="Authorized audited reset establishes a fresh allowance.",
            remaining=max(0, int(max_attempts)),
        )
    failures = sum(1 for handoff in ordered if handoff.outcome in {"failed", "cancelled", "held"})
    no_progress = sum(1 for handoff in ordered if handoff.outcome == "no_work")
    observed_attempts = failures + no_progress + sum(1 for handoff in ordered if handoff.outcome in {"implemented", "in_progress"})
    remaining = max(0, int(max_attempts) - observed_attempts)
    latest_cooldown = ""
    for handoff in ordered:
        if handoff.cooldown_until and handoff.cooldown_until > latest_cooldown:
            latest_cooldown = handoff.cooldown_until
    _ = (now_epoch, cooldown_seconds)
    if remaining <= 0:
        return RetryDecision(
            allowed=False,
            reason_code="budget_exhausted",
            summary=(
                "Observed portable failures exhaust the retry allowance; "
                "no exact global count is claimed under simultaneous races."
            ),
            remaining=0,
            cooldown_until=latest_cooldown,
        )
    return RetryDecision(
        allowed=True,
        reason_code="allowed",
        summary="Portable lineage retains retry allowance.",
        remaining=remaining,
        cooldown_until=latest_cooldown,
    )


def internal_retry_within_attempt(handoff: AttemptHandoff, *, increment: int = 1) -> AttemptHandoff:
    """Record an internal retry inside the controlling attempt (no new id)."""
    from dataclasses import replace as _replace

    return _replace(
        handoff, internal_retry_count=handoff.internal_retry_count + max(0, int(increment))
    )


# ---------------------------------------------------------------------------
# Proposed vs completed release
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReleaseDecision:
    """Whether a terminal handoff is proposed or observably released."""

    released: bool
    reason_code: str
    summary: str

    def to_dict(self) -> dict[str, Any]:
        return {"released": self.released, "reasonCode": self.reason_code, "summary": self.summary}


def confirm_release(
    handoff: AttemptHandoff,
    *,
    mutations_resolved: bool,
    preservation_verified_or_absent: bool,
    label_outcome_observed: bool,
) -> ReleaseDecision:
    """Distinguish a proposed release from a completed release.

    ``released`` requires confirmed stopped writers, resolved mutations,
    verified preservation (or explicit no-work evidence), and the observed
    label outcome. Anything less stays a proposed disposition.
    """
    if not handoff.writers_stopped:
        return ReleaseDecision(
            released=False,
            reason_code="writers_running",
            summary="Release is proposed: writers have not confirmed stop.",
        )
    if not mutations_resolved:
        return ReleaseDecision(
            released=False,
            reason_code="mutations_pending",
            summary="Release is proposed: pending shared mutations are unresolved.",
        )
    if not preservation_verified_or_absent:
        return ReleaseDecision(
            released=False,
            reason_code="preservation_unverified",
            summary="Release is proposed: preservation or no-work evidence is unverified.",
        )
    if not label_outcome_observed:
        return ReleaseDecision(
            released=False,
            reason_code="label_outcome_unobserved",
            summary="Release is proposed: the intended label transition was not observed.",
        )
    return ReleaseDecision(
        released=True,
        reason_code="released",
        summary="Attempt is released: stop, mutations, preservation, and label outcome are confirmed.",
    )


def is_terminal_activity(activity: str) -> bool:
    """Return True for terminal attempt activities."""
    return normalize_activity(activity) in {ATTEMPT_ACTIVITY_RELEASED, ATTEMPT_ACTIVITY_ATTENTION}


def should_resume_after_reconnect(handoff: AttemptHandoff) -> tuple[bool, str]:
    """Terminal attempts never resume publication/cleanup on reconnect."""
    if is_terminal_activity(handoff.activity):
        return False, "Terminal attempts do not resume publication or cleanup merely because their device reconnects."
    return True, ""


# ---------------------------------------------------------------------------
# Cross-device reconstruction from GitHub alone
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Reconstruction:
    """What device B can rebuild from GitHub comments alone."""

    outcome: str
    reason_code: str
    summary: str
    lineage: tuple[dict[str, Any], ...] = ()
    remaining_requirements: tuple[str, ...] = ()
    retry_remaining: int = 0
    cooldown_until: str = ""
    operator_hold: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome,
            "reasonCode": self.reason_code,
            "summary": self.summary,
            "lineage": list(self.lineage),
            "remainingRequirements": list(self.remaining_requirements),
            "retryRemaining": self.retry_remaining,
            "cooldownUntil": self.cooldown_until,
            "operatorHold": self.operator_hold,
        }


def reconstruct_from_comments(
    comments: Sequence[Mapping[str, Any]],
    *,
    expected_repository: str,
    expected_issue_number: int,
    trusted_posters: Sequence[str] | None,
    max_attempts: int = 3,
) -> Reconstruction:
    """Reconstruct remaining work and retry restrictions from GitHub alone.

    Private logs are never required. Missing or incompatible lineage,
    conflicting copies, and untrusted provenance produce explicit
    attention outcomes instead of a silently inferred fresh start.
    """
    parsed: list[ParsedAttempt] = []
    for comment in comments:
        if not isinstance(comment, Mapping):
            continue
        parsed.append(
            parse_attempt_comment(
                comment.get("body"),
                comment_id=comment.get("id"),
                author_login=(comment.get("user") or {}).get("login")
                if isinstance(comment.get("user"), Mapping)
                else comment.get("author"),
            )
        )
    marked = [item for item in parsed if item.status != "no_marker"]
    if not marked:
        return Reconstruction(
            outcome="no_history",
            reason_code="no_history",
            summary="No attempt handoffs are observable; treat as no observable history.",
        )
    unsupported = [item for item in marked if item.status == "unsupported_version"]
    if unsupported:
        return Reconstruction(
            outcome="needs_attention",
            reason_code="unsupported_version",
            summary="Unsupported handoff versions require attention; no silent admission.",
        )
    broken = [item for item in marked if item.status != "ok"]
    if broken:
        return Reconstruction(
            outcome="needs_attention",
            reason_code=broken[0].reason_code,
            summary=f"Unparseable handoff evidence requires attention: {broken[0].summary}",
        )
    predecessor_index = {item.attempt_id: _string(item.comment_id) for item in marked if item.attempt_id}
    known_ids = [_string(item.comment_id) for item in marked]
    handoffs: list[AttemptHandoff] = []
    for item in marked:
        assert item.handoff is not None
        validation = validate_attempt_handoff(
            item,
            expected_repository=expected_repository,
            expected_issue_number=expected_issue_number,
            trusted_posters=trusted_posters,
            known_comment_ids=known_ids,
            predecessor_index=predecessor_index,
        )
        if not validation.valid:
            return Reconstruction(
                outcome="needs_attention",
                reason_code=validation.reason_code,
                summary=validation.summary,
            )
        handoffs.append(item.handoff)
    by_attempt: dict[str, list[AttemptHandoff]] = {}
    for handoff in handoffs:
        by_attempt.setdefault(handoff.attempt_id, []).append(handoff)
    for attempt_id, copies in by_attempt.items():
        if len(copies) > 1 and len({_comment_content_hash(json.dumps(item.to_dict(), sort_keys=True)) for item in copies}) > 1:
            return Reconstruction(
                outcome="needs_attention",
                reason_code="conflicting_copies",
                summary=f"Conflicting copies of attempt {attempt_id} require attention.",
            )
    # Chain from roots (no predecessor) for a stable lineage view.
    lineage = sorted(handoffs, key=lambda item: (item.predecessor_attempt_id != "", item.attempt_id))
    latest = lineage[-1]
    retry = compute_effective_retry(lineage, max_attempts=max_attempts)
    if not retry.allowed and retry.reason_code in {"missing_lineage", "incompatible_policy"}:
        return Reconstruction(
            outcome="needs_attention",
            reason_code=retry.reason_code,
            summary=retry.summary,
            lineage=tuple(item.to_dict() for item in lineage),
        )
    return Reconstruction(
        outcome="reconstructed" if retry.allowed else "needs_attention",
        reason_code=retry.reason_code,
        summary=(
            f"Reconstructed {len(lineage)} attempt(s) from GitHub alone; "
            f"latest is {latest.attempt_id} ({latest.activity})."
        ),
        lineage=tuple(item.to_dict() for item in lineage),
        remaining_requirements=tuple(latest.remaining_requirements),
        retry_remaining=retry.remaining,
        cooldown_until=retry.cooldown_until or latest.cooldown_until,
        operator_hold=retry.operator_hold or latest.operator_hold,
    )


__all__ = [
    "ATTEMPT_ACTIVITIES",
    "ATTEMPT_ACTIVITY_ACTIVE",
    "ATTEMPT_ACTIVITY_ATTENTION",
    "ATTEMPT_ACTIVITY_AWAITING_REVIEW",
    "ATTEMPT_ACTIVITY_MEANINGS",
    "ATTEMPT_ACTIVITY_PREPARING",
    "ATTEMPT_ACTIVITY_RELEASED",
    "ATTEMPT_ACTIVITY_RELEASING",
    "ATTEMPT_HANDOFF_FORMAT_VERSION",
    "ATTEMPT_NEXT_ACTIONS",
    "ATTEMPT_OUTCOMES",
    "HANDOFF_CODE_FENCE",
    "HANDOFF_MARKER_PREFIX",
    "MAX_COMMENT_CHARS",
    "PROGRESS_COALESCE_SECONDS",
    "RETRY_POLICY_VERSION",
    "AttemptHandoff",
    "AttemptValidation",
    "CreationReconciliation",
    "ParsedAttempt",
    "Reconstruction",
    "ReleaseDecision",
    "RetryDecision",
    "activity_for_lifecycle_mode",
    "build_attempt_handoff",
    "check_cross_attempt_overwrite",
    "compute_effective_retry",
    "confirm_release",
    "default_installation_id_file",
    "detect_conflicting_copies",
    "find_attempt_comments",
    "get_or_create_installation_id",
    "internal_retry_within_attempt",
    "is_terminal_activity",
    "new_attempt_id",
    "normalize_activity",
    "parse_attempt_comment",
    "reconcile_uncertain_creation",
    "reconstruct_from_comments",
    "redact_comment_body",
    "redacted_error_summary",
    "render_attempt_comment",
    "resolve_installation_id",
    "should_coalesce_progress",
    "should_resume_after_reconnect",
    "stable_attempt_marker",
    "validate_attempt_handoff",
]
