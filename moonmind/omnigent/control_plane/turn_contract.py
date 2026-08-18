"""Canonical session/turn ownership contract.

Source: MoonLadderStudios/MoonMind#3707 ([Omnigent control plane 6/11]
Unify continuations, remediation, checkpoints, and chat under canonical session
and turn ownership).

This module is the single typed contract that same-session continuation,
remediation, checkpoint recovery, native chat, steering, approval responses, and
linked branches all agree on. It builds on the #3703 canonical aggregates
(:mod:`moonmind.omnigent.control_plane.records`) and the #3702 reconciler
vocabulary; it does **not** reimplement provider data collection, comment/blocker
classification, or completion rules.

It is *pure domain*: no database, network, filesystem, Docker, artifact, logging,
telemetry, or Temporal dependency. It operates only on the frozen records defined
beside it, and it never mutates its inputs. The repository-backed executor that
applies these decisions atomically lives in
:mod:`moonmind.omnigent.control_plane.turn_service`.

Design invariants enforced here:

* Every provider message is one *turn attempt* with one of eight typed source
  kinds. Source kind affects authorization and policy, never the fundamental
  idempotency or observation model.
* Same-session work reuses one canonical session and one chat binding. New work
  is a new turn attempt or an explicit branch -- never another ambiguous session
  row.
* Attempt terminality, session terminality, remediation-loop terminality, and
  workflow terminality are distinct. No lifecycle infers another's terminality
  from a matching timestamp or shared provider id.
* Changed immutable dimensions fail closed with ``branch_required`` rather than
  silently mutating the existing session.
* Chat capability and read-only posture derive from the canonical session
  authority; an attempt row can never supersede chat authority by being newer.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from enum import Enum
from typing import Any, Optional

from .records import (
    ChatBindingAliasRecord,
    SessionRecord,
    TurnAttemptRecord,
    compute_digest,
)

# ---------------------------------------------------------------------------
# Closed vocabularies
# ---------------------------------------------------------------------------


class TurnSourceKind(str, Enum):
    """The eight source kinds that may create a turn attempt.

    Source kind steers authorization and policy only. It never changes the
    typed command path, the idempotency model, or the observation model.
    """

    INITIAL = "initial"
    REPOSITORY_CONTINUATION = "repository_continuation"
    REMEDIATION = "remediation"
    WORKFLOW_CHAT = "workflow_chat"
    STEERING = "steering"
    APPROVAL_RESPONSE = "approval_response"
    CHECKPOINT_RESUME = "checkpoint_resume"
    LINKED_BRANCH = "linked_branch"


#: Source kinds that allocate a *new* canonical session authority (and a new
#: chat binding). Everything else reuses the existing canonical session.
SESSION_ALLOCATING_SOURCE_KINDS: frozenset[TurnSourceKind] = frozenset(
    {TurnSourceKind.INITIAL, TurnSourceKind.LINKED_BRANCH}
)

#: Source kinds whose turn attempt delivers caller/authored instruction content
#: to the provider and therefore requires the outbound security scan. An
#: approval response resolves an elicitation (a control), so it carries no
#: outbound instruction payload to scan.
OUTBOUND_SCAN_SOURCE_KINDS: frozenset[TurnSourceKind] = frozenset(
    {
        TurnSourceKind.INITIAL,
        TurnSourceKind.REPOSITORY_CONTINUATION,
        TurnSourceKind.REMEDIATION,
        TurnSourceKind.WORKFLOW_CHAT,
        TurnSourceKind.STEERING,
        TurnSourceKind.CHECKPOINT_RESUME,
        TurnSourceKind.LINKED_BRANCH,
    }
)

#: Map a source kind to the durable ``lineage_kind`` stored on the turn attempt
#: (#3703 vocabulary: ``initial`` / ``instruction`` / ``continuation``). The
#: richer source-kind concept is carried alongside in command/decision journals;
#: the lineage column stays within its existing closed vocabulary so no schema
#: migration is required.
_LINEAGE_KIND_FOR_SOURCE: dict[TurnSourceKind, str] = {
    TurnSourceKind.INITIAL: "initial",
    TurnSourceKind.LINKED_BRANCH: "initial",
    TurnSourceKind.REPOSITORY_CONTINUATION: "continuation",
    TurnSourceKind.REMEDIATION: "continuation",
    TurnSourceKind.WORKFLOW_CHAT: "instruction",
    TurnSourceKind.STEERING: "instruction",
    TurnSourceKind.APPROVAL_RESPONSE: "instruction",
    TurnSourceKind.CHECKPOINT_RESUME: "continuation",
}


class TurnCommandStep(str, Enum):
    """The one typed command path every provider message flows through."""

    CREATE_TURN_ATTEMPT = "create_turn_attempt"
    VALIDATE_AUTHORITY = "validate_immutable_session_and_caller_authority"
    OUTBOUND_SECURITY_SCAN = "outbound_security_scan"
    PREPARE_DIGEST = "prepare_digest_and_idempotency_marker"
    CLAIM_SUBMIT_COMMAND = "claim_fenced_submit_command"
    SUBMIT_OR_RECONCILE = "submit_or_reconcile_delivery_unknown"
    OBSERVE_PROVIDER_TURN = "observe_provider_turn"
    RECORD_TERMINAL_EVIDENCE = "record_terminal_attempt_evidence"


class RecoveryMode(str, Enum):
    """One typed decision boundary for checkpoint recovery and branching."""

    LIVE_REATTACH = "live_reattach"
    COLD_RESTORE = "cold_restore"
    BRANCH_REQUIRED = "branch_required"
    RESUME_UNAVAILABLE = "resume_unavailable"


class ChatCapability(str, Enum):
    """Browser-visible capability derived from canonical session authority."""

    READ_WRITE = "read_write"
    READ_ONLY = "read_only"
    UNAVAILABLE = "unavailable"


class Lifecycle(str, Enum):
    """The lifecycles whose terminal meanings must stay distinct (#3707)."""

    WORKFLOW_EXECUTION = "workflow_execution"
    STEP_EXECUTION = "step_execution"
    AGENT_RUN = "agent_run"
    OMNIGENT_SESSION = "omnigent_session"
    TURN_ATTEMPT = "turn_attempt"
    REMEDIATION_LOOP = "remediation_loop"
    CHECKPOINT_BRANCH = "checkpoint_branch"
    CHAT_BINDING = "chat_binding"
    HOST_LEASE = "host_lease"
    PROVIDER_PROFILE_LEASE = "provider_profile_lease"


@dataclass(frozen=True)
class LifecycleOwnership:
    """Owner and terminal meaning for one lifecycle."""

    lifecycle: Lifecycle
    owner: str
    terminal_meaning: str


#: The canonical lifecycle-ownership matrix. It is enforceable data, not prose:
#: consumers assert against it so no lifecycle infers another's terminality from
#: a matching timestamp or shared provider id.
LIFECYCLE_OWNERSHIP_MATRIX: tuple[LifecycleOwnership, ...] = (
    LifecycleOwnership(
        Lifecycle.WORKFLOW_EXECUTION,
        "MoonMind.UserWorkflow",
        "Product workflow completed",
    ),
    LifecycleOwnership(
        Lifecycle.STEP_EXECUTION,
        "Step ledger",
        "This logical Step execution completed",
    ),
    LifecycleOwnership(
        Lifecycle.AGENT_RUN,
        "MoonMind.AgentRun",
        "One agent execution contract completed",
    ),
    LifecycleOwnership(
        Lifecycle.OMNIGENT_SESSION,
        "MoonMind.OmnigentSession",
        "Provider session is no longer active or resumable under policy",
    ),
    LifecycleOwnership(
        Lifecycle.TURN_ATTEMPT,
        "Session workflow",
        "One submitted instruction reached a terminal outcome",
    ),
    LifecycleOwnership(
        Lifecycle.REMEDIATION_LOOP,
        "Remediation controller",
        "Candidate passed, policy exhausted, or intervention required",
    ),
    LifecycleOwnership(
        Lifecycle.CHECKPOINT_BRANCH,
        "Branch workflow/ledger",
        "Branch head reached a durable outcome",
    ),
    LifecycleOwnership(
        Lifecycle.CHAT_BINDING,
        "Canonical session authority",
        "Caller can read or mutate the canonical session",
    ),
    LifecycleOwnership(
        Lifecycle.HOST_LEASE,
        "Host manager",
        "Host realization is no longer reserved",
    ),
    LifecycleOwnership(
        Lifecycle.PROVIDER_PROFILE_LEASE,
        "Profile manager",
        "Credential consumer is gone and capacity is releasable",
    ),
)


#: Canonical terminal provider-session states. Final session terminality flips a
#: chat binding to read-only. This is the single vocabulary consumers share
#: instead of redefining a terminal set per module.
CANONICAL_TERMINAL_SESSION_STATES: frozenset[str] = frozenset(
    {"completed", "failed", "canceled", "cancelled", "timed_out", "stopped"}
)

# Canonical cleanup lifecycle states for the shared session ``cleanup_state``.
CLEANUP_PENDING = "pending"
CLEANUP_FENCED = "fenced"
CLEANUP_IN_PROGRESS = "in_progress"
CLEANUP_COMPLETE = "complete"
CLEANUP_RELEASED = "released"

#: Cleanup states past which the canonical provider authority has been torn down.
#: A same-session continuation submitted at or after these states must branch or
#: open a new session rather than resurrect deleted provider authority.
TERMINAL_CLEANUP_STATES: frozenset[str] = frozenset(
    {CLEANUP_COMPLETE, CLEANUP_RELEASED}
)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class TurnContractError(RuntimeError):
    """Base class for canonical turn/session contract violations."""


class BranchRequiredError(TurnContractError):
    """A reuse turn changed one or more immutable session dimensions.

    Rather than silently mutating the existing canonical session, the caller
    must open a new branch / new session authority.
    """

    def __init__(self, session_id: str, changed_dimensions: tuple[str, ...]) -> None:
        self.session_id = session_id
        self.changed_dimensions = tuple(changed_dimensions)
        super().__init__(
            f"Session {session_id!r} requires a branch: immutable dimensions "
            f"changed {sorted(self.changed_dimensions)}; reuse is not permitted "
            "(open a linked_branch / new session instead)"
        )


class CallerAuthorityError(TurnContractError):
    """The caller/controller is not authorized to submit this turn."""


class SessionTerminalError(TurnContractError):
    """A reuse turn targeted a session whose provider authority is gone.

    A continuation after terminal cleanup requires an explicit linked-branch or
    new-session policy, never resurrection of deleted provider authority.
    """


class RemediationAuthorityError(TurnContractError):
    """A remediation turn attempted to broaden execution authority."""


class CleanupFenceError(TurnContractError):
    """A cleanup / lease-release operation collided with active work, or a
    continuation was admitted after terminal cleanup."""


# ---------------------------------------------------------------------------
# Immutable session dimensions
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ImmutableSessionDimensions:
    """The immutable execution dimensions a same-session reuse must preserve.

    Two dimension sets are considered to *differ* on a field only when both
    sides carry a concrete value and those values are not equal. A field that is
    unknown (``None``) on either side is not treated as a change, so missing
    telemetry never forces a spurious branch; authority-sensitive changes
    (profile, workspace, repository, branch) still fail closed the moment a
    concrete new value contradicts the session's own value.
    """

    provider: Optional[str] = None
    model: Optional[str] = None
    compatibility_profile: Optional[str] = None
    provider_profile_id: Optional[str] = None
    policy_ref: Optional[str] = None
    image_manifest_ref: Optional[str] = None
    compatibility_ref: Optional[str] = None
    repository: Optional[str] = None
    branch: Optional[str] = None
    workspace_ref: Optional[str] = None
    intent_digest: Optional[str] = None

    def changed_from(self, other: "ImmutableSessionDimensions") -> tuple[str, ...]:
        """Return the names of dimensions that concretely changed vs ``other``."""

        changed: list[str] = []
        for f in fields(self):
            mine = getattr(self, f.name)
            theirs = getattr(other, f.name)
            if mine is not None and theirs is not None and mine != theirs:
                changed.append(f.name)
        return tuple(changed)

    @classmethod
    def from_session(cls, session: SessionRecord) -> "ImmutableSessionDimensions":
        """Extract the immutable dimensions recorded on a canonical session."""

        meta = session.metadata or {}
        return cls(
            provider=session.provider,
            model=meta.get("model"),
            compatibility_profile=session.compatibility_profile,
            provider_profile_id=session.provider_profile_id,
            policy_ref=meta.get("policy_ref"),
            image_manifest_ref=session.image_manifest_ref,
            compatibility_ref=session.compatibility_ref,
            repository=meta.get("repository"),
            branch=meta.get("branch"),
            workspace_ref=meta.get("workspace_ref"),
            intent_digest=session.intent_digest,
        )

    def as_session_metadata(self) -> dict[str, Any]:
        """Dimensions that live in session metadata (not first-class columns)."""

        out: dict[str, Any] = {}
        for name in ("model", "policy_ref", "repository", "branch", "workspace_ref"):
            value = getattr(self, name)
            if value is not None:
                out[name] = value
        return out


# ---------------------------------------------------------------------------
# Remediation turn intent
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RemediationTurnIntent:
    """Typed remediation-turn intent submitted by the remediation controller.

    The session workflow owns provider turn submission; the remediation
    controller owns whether another attempt is admitted. Neither may fabricate
    the other's result. The remediator may never broaden the base session's
    profile, workspace, or publication authority.
    """

    loop_id: str
    remediation_attempt_ordinal: int
    of_turn_attempt_id: str
    gate_result_ref: str
    remaining_work_ref: str
    candidate_workspace_ref: str
    remediator_skill: str
    runtime_authority_ref: str
    production_boundary_evidence_ref: str
    attempt_budget: int
    branch_budget: int
    verification_requirements: tuple[str, ...] = ()
    candidate_checkpoint_ref: Optional[str] = None
    allow_same_session_reuse: bool = True
    grants_publication_authority: bool = False
    granted_dimensions: ImmutableSessionDimensions = field(
        default_factory=ImmutableSessionDimensions
    )


def validate_remediation_authority(
    *,
    base_dimensions: ImmutableSessionDimensions,
    intent: RemediationTurnIntent,
    base_grants_publication_authority: bool = False,
) -> None:
    """Fail closed if a remediation turn would broaden execution authority.

    A remediator may reuse or *narrow* the base session's profile/workspace, and
    it may never grant itself publication authority the base session did not have.
    """

    if intent.attempt_budget <= 0 or intent.branch_budget <= 0:
        raise RemediationAuthorityError(
            f"Remediation loop {intent.loop_id!r} must carry positive attempt and "
            f"branch budgets (got attempt={intent.attempt_budget}, "
            f"branch={intent.branch_budget})"
        )
    if intent.remediation_attempt_ordinal > intent.attempt_budget:
        raise RemediationAuthorityError(
            f"Remediation loop {intent.loop_id!r} attempt ordinal "
            f"{intent.remediation_attempt_ordinal} exceeds attempt budget "
            f"{intent.attempt_budget}"
        )
    for ref_name in ("gate_result_ref", "remaining_work_ref"):
        if not getattr(intent, ref_name):
            raise RemediationAuthorityError(
                f"Remediation loop {intent.loop_id!r} is missing required "
                f"durable evidence {ref_name!r}"
            )
    if not intent.production_boundary_evidence_ref:
        raise RemediationAuthorityError(
            f"Remediation loop {intent.loop_id!r} is missing required "
            "production-boundary evidence"
        )

    granted = intent.granted_dimensions
    for authority_field in ("provider_profile_id", "workspace_ref"):
        base_value = getattr(base_dimensions, authority_field)
        granted_value = getattr(granted, authority_field)
        if (
            granted_value is not None
            and base_value is not None
            and granted_value != base_value
        ):
            raise RemediationAuthorityError(
                f"Remediation loop {intent.loop_id!r} may not broaden "
                f"{authority_field}: base={base_value!r} granted={granted_value!r}"
            )
    if intent.grants_publication_authority and not base_grants_publication_authority:
        raise RemediationAuthorityError(
            f"Remediation loop {intent.loop_id!r} may not grant itself "
            "publication authority the base session does not hold"
        )


# ---------------------------------------------------------------------------
# Turn submission request + plan
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TurnSubmissionRequest:
    """Everything needed to plan and submit one turn attempt."""

    session_id: str
    source_kind: TurnSourceKind
    caller_id: str
    instruction_digest: str
    controller_id: Optional[str] = None
    requested_dimensions: ImmutableSessionDimensions = field(
        default_factory=ImmutableSessionDimensions
    )
    remediation: Optional[RemediationTurnIntent] = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TurnSubmissionPlan:
    """The typed command path a request resolves to (no side effects)."""

    session_id: str
    source_kind: TurnSourceKind
    reuses_session: bool
    reuses_chat_binding: bool
    allocates_new_session: bool
    lineage_kind: str
    requires_outbound_scan: bool
    steps: tuple[TurnCommandStep, ...]
    idempotency_scope: str


def idempotency_scope_for(request: TurnSubmissionRequest) -> str:
    """Deterministic idempotency scope for a turn attempt.

    Identical logical work (same session, source kind, instruction, and -- for
    remediation -- loop/ordinal) collapses to the same scope, so a redelivered
    continuation cannot duplicate a turn.
    """

    parts: dict[str, Any] = {
        "session_id": request.session_id,
        "source_kind": request.source_kind.value,
        "instruction_digest": request.instruction_digest,
    }
    if request.remediation is not None:
        parts["loop_id"] = request.remediation.loop_id
        parts["ordinal"] = request.remediation.remediation_attempt_ordinal
    return compute_digest(parts)


def plan_turn_submission(
    request: TurnSubmissionRequest,
    session: Optional[SessionRecord],
) -> TurnSubmissionPlan:
    """Resolve a request to its typed command path, or fail closed.

    Raises:
        BranchRequiredError: a reuse turn changed an immutable dimension.
        SessionTerminalError: a reuse turn targeted a session whose provider
            authority is terminal or cleaned up.
        CallerAuthorityError: identity/session preconditions are not met.
    """

    kind = request.source_kind
    allocates_new_session = kind in SESSION_ALLOCATING_SOURCE_KINDS

    if allocates_new_session:
        # A brand-new session (initial) or an explicit branch gets its own
        # canonical session row and its own chat binding.
        reuses_session = False
        reuses_chat_binding = False
    else:
        if session is None:
            raise CallerAuthorityError(
                f"{kind.value} turn requires an existing canonical session "
                f"{request.session_id!r}"
            )
        if session.session_id != request.session_id:
            raise CallerAuthorityError(
                f"Session identity mismatch: request {request.session_id!r} != "
                f"{session.session_id!r}"
            )
        if session.is_terminal or session.cleanup_state in TERMINAL_CLEANUP_STATES:
            raise SessionTerminalError(
                f"Session {request.session_id!r} is terminal "
                f"(terminal_state={session.terminal_state!r}, "
                f"cleanup_state={session.cleanup_state!r}); a continuation must "
                "open a linked_branch or a new session"
            )
        changed = request.requested_dimensions.changed_from(
            ImmutableSessionDimensions.from_session(session)
        )
        if changed:
            raise BranchRequiredError(request.session_id, changed)
        reuses_session = True
        reuses_chat_binding = True

    requires_scan = kind in OUTBOUND_SCAN_SOURCE_KINDS
    steps: list[TurnCommandStep] = [
        TurnCommandStep.CREATE_TURN_ATTEMPT,
        TurnCommandStep.VALIDATE_AUTHORITY,
    ]
    if requires_scan:
        steps.append(TurnCommandStep.OUTBOUND_SECURITY_SCAN)
    steps.extend(
        (
            TurnCommandStep.PREPARE_DIGEST,
            TurnCommandStep.CLAIM_SUBMIT_COMMAND,
            TurnCommandStep.SUBMIT_OR_RECONCILE,
            TurnCommandStep.OBSERVE_PROVIDER_TURN,
            TurnCommandStep.RECORD_TERMINAL_EVIDENCE,
        )
    )

    return TurnSubmissionPlan(
        session_id=request.session_id,
        source_kind=kind,
        reuses_session=reuses_session,
        reuses_chat_binding=reuses_chat_binding,
        allocates_new_session=allocates_new_session,
        lineage_kind=_LINEAGE_KIND_FOR_SOURCE[kind],
        requires_outbound_scan=requires_scan,
        steps=tuple(steps),
        idempotency_scope=idempotency_scope_for(request),
    )


# ---------------------------------------------------------------------------
# Checkpoint recovery / branch decision
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RecoveryEvidence:
    """Evidence for the unified recovery decision boundary.

    ``intent_dimensions`` vs ``session_dimensions`` gate branch-required.
    Live-reattach authority (profile lease, host, provider session, cursor,
    first-message, credential generation) is durable authority, never inferred
    from a shared provider id. Cold-restore is artifact-backed and does not
    trust any stale host-local path.
    """

    intent_dimensions: ImmutableSessionDimensions
    session_dimensions: ImmutableSessionDimensions
    provider_profile_lease_current: bool = False
    host_available: bool = False
    provider_session_reachable: bool = False
    cursor_present: bool = False
    first_message_consistent: bool = False
    credential_generation_current: bool = False
    workspace_artifact_valid: bool = False
    session_evidence_valid: bool = False


@dataclass(frozen=True)
class RecoveryDecision:
    """The single output of :func:`decide_recovery`."""

    mode: RecoveryMode
    reason: str
    changed_dimensions: tuple[str, ...] = ()


def decide_recovery(evidence: RecoveryEvidence) -> RecoveryDecision:
    """Pick exactly one recovery mode from evidence, fail-closed.

    Ordering: an immutable-input change forces ``branch_required`` first; then
    complete live authority yields ``live_reattach``; then artifact-backed
    workspace + session evidence yields ``cold_restore``; otherwise resume is
    unavailable.
    """

    changed = evidence.intent_dimensions.changed_from(evidence.session_dimensions)
    if changed:
        return RecoveryDecision(
            RecoveryMode.BRANCH_REQUIRED,
            "immutable_input_changed",
            tuple(changed),
        )

    live_ok = all(
        (
            evidence.provider_profile_lease_current,
            evidence.host_available,
            evidence.provider_session_reachable,
            evidence.cursor_present,
            evidence.first_message_consistent,
            evidence.credential_generation_current,
        )
    )
    if live_ok:
        return RecoveryDecision(RecoveryMode.LIVE_REATTACH, "live_authority_complete")

    if evidence.workspace_artifact_valid and evidence.session_evidence_valid:
        return RecoveryDecision(
            RecoveryMode.COLD_RESTORE, "artifact_backed_evidence_valid"
        )

    return RecoveryDecision(
        RecoveryMode.RESUME_UNAVAILABLE, "no_recoverable_authority_or_evidence"
    )


# ---------------------------------------------------------------------------
# Chat capability derivation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ChatCapabilityDecision:
    """Browser-visible chat capability derived from canonical authority."""

    capability: ChatCapability
    read_only: bool
    session_id: Optional[str]
    unavailable_reason: Optional[str] = None
    historical_read_available: bool = False


def derive_chat_capability(
    *,
    alias: Optional[ChatBindingAliasRecord],
    session: Optional[SessionRecord],
    caller_authorized: bool,
    active_turn: Optional[TurnAttemptRecord] = None,
) -> ChatCapabilityDecision:
    """Derive chat capability strictly from the canonical session authority.

    The chat binding belongs only to the canonical session. An attempt row can
    never supersede chat authority by being newer: ``active_turn`` is accepted
    for context only and its terminality never downgrades write capability while
    the session itself remains active. Final *session* terminality flips the
    binding to read-only, and historical diagnostic reads survive host/provider
    cleanup.
    """

    if alias is None or not alias.resolves:
        reason = "chat_binding_unresolved"
        if alias is not None and alias.diagnostic_reason:
            reason = alias.diagnostic_reason
        return ChatCapabilityDecision(
            ChatCapability.UNAVAILABLE, True, None, reason, False
        )

    if session is None:
        return ChatCapabilityDecision(
            ChatCapability.UNAVAILABLE, True, alias.session_id, "session_missing", False
        )

    # Once a canonical session has existed, its transcript and evidence remain
    # readable even after provider/host/workspace cleanup.
    historical = True

    if not caller_authorized:
        return ChatCapabilityDecision(
            ChatCapability.UNAVAILABLE,
            True,
            session.session_id,
            "caller_not_authorized",
            historical,
        )

    session_terminal = (
        session.is_terminal
        or (session.terminal_state in CANONICAL_TERMINAL_SESSION_STATES)
    )
    cleaned_up = (
        session.cleanup_state in TERMINAL_CLEANUP_STATES
        or session.historical_read_state != "live"
    )

    if session_terminal or cleaned_up:
        reason = "session_terminal" if session_terminal else "session_cleaned_up"
        return ChatCapabilityDecision(
            ChatCapability.READ_ONLY,
            True,
            session.session_id,
            reason,
            historical,
        )

    # Session is active: an individual terminal attempt does not close the chat.
    return ChatCapabilityDecision(
        ChatCapability.READ_WRITE,
        False,
        session.session_id,
        None,
        historical,
    )


# ---------------------------------------------------------------------------
# Cleanup coordination / fencing
# ---------------------------------------------------------------------------


class CleanupOperation(str, Enum):
    """Operations that tear down or release canonical session authority."""

    SESSION_STOP = "session_stop"
    HOST_CLEANUP = "host_cleanup"
    PROVIDER_PROFILE_RELEASE = "provider_profile_release"
    REPOSITORY_PUBLICATION = "repository_publication"
    JANITOR_RECOVERY = "janitor_recovery"


@dataclass(frozen=True)
class LifecycleActivity:
    """Snapshot of the concurrent work that fences cleanup."""

    active_turn: bool = False
    remediation_admitted: bool = False
    linked_continuation_active: bool = False
    publication_in_progress: bool = False

    @property
    def any_active(self) -> bool:
        return (
            self.active_turn
            or self.remediation_admitted
            or self.linked_continuation_active
            or self.publication_in_progress
        )


class CleanupDisposition(str, Enum):
    ADMIT = "admit"
    FENCE = "fence"


@dataclass(frozen=True)
class CleanupDecision:
    disposition: CleanupDisposition
    reason: str


def evaluate_cleanup_admission(
    *,
    operation: CleanupOperation,
    activity: LifecycleActivity,
) -> CleanupDecision:
    """Decide whether a cleanup/release operation may proceed.

    An accepted new turn (or admitted remediation, linked continuation, or
    in-progress publication) fences incompatible teardown until it settles.
    Janitor recovery only reclaims genuinely idle authority, so it too is fenced
    while any work is active. Repository publication is compatible with an active
    turn (it is that turn's own side effect) and is never fenced here.
    """

    if operation is CleanupOperation.REPOSITORY_PUBLICATION:
        return CleanupDecision(CleanupDisposition.ADMIT, "publication_is_turn_effect")

    if activity.any_active:
        return CleanupDecision(
            CleanupDisposition.FENCE, "active_work_present"
        )
    return CleanupDecision(CleanupDisposition.ADMIT, "no_active_work")


def admit_continuation(
    *,
    session: SessionRecord,
    activity: LifecycleActivity,
) -> None:
    """Fail closed if a same-session continuation cannot be safely admitted.

    A continuation submitted at or after terminal cleanup must not resurrect
    deleted provider authority; it requires an explicit linked-branch or a new
    session instead.
    """

    if session.is_terminal or session.cleanup_state in TERMINAL_CLEANUP_STATES:
        raise CleanupFenceError(
            f"Session {session.session_id!r} has reached terminal cleanup "
            f"(terminal_state={session.terminal_state!r}, "
            f"cleanup_state={session.cleanup_state!r}); a continuation requires a "
            "linked_branch or a new session"
        )


__all__ = [
    "TurnSourceKind",
    "SESSION_ALLOCATING_SOURCE_KINDS",
    "OUTBOUND_SCAN_SOURCE_KINDS",
    "TurnCommandStep",
    "RecoveryMode",
    "ChatCapability",
    "Lifecycle",
    "LifecycleOwnership",
    "LIFECYCLE_OWNERSHIP_MATRIX",
    "CANONICAL_TERMINAL_SESSION_STATES",
    "CLEANUP_PENDING",
    "CLEANUP_FENCED",
    "CLEANUP_IN_PROGRESS",
    "CLEANUP_COMPLETE",
    "CLEANUP_RELEASED",
    "TERMINAL_CLEANUP_STATES",
    "TurnContractError",
    "BranchRequiredError",
    "CallerAuthorityError",
    "SessionTerminalError",
    "RemediationAuthorityError",
    "CleanupFenceError",
    "ImmutableSessionDimensions",
    "RemediationTurnIntent",
    "validate_remediation_authority",
    "TurnSubmissionRequest",
    "TurnSubmissionPlan",
    "idempotency_scope_for",
    "plan_turn_submission",
    "RecoveryEvidence",
    "RecoveryDecision",
    "decide_recovery",
    "ChatCapabilityDecision",
    "derive_chat_capability",
    "CleanupOperation",
    "LifecycleActivity",
    "CleanupDisposition",
    "CleanupDecision",
    "evaluate_cleanup_admission",
    "admit_continuation",
]
