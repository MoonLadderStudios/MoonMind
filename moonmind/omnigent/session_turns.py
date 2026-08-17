"""Canonical session and turn-attempt ownership contract.

MoonLadderStudios/MoonMind#3707 unifies same-session continuations, remediation
attempts, checkpoint recovery, chat messages, steering actions, approval
responses, and linked branches under one ``OmnigentSession`` + ``OmnigentTurnAttempt``
ownership model. Previously each of these features reconstructed or mutated
overlapping authority independently: continuations minted new bridge-session
rows, remediation ownership was inferred from generic annotations, checkpoint
recovery chose between live reattach / cold restore / branch through separate
paths, and native chat resolved authority from bridge rows that may represent
individual attempts.

This module is the runtime-neutral contract layer those consumers agree on:

* one canonical session authority (``CanonicalSessionRef``) that owns the chat
  binding and the immutable dimensions;
* one typed turn-submission command (``TurnSubmission``) with a fixed set of
  source kinds (``TurnSourceKind``);
* one continuation decision that reuses the session or demands a branch when an
  immutable dimension changed, sharing the checkpoint recovery vocabulary
  (``OmnigentRecoveryMode`` / ``IMMUTABLE_RECOVERY_DIMENSIONS``);
* a lifecycle-ownership matrix plus the guard that keeps attempt terminality
  from being mistaken for session terminality.

The module carries compact refs and metadata only — never large content or
credentials — so it is safe to reference from workflow code and Activities.
Durable persistence of the canonical session/turn rows is owned by the schema
and store changes tracked under the parent control-plane epic (#3701) and is
intentionally out of scope here; this layer defines the shared contract those
rows and their consumers must honour.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

from pydantic import BaseModel, ConfigDict, Field, model_validator

from moonmind.omnigent.checkpoints import (
    IMMUTABLE_RECOVERY_DIMENSIONS,
    OmnigentRecoveryMode,
)

_DIGEST = r"^sha256:[0-9a-f]{64}$"
_DURABLE_REF_SCHEMES = ("artifact://", "ref://", "credential://", "secret://")
_CREDENTIAL_MARKERS = ("bearer ", "token=", "password=", "authorization:")


class TurnSourceKind(str, Enum):
    """The distinct origins a provider turn can have.

    Source kind affects authorization and policy, not the fundamental idempotency
    or observation model: every kind flows through the same typed command path.
    """

    INITIAL = "initial"
    REPOSITORY_CONTINUATION = "repository_continuation"
    REMEDIATION = "remediation"
    WORKFLOW_CHAT = "workflow_chat"
    STEERING = "steering"
    APPROVAL_RESPONSE = "approval_response"
    CHECKPOINT_RESUME = "checkpoint_resume"
    LINKED_BRANCH = "linked_branch"


# Source kinds that create their own session/branch authority rather than
# reusing an existing provider session. They are rejected by
# :func:`build_continuation_turn`, which only mints same-session turns.
_NEW_SESSION_SOURCE_KINDS = frozenset(
    {TurnSourceKind.INITIAL, TurnSourceKind.LINKED_BRANCH}
)


class TurnDeliveryState(str, Enum):
    """Observation state of a submitted turn relative to the provider."""

    PENDING = "pending"
    DELIVERED = "delivered"
    DELIVERY_UNKNOWN = "delivery_unknown"


class OmnigentLifecycle(str, Enum):
    """The distinct lifecycles whose terminality must not be conflated."""

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


@dataclass(frozen=True, slots=True)
class LifecycleOwnership:
    """The single owner of a lifecycle and its terminal meaning."""

    owner: str
    terminal_meaning: str


# The lifecycle ownership matrix. No lifecycle may infer another lifecycle's
# terminality merely from matching timestamps or a shared provider ID.
LIFECYCLE_OWNERSHIP: Mapping[OmnigentLifecycle, LifecycleOwnership] = {
    OmnigentLifecycle.WORKFLOW_EXECUTION: LifecycleOwnership(
        "MoonMind.UserWorkflow", "Product workflow completed"
    ),
    OmnigentLifecycle.STEP_EXECUTION: LifecycleOwnership(
        "Step ledger", "This logical Step execution completed"
    ),
    OmnigentLifecycle.AGENT_RUN: LifecycleOwnership(
        "MoonMind.AgentRun", "One agent execution contract completed"
    ),
    OmnigentLifecycle.OMNIGENT_SESSION: LifecycleOwnership(
        "MoonMind.OmnigentSession",
        "Provider session is no longer active or resumable under policy",
    ),
    OmnigentLifecycle.TURN_ATTEMPT: LifecycleOwnership(
        "Session workflow", "One submitted instruction reached a terminal outcome"
    ),
    OmnigentLifecycle.REMEDIATION_LOOP: LifecycleOwnership(
        "Remediation controller",
        "Candidate passed, policy exhausted, or intervention required",
    ),
    OmnigentLifecycle.CHECKPOINT_BRANCH: LifecycleOwnership(
        "Branch workflow/ledger", "Branch head reached a durable outcome"
    ),
    OmnigentLifecycle.CHAT_BINDING: LifecycleOwnership(
        "Canonical session authority", "Caller can read or mutate the canonical session"
    ),
    OmnigentLifecycle.HOST_LEASE: LifecycleOwnership(
        "Host manager", "Host realization is no longer reserved"
    ),
    OmnigentLifecycle.PROVIDER_PROFILE_LEASE: LifecycleOwnership(
        "Profile manager", "Credential consumer is gone and capacity is releasable"
    ),
}


def session_is_terminal(
    *,
    attempt_terminal: bool,
    session_policy_terminal: bool,
    authoritative_session_evidence: bool,
) -> bool:
    """Decide session terminality without inferring it from an attempt.

    A turn attempt reaching a terminal outcome never terminalizes the canonical
    session: the session is terminal only when session policy *and* authoritative
    session evidence require it. ``attempt_terminal`` is accepted for symmetry and
    deliberately ignored so callers cannot smuggle attempt completion, a matching
    timestamp, or a shared provider id into a session-terminality decision.
    """

    _ = attempt_terminal  # never a source of session terminality
    return bool(session_policy_terminal and authoritative_session_evidence)


def _require_durable_ref(field: str, value: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field} must be a durable reference")
    lowered = text.lower()
    if any(marker in lowered for marker in _CREDENTIAL_MARKERS):
        raise ValueError(f"{field} must be a reference, not credential data")
    if not text.startswith(_DURABLE_REF_SCHEMES):
        raise ValueError(f"{field} must be a durable artifact/credential reference")
    return text


class CanonicalSessionRef(BaseModel):
    """The one canonical session authority a turn is submitted against.

    ``canonical_session_id`` and ``chat_binding_id`` are owned here and reused by
    every same-session turn; an attempt row can never supersede this authority by
    being newer.
    """

    model_config = ConfigDict(populate_by_name=True, extra="forbid", frozen=True)

    canonical_session_id: str = Field(..., alias="canonicalSessionId", min_length=1)
    chat_binding_id: str | None = Field(None, alias="chatBindingId")
    provider_profile_id: str = Field(..., alias="providerProfileId", min_length=1)
    immutable_dimensions: dict[str, Any] = Field(..., alias="immutableDimensions")
    session_terminal: bool = Field(False, alias="sessionTerminal")

    @model_validator(mode="after")
    def _dimensions_are_complete(self) -> "CanonicalSessionRef":
        missing = [
            dimension
            for dimension in IMMUTABLE_RECOVERY_DIMENSIONS
            if dimension not in self.immutable_dimensions
        ]
        if missing:
            raise ValueError(
                "canonical session is missing immutable dimensions: "
                + ", ".join(missing)
            )
        extra = [
            key
            for key in self.immutable_dimensions
            if key not in IMMUTABLE_RECOVERY_DIMENSIONS
        ]
        if extra:
            raise ValueError(
                "canonical session declares unknown immutable dimensions: "
                + ", ".join(extra)
            )
        if self.provider_profile_id != str(
            self.immutable_dimensions.get("providerProfileId")
        ):
            raise ValueError(
                "providerProfileId must match the immutable dimension snapshot"
            )
        return self


class TurnSubmission(BaseModel):
    """One typed turn-attempt command against a canonical session.

    Attempt identity (``turn_attempt_id`` / ``idempotency_key``) is always
    distinct from the session it addresses. The fenced ``submit_command_id`` and
    ``delivery_state`` carry the submit/observe stage of the turn-submission
    contract without duplicating the canonical session's immutable authority.
    """

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    schema_version: str = Field("v1", alias="schemaVersion")
    canonical_session_id: str = Field(..., alias="canonicalSessionId", min_length=1)
    chat_binding_id: str | None = Field(None, alias="chatBindingId")
    turn_attempt_id: str = Field(..., alias="turnAttemptId", min_length=1)
    source_kind: TurnSourceKind = Field(..., alias="sourceKind")
    idempotency_key: str = Field(..., alias="idempotencyKey", min_length=1)
    instruction_digest: str = Field(..., alias="instructionDigest", pattern=_DIGEST)
    submit_command_id: str | None = Field(None, alias="submitCommandId")
    delivery_state: TurnDeliveryState = Field(
        TurnDeliveryState.PENDING, alias="deliveryState"
    )
    attempt_terminal: bool = Field(False, alias="attemptTerminal")

    @model_validator(mode="after")
    def _attempt_is_not_the_session(self) -> "TurnSubmission":
        if self.turn_attempt_id == self.canonical_session_id:
            raise ValueError("turn attempt id must differ from the canonical session id")
        if self.idempotency_key == self.canonical_session_id:
            raise ValueError("turn idempotency key must differ from the session id")
        return self


class ContinuationDecision(str, Enum):
    """Outcome of a same-session continuation admission check."""

    ACCEPT_SAME_SESSION = "accept_same_session"
    # Shares the recovery vocabulary so continuation and checkpoint agree.
    BRANCH_REQUIRED = OmnigentRecoveryMode.BRANCH_REQUIRED.value


class ContinuationOutcome(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid", frozen=True)

    decision: ContinuationDecision
    changed_dimensions: list[str] = Field(
        default_factory=list, alias="changedDimensions", max_length=20
    )
    reason_codes: list[str] = Field(
        default_factory=list, alias="reasonCodes", max_length=20
    )


def decide_continuation(
    current: Mapping[str, Any],
    requested: Mapping[str, Any],
) -> ContinuationOutcome:
    """Admit a same-session continuation or demand a branch.

    A continuation may reuse the provider session only when no immutable dimension
    changed. Any changed dimension yields ``branch_required`` rather than silently
    mutating the existing session, using the same dimension set and reason codes as
    the checkpoint recovery boundary.
    """

    missing = [
        dimension
        for dimension in IMMUTABLE_RECOVERY_DIMENSIONS
        if dimension not in current or dimension not in requested
    ]
    if missing:
        raise ValueError(
            "continuation requires complete immutable dimensions: "
            + ", ".join(missing)
        )
    changed = [
        dimension
        for dimension in IMMUTABLE_RECOVERY_DIMENSIONS
        if current[dimension] != requested[dimension]
    ]
    if changed:
        return ContinuationOutcome(
            decision=ContinuationDecision.BRANCH_REQUIRED,
            changedDimensions=changed[:20],
            reasonCodes=[f"immutable_{dimension}_changed" for dimension in changed[:20]],
        )
    return ContinuationOutcome(decision=ContinuationDecision.ACCEPT_SAME_SESSION)


def build_continuation_turn(
    session: CanonicalSessionRef,
    *,
    source_kind: TurnSourceKind,
    turn_attempt_id: str,
    idempotency_key: str,
    instruction_digest: str,
    submit_command_id: str | None = None,
    prior_turn_attempt_id: str | None = None,
    prior_idempotency_key: str | None = None,
) -> TurnSubmission:
    """Mint a same-session continuation turn against ``session``.

    Reuses the canonical session id and chat binding, allocates a new turn-attempt
    id and idempotency key, and never allocates another session or chat binding.
    ``initial`` and ``linked_branch`` are rejected because they establish new
    session authority rather than continuing an existing one.
    """

    if session.session_terminal:
        raise ValueError(
            "cannot continue a terminal canonical session; require an explicit "
            "linked-branch or new-session policy"
        )
    if source_kind in _NEW_SESSION_SOURCE_KINDS:
        raise ValueError(
            f"source kind {source_kind.value} does not continue an existing session"
        )
    if prior_turn_attempt_id is not None and turn_attempt_id == prior_turn_attempt_id:
        raise ValueError("continuation must allocate a new turn-attempt id")
    if prior_idempotency_key is not None and idempotency_key == prior_idempotency_key:
        raise ValueError("continuation must allocate a new idempotency key")
    return TurnSubmission(
        canonicalSessionId=session.canonical_session_id,
        chatBindingId=session.chat_binding_id,
        turnAttemptId=turn_attempt_id,
        sourceKind=source_kind,
        idempotencyKey=idempotency_key,
        instructionDigest=instruction_digest,
        submitCommandId=submit_command_id,
    )


class RemediationTurnIntent(BaseModel):
    """Typed remediation-turn intent submitted through the session turn path.

    The remediation controller owns *whether* another attempt is admitted; the
    session workflow owns provider turn submission. This intent carries the exact
    durable refs the controller must supply and structurally cannot broaden
    profile, workspace, or publication authority: the requested-authority fields
    are validated against the canonical session at submission and any divergence is
    rejected instead of silently widening authority.
    """

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    loop_id: str = Field(..., alias="loopId", min_length=1)
    attempt_ordinal: int = Field(..., alias="attemptOrdinal", ge=0)
    gate_result_ref: str = Field(..., alias="gateResultRef", min_length=1)
    remaining_work_ref: str = Field(..., alias="remainingWorkRef", min_length=1)
    candidate_workspace_ref: str = Field(
        ..., alias="candidateWorkspaceRef", min_length=1
    )
    checkpoint_ref: str = Field(..., alias="checkpointRef", min_length=1)
    remediator_skill: str = Field(..., alias="remediatorSkill", min_length=1)
    runtime_authority_ref: str = Field(
        ..., alias="runtimeAuthorityRef", min_length=1
    )
    verification_requirements: list[str] = Field(
        ..., alias="verificationRequirements", min_length=1, max_length=32
    )
    attempt_budget: int = Field(..., alias="attemptBudget", ge=1)
    branch_budget: int = Field(..., alias="branchBudget", ge=0)
    same_session_reuse_allowed: bool = Field(..., alias="sameSessionReuseAllowed")
    production_boundary_evidence_ref: str = Field(
        ..., alias="productionBoundaryEvidenceRef", min_length=1
    )
    # Requested-authority fields exist only so a remediator that *tries* to change
    # them is rejected; absent values mean "keep the canonical session authority".
    requested_provider_profile_id: str | None = Field(
        None, alias="requestedProviderProfileId"
    )
    requested_publish_mode: str | None = Field(None, alias="requestedPublishMode")
    requested_workspace_ref: str | None = Field(None, alias="requestedWorkspaceRef")

    @model_validator(mode="after")
    def _refs_are_durable(self) -> "RemediationTurnIntent":
        for field, value in (
            ("gateResultRef", self.gate_result_ref),
            ("remainingWorkRef", self.remaining_work_ref),
            ("candidateWorkspaceRef", self.candidate_workspace_ref),
            ("checkpointRef", self.checkpoint_ref),
            ("runtimeAuthorityRef", self.runtime_authority_ref),
            ("productionBoundaryEvidenceRef", self.production_boundary_evidence_ref),
        ):
            _require_durable_ref(field, value)
        if self.requested_workspace_ref is not None:
            _require_durable_ref("requestedWorkspaceRef", self.requested_workspace_ref)
        return self

    def assert_within_session_authority(self, session: CanonicalSessionRef) -> None:
        """Reject any attempt to broaden profile, workspace, or publication authority."""

        if (
            self.requested_provider_profile_id is not None
            and self.requested_provider_profile_id != session.provider_profile_id
        ):
            raise ValueError(
                "remediation may not change the canonical Provider Profile authority"
            )
        if (
            self.requested_publish_mode is not None
            and self.requested_publish_mode
            != str(session.immutable_dimensions.get("publishMode"))
        ):
            raise ValueError(
                "remediation may not broaden the canonical publication authority"
            )

    def into_turn_submission(
        self,
        session: CanonicalSessionRef,
        *,
        turn_attempt_id: str,
        idempotency_key: str,
        instruction_digest: str,
        submit_command_id: str | None = None,
    ) -> TurnSubmission:
        """Compile the remediation intent into a same-session remediation turn.

        Requires ``same_session_reuse_allowed``; when reuse is not allowed the
        controller must branch rather than resurrect the existing session.
        """

        self.assert_within_session_authority(session)
        if not self.same_session_reuse_allowed:
            raise ValueError(
                "remediation intent forbids same-session reuse; a branch is required"
            )
        return build_continuation_turn(
            session,
            source_kind=TurnSourceKind.REMEDIATION,
            turn_attempt_id=turn_attempt_id,
            idempotency_key=idempotency_key,
            instruction_digest=instruction_digest,
            submit_command_id=submit_command_id,
        )


__all__ = [
    "CanonicalSessionRef",
    "ContinuationDecision",
    "ContinuationOutcome",
    "LIFECYCLE_OWNERSHIP",
    "LifecycleOwnership",
    "OmnigentLifecycle",
    "RemediationTurnIntent",
    "TurnDeliveryState",
    "TurnSourceKind",
    "TurnSubmission",
    "build_continuation_turn",
    "decide_continuation",
    "session_is_terminal",
]
