"""Portable per-attempt GitHub issue handoffs and cross-deployment retry history.

Single policy entrypoint for issue MoonLadderStudios/MoonMind#4177
(design: docs/Workflows/GitHubIssueStatusStateMachineDesign.md, sections 4
and 5.3). Deterministic and side-effect-free, except for the explicit
installation-identity persistence helper: trusted Activities/services perform
GitHub reads/writes; this module decides what comments mean and what the
retry/release policy allows.

Contract summary:
  - Every issue-work attempt gets one identifiable GitHub comment that another
    independent deployment can validate and use. GitHub is the shared handoff
    surface, not a pointer to another device's private database or MinIO.
  - A stable installation identity differs across deployments; shared GitHub
    usernames are not deployment identities. One canonical resolver owns it
    (no competing internal aliases).
  - One versioned, bounded comment representation carries a readable summary
    plus machine-readable metadata. No equivalent label families are
    introduced: activity stays a comment field, never a label.
  - Provenance validation treats a copied machine marker as unauthenticated,
    issue prose as untrusted reference content, and shared-account comments
    as mutually non-adversarial only (never a security boundary).
  - Writes serialize per attempt: uncertain creates reconcile by stable
    marker, same-ID duplicates are one logical attempt, conflicts require
    attention (never last-timestamp-wins), progress coalesces within rate
    limits, and no attempt overwrites another attempt's comment.
  - Retry history (failed-attempt/no-progress linkage, allowance, cooldown,
    operator hold) survives new workflow IDs, device changes, and label
    clears. Missing/incompatible lineage blocks automatic recovery; audited
    resets only; no exact global counter is claimed under races.
  - Proposed release vs completed release are distinct: ``released`` requires
    confirmed stopped writers, settled mutations, verified preservation (or
    explicit no-work evidence), and observed label outcome. Released attempts
    never resume publication/cleanup on reconnect.
  - Outbound comments and structured errors pass the existing
    scanning/redaction boundary; local workflow links stay diagnostics-only.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Mapping, Sequence

#: Version of the machine-readable attempt comment envelope. Unknown versions
#: fail closed (attention), they are never silently admitted or normalized.
ATTEMPT_COMMENT_VERSION = 1
SUPPORTED_ATTEMPT_COMMENT_VERSIONS = frozenset({1})

#: Hard bound for the rendered comment body. The human summary plus the fenced
#: metadata block never exceed this; longer free-text fields are truncated
#: with an explicit marker rather than silently dropped.
MAX_ATTEMPT_COMMENT_CHARS = 8000

#: Stable marker prefix identifying MoonMind attempt comments. The attempt ID
#: rendezvous key is the ``attempt="<id>"`` attribute inside the marker.
ATTEMPT_MARKER_PREFIX = "<!-- moonmind-issue-attempt"
ATTEMPT_MARKER_SUFFIX = "-->"
ATTEMPT_FENCE_OPEN = "```json moonmind-issue-attempt"
ATTEMPT_FENCE_CLOSE = "```"

#: Canonical attempt activities (comment field only; never labels).
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

#: Canonical next actions carried in the handoff.
ATTEMPT_NEXT_ACTIONS = frozenset(
    {
        "fresh_retry",
        "continue_implementation",
        "verify",
        "continue_review",
        "finalize_status",
        "obtain_operator_attention",
    }
)

#: Progress reports coalesce within this window instead of rewriting the
#: attempt comment on every runtime poll.
PROGRESS_COALESCE_SECONDS = 60

#: Default retry policy when the portable handoff carries explicit history.
#: Operators tune these through the handoff/policy inputs consumed here, not
#: through hidden per-runtime fallbacks.
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_COOLDOWN_SECONDS = 300

INSTALLATION_ID_ENV_VARS = (
    "MOONMIND_INSTALLATION_ID",
    "MOONMIND_DEPLOYMENT_ID",
    "MOONMIND_INSTANCE_ID",
)
# Persistent deployment-owned location first (mounted named volume on the
# integrations worker); legacy relative path is a local-dev fallback that is
# still honored on read for backwards compatibility.
DEFAULT_INSTALLATION_ID_PATH = Path("/app/var/secrets/moonmind-installation-id")
LEGACY_INSTALLATION_ID_PATH = Path("var/moonmind-installation-id")

_ATTEMPT_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_MARKER_ATTRS_RE = re.compile(r'(\w[\w-]*)="([^"]*)"')
_FENCE_RE = re.compile(
    r"```json moonmind-issue-attempt\s*\n(.*?)\n```", re.DOTALL
)


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _isoformat(value: Any) -> str:
    if isinstance(value, datetime):
        moment = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
        return moment.astimezone(UTC).isoformat()
    return str(value or "")


def _parse_moment(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


# ---------------------------------------------------------------------------
# Requirement 1: stable installation identity + globally unique attempt ID
# ---------------------------------------------------------------------------


def _normalize_installation_id(value: Any) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "", str(value or "").strip())[:128]


def resolve_installation_id(
    explicit: Any = None,
    *,
    environ: Mapping[str, str] | None = None,
    path: Path | str | None = None,
    persist: bool = True,
) -> str:
    """Resolve the single stable installation identity for this deployment.

    Precedence is deterministic and alias-free: explicit input, then the
    canonical environment names (``MOONMIND_INSTALLATION_ID`` pins one
    canonical ID across scaled replicas), then the persisted deployment-owned
    file (created once on the persistent secrets volume and reused across
    restarts/retries/replicas sharing that volume), then the legacy relative
    path for backwards compatibility. The value differs across deployments
    because each deployment owns its environment/file; shared GitHub usernames
    are never consulted here.
    """
    env = os.environ if environ is None else environ
    candidate = _normalize_installation_id(explicit)
    if candidate:
        return candidate
    for name in INSTALLATION_ID_ENV_VARS:
        candidate = _normalize_installation_id((env or {}).get(name))
        if candidate:
            return candidate
    targets: list[Path] = []
    if path is not None:
        targets = [Path(path)]
    else:
        targets = [DEFAULT_INSTALLATION_ID_PATH, LEGACY_INSTALLATION_ID_PATH]
    for target in targets:
        try:
            if target.exists():
                candidate = _normalize_installation_id(
                    target.read_text(encoding="utf-8")
                )
                if candidate:
                    return candidate
        except OSError:
            # Missing/unreadable file means no persisted identity yet; fall
            # through to generate (and best-effort persist) a fresh value.
            continue
    generated = uuid.uuid4().hex
    if persist:
        primary = targets[0]
        try:
            primary.parent.mkdir(parents=True, exist_ok=True)
            primary.write_text(generated + "\n", encoding="utf-8")
        except OSError:
            # Ephemeral filesystems may reject the write; the generated
            # value is still returned for this invocation.
            pass
    return generated


def build_attempt_id(
    *,
    repository: str,
    issue_number: int,
    workflow_id: str = "",
    run_id: str = "",
    deployment_id: str = "",
) -> str:
    """Build a globally unique attempt ID bound to repo/issue/workflow/run.

    The ID is deterministically derived from the stable scope whenever the
    caller supplies a workflow or run identity, so an Activity retry that
    receives the same original inputs reconciles the same marker instead of
    minting a second logical attempt. Callers without any stable scope get a
    fresh random ID. The durable assignment must happen before the retryable
    Activity boundary: workflows should mint (or forward) ``attemptId`` once
    and reuse it from ``inputs``/``previousOutputs`` on retry.
    """
    stable_scope = [
        str(repository or "").strip().lower(),
        str(issue_number or ""),
        str(workflow_id or "").strip(),
        str(run_id or "").strip(),
        str(deployment_id or "").strip(),
    ]
    if str(workflow_id or "").strip() or str(run_id or "").strip():
        scope = "|".join(stable_scope)
    else:
        scope = "|".join(stable_scope + [uuid.uuid4().hex])
    return hashlib.sha256(scope.encode("utf-8")).hexdigest()[:32]


def normalize_attempt_id(value: Any) -> str:
    """Return the canonical attempt ID or ``""`` when malformed."""
    candidate = str(value or "").strip().lower()
    if _ATTEMPT_ID_RE.fullmatch(candidate):
        return candidate
    return ""


def attempt_binding_key(*, repository: str, issue_number: int, attempt_id: str) -> str:
    """Return the stable reconcile key for uncertain creates/retries."""
    return (
        f"{str(repository or '').strip().lower()}#"
        f"{int(issue_number)}:{normalize_attempt_id(attempt_id)}"
    )


# ---------------------------------------------------------------------------
# Requirement 2: one versioned, bounded comment representation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AttemptHandoff:
    """Portable per-attempt handoff carried in one GitHub issue comment."""

    repository: str
    issue_number: int
    attempt_id: str
    deployment_id: str
    workflow_id: str = ""
    run_id: str = ""
    version: int = ATTEMPT_COMMENT_VERSION
    predecessor_attempt_id: str = ""
    predecessor_comment_id: str = ""
    activity: str = ATTEMPT_ACTIVITY_ACTIVE
    last_report: str = ""
    last_activity_at: str = ""
    writers_stopped: bool = False
    publication_outcome: str = ""
    pending_disposition: str = ""
    pull_request_url: str = ""
    head_sha: str = ""
    head_branch: str = ""
    base_branch: str = ""
    saved_branch: str = ""
    saved_sha: str = ""
    outcome: str = ""
    met_requirements: tuple[str, ...] = ()
    unmet_requirements: tuple[str, ...] = ()
    verification_summary: str = ""
    next_action: str = ""
    retry_history: tuple[str, ...] = ()
    retry_allowance: str = ""
    cooldown_until: str = ""
    operator_hold: bool = False
    reset_record: str = ""
    policy_ref: str = ""
    diagnostics_ref: str = ""

    def to_metadata(self) -> dict[str, Any]:
        return {
            "format": "moonmind-issue-attempt",
            "version": int(self.version),
            "repository": str(self.repository or ""),
            "issueNumber": int(self.issue_number),
            "attemptId": normalize_attempt_id(self.attempt_id),
            "deploymentId": str(self.deployment_id or ""),
            "workflowId": str(self.workflow_id or ""),
            "runId": str(self.run_id or ""),
            "predecessorAttemptId": normalize_attempt_id(
                self.predecessor_attempt_id
            ),
            "predecessorCommentId": str(self.predecessor_comment_id or ""),
            "activity": str(self.activity or ""),
            "lastReport": str(self.last_report or ""),
            "lastActivityAt": str(self.last_activity_at or ""),
            "writersStopped": bool(self.writers_stopped),
            "publicationOutcome": str(self.publication_outcome or ""),
            "pendingDisposition": str(self.pending_disposition or ""),
            "pullRequestUrl": str(self.pull_request_url or ""),
            "headSha": str(self.head_sha or ""),
            "headBranch": str(self.head_branch or ""),
            "baseBranch": str(self.base_branch or ""),
            "savedBranch": str(self.saved_branch or ""),
            "savedSha": str(self.saved_sha or ""),
            "outcome": str(self.outcome or ""),
            "metRequirements": list(self.met_requirements),
            "unmetRequirements": list(self.unmet_requirements),
            "verificationSummary": str(self.verification_summary or ""),
            "nextAction": str(self.next_action or ""),
            "retryHistory": list(self.retry_history),
            "retryAllowance": str(self.retry_allowance or ""),
            "cooldownUntil": str(self.cooldown_until or ""),
            "operatorHold": bool(self.operator_hold),
            "resetRecord": str(self.reset_record or ""),
            "policyRef": str(self.policy_ref or ""),
            "diagnosticsRef": str(self.diagnostics_ref or ""),
        }


_ACTIVITY_SUMMARY = {
    ATTEMPT_ACTIVITY_PREPARING: "preparing work",
    ATTEMPT_ACTIVITY_ACTIVE: "started implementation",
    ATTEMPT_ACTIVITY_AWAITING_REVIEW: "awaiting review",
    ATTEMPT_ACTIVITY_RELEASING: "releasing lifecycle status",
    ATTEMPT_ACTIVITY_RELEASED: "released lifecycle status",
    ATTEMPT_ACTIVITY_ATTENTION: "needing attention",
}


def _truncate(text: str, limit: int) -> str:
    cleaned = str(text or "")
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: max(0, limit - len(" …[truncated]"))] + " …[truncated]"


def render_attempt_comment(handoff: AttemptHandoff) -> str:
    """Render one bounded attempt comment (readable summary + metadata)."""
    metadata = handoff.to_metadata()
    machine = json.dumps(metadata, sort_keys=True, separators=(",", ":"))
    marker = (
        f"{ATTEMPT_MARKER_PREFIX} v={int(handoff.version)} "
        f'attempt="{normalize_attempt_id(handoff.attempt_id)}" '
        f'deployment="{str(handoff.deployment_id or "")[:128]}" '
        f'issue="{str(handoff.repository or "")}#{int(handoff.issue_number)}"'
        f"{ATTEMPT_MARKER_SUFFIX}"
    )
    activity_phrase = _ACTIVITY_SUMMARY.get(
        str(handoff.activity or ""), str(handoff.activity or "active")
    )
    issue_ref = f"{handoff.repository}#{int(handoff.issue_number)}"
    summary_lines = [
        f"MoonMind attempt `{normalize_attempt_id(handoff.attempt_id)[:12]}` "
        f"({activity_phrase}) for {issue_ref}.",
    ]
    # Preserve the legacy lifecycle sentence shapes so existing projections
    # keep reading the same human dispositions from the new envelope.
    if handoff.activity == ATTEMPT_ACTIVITY_ATTENTION:
        summary_lines.append(
            f"MoonMind flagged {issue_ref} as needing attention; "
            "operator resolution is required before automatic work continues."
        )
    elif handoff.activity in {ATTEMPT_ACTIVITY_RELEASING, ATTEMPT_ACTIVITY_RELEASED}:
        summary_lines.append(
            f"MoonMind recorded a continuation handoff for {issue_ref}; "
            "resume from the preserved work instead of starting fresh."
            if handoff.pending_disposition or handoff.pull_request_url
            else f"MoonMind released {issue_ref} to available with terminal proof; "
            "it is eligible for fresh admission."
        )
    if handoff.last_report:
        summary_lines.append(_truncate(str(handoff.last_report), 500))
    if handoff.next_action:
        summary_lines.append(f"Next action: {_truncate(str(handoff.next_action), 200)}.")
    if handoff.operator_hold:
        summary_lines.append("Operator hold is in effect; automatic retry is blocked.")
    summary = "\n".join(line for line in summary_lines if line)
    body = (
        f"{marker}\n{summary}\n\n"
        f"{ATTEMPT_FENCE_OPEN}\n{machine}\n{ATTEMPT_FENCE_CLOSE}\n"
        f"{ATTEMPT_MARKER_PREFIX} end{ATTEMPT_MARKER_SUFFIX}"
    )
    if len(body) <= MAX_ATTEMPT_COMMENT_CHARS:
        return body
    # Bound by shrinking free-text fields first, then dropping optional
    # variable-length fields entirely until a complete JSON envelope fits.
    # The envelope is never sliced mid-JSON: a truncated fence would make
    # parse_attempt_comment return empty metadata and destroy the durable
    # cross-deployment handoff.
    candidates = [
        {
            **handoff.__dict__,
            "last_report": _truncate(handoff.last_report, 200),
            "verification_summary": _truncate(handoff.verification_summary, 200),
            "diagnostics_ref": "",
        },
        {
            **handoff.__dict__,
            "last_report": _truncate(handoff.last_report, 100),
            "verification_summary": "",
            "diagnostics_ref": "",
            "met_requirements": (),
            "retry_history": tuple(list(handoff.retry_history)[:10]),
        },
        {
            **handoff.__dict__,
            "last_report": "",
            "verification_summary": "",
            "diagnostics_ref": "",
            "met_requirements": (),
            "unmet_requirements": tuple(list(handoff.unmet_requirements)[:20]),
            "retry_history": tuple(list(handoff.retry_history)[:10]),
        },
        {
            **handoff.__dict__,
            "last_report": "",
            "verification_summary": "",
            "diagnostics_ref": "",
            "met_requirements": (),
            "unmet_requirements": (),
            "retry_history": (),
        },
    ]
    for fields in candidates:
        shrunk = AttemptHandoff(**fields)
        machine = json.dumps(shrunk.to_metadata(), sort_keys=True, separators=(",", ":"))
        candidate_body = (
            f"{marker}\n{summary[:1500]}\n\n"
            f"{ATTEMPT_FENCE_OPEN}\n{machine}\n{ATTEMPT_FENCE_CLOSE}\n"
            f"{ATTEMPT_MARKER_PREFIX} end{ATTEMPT_MARKER_SUFFIX}"
        )
        if len(candidate_body) <= MAX_ATTEMPT_COMMENT_CHARS:
            return candidate_body
    # Minimal envelope: still a complete, parseable handoff carrying the
    # stable identity; optional detail is omitted rather than corrupted.
    minimal = AttemptHandoff(
        repository=handoff.repository,
        issue_number=handoff.issue_number,
        attempt_id=handoff.attempt_id,
        deployment_id=handoff.deployment_id,
        workflow_id=handoff.workflow_id,
        run_id=handoff.run_id,
        version=handoff.version,
        activity=handoff.activity,
        outcome=handoff.outcome,
        next_action=handoff.next_action,
    )
    machine = json.dumps(minimal.to_metadata(), sort_keys=True, separators=(",", ":"))
    minimal_body = (
        f"{marker}\n{summary[:500]}\n\n"
        f"{ATTEMPT_FENCE_OPEN}\n{machine}\n{ATTEMPT_FENCE_CLOSE}\n"
        f"{ATTEMPT_MARKER_PREFIX} end{ATTEMPT_MARKER_SUFFIX}"
    )
    return minimal_body[:MAX_ATTEMPT_COMMENT_CHARS] if len(minimal_body) > MAX_ATTEMPT_COMMENT_CHARS else minimal_body


@dataclass(frozen=True)
class ParsedAttemptComment:
    """One GitHub comment carrying (or claiming) an attempt envelope."""

    comment_id: str
    author: str
    created_at: str
    updated_at: str
    attempt_id: str
    deployment_id: str
    version: int
    metadata: dict[str, Any]
    raw_body: str = ""
    has_marker: bool = False
    has_fence: bool = False


def parse_attempt_comment(comment: Mapping[str, Any]) -> ParsedAttemptComment | None:
    """Parse one GitHub comment into an attempt envelope (``None`` = none)."""
    body = str(comment.get("body") or "")
    if ATTEMPT_MARKER_PREFIX not in body:
        return None
    marker_attrs: dict[str, str] = {}
    # Restrict attribute parsing to the single canonical opening marker so
    # human-readable prose after the marker (e.g. lastReport text containing
    # attempt="..." or deployment="...") cannot shadow the true identity.
    marker_end = body.find(ATTEMPT_MARKER_SUFFIX)
    marker_scope = body[: marker_end + len(ATTEMPT_MARKER_SUFFIX)] if marker_end != -1 else body
    for match in _MARKER_ATTRS_RE.finditer(marker_scope):
        marker_attrs[match.group(1)] = match.group(2)
    version = 0
    version_match = re.search(r"\bv=(\d+)", marker_scope)
    if version_match:
        try:
            version = int(version_match.group(1))
        except ValueError:
            version = 0
    metadata: dict[str, Any] = {}
    has_fence = ATTEMPT_FENCE_OPEN in body
    fence_match = _FENCE_RE.search(body)
    if fence_match:
        try:
            payload = json.loads(fence_match.group(1))
            if isinstance(payload, Mapping):
                metadata = dict(payload)
                raw_version = metadata.get("version")
                try:
                    version = int(str(raw_version))
                except (TypeError, ValueError):
                    # Keep the marker-derived version; an unparsable
                    # envelope version falls through to validation.
                    pass
        except (ValueError, TypeError):
            metadata = {}
    if not version:
        raw_version = metadata.get("version")
        try:
            version = int(str(raw_version)) if raw_version is not None else 0
        except (TypeError, ValueError):
            version = 0
    attempt_id = normalize_attempt_id(
        marker_attrs.get("attempt") or metadata.get("attemptId")
    )
    deployment_id = str(
        marker_attrs.get("deployment") or metadata.get("deploymentId") or ""
    ).strip()[:128]
    user = comment.get("user")
    author = ""
    if isinstance(user, Mapping):
        author = str(user.get("login") or "").strip()
    return ParsedAttemptComment(
        comment_id=str(comment.get("id") or "").strip(),
        author=author,
        created_at=str(comment.get("created_at") or ""),
        updated_at=str(comment.get("updated_at") or ""),
        attempt_id=attempt_id,
        deployment_id=deployment_id,
        version=version,
        metadata=metadata,
        raw_body=body,
        has_marker=True,
        has_fence=has_fence,
    )


def collect_attempt_comments(
    comments: Sequence[Mapping[str, Any]] | None,
) -> list[ParsedAttemptComment]:
    """Collect parsed attempt comments, skipping non-attempt prose."""
    parsed: list[ParsedAttemptComment] = []
    for comment in comments or []:
        if not isinstance(comment, Mapping):
            continue
        item = parse_attempt_comment(comment)
        if item is not None:
            parsed.append(item)
    return parsed


# ---------------------------------------------------------------------------
# Requirement 3: provenance + schema + lineage validation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AttemptValidation:
    """Explicit validation outcome for one parsed attempt comment."""

    outcome: str
    reason_code: str
    detail: str = ""
    usable: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome,
            "reasonCode": self.reason_code,
            "detail": self.detail,
            "usable": self.usable,
        }


def validate_attempt_comment(
    parsed: ParsedAttemptComment,
    *,
    repository: str,
    issue_number: int,
    trusted_posters: Sequence[str] | None = None,
    known_attempt_ids: Sequence[str] | None = None,
    require_trusted_poster: bool = True,
) -> AttemptValidation:
    """Validate one parsed attempt comment without trusting its marker.

    A copied machine marker is not authentication: the poster must belong to
    the trusted GitHub identity set, the schema version must be supported, the
    issue identity must match exactly, and predecessor links must resolve to
    known attempt history. Issue prose outside the envelope stays untrusted
    reference content and is never executed or treated as authority.
    """
    if parsed.version not in SUPPORTED_ATTEMPT_COMMENT_VERSIONS:
        return AttemptValidation(
            outcome="rejected",
            reason_code="unsupported_version",
            detail=f"Unsupported attempt format version {parsed.version}; attention required.",
        )
    if not parsed.attempt_id:
        return AttemptValidation(
            outcome="rejected",
            reason_code="missing_attempt_id",
            detail="Attempt marker has no usable attempt ID; attention required.",
        )
    metadata = parsed.metadata or {}
    if not parsed.has_fence or not metadata:
        return AttemptValidation(
            outcome="rejected",
            reason_code="missing_metadata",
            detail="Attempt marker has no machine-readable metadata; attention required.",
        )
    meta_repo = str(metadata.get("repository") or "").strip().lower()
    try:
        meta_number = int(str(metadata.get("issueNumber") or "0").strip())
    except (TypeError, ValueError):
        meta_number = 0
    if (
        meta_repo != str(repository or "").strip().lower()
        or meta_number != int(issue_number)
    ):
        return AttemptValidation(
            outcome="rejected",
            reason_code="issue_mismatch",
            detail="Attempt envelope names a different repository/issue; rejected.",
        )
    if str(metadata.get("format") or "") != "moonmind-issue-attempt":
        return AttemptValidation(
            outcome="rejected",
            reason_code="unsupported_format",
            detail="Attempt envelope format is not recognized; attention required.",
        )
    if require_trusted_poster and trusted_posters is not None:
        trusted = {str(name).strip().lower() for name in trusted_posters if str(name).strip()}
        if trusted and parsed.author.strip().lower() not in trusted:
            return AttemptValidation(
                outcome="rejected",
                reason_code="untrusted_poster",
                detail=(
                    "Comment poster is not in the trusted GitHub identity set; "
                    "a copied machine marker is not authentication."
                ),
            )
    predecessor = normalize_attempt_id(metadata.get("predecessorAttemptId"))
    if predecessor and known_attempt_ids is not None:
        known = {normalize_attempt_id(item) for item in known_attempt_ids}
        known.discard("")
        if predecessor not in known:
            return AttemptValidation(
                outcome="rejected",
                reason_code="missing_predecessor",
                detail=(
                    "Predecessor attempt is not in the referenced GitHub history; "
                    "a fresh start is not inferred."
                ),
            )
    pr_url = str(metadata.get("pullRequestUrl") or "").strip()
    if pr_url and not _looks_like_github_pr_url(pr_url, repository=repository):
        return AttemptValidation(
            outcome="rejected",
            reason_code="invalid_reference",
            detail="Referenced pull request URL is inconsistent; attention required.",
        )
    activity = str(metadata.get("activity") or "")
    if activity and activity not in ATTEMPT_ACTIVITIES:
        return AttemptValidation(
            outcome="rejected",
            reason_code="unsupported_activity",
            detail=f"Unknown attempt activity {activity!r}; attention required.",
        )
    return AttemptValidation(
        outcome="usable",
        reason_code="usable",
        detail="Attempt comment validated against trusted provenance and schema.",
        usable=True,
    )


def _looks_like_github_pr_url(url: str, *, repository: str) -> bool:
    text = str(url or "").strip()
    match = re.match(
        r"^https://github\.com/([^/]+/[^/]+)/pull/(\d+)(?:[/?#].*)?$", text
    )
    if not match:
        return False
    return match.group(1).strip().lower() == str(repository or "").strip().lower()


# ---------------------------------------------------------------------------
# Requirement 4: per-attempt serialization, reconciliation, coalescing
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AttemptWritePlan:
    """Serialization decision for one attempt's own comment."""

    action: str
    comment_id: str = ""
    reason_code: str = ""
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "commentId": self.comment_id,
            "reasonCode": self.reason_code,
            "detail": self.detail,
        }


