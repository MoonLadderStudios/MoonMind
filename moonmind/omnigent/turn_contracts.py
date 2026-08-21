"""Canonical Omnigent turn-command contracts.

Source issue: MoonLadderStudios/MoonMind#3707.

One typed turn-command path is the only way to submit new work to an existing
Omnigent session. This module owns the *pure* half of that path:

* :class:`OmnigentTurnSource` -- the closed, versioned source vocabulary. Source
  kind changes authorization, evidence, and policy; it never changes the
  command, idempotency, fencing, observation, or terminality model.
* :data:`TURN_PRODUCER_SOURCES` -- the production-producer inventory. Every
  production caller that can submit follow-up work names itself here and is
  bound to exactly one source kind, so a new alternate submission path cannot be
  added without appearing in this inventory.
* :class:`ImmutableExecutionAuthority` -- the plan-bound dimensions a turn is
  admitted against (harness, execution realizer, model, repository, workspace,
  Skill, launch, publication, policy). Changing any of them is a branch, never a
  silent mutation of the prior session.
* :class:`TurnDisposition` -- the single evidence-gated decision boundary shared
  by live reattach, cold restore, branching, new-session, and unavailable
  outcomes.

Everything here is host- and harness-neutral: there are no provider names,
no Codex-versus-OpenCode lifecycle branches, and no database or Temporal
dependencies. Harness-specific message or resume behavior belongs behind the
selected Omnigent adapter or realizer; the session supervisor retains canonical
ownership.

The durable half of the path (turn-attempt creation, fenced command journalling,
cleanup fencing, supervisor dispatch) lives in
:mod:`moonmind.omnigent.control_plane.turn_service`.
"""

from __future__ import annotations

import hashlib
import json
from enum import Enum
from types import MappingProxyType
from typing import Mapping

from pydantic import BaseModel, ConfigDict, Field

TURN_SOURCE_VOCABULARY_VERSION = "moonmind.omnigent-turn-source/v1"
TURN_ADMISSION_CONTRACT_VERSION = "moonmind.omnigent-turn-admission/v1"

#: The one canonical command type journalled for every submitted turn, whatever
#: the source kind. A producer that needs a different command type is submitting
#: something other than a turn.
CANONICAL_SUBMIT_COMMAND_TYPE = "omnigent.submit_turn"


class OmnigentTurnSource(str, Enum):
    """Closed, versioned vocabulary of canonical turn sources (#3707).

    Membership is exhaustive: an unrecognized source fails closed in
    :func:`resolve_turn_source` rather than degrading to a permissive default.
    """

    #: The session's first instruction; establishes the session and binding.
    INITIAL = "initial"
    #: Follow-up work derived from repository output of a prior turn.
    REPOSITORY_CONTINUATION = "repository_continuation"
    #: A typed remediation attempt for a failed gate on a prior turn.
    REMEDIATION = "remediation"
    #: An end-user message submitted through native Workflow Chat.
    WORKFLOW_CHAT = "workflow_chat"
    #: An operator steering instruction against a running session.
    STEERING = "steering"
    #: A response to an approval or elicitation request.
    APPROVAL_RESPONSE = "approval_response"
    #: Resuming from an artifact-backed checkpoint.
    CHECKPOINT_RESUME = "checkpoint_resume"
    #: Work submitted on a linked branch of a prior session.
    LINKED_BRANCH = "linked_branch"


