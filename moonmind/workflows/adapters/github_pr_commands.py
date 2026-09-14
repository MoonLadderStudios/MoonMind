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

Execution-binding scope: this module owns the hermetic command-semantics
boundary (parse -> eligibility -> authorization -> preflight -> freeze ->
binding -> feedback). The GitHub App transport receiver, durable delivery
store, and Temporal dispatch/recovery workflow belong to #3967, which binds
to this contract as its handoff: #3967 supplies the verified-event facts
(coerced via :func:`verified_command_event_from_mapping`, the serialized
payload shape its activity wrapper receives), stores the stable
``identity_key`` as its durable idempotency key, transitions the command to
``queued`` feedback only after objective Temporal accept evidence is
recorded, reuses the existing dispatch/result on ``redelivery_reuse`` (via
:func:`classify_redelivery`), and re-runs :func:`revalidate_before_mutation`
plus :func:`resolve_competing_run` after any restart before mutation. No
durable store, network I/O, or workflow scheduler lives in this module by
design; :func:`handle_verified_command_event` is the single hermetic
journey entrypoint the transport/activity calls, and it reports
``dispatch_ready`` (never ``queued``) because it performs no scheduling.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Optional

__all__ = [
    "CANONICAL_COMMANDS",
    "COMMAND_SKILL_BINDINGS",
    "FEEDBACK_STATES",
    "AuthorizationDecision",
    "AuthorizationRequest",
    "CommandIdentity",
    "CommandJourneyResult",
    "CompetingRunDecision",
    "FrozenCommandDispatch",
    "ParsedCommand",
    "PreflightDecision",
    "PreflightRequest",
    "RevalidationDecision",
    "SkillBinding",
    "VerifiedCommandEvent",
    "build_feedback_body",
    "classify_feedback_write_outcome",
    "classify_redelivery",
    "command_identity",
    "evaluate_command_preflight",
    "evaluate_dispatch_authorization",
    "evaluate_event_eligibility",
    "feedback_triggers_bot",
    "freeze_command_dispatch",
    "handle_verified_command_event",
    "normalize_command_line",
    "parse_pr_command",
    "redact_for_feedback",
    "resolve_competing_run",
    "resolve_skill_binding",
    "revalidate_before_mutation",
    "scan_for_secrets",
    "verified_command_event_from_mapping",
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
    """Existing Skill identity a canonical command dispatches to."""

    skill_id: str
    grants_merge_permission: bool = False
    requires_publication_evidence: bool = True


#: Normalized command text -> existing Skill binding. ``resolve`` maps to the
#: pr-resolver Skill under its declared publication policy; the command itself
#: never grants merge permission. Bindings carry only the canonical
#: skill-name identity: task presets are identified by preset-slug through a
#: separate catalog (for example ``pr-review-resolve``), so no preset alias
#: is stored here.
COMMAND_SKILL_BINDINGS: dict[str, SkillBinding] = {
    "fix comments": SkillBinding(skill_id="fix-comments"),
    "fix merge conflicts": SkillBinding(skill_id="fix-merge-conflicts"),
    "resolve": SkillBinding(
        skill_id="pr-resolver",
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
    # Fail-closed capability evidence: the transport must affirmatively prove
    # a compatible runtime can execute the resolved Skill. An omitted flag is
    # unproven capability and blocks before paid execution.
    skill_capability_supported: bool = False


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
# Hermetic journey entrypoint (single production-boundary contract)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class VerifiedCommandEvent:
    """One transport-verified command event supplied by the #3967 owner.

    ``transport_verified`` lives inside :attr:`authorization`; every other
    fact is the current value the transport resolved at receipt time.
    ``workflow_ref`` is the inspectable run link the dispatcher assigned
    (or ``""`` when the dispatcher has not assigned one yet, in which case
    the stable ``identity_key`` is used as the ref). ``skill_snapshot_ref``
    and ``connection_id`` are frozen into the dispatch record unchanged.
    """

    comment_body: str = ""
    installation_id: str = ""
    repository: str = ""
    pr_number: int = 0
    comment_id: str = ""
    is_edited: bool = False
    is_inline: bool = False
    is_bot_actor: bool = False
    trusted_automation_permitted: bool = False
    authorization: AuthorizationRequest = AuthorizationRequest()
    pr_base_ref: str = ""
    pr_base_sha: str = ""
    pr_head_sha: str = ""
    is_fork: bool = False
    fork_write_permitted: bool = False
    branch_write_authorized: bool = False
    permissions_revoked: bool = False
    # Fail-closed capability evidence, mirroring PreflightRequest: the event
    # must carry affirmative proof that a compatible runtime can execute the
    # resolved Skill. An omitted flag blocks before paid execution.
    skill_capability_supported: bool = False
    skill_snapshot_ref: str = ""
    connection_id: str = ""
    workflow_ref: str = ""


def _mapping_str(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key, "")
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    raise ValueError(f"verified command event field {key!r} must be a string")


def _mapping_bool(payload: Mapping[str, Any], key: str) -> bool:
    value = payload.get(key, False)
    if isinstance(value, bool):
        return value
    raise ValueError(f"verified command event field {key!r} must be a boolean")


def _mapping_int(payload: Mapping[str, Any], key: str) -> int:
    value = payload.get(key, 0)
    if isinstance(value, bool):
        raise ValueError(f"verified command event field {key!r} must be an integer")
    if isinstance(value, int):
        return value
    raise ValueError(f"verified command event field {key!r} must be an integer")


def _mapping_authorization(payload: Mapping[str, Any]) -> AuthorizationRequest:
    raw = payload.get("authorization", {})
    if raw is None:
        raw = {}
    if not isinstance(raw, Mapping):
        raise ValueError("verified command event field 'authorization' must be a mapping")
    return AuthorizationRequest(
        transport_verified=_mapping_bool(raw, "transport_verified"),
        repository_opted_in=_mapping_bool(raw, "repository_opted_in"),
        actor_authorized=_mapping_bool(raw, "actor_authorized"),
        connection_available=_mapping_bool(raw, "connection_available"),
        budget_available=_mapping_bool(raw, "budget_available"),
        publication_allowed=_mapping_bool(raw, "publication_allowed"),
    )


def verified_command_event_from_mapping(
    payload: Mapping[str, Any],
) -> VerifiedCommandEvent:
    """Coerce a serialized verified-event payload into a VerifiedCommandEvent.

    This is the exact invocation shape the #3967 Temporal activity wrapper
    uses: it receives a JSON-compatible mapping (never keyword arguments) and
    must reach the hermetic journey without inventing authority. Unknown keys
    are ignored for forward compatibility; missing authorization or
    capability facts fail closed through the authorization and preflight
    gates (every default denies). A malformed field raises ``ValueError``
    before any paid work.
    """
    if not isinstance(payload, Mapping):
        raise ValueError("verified command event payload must be a mapping")
    return VerifiedCommandEvent(
        comment_body=_mapping_str(payload, "comment_body"),
        installation_id=_mapping_str(payload, "installation_id"),
        repository=_mapping_str(payload, "repository"),
        pr_number=_mapping_int(payload, "pr_number"),
        comment_id=_mapping_str(payload, "comment_id"),
        is_edited=_mapping_bool(payload, "is_edited"),
        is_inline=_mapping_bool(payload, "is_inline"),
        is_bot_actor=_mapping_bool(payload, "is_bot_actor"),
        trusted_automation_permitted=_mapping_bool(
            payload, "trusted_automation_permitted"
        ),
        authorization=_mapping_authorization(payload),
        pr_base_ref=_mapping_str(payload, "pr_base_ref"),
        pr_base_sha=_mapping_str(payload, "pr_base_sha"),
        pr_head_sha=_mapping_str(payload, "pr_head_sha"),
        is_fork=_mapping_bool(payload, "is_fork"),
        fork_write_permitted=_mapping_bool(payload, "fork_write_permitted"),
        branch_write_authorized=_mapping_bool(payload, "branch_write_authorized"),
        permissions_revoked=_mapping_bool(payload, "permissions_revoked"),
        skill_capability_supported=_mapping_bool(
            payload, "skill_capability_supported"
        ),
        skill_snapshot_ref=_mapping_str(payload, "skill_snapshot_ref"),
        connection_id=_mapping_str(payload, "connection_id"),
        workflow_ref=_mapping_str(payload, "workflow_ref"),
    )


@dataclass(frozen=True, slots=True)
class CommandJourneyResult:
    """Outcome of one hermetic command journey.

    ``outcome`` is ``"dispatch_ready"`` (frozen dispatch-ready record plus
    ``requested`` feedback: no scheduler has accepted the command yet),
    ``"help"`` (unknown explicit command: bounded help, no work),
    ``"ignored"`` (ordinary conversation or negative guard: no work), or
    ``"blocked"`` (eligible command stopped by a gate before paid work).
    Only ``"dispatch_ready"`` carries a ``dispatch`` record and a feedback
    body; every other outcome carries no dispatch and no false feedback. The
    durable scheduling owner transitions the command to ``queued`` feedback
    only after objective Temporal accept evidence is recorded.
    """

    outcome: str
    reason_code: str
    normalized_command: str = ""
    skill_id: str = ""
    identity_key: str = ""
    dispatch: Optional[FrozenCommandDispatch] = None
    feedback_body: str = ""


def handle_verified_command_event(event: VerifiedCommandEvent) -> CommandJourneyResult:
    """Run the full hermetic journey for one verified command event.

    Chains the existing boundary contracts in production order --
    parse -> eligibility -> authorization -> Skill binding -> preflight ->
    freeze -> requested feedback -- without I/O, scheduling, or Skill
    execution. The caller (#3967 transport / Temporal dispatch activity)
    resolves and runs the frozen Skill bundle, stores ``identity_key``
    durably, transitions the command to ``queued`` feedback only after
    objective Temporal accept evidence is recorded, and posts/maintains the
    feedback comment. This function never grants merge permission, never
    infers publication evidence, never claims scheduling, and never invents
    another scheduler.
    """
    parsed = parse_pr_command(event.comment_body)
    if parsed.outcome == "help":
        return CommandJourneyResult(
            outcome="help",
            reason_code=parsed.reason_code,
            normalized_command=parsed.normalized_command,
        )
    if parsed.outcome != "dispatch":
        return CommandJourneyResult(
            outcome="ignored", reason_code=parsed.reason_code
        )

    eligible, eligibility_reason = evaluate_event_eligibility(
        parsed,
        is_edited=event.is_edited,
        is_inline=event.is_inline,
        is_bot_actor=event.is_bot_actor,
        trusted_automation_permitted=event.trusted_automation_permitted,
    )
    if not eligible:
        return CommandJourneyResult(
            outcome="blocked",
            reason_code=eligibility_reason,
            normalized_command=parsed.normalized_command,
            skill_id=parsed.skill_id,
        )

    authz = evaluate_dispatch_authorization(event.authorization)
    if not authz.allowed:
        return CommandJourneyResult(
            outcome="blocked",
            reason_code=authz.reason_code,
            normalized_command=parsed.normalized_command,
            skill_id=parsed.skill_id,
        )

    binding = resolve_skill_binding(parsed.normalized_command)
    if binding is None:
        return CommandJourneyResult(
            outcome="blocked",
            reason_code="unsupported_skill_capability",
            normalized_command=parsed.normalized_command,
        )

    preflight = evaluate_command_preflight(
        PreflightRequest(
            skill_id=binding.skill_id,
            pr_base_ref=event.pr_base_ref,
            pr_base_sha=event.pr_base_sha,
            pr_head_sha=event.pr_head_sha,
            is_fork=event.is_fork,
            fork_write_permitted=event.fork_write_permitted,
            branch_write_authorized=event.branch_write_authorized,
            permissions_revoked=event.permissions_revoked,
            skill_capability_supported=event.skill_capability_supported,
        )
    )
    if not preflight.ready:
        return CommandJourneyResult(
            outcome="blocked",
            reason_code=preflight.reason_code,
            normalized_command=parsed.normalized_command,
            skill_id=binding.skill_id,
        )

    identity = command_identity(
        installation_id=event.installation_id,
        repository=event.repository,
        pr_number=event.pr_number,
        comment_id=event.comment_id,
        normalized_command=parsed.normalized_command,
        comment_body=event.comment_body,
    )
    frozen = freeze_command_dispatch(
        identity_key=identity.identity_key,
        skill_id=binding.skill_id,
        skill_snapshot_ref=event.skill_snapshot_ref,
        repository=event.repository,
        pr_number=event.pr_number,
        pr_head_sha=event.pr_head_sha,
        pr_base_ref=event.pr_base_ref,
        pr_base_sha=event.pr_base_sha,
        merge_target_ref=preflight.merge_target_ref,
        connection_id=event.connection_id,
    )
    feedback = build_feedback_body(
        command_label=f"@mm {parsed.normalized_command}",
        skill_id=binding.skill_id,
        state="requested",
        workflow_ref=event.workflow_ref or identity.identity_key,
    )
    return CommandJourneyResult(
        outcome="dispatch_ready",
        reason_code="dispatch_ready_awaiting_temporal_accept",
        normalized_command=parsed.normalized_command,
        skill_id=binding.skill_id,
        identity_key=identity.identity_key,
        dispatch=frozen,
        feedback_body=feedback,
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
    frozen_authorization: AuthorizationRequest,
    current_authorization: AuthorizationRequest,
    frozen_preflight: PreflightRequest,
    current_preflight: PreflightRequest,
) -> RevalidationDecision:
    """Re-run the complete authorization/preflight contract before mutation.

    Recovery (restart, redelivery, competing-run resume) must not continue
    under stale authority: repository opt-in, App connection availability,
    deployment budget, publication policy, fork/branch write permission, and
    Skill capability evidence can all be revoked without changing the PR SHAs
    or the original actor authorization. This re-evaluates the frozen and
    current authorization gate plus the current preflight gate, then rejects
    moved head/base or a changed Skill binding. Any failure blocks with the
    underlying contract reason; a stale request must never silently become a
    new billable request against different authority or a different head.
    """
    frozen_authz = evaluate_dispatch_authorization(frozen_authorization)
    if not frozen_authz.allowed:
        return RevalidationDecision(False, frozen_authz.reason_code)
    current_authz = evaluate_dispatch_authorization(current_authorization)
    if not current_authz.allowed:
        return RevalidationDecision(False, current_authz.reason_code)
    current_check = evaluate_command_preflight(current_preflight)
    if not current_check.ready:
        return RevalidationDecision(False, current_check.reason_code)
    if current_preflight.pr_head_sha != frozen_preflight.pr_head_sha:
        return RevalidationDecision(False, "stale_head")
    if (
        current_preflight.pr_base_sha != frozen_preflight.pr_base_sha
        or current_preflight.pr_base_ref.strip()
        != frozen_preflight.pr_base_ref.strip()
    ):
        return RevalidationDecision(False, "stale_base")
    if current_preflight.skill_id != frozen_preflight.skill_id:
        return RevalidationDecision(False, "stale_skill_binding")
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
#: ``requested`` is the only state this hermetic boundary emits on the accept
#: path: the dispatch-ready record is frozen but no scheduler has accepted it
#: yet. The durable scheduling owner (#3967) transitions to ``queued`` only
#: after objective Temporal accept evidence is recorded.
FEEDBACK_STATES: tuple[str, ...] = (
    "requested",
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
    ("atlassian_token", re.compile(r"\bATATT[A-Za-z0-9_-]{10,}")),
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


def _scan_feedback_inputs(*, workflow_ref: str, detail: str) -> tuple[bool, str]:
    """Decide whether feedback inputs must be withheld as credentials.

    Returns ``(blocked, block_detail)``. The canonical repository outbound
    scanner (``moonmind.security``) is the authority: any finding -- or any
    scanner failure -- blocks posting the detail instead of publishing
    best-effort redaction of arbitrary diagnostics. The local
    :func:`scan_for_secrets` shapes run as defense in depth so currently
    unscanned credential shapes still withhold detail. Only sanitized
    categories (never raw values) are returned in ``block_detail``.
    """
    raw = f"{workflow_ref or ''}\n{detail or ''}"
    try:
        from moonmind.security.outbound_scan import scan_outbound_text

        result = scan_outbound_text(
            raw,
            location="github_pr_commands.feedback",
            high_security_mode=True,
        )
    except Exception as exc:  # noqa: BLE001 - scanner failure must fail closed
        return True, f"scanner unavailable ({exc.__class__.__name__})"
    if not result.allowed:
        categories = sorted({finding.category for finding in result.findings})
        return True, "; ".join(categories) or "secret-like content detected"
    local_kinds = scan_for_secrets(raw)
    if local_kinds:
        return True, "; ".join(sorted(set(local_kinds)))
    return False, ""


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
    retrigger the bot. When ``detail`` or ``workflow_ref`` contains
    credential-like content, the detail is withheld as a blocked outcome
    instead of being published through redaction.
    """
    safe_state = state if state in FEEDBACK_STATES else "blocked"
    blocked, block_detail = _scan_feedback_inputs(
        workflow_ref=workflow_ref, detail=detail
    )
    if blocked:
        return "\n".join(
            [
                f"MoonMind PR command `{command_label}` ({skill_id}): blocked.",
                "Detail: Feedback withheld by outbound scan "
                f"({redact_for_feedback(block_detail)[:200]}).",
            ]
        )
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