def select_own_comment(
    parsed: Sequence[ParsedAttemptComment],
    *,
    attempt_id: str,
) -> AttemptWritePlan:
    """Decide the single write target for this attempt's own comment.

    Duplicate same-ID comments are one logical attempt (update the earliest
    stable copy); conflicting copies with the same ID but divergent payloads
    require attention instead of last-timestamp-wins; another attempt's comment
    is never selected as a write target.
    """
    own = [item for item in parsed if item.attempt_id == normalize_attempt_id(attempt_id)]
    if not own:
        return AttemptWritePlan(
            action="create",
            reason_code="no_own_comment",
            detail="No existing comment carries this attempt ID; create one.",
        )
    payloads = {json.dumps(item.metadata, sort_keys=True) for item in own}
    if len(payloads) > 1:
        return AttemptWritePlan(
            action="attention",
            reason_code="conflicting_copies",
            detail=(
                "Multiple same-ID comments carry conflicting payloads; "
                "reconciliation is required instead of last-timestamp-wins."
            ),
        )
    earliest = sorted(own, key=lambda item: (item.created_at, item.comment_id))[0]
    return AttemptWritePlan(
        action="update",
        comment_id=earliest.comment_id,
        reason_code="own_comment_found",
        detail="Updating this attempt's own comment; other attempts untouched.",
    )