class TurnSourcePolicy(BaseModel):
    """Per-source authorization, evidence, and reuse policy.

    Authorization, evidence, and session reuse are the only three concerns that
    vary by source. Idempotency, fencing, observation, and terminality are
    source-independent by contract, so nothing in this model can influence them.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    source: OmnigentTurnSource
    #: The turn must name the end user it acts for and match the session owner.
    requires_end_user_actor: bool = Field(alias="requiresEndUserActor")
    #: The turn must name the prior turn it continues.
    requires_parent_turn: bool = Field(alias="requiresParentTurn")
    #: The turn must name the turn it remediates plus its gate evidence.
    requires_remediation_evidence: bool = Field(alias="requiresRemediationEvidence")
    #: The turn must name an artifact-backed checkpoint.
    requires_checkpoint_evidence: bool = Field(alias="requiresCheckpointEvidence")
    #: Policy permits this source to reuse the prior canonical session.
    may_reuse_session: bool = Field(alias="mayReuseSession")
    #: Policy always requires a new canonical session for this source.
    requires_new_session: bool = Field(alias="requiresNewSession")
    #: The turn must resolve an interactive chat binding owned by the session.
    requires_chat_binding: bool = Field(alias="requiresChatBinding")


def _policy(
    source: OmnigentTurnSource,
    *,
    end_user: bool = False,
    parent: bool = False,
    remediation: bool = False,
    checkpoint: bool = False,
    reuse: bool = True,
    new_session: bool = False,
    chat_binding: bool = False,
) -> TurnSourcePolicy:
    return TurnSourcePolicy(
        source=source,
        requiresEndUserActor=end_user,
        requiresParentTurn=parent,
        requiresRemediationEvidence=remediation,
        requiresCheckpointEvidence=checkpoint,
        mayReuseSession=reuse,
        requiresNewSession=new_session,
        requiresChatBinding=chat_binding,
    )


TURN_SOURCE_POLICIES: Mapping[OmnigentTurnSource, TurnSourcePolicy] = MappingProxyType(
    {
        OmnigentTurnSource.INITIAL: _policy(
            OmnigentTurnSource.INITIAL, reuse=False, new_session=True
        ),
        OmnigentTurnSource.REPOSITORY_CONTINUATION: _policy(
            OmnigentTurnSource.REPOSITORY_CONTINUATION, parent=True
        ),
        OmnigentTurnSource.REMEDIATION: _policy(
            OmnigentTurnSource.REMEDIATION, parent=True, remediation=True
        ),
        OmnigentTurnSource.WORKFLOW_CHAT: _policy(
            OmnigentTurnSource.WORKFLOW_CHAT, end_user=True, chat_binding=True
        ),
        OmnigentTurnSource.STEERING: _policy(
            OmnigentTurnSource.STEERING, end_user=True
        ),
        OmnigentTurnSource.APPROVAL_RESPONSE: _policy(
            OmnigentTurnSource.APPROVAL_RESPONSE, end_user=True, parent=True
        ),
        OmnigentTurnSource.CHECKPOINT_RESUME: _policy(
            OmnigentTurnSource.CHECKPOINT_RESUME, checkpoint=True
        ),
        # A linked branch is a branch: policy always allocates a new canonical
        # session so branch work can never mutate the source session.
        OmnigentTurnSource.LINKED_BRANCH: _policy(
            OmnigentTurnSource.LINKED_BRANCH,
            checkpoint=True,
            reuse=False,
            new_session=True,
        ),
    }
)

#: Every production caller that can create or submit follow-up Omnigent work,
#: bound to exactly one canonical source kind. A producer absent from this
#: inventory cannot reach :class:`~moonmind.omnigent.control_plane.turn_service.CanonicalTurnService`,
#: so an alternate submission path is a contract violation rather than a
#: silently-accepted second authority.
TURN_PRODUCER_SOURCES: Mapping[str, OmnigentTurnSource] = MappingProxyType(
    {
        "omnigent.session_supervisor.initial": OmnigentTurnSource.INITIAL,
        "omnigent.repository_output_continuation": (
            OmnigentTurnSource.REPOSITORY_CONTINUATION
        ),
        "omnigent.remediation_controller": OmnigentTurnSource.REMEDIATION,
        "omnigent.workflow_chat.http": OmnigentTurnSource.WORKFLOW_CHAT,
        "omnigent.workflow_chat.websocket": OmnigentTurnSource.WORKFLOW_CHAT,
        "omnigent.workflow_chat.steering": OmnigentTurnSource.STEERING,
        "omnigent.workflow_chat.approval_response": (
            OmnigentTurnSource.APPROVAL_RESPONSE
        ),
        "omnigent.checkpoint_resume": OmnigentTurnSource.CHECKPOINT_RESUME,
        "omnigent.checkpoint_branch_turn": OmnigentTurnSource.LINKED_BRANCH,
        "omnigent.linked_branch_workflow": OmnigentTurnSource.LINKED_BRANCH,
        "omnigent.edit_and_rerun_reconstruction": (
            OmnigentTurnSource.REPOSITORY_CONTINUATION
        ),
        "omnigent.execution_realizer": OmnigentTurnSource.REPOSITORY_CONTINUATION,
    }
)


class UnknownTurnSourceError(ValueError):
    """Raised when a submission names a source outside the closed vocabulary."""

    def __init__(self, value: object) -> None:
        self.value = value
        super().__init__(
            f"Unknown Omnigent turn source {value!r}; "
            f"{TURN_SOURCE_VOCABULARY_VERSION} permits only "
            f"{sorted(item.value for item in OmnigentTurnSource)}"
        )


class UnknownTurnProducerError(ValueError):
    """Raised when a submission names a producer outside the inventory."""

    def __init__(self, producer: object) -> None:
        self.producer = producer
        super().__init__(
            f"Unknown Omnigent turn producer {producer!r}; every production "
            "follow-up producer must be registered in TURN_PRODUCER_SOURCES"
        )


def resolve_turn_source(value: object) -> OmnigentTurnSource:
    """Coerce a wire value into the closed vocabulary, failing closed."""

    if isinstance(value, OmnigentTurnSource):
        return value
    try:
        return OmnigentTurnSource(str(value or "").strip())
    except ValueError as exc:
        raise UnknownTurnSourceError(value) from exc


def turn_source_policy(source: object) -> TurnSourcePolicy:
    """Return the authorization/evidence/reuse policy for one source."""

    return TURN_SOURCE_POLICIES[resolve_turn_source(source)]


def resolve_producer_source(producer: object) -> OmnigentTurnSource:
    """Return the one source kind a registered production producer may submit."""

    key = str(producer or "").strip()
    if key not in TURN_PRODUCER_SOURCES:
        raise UnknownTurnProducerError(producer)
    return TURN_PRODUCER_SOURCES[key]


# --- Immutable execution authority -------------------------------------------

#: Human-readable dimension names in the order they are reported. Order is
#: stable so a decision's ``changed_dimensions`` is deterministic.
IMMUTABLE_AUTHORITY_DIMENSIONS: tuple[str, ...] = (
    "executionPlanRef",
    "runtimeBindingRef",
    "harnessId",
    "executionRealizerRef",
    "providerProfileId",
    "providerProfileGeneration",
    "modelConfigDigest",
    "repositoryRef",
    "branchRef",
    "workspaceIntentRef",
    "resolvedSkillsDigest",
    "launchPolicyRef",
    "policySnapshotRef",
    "publicationAuthorityRef",
)

#: The subset of dimensions a remediation attempt may never change. Remediation
#: narrows work; it never broadens harness, profile, model, workspace, Skill,
#: publication, or policy authority (#3707 AC6).
REMEDIATION_LOCKED_DIMENSIONS: frozenset[str] = frozenset(
    {
        "harnessId",
        "executionRealizerRef",
        "providerProfileId",
        "modelConfigDigest",
        "repositoryRef",
        "branchRef",
        "workspaceIntentRef",
        "resolvedSkillsDigest",
        "launchPolicyRef",
        "policySnapshotRef",
        "publicationAuthorityRef",
    }
)


class ImmutableExecutionAuthority(BaseModel):
    """The plan-bound dimensions a turn is admitted against.

    Sourced from the recorded execution plan and runtime binding, never from
    live provider state or a caller-supplied override. Every field is a
    digest, ref, or generation ordinal: no credentials, host paths, or provider
    secrets may be carried here.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    execution_plan_ref: str | None = Field(None, alias="executionPlanRef")
    runtime_binding_ref: str | None = Field(None, alias="runtimeBindingRef")
    harness_id: str | None = Field(None, alias="harnessId")
    execution_realizer_ref: str | None = Field(None, alias="executionRealizerRef")
    provider_profile_id: str | None = Field(None, alias="providerProfileId")
    provider_profile_generation: int | None = Field(
        None, alias="providerProfileGeneration"
    )
    model_config_digest: str | None = Field(None, alias="modelConfigDigest")
    repository_ref: str | None = Field(None, alias="repositoryRef")
    branch_ref: str | None = Field(None, alias="branchRef")
    workspace_intent_ref: str | None = Field(None, alias="workspaceIntentRef")
    resolved_skills_digest: str | None = Field(None, alias="resolvedSkillsDigest")
    launch_policy_ref: str | None = Field(None, alias="launchPolicyRef")
    policy_snapshot_ref: str | None = Field(None, alias="policySnapshotRef")
    publication_authority_ref: str | None = Field(
        None, alias="publicationAuthorityRef"
    )

    def as_dict(self) -> dict[str, object]:
        return self.model_dump(by_alias=True, mode="json")

    @property
    def authority_digest(self) -> str:
        """Deterministic digest of every immutable dimension."""

        canonical = json.dumps(
            self.as_dict(), sort_keys=True, separators=(",", ":"), default=str
        )
        return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def changed_dimensions(
        self, other: "ImmutableExecutionAuthority"
    ) -> tuple[str, ...]:
        """Names of dimensions that differ from ``other``.

        A dimension the requester left unspecified (``None``) is not a change:
        the recorded plan remains authoritative. Only an explicitly different
        value counts, so a compact request cannot force a spurious branch.
        """

        mine = self.as_dict()
        theirs = other.as_dict()
        return tuple(
            name
            for name in IMMUTABLE_AUTHORITY_DIMENSIONS
            if mine.get(name) is not None and mine.get(name) != theirs.get(name)
        )


