"""Portable per-attempt GitHub handoffs and cross-deployment retry history.

Single policy entrypoint for GitHub issue attempt identity, the versioned
bounded attempt-comment representation, provenance validation, serialized
per-attempt writes, portable retry/cooldown/hold calculation, proposed-vs-
completed release distinction, and outbound redaction (design:
docs/Workflows/GitHubIssueStatusStateMachineDesign.md, sections 4 and 5.3;
implements MoonLadderStudios/MoonMind#4177).

Deterministic and side-effect-free: no network I/O in this module. Trusted
Activities/services perform reads/writes via
:func:`publish_attempt_handoff`; this module decides what the reads mean,
what may be written, and what the retry/release outcome is.

Contract summary:
  - Every issue-work attempt has one globally unique attempt ID bound to the
    exact repository/issue plus local workflow/run, and one stable
    installation (deployment) ID that differs across deployments even when
    they share a GitHub account. Shared usernames are not identities.
  - Each attempt owns a single identifiable issue comment carrying a readable
    summary plus machine-readable metadata. Attempts update only their own
    comment; duplicate same-ID comments are one logical attempt.
  - A copied machine marker is not authentication: callers supply trusted
    poster provenance observed from the authenticated GitHub API.
  - GitHub is the shared handoff surface. Local workflow links are optional
    diagnostics, never the sole recoverability evidence.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import uuid
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

# ---------------------------------------------------------------------------
# Identity (work item 1)
# ---------------------------------------------------------------------------

#: Environment variables carrying the stable installation identity, in
#: precedence order. One canonical source only: no competing internal aliases
#: are introduced by this module.
INSTALLATION_ID_ENV_VARS = ("MOONMIND_INSTALLATION_ID", "MOONMIND_DEPLOYMENT_ID")

_ATTEMPT_ID_RE = re.compile(r"^att-[0-9a-f]{12}-[0-9a-f]{8}$")
_INSTALLATION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,127}$")
_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


@dataclass(frozen=True)
class InstallationIdentity:
    """Stable deployment identity for attempt comments."""

    installation_id: str
    source: str  # "explicit" | env var name

    def to_dict(self) -> dict[str, Any]:
        return {"installationId": self.installation_id, "source": self.source}


def resolve_installation_id(
    explicit: str | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> InstallationIdentity:
    """Resolve the stable installation ID from one canonical source.

    Raises ``ValueError`` when no stable identity is configured so a
    deployment cannot silently share or invent per-run identities. Callers
    persist the configured value across restart/retry (it is read, never
    regenerated per run).
    """
    if explicit is not None and str(explicit).strip():
        candidate = str(explicit).strip()
        if not _INSTALLATION_ID_RE.fullmatch(candidate):
            raise ValueError("Invalid installation ID: must match [A-Za-z0-9._:-]{3,128}.")
        return InstallationIdentity(installation_id=candidate, source="explicit")
    env = environ if environ is not None else os.environ
    for name in INSTALLATION_ID_ENV_VARS:
        value = str(env.get(name) or "").strip()
        if value:
            if not _INSTALLATION_ID_RE.fullmatch(value):
                raise ValueError(f"Invalid {name}: must match [A-Za-z0-9._:-]{{3,128}}.")
            return InstallationIdentity(installation_id=value, source=name)
    raise ValueError(
        "No stable installation identity is configured; set MOONMIND_INSTALLATION_ID "
        "(documented alias MOONMIND_DEPLOYMENT_ID) to a value that differs across "
        "deployments and persists through restart/retry."
    )


def new_attempt_id(
    *,
    repository: str,
    issue_number: int,
    installation_id: str,
    workflow_id: str,
    run_id: str,
    nonce: str | None = None,
) -> str:
    """Build a globally unique attempt ID bound to repo/issue + workflow/run.

    Format ``att-<12 hex>-<8 hex>``: the first group binds the exact
    repository/issue, installation, workflow, and run; the second group is a
    fresh nonce so two announcements for the same binding never collide.
    """
    repo = str(repository).strip()
    if not _REPO_RE.fullmatch(repo):
        raise ValueError("Invalid repository: expected owner/name.")
    if type(issue_number) is not int or issue_number <= 0:
        raise ValueError("Invalid issue number.")
    for label, value in (
        ("installation_id", installation_id),
        ("workflow_id", workflow_id),
        ("run_id", run_id),
    ):
        if not str(value or "").strip():
            raise ValueError(f"Missing {label} for attempt binding.")
    raw_nonce = str(nonce or "").strip().lower() or uuid.uuid4().hex[:8]
    if not re.fullmatch(r"[0-9a-f]{8}", raw_nonce):
        raw_nonce = hashlib.sha256(raw_nonce.encode()).hexdigest()[:8]
    binding = "|".join(
        [repo.lower(), str(issue_number), str(installation_id).strip(), str(workflow_id).strip(), str(run_id).strip()]
    )
    digest = hashlib.sha256(binding.encode()).hexdigest()[:12]
    return f"att-{digest}-{raw_nonce}"


def is_attempt_id(value: Any) -> bool:
    """Return True when *value* is a well-formed attempt ID."""
    return isinstance(value, str) and _ATTEMPT_ID_RE.fullmatch(value.strip()) is not None


# ---------------------------------------------------------------------------
# Versioned bounded comment representation (work item 2)
# ---------------------------------------------------------------------------

#: Current machine-readable comment schema version. Parsers accept only
#: versions in :data:`SUPPORTED_COMMENT_VERSIONS` and fail closed otherwise.
COMMENT_SCHEMA_VERSION = 1
SUPPORTED_COMMENT_VERSIONS = frozenset({1})

#: Outbound bounds: comments stay small, human-readable, and free of private
#: payloads. Per-field limits apply before the total cap.
MAX_COMMENT_CHARS = 8000
MAX_FIELD_CHARS = 1000
MAX_LIST_ITEMS = 20
MAX_REMAINING_REQUIREMENTS = 20

#: Marker identifying one logical attempt. The marker alone never
#: authenticates a comment (see :func:`validate_attempt_comment`).
MARKER_RE = re.compile(r"<!--\s*moonmind-attempt:\s*id=(\S+)\s+v=(\d+)\s*-->")
FENCE_OPEN = "```moonmind-attempt-json"
FENCE_CLOSE = "```"

#: Attempt activity vocabulary (design section 4.1). No equivalent label
#: families are introduced: activity is comment metadata only.
ACTIVITY_PREPARING = "preparing"
ACTIVITY_ACTIVE = "active"
ACTIVITY_AWAITING_REVIEW = "awaiting-review"
ACTIVITY_RELEASING = "releasing"
ACTIVITY_RELEASED = "released"
ACTIVITY_ATTENTION = "attention"
KNOWN_ACTIVITIES = frozenset(
    {
        ACTIVITY_PREPARING,
        ACTIVITY_ACTIVE,
        ACTIVITY_AWAITING_REVIEW,
        ACTIVITY_RELEASING,
        ACTIVITY_RELEASED,
        ACTIVITY_ATTENTION,
    }
)

#: Next-action vocabulary for terminal handoffs.
NEXT_FRESH_RETRY = "fresh-retry"
NEXT_CONTINUE_IMPLEMENTATION = "continue-implementation"
NEXT_VERIFY = "verify"
NEXT_CONTINUE_REVIEW = "continue-review"
NEXT_FINALIZE_STATUS = "finalize-status"
NEXT_OPERATOR_ATTENTION = "operator-attention"
KNOWN_NEXT_ACTIONS = frozenset(
    {
        NEXT_FRESH_RETRY,
        NEXT_CONTINUE_IMPLEMENTATION,
        NEXT_VERIFY,
        NEXT_CONTINUE_REVIEW,
        NEXT_FINALIZE_STATUS,
        NEXT_OPERATOR_ATTENTION,
    }
)


#: Metadata budget inside the total comment cap: the machine block must
#: always fit, so free-text fields shrink before the human summary does.
MAX_METADATA_CHARS = 6000


def _truncate_text(value: Any, limit: int = MAX_FIELD_CHARS) -> str:
    text = "" if value is None else str(value)
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _fit_metadata(data: dict[str, Any]) -> dict[str, Any]:
    """Shrink free-text metadata deterministically until it fits its budget."""
    data = dict(data)
    remaining = list(data.get("remainingRequirements") or [])
    last_report = str(data.get("lastReport") or "")
    verification = str(data.get("verificationSummary") or "")
    hold_reason = str(data.get("holdReason") or "")
    while len(json.dumps(data, sort_keys=True, separators=(",", ":"))) > MAX_METADATA_CHARS:
        if len(remaining) > 5:
            remaining = remaining[: max(5, len(remaining) // 2)]
            data["remainingRequirements"] = remaining
        elif len(last_report) > 200:
            last_report = last_report[:199] + "…"
            data["lastReport"] = last_report
        elif len(verification) > 200:
            verification = verification[:199] + "…"
            data["verificationSummary"] = verification
        elif len(hold_reason) > 200:
            hold_reason = hold_reason[:199] + "…"
            data["holdReason"] = hold_reason
        elif remaining:
            remaining = remaining[:-1]
            data["remainingRequirements"] = remaining
        else:
            break
    return data


def _truncate_list(values: Any, limit: int = MAX_LIST_ITEMS) -> list[str]:
    if not isinstance(values, (list, tuple)):
        return []
    items = [_truncate_text(item) for item in values if str(item or "").strip()]
    return items[:limit]


@dataclass(frozen=True)
class AttemptHandoff:
    """Portable per-attempt handoff (design section 4.1)."""

    schema_version: int = COMMENT_SCHEMA_VERSION
    attempt_id: str = ""
    deployment_id: str = ""
    repository: str = ""
    issue_number: int = 0
    workflow_id: str = ""
    run_id: str = ""
    # Lineage.
    predecessor_attempt_id: str = ""
    predecessor_comment_id: int = 0
    # Activity and reporting.
    activity: str = ACTIVITY_ACTIVE
    last_report: str = ""
    # Stop/publication evidence.
    writers_stopped: bool = False
    publication_disposition: str = ""
    # Preserved work: exact PR/head/base or saved branch/SHA.
    pr_url: str = ""
    pr_head_sha: str = ""
    pr_base: str = ""
    saved_branch: str = ""
    saved_sha: str = ""
    # Result and routing.
    outcome: str = ""
    remaining_requirements: tuple[str, ...] = ()
    verification_summary: str = ""
    next_action: str = NEXT_OPERATOR_ATTENTION
    # Retry eligibility (portable history; see work item 5).
    failed_attempts: int = 0
    no_progress_count: int = 0
    internal_retry_count: int = 0
    remaining_allowance: int = 0
    cooldown_until: str = ""
    operator_hold: bool = False
    hold_reason: str = ""
    reset_generation: int = 0
    reset_reason: str = ""
    reset_author: str = ""
    policy_id: str = ""
    # Release state (see work item 6).
    proposed_disposition: str = ""
    released: bool = False
    mutations_settled: bool = False
    preservation_verified: bool = False
    label_outcome_observed: str = ""
    # Optional diagnostics only; never required for recovery.
    workflow_link: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": self.schema_version,
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
            "publicationDisposition": self.publication_disposition,
            "prUrl": self.pr_url,
            "prHeadSha": self.pr_head_sha,
            "prBase": self.pr_base,
            "savedBranch": self.saved_branch,
            "savedSha": self.saved_sha,
            "outcome": self.outcome,
            "remainingRequirements": list(self.remaining_requirements),
            "verificationSummary": self.verification_summary,
            "nextAction": self.next_action,
            "failedAttempts": self.failed_attempts,
            "noProgressCount": self.no_progress_count,
            "internalRetryCount": self.internal_retry_count,
            "remainingAllowance": self.remaining_allowance,
            "cooldownUntil": self.cooldown_until,
            "operatorHold": self.operator_hold,
            "holdReason": self.hold_reason,
            "resetGeneration": self.reset_generation,
            "resetReason": self.reset_reason,
            "resetAuthor": self.reset_author,
            "policyId": self.policy_id,
            "proposedDisposition": self.proposed_disposition,
            "released": self.released,
            "mutationsSettled": self.mutations_settled,
            "preservationVerified": self.preservation_verified,
            "labelOutcomeObserved": self.label_outcome_observed,
            "workflowLink": self.workflow_link,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "AttemptHandoff":
        """Build a handoff from parsed metadata, applying bounds."""
        if not isinstance(payload, Mapping):
            raise ValueError("Attempt metadata must be a JSON object.")
        try:
            version = int(payload.get("schemaVersion", 0))
        except (TypeError, ValueError) as exc:
            raise ValueError("Attempt metadata has no supported schemaVersion.") from exc
        if version not in SUPPORTED_COMMENT_VERSIONS:
            raise ValueError(f"Unsupported attempt schema version: {version}.")
        attempt_id = str(payload.get("attemptId") or "").strip()
        if not is_attempt_id(attempt_id):
            raise ValueError("Attempt metadata has no valid attemptId.")
        repository = str(payload.get("repository") or "").strip()
        if not _REPO_RE.fullmatch(repository):
            raise ValueError("Attempt metadata has no valid repository.")
        try:
            issue_number = int(payload.get("issueNumber", 0))
        except (TypeError, ValueError) as exc:
            raise ValueError("Attempt metadata has no valid issueNumber.") from exc
        if issue_number <= 0:
            raise ValueError("Attempt metadata has no valid issueNumber.")
        activity = str(payload.get("activity") or "").strip() or ACTIVITY_ACTIVE
        if activity not in KNOWN_ACTIVITIES:
            raise ValueError(f"Unknown attempt activity: {activity}.")
        next_action = str(payload.get("nextAction") or "").strip() or NEXT_OPERATOR_ATTENTION
        if next_action not in KNOWN_NEXT_ACTIONS:
            raise ValueError(f"Unknown next action: {next_action}.")
        predecessor_attempt = str(payload.get("predecessorAttemptId") or "").strip()
        if predecessor_attempt and not is_attempt_id(predecessor_attempt):
            raise ValueError("Invalid predecessorAttemptId.")
        try:
            predecessor_comment = int(payload.get("predecessorCommentId") or 0)
        except (TypeError, ValueError):
            predecessor_comment = 0

        def _nonneg(name: str) -> int:
            try:
                value = int(payload.get(name, 0) or 0)
            except (TypeError, ValueError):
                value = 0
            return max(0, value)

        return cls(
            schema_version=version,
            attempt_id=attempt_id,
            deployment_id=_truncate_text(payload.get("deploymentId"), 128),
            repository=repository,
            issue_number=issue_number,
            workflow_id=_truncate_text(payload.get("workflowId"), 200),
            run_id=_truncate_text(payload.get("runId"), 200),
            predecessor_attempt_id=predecessor_attempt,
            predecessor_comment_id=max(0, predecessor_comment),
            activity=activity,
            last_report=_truncate_text(payload.get("lastReport")),
            writers_stopped=bool(payload.get("writersStopped", False)),
            publication_disposition=_truncate_text(payload.get("publicationDisposition"), 200),
            pr_url=_truncate_text(payload.get("prUrl"), 300),
            pr_head_sha=_truncate_text(payload.get("prHeadSha"), 100),
            pr_base=_truncate_text(payload.get("prBase"), 200),
            saved_branch=_truncate_text(payload.get("savedBranch"), 200),
            saved_sha=_truncate_text(payload.get("savedSha"), 100),
            outcome=_truncate_text(payload.get("outcome"), 200),
            remaining_requirements=tuple(
                _truncate_list(payload.get("remainingRequirements"), MAX_REMAINING_REQUIREMENTS)
            ),
            verification_summary=_truncate_text(payload.get("verificationSummary")),
            next_action=next_action,
            failed_attempts=_nonneg("failedAttempts"),
            no_progress_count=_nonneg("noProgressCount"),
            internal_retry_count=_nonneg("internalRetryCount"),
            remaining_allowance=_nonneg("remainingAllowance"),
            cooldown_until=_truncate_text(payload.get("cooldownUntil"), 100),
            operator_hold=bool(payload.get("operatorHold", False)),
            hold_reason=_truncate_text(payload.get("holdReason")),
            reset_generation=_nonneg("resetGeneration"),
            reset_reason=_truncate_text(payload.get("resetReason")),
            reset_author=_truncate_text(payload.get("resetAuthor"), 200),
            policy_id=_truncate_text(payload.get("policyId"), 200),
            proposed_disposition=_truncate_text(payload.get("proposedDisposition"), 200),
            released=bool(payload.get("released", False)),
            mutations_settled=bool(payload.get("mutationsSettled", False)),
            preservation_verified=bool(payload.get("preservationVerified", False)),
            label_outcome_observed=_truncate_text(payload.get("labelOutcomeObserved"), 200),
            workflow_link=_truncate_text(payload.get("workflowLink"), 300),
        )


def marker_for(attempt_id: str, version: int = COMMENT_SCHEMA_VERSION) -> str:
    """Return the stable machine marker for one attempt."""
    return f"<!-- moonmind-attempt: id={attempt_id} v={version} -->"


def render_comment_body(handoff: AttemptHandoff) -> str:
    """Render the bounded human summary plus machine metadata block.

    Free text is redacted before rendering (see work item 7); the result is
    capped at :data:`MAX_COMMENT_CHARS` with metadata preserved.
    """
    from moonmind.utils.logging import redact_sensitive_text

    redacted = _redacted_handoff(handoff)
    data = _fit_metadata(redacted.to_dict())
    metadata = json.dumps(data, sort_keys=True, separators=(",", ":"))
    preserved = _preserved_work_summary(redacted)
    remaining = "; ".join(redacted.remaining_requirements[:5])
    if len(redacted.remaining_requirements) > 5:
        remaining += f"; …({len(redacted.remaining_requirements) - 5} more)"
    lines = [
        marker_for(redacted.attempt_id, redacted.schema_version),
        f"## MoonMind attempt `{redacted.attempt_id}` — {redacted.activity}",
        "",
        f"Deployment `{redacted.deployment_id or 'unknown'}` · "
        f"{redacted.repository}#{redacted.issue_number} · "
        f"workflow `{redacted.workflow_id or 'unknown'}` run `{redacted.run_id or 'unknown'}`.",
    ]
    if redacted.predecessor_attempt_id:
        lines.append(
            f"Continues attempt `{redacted.predecessor_attempt_id}`"
            + (f" (comment {redacted.predecessor_comment_id})" if redacted.predecessor_comment_id else "")
            + "."
        )
    lines.append(f"Last report: {redacted.last_report or '—'}")
    lines.append(f"Writers stopped: {'yes' if redacted.writers_stopped else 'no'} · Outcome: {redacted.outcome or '—'}")
    lines.append(f"Preserved work: {preserved}")
    lines.append(f"Remaining: {remaining or '—'}")
    lines.append(f"Verification: {redacted.verification_summary or '—'}")
    lines.append(f"Next action: {redacted.next_action}")
    retry_bits = [f"failed={redacted.failed_attempts}", f"no-progress={redacted.no_progress_count}"]
    if redacted.remaining_allowance or redacted.cooldown_until:
        retry_bits.append(f"remaining={redacted.remaining_allowance}")
    if redacted.cooldown_until:
        retry_bits.append(f"cooldown-until={redacted.cooldown_until}")
    if redacted.operator_hold:
        retry_bits.append(f"hold={redacted.hold_reason or 'operator hold'}")
    lines.append(f"Retry: {', '.join(retry_bits)}")
    if redacted.released:
        lines.append(f"Released ({redacted.proposed_disposition or 'terminal'}).")
    elif redacted.proposed_disposition:
        lines.append(f"Proposed disposition: {redacted.proposed_disposition} (not yet released).")
    lines += ["", FENCE_OPEN, metadata, FENCE_CLOSE]
    body = "\n".join(lines)
    body = redact_sensitive_text(body)
    if len(body) <= MAX_COMMENT_CHARS:
        return body
    # Preserve the machine block: shrink the human summary to fit.
    fence = f"\n{FENCE_OPEN}\n{metadata}\n{FENCE_CLOSE}"
    head_budget = MAX_COMMENT_CHARS - len(fence) - len(marker_for(redacted.attempt_id)) - 2
    head_lines = [marker_for(redacted.attempt_id, redacted.schema_version)]
    head_lines.append(f"## MoonMind attempt `{redacted.attempt_id}` — {redacted.activity}")
    head_lines.append(f"{redacted.repository}#{redacted.issue_number} · next: {redacted.next_action}")
    head = redact_sensitive_text("\n".join(head_lines))[: max(0, head_budget)]
    return f"{head}{fence}"


def _preserved_work_summary(handoff: AttemptHandoff) -> str:
    if handoff.pr_url:
        bits = handoff.pr_url
        if handoff.pr_head_sha:
            bits += f" @ {handoff.pr_head_sha[:12]}"
        if handoff.pr_base:
            bits += f" (base {handoff.pr_base})"
        return bits
    if handoff.saved_branch:
        bits = handoff.saved_branch
        if handoff.saved_sha:
            bits += f" @ {handoff.saved_sha[:12]}"
        return bits
    return "none"


def parse_comment_body(body: Any) -> AttemptHandoff:
    """Parse one issue comment into an :class:`AttemptHandoff`.

    Raises ``ValueError`` with an explicit reason for unsupported formats
    instead of inferring a fresh start.
    """
    if not isinstance(body, str) or not body.strip():
        raise ValueError("Comment has no parseable attempt marker.")
    match = MARKER_RE.search(body)
    if not match:
        raise ValueError("Comment carries no MoonMind attempt marker.")
    marker_attempt, marker_version = match.group(1), int(match.group(2))
    if marker_version not in SUPPORTED_COMMENT_VERSIONS:
        raise ValueError(f"Unsupported attempt schema version: {marker_version}.")
    if FENCE_OPEN not in body or FENCE_CLOSE not in body:
        raise ValueError("Comment marker has no machine metadata block.")
    try:
        encoded = body.split(FENCE_OPEN, 1)[1].rsplit(FENCE_CLOSE, 1)[0].strip()
        payload = json.loads(encoded)
    except (ValueError, IndexError) as exc:
        raise ValueError("Comment metadata block is not valid JSON.") from exc
    handoff = AttemptHandoff.from_dict(payload)
    if handoff.attempt_id != marker_attempt:
        raise ValueError("Comment marker attempt does not match its metadata.")
    return handoff


def try_parse_comment_body(body: Any) -> tuple[AttemptHandoff | None, str]:
    """Parse leniently, returning ``(handoff, error)`` instead of raising."""
    try:
        return parse_comment_body(body), ""
    except ValueError as exc:
        return None, str(exc)


# ---------------------------------------------------------------------------
# Provenance and schema validation (work item 3)
# ---------------------------------------------------------------------------

VALIDATION_OK = "ok"
VALIDATION_SPOOFED = "untrusted_poster"
VALIDATION_VERSION = "unsupported_version"
VALIDATION_ISSUE_MISMATCH = "issue_mismatch"
VALIDATION_PREDECESSOR_MISSING = "predecessor_missing"
VALIDATION_PREDECESSOR_UNKNOWN = "predecessor_unknown"
VALIDATION_SCHEMA = "schema_invalid"
VALIDATION_REF = "invalid_reference"


@dataclass(frozen=True)
class CommentValidation:
    """Explicit validation outcome for one candidate attempt comment."""

    ok: bool
    code: str
    detail: str = ""
    handoff: AttemptHandoff | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "code": self.code,
            "detail": self.detail,
            "handoff": self.handoff.to_dict() if self.handoff is not None else None,
        }


def _is_trusted_poster(
    *,
    poster_login: str,
    poster_type: str,
    trusted_logins: Sequence[str] | None,
) -> bool:
    if not poster_login or not poster_login.strip():
        return False
    allowed = {str(login).strip().lower() for login in (trusted_logins or []) if str(login or "").strip()}
    if not allowed:
        return False
    if str(poster_login).strip().lower() not in allowed:
        return False
    return str(poster_type or "").strip().lower() in {"bot", "user", "organization"}


_GITHUB_PR_RE = re.compile(r"^https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/pull/[1-9]\d*$")
_GITHUB_BRANCH_RE = re.compile(r"^[A-Za-z0-9_.-][A-Za-z0-9_.\-/]{0,199}$")
_SHA_RE = re.compile(r"^[0-9a-f]{7,64}$")


def _check_github_references(handoff: AttemptHandoff) -> str:
    if handoff.pr_url and not _GITHUB_PR_RE.fullmatch(handoff.pr_url):
        return f"Referenced PR URL is not a valid GitHub PR: {handoff.pr_url[:80]}."
    for label, value in (("prHeadSha", handoff.pr_head_sha), ("savedSha", handoff.saved_sha)):
        if value and not _SHA_RE.fullmatch(value.strip().lower()):
            return f"Referenced {label} is not a valid commit SHA."
    for label, value in (("prBase", handoff.pr_base), ("savedBranch", handoff.saved_branch)):
        if value and not _GITHUB_BRANCH_RE.fullmatch(value.strip()):
            return f"Referenced {label} is not a valid branch name."
    return ""


def validate_attempt_comment(
    *,
    body: Any,
    repository: str,
    issue_number: int,
    poster_login: str = "",
    poster_type: str = "",
    trusted_poster_logins: Sequence[str] | None = None,
    known_attempt_ids: Sequence[str] | None = None,
) -> CommentValidation:
    """Validate one comment's provenance, schema, identity, and lineage.

    A copied machine marker is not authentication: the caller supplies the
    poster identity observed from the authenticated GitHub API
    (``user.login``/``user.type``), which must appear in the deployment's
    trusted poster set. Issue prose and arbitrary comments stay untrusted
    reference content; inconsistent or missing referenced history is
    rejected rather than treated as a fresh start. Shared-account comments
    are never claimed to separate mutually adversarial devices.
    """
    if not _is_trusted_poster(
        poster_login=poster_login, poster_type=poster_type, trusted_logins=trusted_poster_logins
    ):
        return CommentValidation(ok=False, code=VALIDATION_SPOOFED, detail="Poster is not in the trusted attempt-writer set.")
    handoff, error = try_parse_comment_body(body)
    if handoff is None:
        code = VALIDATION_VERSION if "Unsupported attempt schema version" in error else VALIDATION_SCHEMA
        return CommentValidation(ok=False, code=code, detail=error)
    if (
        handoff.repository.lower() != str(repository).strip().lower()
        or handoff.issue_number != issue_number
    ):
        return CommentValidation(
            ok=False,
            code=VALIDATION_ISSUE_MISMATCH,
            detail=f"Comment binds {handoff.repository}#{handoff.issue_number}, not {repository}#{issue_number}.",
            handoff=handoff,
        )
    if handoff.predecessor_attempt_id:
        known = {str(item).strip() for item in (known_attempt_ids or []) if str(item or "").strip()}
        if not known:
            return CommentValidation(
                ok=False, code=VALIDATION_PREDECESSOR_MISSING, handoff=handoff,
                detail="Comment references a predecessor but no predecessor history was supplied; not inferring a fresh start.",
            )
        if handoff.predecessor_attempt_id not in known:
            return CommentValidation(
                ok=False, code=VALIDATION_PREDECESSOR_UNKNOWN, handoff=handoff,
                detail=f"Predecessor {handoff.predecessor_attempt_id} is not in the observed attempt history.",
            )
    ref_error = _check_github_references(handoff)
    if ref_error:
        return CommentValidation(ok=False, code=VALIDATION_REF, detail=ref_error, handoff=handoff)
    return CommentValidation(ok=True, code=VALIDATION_OK, handoff=handoff)


# ---------------------------------------------------------------------------
# Serialized per-attempt writes (work item 4)
# ---------------------------------------------------------------------------

#: Minimum seconds between routine progress-report updates of one comment.
PROGRESS_COALESCE_SECONDS = 300

RECONCILE_CREATE = "create"
RECONCILE_REUSE = "reuse"
RECONCILE_DUPLICATE_SAME = "duplicate_same"
RECONCILE_CONFLICT = "conflict"


@dataclass(frozen=True)
class MarkerReconciliation:
    """Outcome of reconciling uncertain creation by stable attempt marker."""

    outcome: str
    comment_id: int = 0
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"outcome": self.outcome, "commentId": self.comment_id, "detail": self.detail}


def _comment_marker_parts(comment: Mapping[str, Any]) -> tuple[str, str] | None:
    try:
        handoff, _ = try_parse_comment_body(comment.get("body"))
    except Exception:  # pragma: no cover - defensive
        return None
    if handoff is None:
        body = str(comment.get("body") or "")
        match = MARKER_RE.search(body)
        if not match:
            return None
        return match.group(1), match.group(2)
    return handoff.attempt_id, str(handoff.schema_version)


def reconcile_marker_before_create(
    *,
    attempt_id: str,
    comments: Sequence[Mapping[str, Any]],
) -> MarkerReconciliation:
    """Reconcile uncertain comment creation by stable attempt marker.

    A lost create response is an unknown result, not proof of failure: retry
    paths list first and reuse the same-ID comment instead of repeating the
    effect. Duplicate same-ID comments are one logical attempt; conflicting
    copies (same marker, divergent metadata) require attention rather than
    last-timestamp-wins.
    """
    matches: list[Mapping[str, Any]] = []
    for comment in comments:
        if not isinstance(comment, Mapping):
            continue
        parts = _comment_marker_parts(comment)
        if parts is not None and parts[0] == attempt_id:
            matches.append(comment)
    if not matches:
        return MarkerReconciliation(outcome=RECONCILE_CREATE, detail="No comment carries this attempt marker.")
    bodies = [str(match.get("body") or "") for match in matches]
    if len(matches) == 1:
        try:
            comment_id = int(matches[0].get("id") or 0)
        except (TypeError, ValueError):
            comment_id = 0
        return MarkerReconciliation(outcome=RECONCILE_REUSE, comment_id=comment_id, detail="One comment already carries this attempt marker.")
    normalized = {re.sub(r"\s+", " ", body).strip() for body in bodies}
    if len(normalized) == 1:
        try:
            comment_id = int(matches[0].get("id") or 0)
        except (TypeError, ValueError):
            comment_id = 0
        return MarkerReconciliation(
            outcome=RECONCILE_DUPLICATE_SAME, comment_id=comment_id,
            detail=f"{len(matches)} identical comments share attempt {attempt_id}; they are one logical attempt.",
        )
    return MarkerReconciliation(
        outcome=RECONCILE_CONFLICT,
        detail=f"{len(matches)} conflicting comments share attempt {attempt_id}; reconciliation required.",
    )


def select_own_comment(
    *,
    attempt_id: str,
    comments: Sequence[Mapping[str, Any]],
) -> Mapping[str, Any] | None:
    """Return this attempt's own comment, else ``None``.

    Deployments never overwrite another attempt's comment to become the
    current owner: updates require an exact attempt-ID match.
    """
    for comment in comments:
        if not isinstance(comment, Mapping):
            continue
        parts = _comment_marker_parts(comment)
        if parts is not None and parts[0] == attempt_id:
            return comment
    return None


def progress_update_allowed(
    *,
    last_update_epoch: float | None,
    now_epoch: float | None = None,
    coalesce_seconds: float = PROGRESS_COALESCE_SECONDS,
) -> bool:
    """Return True when a routine progress report may be emitted.

    Progress updates are coalesced within bounded rate limits rather than
    emitted on every runtime poll. Forced terminal/proposed-release updates
    bypass this helper entirely.
    """
    if last_update_epoch is None:
        return True
    now = time.time() if now_epoch is None else float(now_epoch)
    try:
        last = float(last_update_epoch)
    except (TypeError, ValueError):
        return True
    return (now - last) >= float(coalesce_seconds)


# ---------------------------------------------------------------------------
# Portable retry history (work item 5)
# ---------------------------------------------------------------------------

RETRY_OK = "ok"
RETRY_HOLD = "operator_hold"
RETRY_COOLDOWN = "cooldown_active"
RETRY_EXHAUSTED = "allowance_exhausted"
RETRY_MISSING_LINEAGE = "missing_lineage"
RETRY_INCOMPATIBLE_POLICY = "incompatible_policy"
RETRY_RESET_AUTHORIZED = "reset_authorized"


@dataclass(frozen=True)
class RetryPolicy:
    """Bounded automatic-retry policy consumed from portable handoffs."""

    policy_id: str = "default"
    max_attempts: int = 3
    cooldown_seconds: int = 3600
    reset_generation: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "policyId": self.policy_id,
            "maxAttempts": self.max_attempts,
            "cooldownSeconds": self.cooldown_seconds,
            "resetGeneration": self.reset_generation,
        }


@dataclass(frozen=True)
class RetryDecision:
    """Effective retry allowance derived from GitHub-visible history alone."""

    allowed: bool
    code: str
    detail: str = ""
    remaining_allowance: int = 0
    cooldown_until: str = ""
    operator_hold: bool = False
    failed_attempts: int = 0
    no_progress_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "code": self.code,
            "detail": self.detail,
            "remainingAllowance": self.remaining_allowance,
            "cooldownUntil": self.cooldown_until,
            "operatorHold": self.operator_hold,
            "failedAttempts": self.failed_attempts,
            "noProgressCount": self.no_progress_count,
        }


def _parse_epoch(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        pass
    from datetime import datetime, timezone

    candidate = text
    if candidate.endswith("Z"):
        candidate = candidate[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def collect_retry_history(
    handoffs: Sequence[AttemptHandoff],
    *,
    policy: RetryPolicy | None = None,
    now_epoch: float | None = None,
) -> RetryDecision:
    """Derive the effective retry decision from linked attempt handoffs alone.

    New workflow IDs, device changes, and label clearing never reset the
    history: only an audited reset record (incremented ``resetGeneration``
    with author and reason) starts a new generation. Internal step retries
    stay within the controlling attempt via ``internalRetryCount`` and never
    create unlimited issue attempts. Missing or incompatible policy lineage
    blocks automatic recovery, and no exact global counter is claimed under
    simultaneous races — the decision reports the observed lineage only.
    """
    active = policy or RetryPolicy()
    now = time.time() if now_epoch is None else float(now_epoch)
    lineage = [item for item in handoffs if isinstance(item, AttemptHandoff)]
    if not lineage:
        remaining = max(0, int(active.max_attempts) - 1)
        return RetryDecision(
            allowed=remaining > 0, code=RETRY_OK if remaining > 0 else RETRY_EXHAUSTED,
            detail="No observable attempt history; treating as first attempt of the policy generation.",
            remaining_allowance=remaining,
        )
    policy_ids = {item.policy_id for item in lineage if item.policy_id}
    if policy_ids and active.policy_id not in policy_ids and "default" not in policy_ids:
        # A lineage pinned to an explicitly different policy generation is
        # incompatible: fail to attention rather than invent a fresh budget.
        # The shared "default" policy id is lineage-compatible with any
        # explicitly named policy of the same generation-0 history.
        first = lineage[0]
        if first.reset_generation != active.reset_generation or first.policy_id not in {"", "default"}:
            return RetryDecision(
                allowed=False, code=RETRY_INCOMPATIBLE_POLICY,
                detail=f"Observed policy lineage {sorted(policy_ids)} is incompatible with {active.policy_id}; attention required.",
                failed_attempts=len(lineage),
            )
    generations = {item.reset_generation for item in lineage}
    current_generation = max(generations)
    if current_generation > active.reset_generation:
        # An audited reset record from another deployment authorizes a new
        # generation only when it carries author and reason.
        resets = [item for item in lineage if item.reset_generation == current_generation]
        if any(item.reset_author and item.reset_reason for item in resets):
            return RetryDecision(
                allowed=True, code=RETRY_RESET_AUTHORIZED,
                detail=f"Authorized reset to generation {current_generation} by {resets[0].reset_author}.",
                remaining_allowance=max(0, int(active.max_attempts) - 1),
                failed_attempts=0, no_progress_count=0,
            )
        return RetryDecision(
            allowed=False, code=RETRY_MISSING_LINEAGE,
            detail=f"Reset to generation {current_generation} lacks audited author/reason; attention required.",
            failed_attempts=len(lineage),
        )
    if current_generation < active.reset_generation:
        return RetryDecision(
            allowed=False, code=RETRY_MISSING_LINEAGE,
            detail="Observed history predates the required reset generation; attention required.",
            failed_attempts=len(lineage),
        )
    generation_items = [item for item in lineage if item.reset_generation == current_generation]
    failed = len(generation_items)
    no_progress = sum(1 for item in generation_items if not _preserved_work_summary(item) or _preserved_work_summary(item) == "none")
    holds = [item for item in generation_items if item.operator_hold]
    if holds:
        reason = holds[-1].hold_reason or "operator hold"
        return RetryDecision(
            allowed=False, code=RETRY_HOLD, detail=f"Operator hold is active: {reason}.",
            operator_hold=True, failed_attempts=failed, no_progress_count=no_progress,
            remaining_allowance=max(0, int(active.max_attempts) - failed),
        )
    latest_cooldown = ""
    latest_epoch: float | None = None
    for item in generation_items:
        epoch = _parse_epoch(item.cooldown_until)
        if epoch is not None and (latest_epoch is None or epoch > latest_epoch):
            latest_epoch = epoch
            latest_cooldown = item.cooldown_until
    if latest_epoch is not None and latest_epoch > now:
        return RetryDecision(
            allowed=False, code=RETRY_COOLDOWN,
            detail=f"Cooldown is active until {latest_cooldown}.",
            cooldown_until=latest_cooldown, failed_attempts=failed,
            no_progress_count=no_progress,
            remaining_allowance=max(0, int(active.max_attempts) - failed),
        )
    remaining = max(0, int(active.max_attempts) - failed)
    if remaining <= 0:
        return RetryDecision(
            allowed=False, code=RETRY_EXHAUSTED,
            detail=f"Retry allowance exhausted: {failed} failed attempt(s) under policy {active.policy_id}.",
            failed_attempts=failed, no_progress_count=no_progress, remaining_allowance=0,
        )
    return RetryDecision(
        allowed=True, code=RETRY_OK,
        detail=f"{remaining} automatic retr(ies) remain under policy {active.policy_id}.",
        remaining_allowance=remaining, failed_attempts=failed, no_progress_count=no_progress,
    )


# ---------------------------------------------------------------------------
# Proposed vs completed release (work item 6)
# ---------------------------------------------------------------------------

RELEASE_PROPOSED = "proposed"
RELEASE_RELEASED = "released"
RELEASE_BLOCKED_WRITERS = "writers_running"
RELEASE_BLOCKED_MUTATIONS = "mutations_unsettled"
RELEASE_BLOCKED_PRESERVATION = "preservation_unverified"
RELEASE_BLOCKED_LABELS = "label_outcome_unobserved"


@dataclass(frozen=True)
class ReleaseEvaluation:
    """Distinction between a proposed release and a completed release."""

    released: bool
    code: str
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"released": self.released, "code": self.code, "detail": self.detail}


def evaluate_release(handoff: AttemptHandoff) -> ReleaseEvaluation:
    """Decide whether a terminal comment records a completed release.

    The terminal comment may record the intended next disposition before
    label changes (``proposedDisposition``), but ``released`` requires
    confirmed stopped writers, resolved mutations, verified preservation or
    explicit no-work evidence, and the observed label outcome.
    """
    if handoff.activity == ACTIVITY_RELEASED or handoff.released:
        if not handoff.writers_stopped:
            return ReleaseEvaluation(released=False, code=RELEASE_BLOCKED_WRITERS, detail="Writers are not confirmed stopped.")
        if not handoff.mutations_settled:
            return ReleaseEvaluation(released=False, code=RELEASE_BLOCKED_MUTATIONS, detail="Pending shared mutations are unsettled.")
        preserved = _preserved_work_summary(handoff) != "none"
        if preserved and not handoff.preservation_verified:
            return ReleaseEvaluation(released=False, code=RELEASE_BLOCKED_PRESERVATION, detail="Preserved work is not verified.")
        if not handoff.label_outcome_observed:
            return ReleaseEvaluation(released=False, code=RELEASE_BLOCKED_LABELS, detail="Intended label transition was not observed.")
        return ReleaseEvaluation(released=True, code=RELEASE_RELEASED, detail="Release is complete: writers stopped, mutations settled, preservation verified, label outcome observed.")
    return ReleaseEvaluation(
        released=False, code=RELEASE_PROPOSED,
        detail=f"Proposed disposition {handoff.proposed_disposition or handoff.activity} is recorded; release is not complete.",
    )


def terminal_attempt_may_write(handoff: AttemptHandoff) -> bool:
    """Return False when a released attempt must perform no further writes.

    Terminal attempts do not resume publication or cleanup merely because
    their device reconnects: a resumed old process rereads GitHub first and
    stops when its attempt is released, superseded, held, or in conflict.
    An attempt that already declares itself released (flag or activity) is
    also barred: a gate failure on such a comment needs attention and
    reconciliation, not more writes from the stale owner.

    Operator hold is enforced at admission (a new attempt while a hold is
    active), not here: the owning deployment must still publish terminal
    handoffs for its held attempt.
    """
    if handoff.released or handoff.activity == ACTIVITY_RELEASED:
        return False
    return not evaluate_release(handoff).released


# ---------------------------------------------------------------------------
# Redaction and private-artifact exclusion (work item 7)
# ---------------------------------------------------------------------------

_PRIVATE_ARTIFACT_MARKERS = (
    "minio",
    "localhost",
    "127.0.0.1",
    "/artifacts/",
    "artifacts/",
    "session payload",
    "session_payload",
)


def _redacted_handoff(handoff: AttemptHandoff) -> AttemptHandoff:
    """Return a copy with outbound scanning/redaction applied."""
    from moonmind.utils.logging import redact_sensitive_payload, redact_sensitive_text

    redacted = redact_sensitive_payload(handoff.to_dict())
    assert isinstance(redacted, dict)
    text_keys = (
        "lastReport", "publicationDisposition", "outcome", "verificationSummary",
        "holdReason", "resetReason", "labelOutcomeObserved", "proposedDisposition",
    )
    for key in text_keys:
        redacted[key] = redact_sensitive_text(str(redacted.get(key) or ""))
    remaining = []
    for item in redacted.get("remainingRequirements") or []:
        cleaned = redact_sensitive_text(str(item or ""))
        if cleaned.strip():
            remaining.append(cleaned[:MAX_FIELD_CHARS])
    redacted["remainingRequirements"] = remaining[:MAX_REMAINING_REQUIREMENTS]
    # Local workflow links are optional diagnostics: keep them only when they
    # are not the sole recoverability evidence and never leak private hosts.
    link = str(redacted.get("workflowLink") or "").strip()
    lowered = link.lower()
    if link and any(marker in lowered for marker in _PRIVATE_ARTIFACT_MARKERS):
        link = ""
    redacted["workflowLink"] = redact_sensitive_text(link)[:300]
    return AttemptHandoff.from_dict(redacted)


def redacted_error_summary(summary: str) -> str:
    """Redact a structured-error publication string via existing scanning."""
    from moonmind.utils.logging import redact_sensitive_text

    return redact_sensitive_text(str(summary or ""))[:MAX_FIELD_CHARS]


# ---------------------------------------------------------------------------
# Trusted Activity/tool path (consumed, not reimplemented, by callers)
# ---------------------------------------------------------------------------

#: Update modes for :func:`publish_attempt_handoff`.
UPDATE_MODE_ANNOUNCE = "announce"
UPDATE_MODE_PROGRESS = "progress"
UPDATE_MODE_TERMINAL = "terminal"
KNOWN_UPDATE_MODES = frozenset({UPDATE_MODE_ANNOUNCE, UPDATE_MODE_PROGRESS, UPDATE_MODE_TERMINAL})


def _string_input(inputs: Mapping[str, Any], *names: str) -> str:
    for name in names:
        value = inputs.get(name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _int_input(inputs: Mapping[str, Any], *names: str) -> int:
    for name in names:
        try:
            value = int(inputs.get(name))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
        if value > 0:
            return value
    return 0


def _handoff_from_inputs(
    *,
    inputs: Mapping[str, Any],
    repository: str,
    issue_number: int,
    attempt_id: str,
    deployment_id: str,
    retry: RetryDecision | None = None,
) -> AttemptHandoff:
    """Build a bounded handoff from tool inputs plus derived retry state."""
    explicit = inputs.get("handoff")
    base = dict(explicit) if isinstance(explicit, Mapping) else {}
    remaining = inputs.get("remainingRequirements", inputs.get("remaining_requirements"))
    if remaining is None:
        remaining = base.get("remainingRequirements", base.get("remaining_requirements", []))

    def _pick(*names: str, default: Any = "") -> Any:
        for name in names:
            if name in inputs and inputs.get(name) not in (None, ""):
                return inputs.get(name)
            if name in base and base.get(name) not in (None, ""):
                return base.get(name)
        return default

    return AttemptHandoff(
        attempt_id=attempt_id,
        deployment_id=deployment_id,
        repository=repository,
        issue_number=issue_number,
        workflow_id=_string_input(inputs, "workflowId", "workflow_id") or str(base.get("workflowId") or base.get("workflow_id") or ""),
        run_id=_string_input(inputs, "runId", "run_id") or str(base.get("runId") or base.get("run_id") or ""),
        predecessor_attempt_id=_string_input(inputs, "predecessorAttemptId", "predecessor_attempt_id")
        or str(base.get("predecessorAttemptId") or ""),
        predecessor_comment_id=_int_input(inputs, "predecessorCommentId", "predecessor_comment_id")
        or int(base.get("predecessorCommentId") or 0),
        activity=_string_input(inputs, "activity") or str(base.get("activity") or ACTIVITY_ACTIVE),
        last_report=_string_input(inputs, "lastReport", "last_report") or str(base.get("lastReport") or ""),
        writers_stopped=bool(_pick("writersStopped", "writers_stopped", default=False)),
        publication_disposition=str(_pick("publicationDisposition", "publication_disposition", default="") or ""),
        pr_url=_string_input(inputs, "prUrl", "pr_url") or str(base.get("prUrl") or ""),
        pr_head_sha=_string_input(inputs, "prHeadSha", "pr_head_sha") or str(base.get("prHeadSha") or ""),
        pr_base=_string_input(inputs, "prBase", "pr_base") or str(base.get("prBase") or ""),
        saved_branch=_string_input(inputs, "savedBranch", "saved_branch") or str(base.get("savedBranch") or ""),
        saved_sha=_string_input(inputs, "savedSha", "saved_sha") or str(base.get("savedSha") or ""),
        outcome=_string_input(inputs, "outcome") or str(base.get("outcome") or ""),
        remaining_requirements=tuple(_truncate_list(remaining, MAX_REMAINING_REQUIREMENTS)),
        verification_summary=_string_input(inputs, "verificationSummary", "verification_summary")
        or str(base.get("verificationSummary") or ""),
        next_action=_string_input(inputs, "nextAction", "next_action") or str(base.get("nextAction") or NEXT_OPERATOR_ATTENTION),
        failed_attempts=int(retry.failed_attempts) if retry is not None else int(base.get("failedAttempts") or 0),
        no_progress_count=int(retry.no_progress_count) if retry is not None else int(base.get("noProgressCount") or 0),
        internal_retry_count=int(_pick("internalRetryCount", "internal_retry_count", default=0) or 0),
        remaining_allowance=int(retry.remaining_allowance) if retry is not None else int(base.get("remainingAllowance") or 0),
        cooldown_until=(retry.cooldown_until if retry is not None else "") or str(base.get("cooldownUntil") or ""),
        operator_hold=bool(_pick("operatorHold", "operator_hold", default=False)),
        hold_reason=_string_input(inputs, "holdReason", "hold_reason") or str(base.get("holdReason") or ""),
        reset_generation=int(_pick("resetGeneration", "reset_generation", default=0) or 0),
        reset_reason=_string_input(inputs, "resetReason", "reset_reason") or str(base.get("resetReason") or ""),
        reset_author=_string_input(inputs, "resetAuthor", "reset_author") or str(base.get("resetAuthor") or ""),
        policy_id=_string_input(inputs, "policyId", "policy_id") or str(base.get("policyId") or "default"),
        proposed_disposition=_string_input(inputs, "proposedDisposition", "proposed_disposition")
        or str(base.get("proposedDisposition") or ""),
        released=bool(_pick("released", default=False)),
        mutations_settled=bool(_pick("mutationsSettled", "mutations_settled", default=False)),
        preservation_verified=bool(_pick("preservationVerified", "preservation_verified", default=False)),
        label_outcome_observed=_string_input(inputs, "labelOutcomeObserved", "label_outcome_observed")
        or str(base.get("labelOutcomeObserved") or ""),
        workflow_link=_string_input(inputs, "workflowLink", "workflow_link") or str(base.get("workflowLink") or ""),
    )


async def reconstruct_retry_from_comments(
    *,
    repository: str,
    issue_number: int,
    comments: Sequence[Mapping[str, Any]],
    trusted_poster_logins: Sequence[str] | None = None,
    policy: RetryPolicy | None = None,
    now_epoch: float | None = None,
) -> dict[str, Any]:
    """Reconstruct portable retry state from GitHub-visible comments alone.

    Device B needs no private logs: every validated attempt comment in the
    lineage contributes its failed-attempt/no-progress evidence, cooldown,
    and operator hold to the effective :class:`RetryDecision`. Comments that
    fail provenance validation are reported as attention items, never
    silently skipped or treated as a clean slate.
    """
    validated: list[AttemptHandoff] = []
    attention: list[dict[str, Any]] = []
    known_ids: list[str] = []
    # Two passes: first collect parseable IDs for predecessor linkage, then
    # validate with the full observed ID set so ordering never matters.
    for comment in comments:
        if not isinstance(comment, Mapping):
            continue
        handoff, _ = try_parse_comment_body(comment.get("body"))
        if handoff is not None and handoff.attempt_id not in known_ids:
            known_ids.append(handoff.attempt_id)
    for comment in comments:
        if not isinstance(comment, Mapping):
            continue
        user = comment.get("user") if isinstance(comment.get("user"), dict) else {}
        validation = validate_attempt_comment(
            body=comment.get("body"),
            repository=repository,
            issue_number=issue_number,
            poster_login=str(comment.get("poster_login") or (user or {}).get("login") or ""),
            poster_type=str(comment.get("poster_type") or (user or {}).get("type") or ""),
            trusted_poster_logins=trusted_poster_logins,
            known_attempt_ids=known_ids,
        )
        if validation.ok and validation.handoff is not None:
            validated.append(validation.handoff)
        else:
            _, parse_error = try_parse_comment_body(comment.get("body"))
            if parse_error or validation.code != VALIDATION_SPOOFED:
                attention.append(
                    {
                        "commentId": comment.get("id"),
                        "code": validation.code,
                        "detail": validation.detail,
                    }
                )
    decision = collect_retry_history(validated, policy=policy, now_epoch=now_epoch)
    return {
        "handoffs": [item.to_dict() for item in validated],
        "attention": attention,
        "retry": decision.to_dict(),
    }


async def publish_attempt_handoff(
    inputs: Mapping[str, Any],
    context: Mapping[str, Any] | None = None,
    *,
    github_service_factory: Any = None,
) -> Any:
    """Create or update this attempt's own GitHub issue comment.

    Real Activity/adapter path: identity resolution, bounded redacted
    rendering, provenance validation, marker reconciliation, coalesced
    progress updates, portable retry derivation, and the proposed-vs-
    released gate run here through the trusted ``GitHubService`` boundary.
    Returns a ``ToolResult``.
    """
    from moonmind.workflows.skills.tool_plan_contracts import ToolResult

    inputs = dict(inputs or {})
    context = dict(context or {})
    repository = _string_input(inputs, "repository", "repo") or _string_input(context, "repository", "repo")
    issue_number = _int_input(inputs, "issueNumber", "issue_number") or _int_input(context, "issueNumber", "issue_number")
    issue_ref = f"{repository}#{issue_number}" if repository and issue_number else "unknown issue"
    if not repository or not _REPO_RE.fullmatch(repository):
        return ToolResult(status="FAILED", outputs={"issueRef": issue_ref, "decision": "blocked", "reasonCode": "invalid_repository", "summary": "publish_attempt_handoff requires an explicit owner/repository scope."})
    if not issue_number:
        return ToolResult(status="FAILED", outputs={"issueRef": issue_ref, "decision": "blocked", "reasonCode": "invalid_issue", "summary": "publish_attempt_handoff requires an issueNumber."})
    mode = _string_input(inputs, "updateMode", "update_mode", "mode") or UPDATE_MODE_ANNOUNCE
    if mode not in KNOWN_UPDATE_MODES:
        return ToolResult(status="FAILED", outputs={"issueRef": issue_ref, "decision": "blocked", "reasonCode": "unsupported_mode", "summary": f"Unsupported update mode: {mode}."})
    trusted_posters = inputs.get("trustedPosterLogins", inputs.get("trusted_poster_logins", context.get("trustedPosterLogins", [])))
    trusted_posters = [str(item) for item in (trusted_posters or []) if str(item or "").strip()]
    try:
        identity = resolve_installation_id(
            _string_input(inputs, "installationId", "installation_id") or None,
            environ=context.get("environ") if isinstance(context.get("environ"), Mapping) else None,
        )
    except ValueError as exc:
        return ToolResult(status="FAILED", outputs={"issueRef": issue_ref, "decision": "blocked", "reasonCode": "missing_installation_identity", "summary": str(exc)})
    workflow_id = _string_input(inputs, "workflowId", "workflow_id") or _string_input(context, "workflowId", "workflow_id")
    run_id = _string_input(inputs, "runId", "run_id") or _string_input(context, "runId", "run_id")
    attempt_id = _string_input(inputs, "attemptId", "attempt_id")
    if attempt_id and not is_attempt_id(attempt_id):
        return ToolResult(status="FAILED", outputs={"issueRef": issue_ref, "decision": "blocked", "reasonCode": "invalid_attempt_id", "summary": f"Invalid attemptId: {attempt_id}."})
    if not attempt_id:
        if not workflow_id or not run_id:
            return ToolResult(status="FAILED", outputs={"issueRef": issue_ref, "decision": "blocked", "reasonCode": "missing_workflow_binding", "summary": "publish_attempt_handoff requires attemptId or workflowId plus runId for attempt binding."})
        attempt_id = new_attempt_id(
            repository=repository, issue_number=issue_number, installation_id=identity.installation_id,
            workflow_id=workflow_id, run_id=run_id,
            nonce=_string_input(inputs, "attemptNonce", "attempt_nonce") or None,
        )
    if github_service_factory is None:
        from moonmind.workflows.adapters.github_service import GitHubService as _GitHubService

        github_service_factory = _GitHubService
    service = github_service_factory()
    listed = await service.list_issue_comments(repo=repository, issue_number=issue_number)
    if not listed.get("ok"):
        return ToolResult(status="FAILED", outputs={"issueRef": issue_ref, "attemptId": attempt_id, "decision": "blocked", "reasonCode": str(listed.get("reasonCode") or "comment_read_failed"), "summary": f"Refusing local-only claim for {issue_ref}: {listed.get('summary')}"})
    comments = [item for item in (listed.get("comments") or []) if isinstance(item, Mapping)]
    known_ids = [str(item.get("attemptId") or "") for item in (inputs.get("knownAttemptIds") or []) if str(item.get("attemptId") or "")]
    for comment in comments:
        parsed, _ = try_parse_comment_body(comment.get("body"))
        if parsed is not None and parsed.attempt_id not in known_ids:
            known_ids.append(parsed.attempt_id)
    # Portable retry derivation from GitHub-visible history (device-B rule).
    policy_inputs = inputs.get("retryPolicy", inputs.get("retry_policy"))
    if isinstance(policy_inputs, Mapping):
        try:
            policy = RetryPolicy(
                policy_id=str(policy_inputs.get("policyId", policy_inputs.get("policy_id", "default")) or "default"),
                max_attempts=int(policy_inputs.get("maxAttempts", policy_inputs.get("max_attempts", 3)) or 3),
                cooldown_seconds=int(policy_inputs.get("cooldownSeconds", policy_inputs.get("cooldown_seconds", 3600)) or 0),
                reset_generation=int(policy_inputs.get("resetGeneration", policy_inputs.get("reset_generation", 0)) or 0),
            )
        except (TypeError, ValueError):
            policy = RetryPolicy()
    else:
        policy = RetryPolicy()
    reconstruction = await reconstruct_retry_from_comments(
        repository=repository, issue_number=issue_number, comments=comments,
        trusted_poster_logins=trusted_posters or None, policy=policy,
    )
    lineage = [AttemptHandoff.from_dict(item) for item in reconstruction["handoffs"]]
    retry = collect_retry_history(lineage, policy=policy)
    try:
        handoff = _handoff_from_inputs(
            inputs=inputs, repository=repository, issue_number=issue_number,
            attempt_id=attempt_id, deployment_id=identity.installation_id, retry=retry,
        )
    except ValueError as exc:
        return ToolResult(status="FAILED", outputs={"issueRef": issue_ref, "attemptId": attempt_id, "decision": "blocked", "reasonCode": "invalid_handoff", "summary": redacted_error_summary(str(exc))})
    # The release gate applies to the observed remote state, not the
    # outgoing update: publishing the released state itself is legitimate,
    # but a released (or held) attempt performs no further writes, even
    # when its device reconnects.
    body = render_comment_body(handoff)
    if len(body) > MAX_COMMENT_CHARS:
        return ToolResult(status="FAILED", outputs={"issueRef": issue_ref, "attemptId": attempt_id, "decision": "blocked", "reasonCode": "comment_unbounded", "summary": "Rendered attempt comment exceeds the outbound bound."})
    reconciliation = reconcile_marker_before_create(attempt_id=attempt_id, comments=comments)
    if reconciliation.outcome == RECONCILE_CONFLICT:
        return ToolResult(status="FAILED", outputs={"issueRef": issue_ref, "attemptId": attempt_id, "decision": "attention", "reasonCode": "conflicting_copies", "retry": retry.to_dict(), "summary": reconciliation.detail})
    if mode == UPDATE_MODE_ANNOUNCE:
        if reconciliation.outcome == RECONCILE_CREATE and retry.code == RETRY_HOLD:
            return ToolResult(status="FAILED", outputs={"issueRef": issue_ref, "attemptId": attempt_id, "decision": "blocked", "reasonCode": "operator_hold", "retry": retry.to_dict(), "summary": f"Refusing a new attempt for {issue_ref} while an operator hold is active; resolve the hold before announcing."})
        if reconciliation.outcome in {RECONCILE_REUSE, RECONCILE_DUPLICATE_SAME}:
            existing, _ = try_parse_comment_body(
                next((comment.get("body") for comment in comments
                      if isinstance(comment, Mapping) and comment.get("id") == reconciliation.comment_id), "")
            ) if reconciliation.comment_id else (None, "")
            if existing is not None and not terminal_attempt_may_write(existing):
                return ToolResult(status="FAILED", outputs={"issueRef": issue_ref, "attemptId": attempt_id, "decision": "blocked", "reasonCode": "already_released", "summary": f"Attempt {attempt_id} is already released or held; no further writes."})
            if reconciliation.outcome == RECONCILE_DUPLICATE_SAME:
                own = select_own_comment(attempt_id=attempt_id, comments=comments)
                own_id = int((own or {}).get("id") or 0) if isinstance(own, Mapping) else 0
                updated = await service.update_issue_comment(repo=repository, comment_id=own_id, body=body)
                if not updated.get("ok"):
                    return ToolResult(status="FAILED", outputs={"issueRef": issue_ref, "attemptId": attempt_id, "decision": "blocked", "reasonCode": str(updated.get("reasonCode") or "comment_update_failed"), "summary": str(updated.get("summary"))})
                return ToolResult(status="COMPLETED", outputs={"issueRef": issue_ref, "attemptId": attempt_id, "decision": "reconciled", "commentId": own_id, "retry": retry.to_dict(), "release": evaluate_release(handoff).to_dict(), "summary": f"Reconciled duplicate markers for {attempt_id} into one logical attempt."})
            return ToolResult(status="COMPLETED", outputs={"issueRef": issue_ref, "attemptId": attempt_id, "decision": "reused", "commentId": reconciliation.comment_id, "retry": retry.to_dict(), "release": evaluate_release(handoff).to_dict(), "summary": f"Attempt comment for {attempt_id} already exists; reconciled without repeating creation."})
        created = await service.create_issue_comment(repo=repository, issue_number=issue_number, body=body)
        if not created.get("ok"):
            return ToolResult(status="FAILED", outputs={"issueRef": issue_ref, "attemptId": attempt_id, "decision": "blocked", "reasonCode": str(created.get("reasonCode") or "comment_create_failed"), "retry": retry.to_dict(), "summary": str(created.get("summary"))})
        return ToolResult(status="COMPLETED", outputs={"issueRef": issue_ref, "attemptId": attempt_id, "decision": "announced", "commentId": created.get("commentId"), "retry": retry.to_dict(), "release": evaluate_release(handoff).to_dict(), "summary": f"Announced attempt {attempt_id} on {issue_ref}."})
    # Progress and terminal modes update only this attempt's own comment.
    own = select_own_comment(attempt_id=attempt_id, comments=comments)
    if own is None:
        return ToolResult(status="FAILED", outputs={"issueRef": issue_ref, "attemptId": attempt_id, "decision": "blocked", "reasonCode": "own_comment_missing", "retry": retry.to_dict(), "summary": f"No comment carries attempt {attempt_id}; announce before updating, and never overwrite another attempt's comment."})
    try:
        own_id = int(own.get("id") or 0)
    except (TypeError, ValueError):
        own_id = 0
    if not own_id:
        return ToolResult(status="FAILED", outputs={"issueRef": issue_ref, "attemptId": attempt_id, "decision": "blocked", "reasonCode": "own_comment_missing", "summary": f"Own comment for {attempt_id} has no stable comment ID."})
    observed, _ = try_parse_comment_body(own.get("body"))
    if observed is not None and not terminal_attempt_may_write(observed):
        return ToolResult(status="FAILED", outputs={"issueRef": issue_ref, "attemptId": attempt_id, "decision": "blocked", "reasonCode": "already_released", "commentId": own_id, "release": evaluate_release(observed).to_dict(), "summary": f"Attempt {attempt_id} is already released or held; no further writes."})
    if mode == UPDATE_MODE_PROGRESS:
        last_update = inputs.get("lastUpdateEpoch", inputs.get("last_update_epoch", own.get("updated_at")))
        try:
            last_epoch = float(last_update) if last_update not in (None, "") else None
        except (TypeError, ValueError):
            last_epoch = _parse_epoch(last_update)
        now_epoch = inputs.get("nowEpoch", inputs.get("now_epoch"))
        try:
            now_value = float(now_epoch) if now_epoch not in (None, "") else None
        except (TypeError, ValueError):
            now_value = None
        if not progress_update_allowed(last_update_epoch=last_epoch, now_epoch=now_value):
            return ToolResult(status="COMPLETED", outputs={"issueRef": issue_ref, "attemptId": attempt_id, "decision": "coalesced", "commentId": own_id, "retry": retry.to_dict(), "summary": "Progress report coalesced within the bounded rate limit."})
        # Out-of-order local updates never overwrite newer remote state with
        # older content: callers pass the base they rendered from.
        base_body = inputs.get("baseCommentBody", inputs.get("base_comment_body"))
        if isinstance(base_body, str) and base_body.strip() and str(own.get("body") or "").strip() != base_body.strip():
            current, _ = try_parse_comment_body(own.get("body"))
            base, _ = try_parse_comment_body(base_body)
            if current is not None and base is not None and current.last_report != base.last_report:
                return ToolResult(status="FAILED", outputs={"issueRef": issue_ref, "attemptId": attempt_id, "decision": "blocked", "reasonCode": "stale_base", "commentId": own_id, "summary": "Own comment changed since this update was rendered; re-read before writing."})
    updated = await service.update_issue_comment(repo=repository, comment_id=own_id, body=body)
    if not updated.get("ok"):
        return ToolResult(status="FAILED", outputs={"issueRef": issue_ref, "attemptId": attempt_id, "decision": "blocked", "reasonCode": str(updated.get("reasonCode") or "comment_update_failed"), "commentId": own_id, "retry": retry.to_dict(), "summary": str(updated.get("summary"))})
    return ToolResult(status="COMPLETED", outputs={"issueRef": issue_ref, "attemptId": attempt_id, "decision": "published", "commentId": own_id, "retry": retry.to_dict(), "release": evaluate_release(handoff).to_dict(), "summary": f"Published attempt {attempt_id} {mode} update on {issue_ref}."})


__all__ = [
    "INSTALLATION_ID_ENV_VARS",
    "COMMENT_SCHEMA_VERSION",
    "SUPPORTED_COMMENT_VERSIONS",
    "MAX_COMMENT_CHARS",
    "MAX_FIELD_CHARS",
    "PROGRESS_COALESCE_SECONDS",
    "ACTIVITY_PREPARING",
    "ACTIVITY_ACTIVE",
    "ACTIVITY_AWAITING_REVIEW",
    "ACTIVITY_RELEASING",
    "ACTIVITY_RELEASED",
    "ACTIVITY_ATTENTION",
    "KNOWN_ACTIVITIES",
    "NEXT_FRESH_RETRY",
    "NEXT_CONTINUE_IMPLEMENTATION",
    "NEXT_VERIFY",
    "NEXT_CONTINUE_REVIEW",
    "NEXT_FINALIZE_STATUS",
    "NEXT_OPERATOR_ATTENTION",
    "KNOWN_NEXT_ACTIONS",
    "VALIDATION_OK",
    "VALIDATION_SPOOFED",
    "VALIDATION_VERSION",
    "VALIDATION_ISSUE_MISMATCH",
    "VALIDATION_PREDECESSOR_MISSING",
    "VALIDATION_PREDECESSOR_UNKNOWN",
    "VALIDATION_SCHEMA",
    "VALIDATION_REF",
    "RECONCILE_CREATE",
    "RECONCILE_REUSE",
    "RECONCILE_DUPLICATE_SAME",
    "RECONCILE_CONFLICT",
    "RETRY_OK",
    "RETRY_HOLD",
    "RETRY_COOLDOWN",
    "RETRY_EXHAUSTED",
    "RETRY_MISSING_LINEAGE",
    "RETRY_INCOMPATIBLE_POLICY",
    "RETRY_RESET_AUTHORIZED",
    "RELEASE_PROPOSED",
    "RELEASE_RELEASED",
    "RELEASE_BLOCKED_WRITERS",
    "RELEASE_BLOCKED_MUTATIONS",
    "RELEASE_BLOCKED_PRESERVATION",
    "RELEASE_BLOCKED_LABELS",
    "InstallationIdentity",
    "AttemptHandoff",
    "CommentValidation",
    "MarkerReconciliation",
    "RetryPolicy",
    "RetryDecision",
    "ReleaseEvaluation",
    "resolve_installation_id",
    "new_attempt_id",
    "is_attempt_id",
    "marker_for",
    "render_comment_body",
    "parse_comment_body",
    "try_parse_comment_body",
    "validate_attempt_comment",
    "reconcile_marker_before_create",
    "select_own_comment",
    "progress_update_allowed",
    "collect_retry_history",
    "evaluate_release",
    "terminal_attempt_may_write",
    "redacted_error_summary",
    "UPDATE_MODE_ANNOUNCE",
    "UPDATE_MODE_PROGRESS",
    "UPDATE_MODE_TERMINAL",
    "KNOWN_UPDATE_MODES",
    "reconstruct_retry_from_comments",
    "publish_attempt_handoff",
]