def reconcile_uncertain_create(
    parsed: Sequence[ParsedAttemptComment],
    *,
    attempt_id: str,
    create_outcome_unknown: bool,
) -> AttemptWritePlan:
    """Reconcile a create whose HTTP response was lost before retrying.

    A lost response is an unknown result, never proof of failure: reread by
    the stable attempt marker first and adopt the observed comment instead of
    blindly posting a duplicate.
    """
    if not create_outcome_unknown:
        return select_own_comment(parsed, attempt_id=attempt_id)
    plan = select_own_comment(parsed, attempt_id=attempt_id)
    if plan.action == "create":
        return AttemptWritePlan(
            action="blocked",
            reason_code="create_outcome_unknown",
            detail=(
                "Create result is unknown and no same-ID comment is observable; "
                "reread GitHub before repeating the create."
            ),
        )
    if plan.action == "update":
        return AttemptWritePlan(
            action="adopt",
            comment_id=plan.comment_id,
            reason_code="create_reconciled",
            detail="Adopted the already-created same-ID comment after response loss.",
        )
    return plan


def should_coalesce_progress(
    *,
    last_update_at: Any,
    now: Any | None = None,
    activity_changed: bool = False,
    terminal_update: bool = False,
) -> tuple[bool, str]:
    """Decide whether a progress report coalesces within rate limits."""
    if activity_changed or terminal_update:
        return False, "activity or terminal transition always publishes"
    last = _parse_moment(last_update_at)
    moment = _parse_moment(now) or _utcnow()
    if last is None:
        return False, "no previous update timestamp"
    elapsed = (moment - last).total_seconds()
    if elapsed < PROGRESS_COALESCE_SECONDS:
        return True, (
            f"progress coalesced: only {elapsed:.0f}s since last update "
            f"(limit {PROGRESS_COALESCE_SECONDS}s)"
        )
    return False, "coalesce window elapsed"


