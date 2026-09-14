"""Bounded ``@mm`` PR-command vocabulary on the authorized GitHub App path.

Issue MoonLadderStudios/MoonMind#763 owns parsing and command semantics for a
small explicit PR-command vocabulary. Transport (GitHub App connection,
signature verification, durable delivery, dispatch recovery) belongs to #3967;
this module is the hermetic command-semantics boundary that the transport
binds to. It performs no network I/O, runs no LLM parser, interpolates no
shell, and keeps no spelling-alias table.

Canonical vocabulary (exact, case-insensitive, whitespace-normalized)::

    @mm fix comments        -> fix-comments        (comment remediation)
    @mm fix merge conflicts -> fix-merge-conflicts (conflict remediation)
    @mm resolve             -> pr-resolver         (declared publication policy)

First-slice scope: newly created top-level comments on an opted-in PR only.
Inline review comments, edited comments, multiple commands, arbitrary
arguments, and cross-repository targets are unsupported and never dispatch.

Body rule (deterministic, fail-closed): the comment body must contain exactly
one non-empty line and that line must be the standalone command. Anything
else -- quoted/fenced/inline-code examples, prose-embedded mentions, hidden
HTML, unsupported suffixes, extra trailing prose, or ambiguous
multiple-command bodies -- is ignored (ordinary conversation) or answered
with bounded help (explicit but unknown ``@mm`` command). Unknown explicit
commands get bounded help; ordinary conversation is ignored.

Event, authorization, preflight, identity, revalidation, delegation, and
feedback decisions are pure functions so hermetic tests and the Temporal
workflow boundary can consume the same contract. Every gate that cannot
prove its precondition fails closed *before* paid execution, and no gate
searches for an alternate PAT/profile/runtime when the authorized
connection is unavailable.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Optional

__all__ = [
    "CANONICAL_COMMANDS",
    "COMMAND_SKILL_BINDINGS",
    "FEEDBACK_STATES",
    "AuthorizationDecision",
    "AuthorizationRequest",
    "CommandIdentity",
    "CompetingRunDecision",
    "FrozenCommandDispatch",
    "ParsedCommand",
    "PreflightDecision",
    "PreflightRequest",
    "RevalidationDecision",
    "SkillBinding",
    "build_feedback_body",
    "classify_feedback_write_outcome",
    "classify_redelivery",
    "command_identity",
    "evaluate_command_preflight",
    "evaluate_dispatch_authorization",
    "evaluate_event_eligibility",
    "feedback_triggers_bot",
    "freeze_command_dispatch",
    "normalize_command_line",
    "parse_pr_command",
    "redact_for_feedback",
    "resolve_competing_run",
    "resolve_skill_binding",
    "revalidate_before_mutation",
    "scan_for_secrets",
]

# ---------------------------------------------------------------------------
# Canonical vocabulary
# ---------------------------------------------------------------------------

#: Normalized command text -> portable Skill identity. The single source of
#: truth for the first-slice vocabulary; there is no alias table.
CANONICAL_COMMANDS: dict[str, str] = {
    "fix comments": "fix-comments",
    "fix merge conflicts": "fix-merge-conflicts",
    "resolve": "pr-resolver",
}


@dataclass(frozen=True, slots=True)
class SkillBinding:
    """Existing preset/Skill identity a canonical command dispatches to."""

    skill_id: str
    preset: str
    grants_merge_permission: bool = False
    requires_publication_evidence: bool = True


#: Normalized command text -> existing Skill binding. ``resolve`` maps to the
#: pr-resolver Skill under its declared publication policy; the command itself
#: never grants merge permission.
COMMAND_SKILL_BINDINGS: dict[str, SkillBinding] = {
    "fix comments": SkillBinding(
        skill_id="fix-comments", preset="fix-comments"
    ),
    "fix merge conflicts": SkillBinding(
        skill_id="fix-merge-conflicts", preset="fix-merge-conflicts"
    ),
    "resolve": SkillBinding(
        skill_id="pr-resolver",
        preset="pr-resolver",
        grants_merge_permission=False,
        requires_publication_evidence=True,
    ),
}

_COMMAND_RE = re.compile(r"^@mm\s+(.+?)\s*$", re.IGNORECASE)
_AT_MENTION_RE = re.compile(r"@mm", re.IGNORECASE)


def normalize_command_line(inner: str) -> str:
    """Collapse internal whitespace and lowercase a matched command inner."""
    return re.sub(r"\s+", " ", inner.strip()).lower()


@dataclass(frozen=True, slots=True)
class ParsedCommand:
    """Outcome of the deterministic body parser."""

    outcome: str  # "dispatch" | "help" | "ignore"
    normalized_command: str = ""
    skill_id: str = ""
    reason_code: str = ""


def parse_pr_command(body: Optional[str]) -> ParsedCommand:
    """Parse a comment body into a dispatch/help/ignore decision.

    Only an exactly-one-non-empty-line body whose line is a standalone
    canonical command dispatches. Explicit ``@mm`` lines that are not
    canonical yield ``help`` (bounded help, no work). Everything else --
    ordinary conversation, quoted/fenced/inline-code examples, hidden HTML,
    prose-embedded mentions, suffixes handled as unknown commands, extra
    trailing lines, multiple commands -- yields ``ignore``.
    """
    if body is None or not body.strip():
        return ParsedCommand(outcome="ignore", reason_code="empty_body")

    lines = body.splitlines()
    non_empty = [line for line in lines if line.strip()]

    if len(non_empty) != 1:
        mentions = sum(1 for line in non_empty if _AT_MENTION_RE.search(line))
        if mentions >= 2:
            return ParsedCommand(outcome="ignore", reason_code="multiple_commands")
        first = non_empty[0].strip() if non_empty else ""
        if _COMMAND_RE.match(first):
            # Standalone command first line but trailing content makes the
            # body ambiguous in the first slice: never guess.
            return ParsedCommand(outcome="ignore", reason_code="ambiguous_body")
        return ParsedCommand(outcome="ignore", reason_code="not_a_command")

    stripped = non_empty[0].strip()

    if stripped.lstrip().startswith(">"):
        return ParsedCommand(outcome="ignore", reason_code="quoted_command")
    if "```" in stripped or "`" in stripped:
        # Fenced blocks and inline-code spans are examples, not invocations.
        return ParsedCommand(outcome="ignore", reason_code="fenced_or_code_command")
    if "<!--" in stripped or "-->" in stripped:
        return ParsedCommand(outcome="ignore", reason_code="hidden_html_command")

    match = _COMMAND_RE.match(stripped)
    if match is None:
        if stripped.lower().startswith("@mm"):
            # Explicit "@mm" or "@mm <unknown...>" on its own line: bounded
            # help, never dispatch.
            return ParsedCommand(outcome="help", reason_code="unknown_command")
        if _AT_MENTION_RE.search(stripped):
            return ParsedCommand(outcome="ignore", reason_code="prose_embedded_mention")
        return ParsedCommand(outcome="ignore", reason_code="not_a_command")

    normalized = normalize_command_line(match.group(1))
    skill_id = CANONICAL_COMMANDS.get(normalized)
    if skill_id is None:
        # Unknown explicit command (including unsupported suffixes such as
        # "@mm fix comments please"): bounded help, no work.
        return ParsedCommand(
            outcome="help",
            normalized_command=normalized,
            reason_code="unknown_command",
        )
    return ParsedCommand(
        outcome="dispatch",
        normalized_command=normalized,
        skill_id=skill_id,
        reason_code="canonical_command",
    )


def resolve_skill_binding(normalized_command: str) -> Optional[SkillBinding]:
    """Return the existing Skill binding for a normalized command, if any."""
    return COMMAND_SKILL_BINDINGS.get(normalized_command)


# ---------------------------------------------------------------------------
# Event eligibility (first-slice transport shape)
# ---------------------------------------------------------------------------


def evaluate_event_eligibility(
    parsed: ParsedCommand,
    *,
    is_edited: bool = False,
    is_inline: bool = False,
    is_bot_actor: bool = False,
    trusted_automation_permitted: bool = False,
) -> tuple[bool, str]:
    """Decide whether a parsed command may proceed to authorization.

    Returns ``(eligible, reason_code)``. Edited comments, inline review
    comments, and bot/self feedback loops are unsupported in the first slice
    and never dispatch, unless a separately explicit trusted-automation
    policy permits the bot actor.
    """
    if parsed.outcome != "dispatch":
        return False, "no_dispatchable_command"
    if is_edited:
        return False, "edited_comment_unsupported"
    if is_inline:
        return False, "inline_comment_unsupported"
    if is_bot_actor and not trusted_automation_permitted:
        return False, "bot_self_loop"
    return True, "eligible"


# ---------------------------------------------------------------------------
# Authorization gate (#3967-bound, fails closed before paid execution)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AuthorizationRequest:
    """Current authorization facts for one verified command event."""

    transport_verified: bool = False
    repository_opted_in: bool = False
    actor_authorized: bool = False
    connection_available: bool = False
    budget_available: bool = False
    publication_allowed: bool = False


@dataclass(frozen=True, slots=True)
class AuthorizationDecision:
    allowed: bool
    reason_code: str


def evaluate_dispatch_authorization(
    request: AuthorizationRequest,
) -> AuthorizationDecision:
    """Gate dispatch on current transport + authorization + policy facts.

    A GitHub signature or collaborator comment alone is not spending
    authority: every fact must hold at dispatch time. This function never
    searches for an alternate PAT/profile/runtime; a missing authorized
    connection blocks with ``connection_unavailable``.
    """
    if not request.transport_verified:
        return AuthorizationDecision(False, "transport_unverified")
    if not request.repository_opted_in:
        return AuthorizationDecision(False, "repository_not_opted_in")
    if not request.actor_authorized:
        return AuthorizationDecision(False, "actor_not_authorized")
    if not request.connection_available:
        return AuthorizationDecision(False, "connection_unavailable")
    if not request.budget_available:
        return AuthorizationDecision(False, "budget_exceeded")
    if not request.publication_allowed:
        return AuthorizationDecision(False, "publication_blocked")
    return AuthorizationDecision(True, "authorized")


# ---------------------------------------------------------------------------
# Preflight: freeze Skill snapshot, head/base, and write authority
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PreflightRequest:
    skill_id: str = ""
    pr_base_ref: str = ""
    pr_base_sha: str = ""
    pr_head_sha: str = ""
    is_fork: bool = False
    fork_write_permitted: bool = False
    branch_write_authorized: bool = False
    permissions_revoked: bool = False
    skill_capability_supported: bool = True


@dataclass(frozen=True, slots=True)
class PreflightDecision:
    ready: bool
    reason_code: str
    merge_target_ref: str = ""


def evaluate_command_preflight(request: PreflightRequest) -> PreflightDecision:
    """Preflight write authority and Skill capability before paid execution.

    The merge target always derives from the PR's actual base ref -- never a
    silent ``origin/main`` substitution. A missing base, fork without write
    permission, revoked permission, missing branch-write authority, or an
    unsupported Skill capability blocks before mutation or spend.
    """
    if request.skill_id not in {"fix-comments", "fix-merge-conflicts", "pr-resolver"}:
        return PreflightDecision(False, "unsupported_skill_capability")
    if not request.skill_capability_supported:
        return PreflightDecision(False, "unsupported_skill_capability")
    if request.permissions_revoked:
        return PreflightDecision(False, "permission_revoked")
    if not request.pr_head_sha.strip():
        return PreflightDecision(False, "missing_head")
    base_ref = request.pr_base_ref.strip()
    if not base_ref or not request.pr_base_sha.strip():
        return PreflightDecision(False, "unsupported_missing_base")
    if request.is_fork and not request.fork_write_permitted:
        return PreflightDecision(False, "fork_write_unavailable")
    if not request.branch_write_authorized:
        return PreflightDecision(False, "branch_write_unavailable")
    return PreflightDecision(True, "ready", merge_target_ref=f"origin/{base_ref}")


# ---------------------------------------------------------------------------
# Stable command identity and redelivery classification
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CommandIdentity:
    identity_key: str
    comment_id: str
    content_digest: str


def command_identity(
    *,
    installation_id: str,
    repository: str,
    pr_number: int,
    comment_id: str,
    normalized_command: str,
    comment_body: str,
) -> CommandIdentity:
    """Build the stable idempotency identity for one command event.

    Scoped to the original event/comment plus the normalized command and a
    content digest of the raw body. Redelivery of the same comment body
    reuses the same key; a fresh deliberate comment is a new key; an edit
    changes the digest and must never silently reuse accepted work.
    Only this bounded normalized evidence (or authorized artifact refs
    derived from it) may be stored.
    """
    slug = re.sub(r"\s+", "-", normalized_command.strip().lower()) or "unknown"
    digest = hashlib.sha256(
        f"{normalized_command}\n{comment_body}".encode("utf-8")
    ).hexdigest()[:16]
    key = (
        f"github-pr-command:v1:{installation_id}:{repository}:"
        f"{pr_number}:{comment_id}:{slug}:{digest}"
    )
    return CommandIdentity(
        identity_key=key, comment_id=str(comment_id), content_digest=digest
    )


def classify_redelivery(
    *,
    stored_comment_id: str,
    stored_digest: str,
    incoming_comment_id: str,
    incoming_digest: str,
) -> str:
    """Classify an incoming delivery against stored command identity.

    Returns ``redelivery_reuse`` (same comment, same content: reuse the
    existing dispatch/result), ``new_request`` (different comment: a fresh
    deliberate request), or ``edited_unsupported`` (same comment, changed
    content: an edit must never silently change or repeat accepted work).
    """
    if str(incoming_comment_id) != str(stored_comment_id):
        return "new_request"
    if str(incoming_digest) == str(stored_digest):
        return "redelivery_reuse"
    return "edited_unsupported"


@dataclass(frozen=True, slots=True)
class FrozenCommandDispatch:
    """Frozen, inspectable dispatch record for one authorized command."""

    identity_key: str
    skill_id: str
    skill_snapshot_ref: str
    repository: str
    pr_number: int
    pr_head_sha: str
    pr_base_ref: str
    pr_base_sha: str
    merge_target_ref: str
    connection_id: str


def freeze_command_dispatch(
    *,
    identity_key: str,
    skill_id: str,
    skill_snapshot_ref: str,
    repository: str,
    pr_number: int,
    pr_head_sha: str,
    pr_base_ref: str,
    pr_base_sha: str,
    merge_target_ref: str,
    connection_id: str,
) -> FrozenCommandDispatch:
    """Freeze the resolved Skill snapshot, source, head/base, and connection."""
    return FrozenCommandDispatch(
        identity_key=identity_key,
        skill_id=skill_id,
        skill_snapshot_ref=skill_snapshot_ref,
        repository=repository,
        pr_number=pr_number,
        pr_head_sha=pr_head_sha,
        pr_base_ref=pr_base_ref,
        pr_base_sha=pr_base_sha,
        merge_target_ref=merge_target_ref,
        connection_id=connection_id,
    )


# ---------------------------------------------------------------------------
# Revalidation and competing-run resolution (single mutation owner)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RevalidationDecision:
    fresh: bool
    reason_code: str


def revalidate_before_mutation(
    *,
    frozen_head_sha: str,
    frozen_base_sha: str,
    frozen_actor_authorized: bool,
    current_head_sha: str,
    current_base_sha: str,
    current_actor_authorized: bool,
) -> RevalidationDecision:
    """Revalidate material head/base/authorization changes before mutation.

    A stale request (moved head/base or revoked authorization) blocks with
    an explicit reason; it must never silently become a new billable request
    against a different head.
    """
    if not current_actor_authorized or not frozen_actor_authorized:
        return RevalidationDecision(False, "authz_revoked")
    if current_head_sha != frozen_head_sha:
        return RevalidationDecision(False, "stale_head")
    if current_base_sha != frozen_base_sha:
        return RevalidationDecision(False, "stale_base")
    return RevalidationDecision(True, "fresh")


@dataclass(frozen=True, slots=True)
class CompetingRunDecision:
    result: str  # "reuse_active" | "queued" | "conflicting" | "none"
    reason_code: str


def resolve_competing_run(
    *,
    has_conflicting_run: bool = False,
    has_active_compatible_run: bool = False,
    has_queued_compatible: bool = False,
) -> CompetingRunDecision:
    """Surface an already-active/queued run or an explicit conflict.

    The command layer never invents another PR scheduler: at most one
    authorized mutation owner may write the PR branch.
    """
    if has_conflicting_run:
        return CompetingRunDecision("conflicting", "conflicting_run")
    if has_active_compatible_run:
        return CompetingRunDecision("reuse_active", "active_compatible_run")
    if has_queued_compatible:
        return CompetingRunDecision("queued", "queued_compatible_run")
    return CompetingRunDecision("none", "no_competing_run")


# ---------------------------------------------------------------------------
# Feedback: one idempotent ack/result comment, loop-safe and redacted
# ---------------------------------------------------------------------------

#: Supported safe feedback states surfaced in the ack/result comment.
FEEDBACK_STATES: tuple[str, ...] = (
    "queued",
    "running",
    "blocked",
    "terminal_success",
    "terminal_partial",
    "terminal_unavailable",
)

_SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("github_token", re.compile(r"\bghp_[A-Za-z0-9]{8,}")),
    ("github_pat", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{8,}")),
    ("github_server_token", re.compile(r"\bghs_[A-Za-z0-9]{8,}")),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_-]{10,}")),
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{10,}")),
    ("slack_token", re.compile(r"\bxox[bpas]-[A-Za-z0-9-]{6,}")),
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("token_assignment", re.compile(r"(?i)\btoken\s*=\s*\S+")),
    ("password_assignment", re.compile(r"(?i)\bpassword\s*=\s*\S+")),
)


def scan_for_secrets(text: str) -> list[str]:
    """Return the secret kinds detected in ``text`` (empty when clean)."""
    found: list[str] = []
    for kind, pattern in _SECRET_PATTERNS:
        if pattern.search(text or ""):
            found.append(kind)
    return found


def redact_for_feedback(text: str) -> str:
    """Redact secret-like spans and raw credential assignments for feedback."""
    redacted = text or ""
    for _, pattern in _SECRET_PATTERNS:
        redacted = pattern.sub("[redacted]", redacted)
    return redacted


def build_feedback_body(
    *,
    command_label: str,
    skill_id: str,
    state: str,
    workflow_ref: str,
    detail: str = "",
) -> str:
    """Build the safe ack/result comment body for one command dispatch.

    Contains the workflow link/ref and a safe state word; carries verified
    completion, partial failure, and unavailable evidence distinctly via
    ``state``. Never includes tokens, private diagnostics, or raw command
    context, and never contains an ``@mm`` command line so feedback cannot
    retrigger the bot.
    """
    safe_state = state if state in FEEDBACK_STATES else "blocked"
    safe_detail = redact_for_feedback(detail)[:500]
    lines = [
        f"MoonMind PR command `{command_label}` ({skill_id}): {safe_state}.",
        f"Workflow: {redact_for_feedback(workflow_ref)[:300]}",
    ]
    if safe_detail.strip():
        lines.append(f"Detail: {safe_detail.strip()}")
    return "\n".join(lines)


def feedback_triggers_bot(body: str) -> bool:
    """Return True if a feedback body could retrigger command dispatch."""
    return parse_pr_command(body).outcome == "dispatch"


def classify_feedback_write_outcome(succeeded: bool) -> str:
    """Classify a feedback post/update attempt as auxiliary-only evidence.

    Feedback-posting failures stay auxiliary: they can never duplicate work
    or overwrite the work outcome.
    """
    return "feedback_posted" if succeeded else "feedback_auxiliary_failure"