# --- Runtime authority evidence ----------------------------------------------

#: Cleanup-authority states, mirrored from the durable control plane so this
#: module stays free of database imports.
CLEANUP_UNCLAIMED = "unclaimed"
CLEANUP_CLAIMED = "claimed"
CLEANUP_COMPLETE = "complete"


class RuntimeAuthorityEvidence(BaseModel):
    """Independently observed evidence gating the resume decision.

    Every field is evidence, not intent: the caller records what it observed and
    the decision function derives the disposition. ``checkpoint_restorable``
    must be backed by artifact evidence -- a destroyed host-local path is not
    cold-restore evidence, so callers set it from checkpoint/workspace artifact
    validation only.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    session_terminal: bool = Field(False, alias="sessionTerminal")
    #: ``live``, ``artifact``, or ``quarantined``.
    historical_read_state: str = Field("live", alias="historicalReadState")
    provider_session_attached: bool = Field(False, alias="providerSessionAttached")
    provider_session_resumable: bool = Field(False, alias="providerSessionResumable")
    host_attached: bool = Field(False, alias="hostAttached")
    host_lease_active: bool = Field(False, alias="hostLeaseActive")
    credential_lease_active: bool = Field(False, alias="credentialLeaseActive")
    provider_profile_generation_current: bool = Field(
        False, alias="providerProfileGenerationCurrent"
    )
    workspace_available: bool = Field(False, alias="workspaceAvailable")
    checkpoint_restorable: bool = Field(False, alias="checkpointRestorable")
    cleanup_state: str = Field(CLEANUP_UNCLAIMED, alias="cleanupState")
    publication_in_progress: bool = Field(False, alias="publicationInProgress")

    @property
    def live_runtime_authority_complete(self) -> bool:
        """True only when every original runtime authority is still current."""

        return all(
            (
                self.provider_session_attached,
                self.provider_session_resumable,
                self.host_attached,
                self.host_lease_active,
                self.credential_lease_active,
                self.provider_profile_generation_current,
                self.workspace_available,
            )
        )


# --- Decision boundary --------------------------------------------------------


class TurnDisposition(str, Enum):
    """The one evidence-gated resume/branch decision vocabulary (#3707)."""

    #: Reuse the live runtime binding and provider session.
    LIVE_REATTACH = "live_reattach"
    #: Rebuild from artifact-backed checkpoint and workspace evidence.
    COLD_RESTORE = "cold_restore"
    #: Immutable authority changed; the caller must branch to a new session.
    BRANCH_REQUIRED = "branch_required"
    #: The prior session cannot be reused at all; allocate a new one.
    NEW_SESSION_REQUIRED = "new_session_required"
    #: No safe resume path exists from the presented evidence.
    RESUME_UNAVAILABLE = "resume_unavailable"


#: Dispositions that admit the turn onto the *existing* canonical session.
SAME_SESSION_DISPOSITIONS: frozenset[TurnDisposition] = frozenset(
    {TurnDisposition.LIVE_REATTACH, TurnDisposition.COLD_RESTORE}
)

# Reason codes. Ordered gates mean the first failing gate wins, so the reason for
# a given (request, evidence) pair is stable and deterministic.
ADMITTED = "admitted"
PRODUCER_SOURCE_MISMATCH = "producer_source_mismatch"
ACTOR_REQUIRED = "actor_required"
ACTOR_NOT_SESSION_OWNER = "actor_not_session_owner"
CHAT_BINDING_REQUIRED = "chat_binding_required"
CHAT_BINDING_MISMATCH = "chat_binding_mismatch"
PARENT_TURN_REQUIRED = "parent_turn_required"
REMEDIATION_EVIDENCE_REQUIRED = "remediation_evidence_required"
CHECKPOINT_EVIDENCE_REQUIRED = "checkpoint_evidence_required"
SESSION_REVISION_CONFLICT = "session_revision_conflict"
FENCING_GENERATION_SUPERSEDED = "fencing_generation_superseded"
SESSION_REUSE_NOT_PERMITTED = "session_reuse_not_permitted"
SESSION_NOT_FOUND = "session_not_found"
REMEDIATION_WOULD_BROADEN_AUTHORITY = "remediation_would_broaden_authority"
IMMUTABLE_AUTHORITY_CHANGED = "immutable_authority_changed"
EXECUTION_PLAN_NOT_RECORDED = "execution_plan_not_recorded"
SESSION_TERMINAL = "session_terminal"
HISTORICAL_READ_ONLY = "historical_read_only"
CLEANUP_IN_PROGRESS = "cleanup_in_progress"
CLEANUP_COMPLETED = "cleanup_completed"
RUNTIME_AUTHORITY_INCOMPLETE = "runtime_authority_incomplete"


class TurnAdmissionRequest(BaseModel):
    """One turn-admission question, expressed entirely in compact authority."""

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    producer: str
    source: OmnigentTurnSource
    session_id: str = Field(alias="sessionId")
    actor_id: str | None = Field(None, alias="actorId")
    session_actor_id: str | None = Field(None, alias="sessionActorId")
    chat_binding_id: str | None = Field(None, alias="chatBindingId")
    session_chat_binding_id: str | None = Field(None, alias="sessionChatBindingId")
    parent_turn_attempt_id: str | None = Field(None, alias="parentTurnAttemptId")
    remediation_of_turn_attempt_id: str | None = Field(
        None, alias="remediationOfTurnAttemptId"
    )
    remediation_gate_ref: str | None = Field(None, alias="remediationGateRef")
    checkpoint_ref: str | None = Field(None, alias="checkpointRef")
    expected_session_revision: int = Field(alias="expectedSessionRevision")
    current_session_revision: int = Field(alias="currentSessionRevision")
    expected_fencing_generation: int = Field(alias="expectedFencingGeneration")
    current_fencing_generation: int = Field(alias="currentFencingGeneration")
    requested_authority: ImmutableExecutionAuthority = Field(
        default_factory=ImmutableExecutionAuthority, alias="requestedAuthority"
    )
    recorded_authority: ImmutableExecutionAuthority = Field(
        default_factory=ImmutableExecutionAuthority, alias="recordedAuthority"
    )
    evidence: RuntimeAuthorityEvidence = Field(
        default_factory=RuntimeAuthorityEvidence
    )


class TurnAdmissionDecision(BaseModel):
    """Typed, replay-safe outcome of one turn admission."""

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    contract_version: str = Field(
        TURN_ADMISSION_CONTRACT_VERSION, alias="contractVersion"
    )
    source_vocabulary_version: str = Field(
        TURN_SOURCE_VOCABULARY_VERSION, alias="sourceVocabularyVersion"
    )
    admitted: bool
    disposition: TurnDisposition
    reason_code: str = Field(alias="reasonCode")
    source: OmnigentTurnSource
    producer: str
    session_id: str = Field(alias="sessionId")
    changed_dimensions: tuple[str, ...] = Field(
        default_factory=tuple, alias="changedDimensions"
    )
    authority_digest: str = Field(alias="authorityDigest")

    @property
    def reuses_session(self) -> bool:
        """True when the decision admits work onto the existing session."""

        return self.admitted and self.disposition in SAME_SESSION_DISPOSITIONS

    def as_dict(self) -> dict[str, object]:
        return self.model_dump(by_alias=True, mode="json")


def _decide(
    request: TurnAdmissionRequest,
    *,
    disposition: TurnDisposition,
    reason: str,
    admitted: bool,
    changed: tuple[str, ...] = (),
) -> TurnAdmissionDecision:
    return TurnAdmissionDecision(
        admitted=admitted,
        disposition=disposition,
        reasonCode=reason,
        source=request.source,
        producer=request.producer,
        sessionId=request.session_id,
        changedDimensions=changed,
        authorityDigest=request.recorded_authority.authority_digest,
    )


def evaluate_turn_admission(
    request: TurnAdmissionRequest,
) -> TurnAdmissionDecision:
    """Return the one typed admission decision for a turn submission.

    Gates are ordered and fail closed on the first unmet one, so the outcome is
    deterministic for a given (request, evidence) pair. No gate consults harness
    identity for lifecycle behavior: the harness only appears as one immutable
    authority dimension that must match the recorded plan.

    A denied decision never authorizes mutation. An admitted decision authorizes
    exactly one fenced turn on the existing session; ``branch_required`` and
    ``new_session_required`` are explicit instructions to allocate new canonical
    authority, never permission to mutate the old session.
    """

    policy = TURN_SOURCE_POLICIES[request.source]

    # 1. Producer/source binding: a registered producer submits exactly one kind.
    if resolve_producer_source(request.producer) is not request.source:
        return _decide(
            request,
            disposition=TurnDisposition.RESUME_UNAVAILABLE,
            reason=PRODUCER_SOURCE_MISMATCH,
            admitted=False,
        )

    # 2. Source authority: caller/controller permission, before any mutation.
    if policy.requires_end_user_actor:
        if not (request.actor_id or "").strip():
            return _decide(
                request,
                disposition=TurnDisposition.RESUME_UNAVAILABLE,
                reason=ACTOR_REQUIRED,
                admitted=False,
            )
        if request.session_actor_id and request.actor_id != request.session_actor_id:
            return _decide(
                request,
                disposition=TurnDisposition.RESUME_UNAVAILABLE,
                reason=ACTOR_NOT_SESSION_OWNER,
                admitted=False,
            )
    if policy.requires_chat_binding:
        if not (request.chat_binding_id or "").strip():
            return _decide(
                request,
                disposition=TurnDisposition.RESUME_UNAVAILABLE,
                reason=CHAT_BINDING_REQUIRED,
                admitted=False,
            )
        if (
            request.session_chat_binding_id
            and request.chat_binding_id != request.session_chat_binding_id
        ):
            return _decide(
                request,
                disposition=TurnDisposition.RESUME_UNAVAILABLE,
                reason=CHAT_BINDING_MISMATCH,
                admitted=False,
            )
    if policy.requires_parent_turn and not (
        request.parent_turn_attempt_id or ""
    ).strip():
        return _decide(
            request,
            disposition=TurnDisposition.RESUME_UNAVAILABLE,
            reason=PARENT_TURN_REQUIRED,
            admitted=False,
        )
    if policy.requires_remediation_evidence and not (
        (request.remediation_of_turn_attempt_id or "").strip()
        and (request.remediation_gate_ref or "").strip()
    ):
        return _decide(
            request,
            disposition=TurnDisposition.RESUME_UNAVAILABLE,
            reason=REMEDIATION_EVIDENCE_REQUIRED,
            admitted=False,
        )
    if policy.requires_checkpoint_evidence and not (
        request.checkpoint_ref or ""
    ).strip():
        return _decide(
            request,
            disposition=TurnDisposition.RESUME_UNAVAILABLE,
            reason=CHECKPOINT_EVIDENCE_REQUIRED,
            admitted=False,
        )

    # 3. Current revision and fencing generation. A stale writer is refused
    #    before any provider mutation so a lost update cannot occur.
    if request.expected_fencing_generation != request.current_fencing_generation:
        return _decide(
            request,
            disposition=TurnDisposition.RESUME_UNAVAILABLE,
            reason=FENCING_GENERATION_SUPERSEDED,
            admitted=False,
        )
    if request.expected_session_revision != request.current_session_revision:
        return _decide(
            request,
            disposition=TurnDisposition.RESUME_UNAVAILABLE,
            reason=SESSION_REVISION_CONFLICT,
            admitted=False,
        )

    # 4. Sources whose policy never reuses a session are told so explicitly
    #    rather than being allowed to mutate the prior session.
    if policy.requires_new_session or not policy.may_reuse_session:
        return _decide(
            request,
            disposition=TurnDisposition.NEW_SESSION_REQUIRED,
            reason=SESSION_REUSE_NOT_PERMITTED,
            admitted=False,
        )

    # 5. Immutable execution authority. The recorded plan must exist and the
    #    requested dimensions must match it.
    changed = request.requested_authority.changed_dimensions(
        request.recorded_authority
    )
    if request.source is OmnigentTurnSource.REMEDIATION:
        broadened = tuple(
            name for name in changed if name in REMEDIATION_LOCKED_DIMENSIONS
        )
        if broadened:
            return _decide(
                request,
                disposition=TurnDisposition.BRANCH_REQUIRED,
                reason=REMEDIATION_WOULD_BROADEN_AUTHORITY,
                admitted=False,
                changed=broadened,
            )
    if changed:
        return _decide(
            request,
            disposition=TurnDisposition.BRANCH_REQUIRED,
            reason=IMMUTABLE_AUTHORITY_CHANGED,
            admitted=False,
            changed=changed,
        )
    if not (request.recorded_authority.execution_plan_ref or "").strip():
        return _decide(
            request,
            disposition=TurnDisposition.NEW_SESSION_REQUIRED,
            reason=EXECUTION_PLAN_NOT_RECORDED,
            admitted=False,
        )

    # 6. Terminality and historical-read authority are distinct: a terminal
    #    session stays readable, but new work needs a new session.
    evidence = request.evidence
    if evidence.session_terminal:
        return _decide(
            request,
            disposition=TurnDisposition.NEW_SESSION_REQUIRED,
            reason=SESSION_TERMINAL,
            admitted=False,
        )
    if evidence.historical_read_state != "live":
        return _decide(
            request,
            disposition=TurnDisposition.NEW_SESSION_REQUIRED,
            reason=HISTORICAL_READ_ONLY,
            admitted=False,
        )

    # 7. Cleanup coordination. An accepted turn must fence incompatible cleanup
    #    before any provider mutation, so a live cleanup claim refuses the turn
    #    and completed cleanup forces a new session.
    if evidence.cleanup_state == CLEANUP_COMPLETE:
        return _decide(
            request,
            disposition=TurnDisposition.NEW_SESSION_REQUIRED,
            reason=CLEANUP_COMPLETED,
            admitted=False,
        )
    if evidence.cleanup_state == CLEANUP_CLAIMED:
        return _decide(
            request,
            disposition=TurnDisposition.RESUME_UNAVAILABLE,
            reason=CLEANUP_IN_PROGRESS,
            admitted=False,
        )

    # 8. Resume path. Live reattach requires *complete* current runtime
    #    authority; otherwise artifact-backed cold restore; otherwise no safe
    #    path exists.
    if evidence.live_runtime_authority_complete:
        return _decide(
            request,
            disposition=TurnDisposition.LIVE_REATTACH,
            reason=ADMITTED,
            admitted=True,
        )
    if evidence.checkpoint_restorable:
        return _decide(
            request,
            disposition=TurnDisposition.COLD_RESTORE,
            reason=ADMITTED,
            admitted=True,
        )
    return _decide(
        request,
        disposition=TurnDisposition.RESUME_UNAVAILABLE,
        reason=RUNTIME_AUTHORITY_INCOMPLETE,
        admitted=False,
    )


__all__ = [
    "ADMITTED",
    "ACTOR_NOT_SESSION_OWNER",
    "ACTOR_REQUIRED",
    "CANONICAL_SUBMIT_COMMAND_TYPE",
    "CHAT_BINDING_MISMATCH",
    "CHAT_BINDING_REQUIRED",
    "CHECKPOINT_EVIDENCE_REQUIRED",
    "CLEANUP_CLAIMED",
    "CLEANUP_COMPLETE",
    "CLEANUP_COMPLETED",
    "CLEANUP_IN_PROGRESS",
    "CLEANUP_UNCLAIMED",
    "EXECUTION_PLAN_NOT_RECORDED",
    "FENCING_GENERATION_SUPERSEDED",
    "HISTORICAL_READ_ONLY",
    "IMMUTABLE_AUTHORITY_CHANGED",
    "IMMUTABLE_AUTHORITY_DIMENSIONS",
    "ImmutableExecutionAuthority",
    "OmnigentTurnSource",
    "PARENT_TURN_REQUIRED",
    "PRODUCER_SOURCE_MISMATCH",
    "REMEDIATION_EVIDENCE_REQUIRED",
    "REMEDIATION_LOCKED_DIMENSIONS",
    "REMEDIATION_WOULD_BROADEN_AUTHORITY",
    "RUNTIME_AUTHORITY_INCOMPLETE",
    "RuntimeAuthorityEvidence",
    "SAME_SESSION_DISPOSITIONS",
    "SESSION_NOT_FOUND",
    "SESSION_REUSE_NOT_PERMITTED",
    "SESSION_REVISION_CONFLICT",
    "SESSION_TERMINAL",
    "TURN_ADMISSION_CONTRACT_VERSION",
    "TURN_PRODUCER_SOURCES",
    "TURN_SOURCE_POLICIES",
    "TURN_SOURCE_VOCABULARY_VERSION",
    "TurnAdmissionDecision",
    "TurnAdmissionRequest",
    "TurnDisposition",
    "TurnSourcePolicy",
    "UnknownTurnProducerError",
    "UnknownTurnSourceError",
    "evaluate_turn_admission",
    "resolve_producer_source",
    "resolve_turn_source",
    "turn_source_policy",
]