# ---------------------------------------------------------------------------
# Requirement 5: portable retry history surviving IDs/devices/labels
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RetryState:
    """Effective retry allowance derived from portable GitHub history."""

    failures: int
    no_progress_count: int
    holds: bool
    cancelled: bool
    allowance_remaining: int
    cooldown_until: str
    blocked: bool
    reason_code: str
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "failures": self.failures,
            "noProgressCount": self.no_progress_count,
            "holds": self.holds,
            "cancelled": self.cancelled,
            "allowanceRemaining": self.allowance_remaining,
            "cooldownUntil": self.cooldown_until,
            "blocked": self.blocked,
            "reasonCode": self.reason_code,
            "detail": self.detail,
        }


def _retry_entries(
    parsed: Sequence[ParsedAttemptComment],
) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for item in parsed:
        metadata = item.metadata or {}
        history = metadata.get("retryHistory")
        items = list(history) if isinstance(history, Sequence) and not isinstance(history, (str, bytes)) else []
        entries.append(
            {
                "attemptId": item.attempt_id,
                "outcome": str(metadata.get("outcome") or ""),
                "operatorHold": bool(metadata.get("operatorHold")),
                "resetRecord": str(metadata.get("resetRecord") or ""),
                "policyRef": str(metadata.get("policyRef") or ""),
                "history": [str(entry) for entry in items],
            }
        )
    return entries


def compute_retry_state(
    parsed: Sequence[ParsedAttemptComment],
    *,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    cooldown_seconds: int = DEFAULT_COOLDOWN_SECONDS,
    policy_ref: str = "",
    now: Any | None = None,
) -> RetryState:
    """Compute the effective retry allowance from linked GitHub history.

    New workflow IDs, device changes, and label clears never reset the count:
    every linked attempt contributes. Internal step retries stay inside the
    controlling attempt (they are not separate comments). Missing or
    incompatible policy lineage blocks automatic recovery; authorized resets
    require an audited ``resetRecord``; simultaneous races yield a bounded
    allowance, never an exact global counter claim.
    """
    moment = _parse_moment(now) or _utcnow()
    # Canonicalize duplicate same-ID comments to one logical attempt: keep
    # the latest observable copy per attempt ID so a retried create that left
    # two same-ID markers cannot exhaust the allowance twice.
    latest_by_id: dict[str, ParsedAttemptComment] = {}
    for item in parsed:
        if not item.attempt_id:
            continue
        key = normalize_attempt_id(item.attempt_id)
        if not key:
            continue
        current = latest_by_id.get(key)
        if current is None or (
            item.updated_at,
            item.created_at,
            item.comment_id,
        ) >= (current.updated_at, current.created_at, current.comment_id):
            latest_by_id[key] = item
    usable = sorted(
        latest_by_id.values(),
        key=lambda item: (item.created_at, item.updated_at, item.comment_id),
    )
    policy_refs = {
        str((item.metadata or {}).get("policyRef") or "") for item in usable
    }
    policy_refs.discard("")
    if len(policy_refs) > 1:
        return RetryState(
            failures=len(usable),
            no_progress_count=0,
            holds=False,
            cancelled=False,
            allowance_remaining=0,
            cooldown_until="",
            blocked=True,
            reason_code="incompatible_policy_lineage",
            detail="Conflicting policy lineage across linked attempts; attention required.",
        )
    if policy_ref and policy_refs and policy_ref not in policy_refs:
        return RetryState(
            failures=len(usable),
            no_progress_count=0,
            holds=False,
            cancelled=False,
            allowance_remaining=0,
            cooldown_until="",
            blocked=True,
            reason_code="incompatible_policy_lineage",
            detail="Portable history uses a different policy lineage; attention required.",
        )
    if not usable:
        return RetryState(
            failures=0,
            no_progress_count=0,
            holds=False,
            cancelled=False,
            allowance_remaining=max(0, int(max_attempts)),
            cooldown_until="",
            blocked=True,
            reason_code="missing_lineage",
            detail="No observable attempt history; automatic recovery is not claimed.",
        )
    failures = 0
    no_progress = 0
    holds = False
    cancelled = False
    latest_cooldown = ""
    latest_cooldown_moment = None
    for item in usable:
        metadata = item.metadata or {}
        # An audited reset/resolution record supersedes earlier holds and
        # failures at its lineage point; later failures/holds still count.
        if str(metadata.get("resetRecord") or "").strip():
            failures = 0
            no_progress = 0
            holds = False
            cancelled = False
        outcome = str(metadata.get("outcome") or "").strip().lower()
        if outcome in {"failed", "failure", "error", "no_progress", "no-progress"}:
            failures += 1
        if outcome in {"no_progress", "no-progress", "no_work", "no-work"}:
            no_progress += 1
        if bool(metadata.get("operatorHold")):
            holds = True
        if outcome in {"cancelled", "canceled"}:
            cancelled = True
        cooldown = str(metadata.get("cooldownUntil") or "").strip()
        if cooldown:
            cooldown_moment = _parse_moment(cooldown)
            if cooldown_moment is not None and (
                latest_cooldown_moment is None or cooldown_moment > latest_cooldown_moment
            ):
                latest_cooldown_moment = cooldown_moment
                latest_cooldown = cooldown
    if holds or cancelled:
        return RetryState(
            failures=failures,
            no_progress_count=no_progress,
            holds=holds,
            cancelled=cancelled,
            allowance_remaining=0,
            cooldown_until=latest_cooldown,
            blocked=True,
            reason_code="operator_hold" if holds else "cancelled",
            detail="Operator hold/cancellation evidence survives new IDs and devices.",
        )
    remaining = max(0, int(max_attempts) - failures)
    if latest_cooldown:
        cooldown_moment = _parse_moment(latest_cooldown)
        if cooldown_moment is not None and cooldown_moment > moment:
            return RetryState(
                failures=failures,
                no_progress_count=no_progress,
                holds=False,
                cancelled=False,
                allowance_remaining=remaining,
                cooldown_until=latest_cooldown,
                blocked=True,
                reason_code="cooldown_active",
                detail="Cooldown from portable history is still active.",
            )
    if remaining <= 0:
        return RetryState(
            failures=failures,
            no_progress_count=no_progress,
            holds=False,
            cancelled=False,
            allowance_remaining=0,
            cooldown_until=latest_cooldown,
            blocked=True,
            reason_code="retry_exhausted",
            detail="Retry allowance from linked GitHub history is exhausted.",
        )
    if failures:
        cooldown_until = (moment + timedelta(seconds=int(cooldown_seconds))).isoformat()
    else:
        cooldown_until = ""
    return RetryState(
        failures=failures,
        no_progress_count=no_progress,
        holds=False,
        cancelled=False,
        allowance_remaining=remaining,
        cooldown_until=cooldown_until,
        blocked=False,
        reason_code="retry_allowed",
        detail=(
            "Retry allowance derived from linked GitHub history; "
            "no exact global counter is claimed under races."
        ),
    )


def reconstruct_handoff(
    parsed: Sequence[ParsedAttemptComment],
    *,
    repository: str,
    issue_number: int,
) -> dict[str, Any]:
    """Reconstruct remaining work + retry restrictions from GitHub alone.

    Device B needs no private logs: the latest usable attempt contributes its
    remaining requirements, next action, preserved PR/branch/SHA, and the
    portable retry state computed above.
    """
    usable = [item for item in parsed if item.attempt_id and (item.metadata or {})]
    if not usable:
        return {
            "reconstructable": False,
            "reasonCode": "missing_lineage",
            "detail": "No usable attempt history is observable on GitHub.",
        }
    latest = sorted(
        usable, key=lambda item: (item.updated_at, item.created_at, item.comment_id)
    )[-1]
    metadata = latest.metadata or {}
    retry = compute_retry_state(parsed)
    return {
        "reconstructable": True,
        "repository": str(repository or ""),
        "issueNumber": int(issue_number),
        "latestAttemptId": latest.attempt_id,
        "latestCommentId": latest.comment_id,
        "deploymentId": latest.deployment_id,
        "activity": str(metadata.get("activity") or ""),
        "unmetRequirements": list(metadata.get("unmetRequirements") or []),
        "nextAction": str(metadata.get("nextAction") or ""),
        "pullRequestUrl": str(metadata.get("pullRequestUrl") or ""),
        "headSha": str(metadata.get("headSha") or ""),
        "headBranch": str(metadata.get("headBranch") or ""),
        "baseBranch": str(metadata.get("baseBranch") or ""),
        "savedBranch": str(metadata.get("savedBranch") or ""),
        "savedSha": str(metadata.get("savedSha") or ""),
        "retry": retry.to_dict(),
    }


# ---------------------------------------------------------------------------
# Requirement 6: proposed release vs completed release
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReleaseDecision:
    """Distinction between a proposed and a completed (released) disposition."""

    released: bool
    reason_code: str
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "released": self.released,
            "reasonCode": self.reason_code,
            "detail": self.detail,
        }


def decide_release(
    metadata: Mapping[str, Any],
    *,
    writers_stopped: bool,
    mutations_settled: bool,
    preservation_verified: bool,
    no_work_evidence: bool = False,
    label_outcome_observed: bool,
) -> ReleaseDecision:
    """Decide whether a terminal comment may record ``released``.

    The terminal comment may always record the intended next disposition
    (``pendingDisposition``); ``released`` additionally requires confirmed
    stopped writers, resolved shared mutations, verified preservation or
    explicit no-work evidence, and the observed label outcome. Terminal
    attempts never resume publication/cleanup merely on device reconnect:
    callers must route a ``released`` attempt to read-only observation.
    """
    _ = metadata
    if not writers_stopped:
        return ReleaseDecision(
            released=False,
            reason_code="writers_not_stopped",
            detail="Release proposed but writers are not confirmed stopped.",
        )
    if not mutations_settled:
        return ReleaseDecision(
            released=False,
            reason_code="mutations_unsettled",
            detail="Release proposed but shared mutations are unresolved.",
        )
    if not (preservation_verified or no_work_evidence):
        return ReleaseDecision(
            released=False,
            reason_code="preservation_unverified",
            detail="Release proposed but preservation/no-work evidence is missing.",
        )
    if not label_outcome_observed:
        return ReleaseDecision(
            released=False,
            reason_code="label_outcome_unobserved",
            detail="Release proposed but the label outcome was not observed.",
        )
    return ReleaseDecision(
        released=True,
        reason_code="released",
        detail="Terminal handoff released: stop, preservation, mutation, and label evidence all observed.",
    )


def is_terminal_released(metadata: Mapping[str, Any]) -> bool:
    """Return True when the handoff records a completed ``released`` state."""
    return (
        str(metadata.get("activity") or "") == ATTEMPT_ACTIVITY_RELEASED
        and str(metadata.get("pendingDisposition") or "released") == "released"
        and bool(metadata.get("writersStopped"))
    )


# ---------------------------------------------------------------------------
# Requirement 7: outbound scanning/redaction boundary
# ---------------------------------------------------------------------------


def redact_comment_text(text: str) -> str:
    """Redact secret-shaped content before it reaches a GitHub comment."""
    from moonmind.utils.logging import redact_sensitive_text

    return redact_sensitive_text(str(text or ""))


def scan_comment_text(
    text: str, *, location: str = "github.issue_attempt.comment"
) -> dict[str, Any]:
    """Scan one outbound comment through the existing outbound boundary."""
    from moonmind.security import scan_outbound_text

    result = scan_outbound_text(
        str(text or ""), location=location, high_security_mode=True
    )
    return {
        "allowed": bool(result.allowed),
        "decision": str(result.decision),
        "diagnostics": list(result.sanitized_diagnostics),
        "policyRef": "moonmind.security.outbound_scan.v1",
    }


def redact_structured_error(error: Mapping[str, Any] | None) -> dict[str, Any]:
    """Redact one structured error payload for GitHub-visible surfaces."""
    from moonmind.utils.logging import redact_sensitive_payload

    payload = dict(error or {})
    redacted = redact_sensitive_payload(payload)
    scan = scan_comment_text(
        json.dumps(payload, sort_keys=True, default=str)[:4000],
        location="github.issue_attempt.error",
    )
    if isinstance(redacted, Mapping):
        redacted = dict(redacted)
    else:
        redacted = {"value": redacted}
    redacted["outboundScan"] = scan
    return redacted


def build_safe_comment(handoff: AttemptHandoff) -> tuple[str, dict[str, Any]]:
    """Render a redacted, scanned, bounded comment plus scan evidence."""
    raw = render_attempt_comment(handoff)
    redacted = redact_comment_text(raw)
    scan = scan_comment_text(redacted)
    return redacted[:MAX_ATTEMPT_COMMENT_CHARS], scan


__all__ = [
    "ATTEMPT_ACTIVITIES",
    "ATTEMPT_ACTIVITY_ACTIVE",
    "ATTEMPT_ACTIVITY_ATTENTION",
    "ATTEMPT_ACTIVITY_AWAITING_REVIEW",
    "ATTEMPT_ACTIVITY_PREPARING",
    "ATTEMPT_ACTIVITY_RELEASING",
    "ATTEMPT_ACTIVITY_RELEASED",
    "ATTEMPT_COMMENT_VERSION",
    "ATTEMPT_FENCE_CLOSE",
    "ATTEMPT_FENCE_OPEN",
    "ATTEMPT_MARKER_PREFIX",
    "ATTEMPT_MARKER_SUFFIX",
    "ATTEMPT_NEXT_ACTIONS",
    "DEFAULT_COOLDOWN_SECONDS",
    "DEFAULT_INSTALLATION_ID_PATH",
    "LEGACY_INSTALLATION_ID_PATH",
    "DEFAULT_MAX_ATTEMPTS",
    "INSTALLATION_ID_ENV_VARS",
    "MAX_ATTEMPT_COMMENT_CHARS",
    "PROGRESS_COALESCE_SECONDS",
    "SUPPORTED_ATTEMPT_COMMENT_VERSIONS",
    "AttemptHandoff",
    "AttemptValidation",
    "AttemptWritePlan",
    "ParsedAttemptComment",
    "ReleaseDecision",
    "RetryState",
    "attempt_binding_key",
    "build_attempt_id",
    "build_safe_comment",
    "collect_attempt_comments",
    "compute_retry_state",
    "decide_release",
    "is_terminal_released",
    "normalize_attempt_id",
    "parse_attempt_comment",
    "reconcile_uncertain_create",
    "reconstruct_handoff",
    "redact_comment_text",
    "redact_structured_error",
    "render_attempt_comment",
    "resolve_installation_id",
    "scan_comment_text",
    "select_own_comment",
    "should_coalesce_progress",
    "validate_attempt_comment",
]
