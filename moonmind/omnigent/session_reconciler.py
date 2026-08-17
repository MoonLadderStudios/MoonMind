"""Pure reconciler and compact contracts for ``MoonMind.OmnigentSession``.

Implements the deterministic core of GitHub issue
MoonLadderStudios/MoonMind#3705 ("[Omnigent control plane 4/11] Add a durable
MoonMind.OmnigentSession Temporal supervisor with bounded activities").

This module is intentionally side-effect free so it is safe to import into
Temporal workflow code and to exhaustively unit test. It defines:

* the compact, immutable, versioned workflow input (:class:`OmnigentSessionIntent`);
* the durable observation frontier carried across reconciliation and
  Continue-As-New (:class:`OmnigentSessionFrontier`);
* the transient per-loop signals (:class:`OmnigentSessionSignals`);
* the reconciler decision (:class:`OmnigentSessionDecision`) with reason codes and
  the bounded commands it authorizes; and
* :func:`reconcile_omnigent_session`, the pure reducer that maps
  (intent, frontier, signals, elapsed) -> decision, and
  :func:`admit_omnigent_session_intent`, the deterministic admission helper.

The reconciler removes correctness dependence on one long-running streaming
activity: provider streams and callbacks become observation sources that wake
reconciliation, while a periodic authoritative snapshot deadline guarantees
eventual convergence after event loss, worker restart, provider restart, or
activity retry.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from moonmind.schemas._validation import NonBlankStr


class OmnigentSessionStatus(str, Enum):
    """Compact, operator-visible status for the session workflow.

    Combines lifecycle phases with the distinct failure-semantic states the
    issue requires so Workflow Detail never conflates "waiting" with "failed".
    """

    RESOLVING_INTENT = "resolving_intent"
    AWAITING_LEASE = "awaiting_lease"
    LAUNCHING = "launching"
    EXECUTING = "executing"
    AWAITING_OBSERVATION = "awaiting_observation"
    INTEGRATION_UNAVAILABLE = "integration_unavailable"
    DELIVERY_UNKNOWN = "delivery_unknown"
    HARVESTING = "harvesting"
    PUBLISHING = "publishing"
    CLEANING_UP = "cleaning_up"
    CLEANUP_INCOMPLETE = "cleanup_incomplete"
    RELEASING_LEASES = "releasing_leases"
    # Terminal states
    COMPLETED = "completed"
    EXECUTION_FAILED = "execution_failed"
    TIMED_OUT = "timed_out"
    CANCELED = "canceled"
    RECONCILIATION_QUARANTINED = "reconciliation_quarantined"


TERMINAL_STATUSES: frozenset[OmnigentSessionStatus] = frozenset(
    {
        OmnigentSessionStatus.COMPLETED,
        OmnigentSessionStatus.EXECUTION_FAILED,
        OmnigentSessionStatus.TIMED_OUT,
        OmnigentSessionStatus.CANCELED,
        OmnigentSessionStatus.RECONCILIATION_QUARANTINED,
    }
)


class OmnigentSessionCommandKind(str, Enum):
    """Bounded, idempotent side-effect commands the reconciler may authorize.

    Each value is also the canonical Temporal activity type executed by the
    session workflow (see ``omnigent_session_activities``).
    """

    RESOLVE_INTENT = "omnigent.resolve_intent"
    LOAD_RECONCILIATION_INPUTS = "omnigent.load_reconciliation_inputs"
    ENSURE_PROVIDER_PROFILE_LEASE = "omnigent.ensure_provider_profile_lease"
    ENSURE_HOST = "omnigent.ensure_host"
    ENSURE_PROVIDER_SESSION = "omnigent.ensure_provider_session"
    SUBMIT_TURN = "omnigent.submit_turn"
    READ_EVENT_BATCH = "omnigent.read_event_batch"
    OBSERVE_SNAPSHOT = "omnigent.observe_snapshot"
    HARVEST_EVIDENCE = "omnigent.harvest_evidence"
    PUBLISH_WORKSPACE = "omnigent.publish_workspace"
    STOP_PROVIDER_SESSION = "omnigent.stop_provider_session"
    STOP_HOST = "omnigent.stop_host"
    RELEASE_LEASES = "omnigent.release_leases"
    PERSIST_DECISION = "omnigent.persist_decision"


ALL_OMNIGENT_SESSION_ACTIVITY_TYPES: tuple[str, ...] = tuple(
    kind.value for kind in OmnigentSessionCommandKind
)


class OmnigentSessionCommandCondition(str, Enum):
    """Outcome condition of the most recently attempted bounded command."""

    OK = "ok"
    INTEGRATION_UNAVAILABLE = "integration_unavailable"
    DELIVERY_UNKNOWN = "delivery_unknown"
    FENCED = "fenced"
    CLEANUP_FAILED = "cleanup_failed"


class OmnigentSessionReconcilePolicy(BaseModel):
    """Bounded timing/limits carried immutably with the session intent."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    max_session_age_seconds: int = Field(
        default=21600, ge=1, alias="maxSessionAgeSeconds"
    )
    snapshot_interval_seconds: int = Field(
        default=30, ge=1, alias="snapshotIntervalSeconds"
    )
    event_batch_wait_seconds: int = Field(
        default=10, ge=1, alias="eventBatchWaitSeconds"
    )
    retry_backoff_seconds: int = Field(default=15, ge=1, alias="retryBackoffSeconds")
    max_turn_attempts: int = Field(default=3, ge=1, alias="maxTurnAttempts")
    continue_as_new_decision_threshold: int = Field(
        default=500, ge=1, alias="continueAsNewDecisionThreshold"
    )
    continue_as_new_history_threshold: int = Field(
        default=8000, ge=1, alias="continueAsNewHistoryThreshold"
    )


class OmnigentSessionIntent(BaseModel):
    """Compact, immutable, versioned authority — the workflow input.

    Carries only safe identifiers and durable refs. Raw credentials, provider
    tokens, mutable Docker paths, large prompts, transcripts, diffs, and
    artifact bodies must never enter this contract (and therefore never enter
    workflow history).
    """

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    canonical_session_id: NonBlankStr = Field(alias="canonicalSessionId")
    execution_intent_ref: NonBlankStr = Field(alias="executionIntentRef")
    execution_intent_digest: NonBlankStr = Field(alias="executionIntentDigest")
    owning_workflow_id: NonBlankStr = Field(alias="owningWorkflowId")
    step_execution_id: NonBlankStr = Field(alias="stepExecutionId")
    agent_run_id: NonBlankStr = Field(alias="agentRunId")
    execution_profile_ref: NonBlankStr = Field(alias="executionProfileRef")
    initial_turn_attempt_id: NonBlankStr = Field(alias="initialTurnAttemptId")
    admitted_feature_generation: int = Field(
        ge=1, alias="admittedFeatureGeneration"
    )
    compatibility_version: int = Field(default=1, ge=1, alias="compatibilityVersion")
    policy: OmnigentSessionReconcilePolicy = Field(
        default_factory=OmnigentSessionReconcilePolicy
    )


class OmnigentSessionWorkflowInput(BaseModel):
    """Input for ``MoonMind.OmnigentSession`` — intent plus Continue-As-New carry.

    New sessions are started with only ``intent``. Continue-As-New carries the
    compact frontier, elapsed/observation epochs, and pending control intent so
    replay and history bounds never lose session identity or evidence.
    """

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    intent: OmnigentSessionIntent
    frontier: "OmnigentSessionFrontier | None" = None
    session_start_epoch_seconds: float | None = Field(
        default=None, alias="sessionStartEpochSeconds"
    )
    last_observation_epoch_seconds: float | None = Field(
        default=None, alias="lastObservationEpochSeconds"
    )
    cancel_requested: bool = Field(default=False, alias="cancelRequested")
    cleanup_requested: bool = Field(default=False, alias="cleanupRequested")
    quarantined: bool = Field(default=False, alias="quarantined")


class OmnigentSessionResult(BaseModel):
    """Compact terminal result returned to the owning ``MoonMind.AgentRun``."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    status: OmnigentSessionStatus
    canonical_session_id: str = Field(alias="canonicalSessionId")
    agent_run_id: str = Field(alias="agentRunId")
    reason_codes: tuple[str, ...] = Field(default=(), alias="reasonCodes")
    terminal_result_ref: str | None = Field(default=None, alias="terminalResultRef")
    diagnostics_ref: str | None = Field(default=None, alias="diagnosticsRef")
    summary: str | None = None
    failure_class: str | None = Field(default=None, alias="failureClass")
    turn_attempts: int = Field(default=0, ge=0, alias="turnAttempts")
    observation_count: int = Field(default=0, ge=0, alias="observationCount")
    decision_count: int = Field(default=0, ge=0, alias="decisionCount")


class OmnigentSessionFrontier(BaseModel):
    """Durable observation frontier + fencing generation for one session.

    This is the minimum replay/Continue-As-New state. Full event and decision
    evidence stays outside workflow history (behind refs).
    """

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    fencing_generation: int = Field(default=1, ge=1, alias="fencingGeneration")
    provider_profile_lease_held: bool = Field(
        default=False, alias="providerProfileLeaseHeld"
    )
    host_ready: bool = Field(default=False, alias="hostReady")
    provider_session_established: bool = Field(
        default=False, alias="providerSessionEstablished"
    )
    provider_session_id: str | None = Field(default=None, alias="providerSessionId")
    current_turn_attempt_id: str | None = Field(
        default=None, alias="currentTurnAttemptId"
    )
    turn_submitted: bool = Field(default=False, alias="turnSubmitted")
    turn_attempts: int = Field(default=0, ge=0, alias="turnAttempts")
    terminal_observed: bool = Field(default=False, alias="terminalObserved")
    terminal_outcome: str | None = Field(default=None, alias="terminalOutcome")
    evidence_harvested: bool = Field(default=False, alias="evidenceHarvested")
    workspace_published: bool = Field(default=False, alias="workspacePublished")
    provider_session_stopped: bool = Field(
        default=False, alias="providerSessionStopped"
    )
    host_stopped: bool = Field(default=False, alias="hostStopped")
    leases_released: bool = Field(default=False, alias="leasesReleased")
    observation_count: int = Field(default=0, ge=0, alias="observationCount")
    decision_count: int = Field(default=0, ge=0, alias="decisionCount")
    last_observed_provider_status: str | None = Field(
        default=None, alias="lastObservedProviderStatus"
    )
    terminal_result_ref: str | None = Field(default=None, alias="terminalResultRef")
    diagnostics_ref: str | None = Field(default=None, alias="diagnosticsRef")
    failure_reason: str | None = Field(default=None, alias="failureReason")


class OmnigentSessionSignals(BaseModel):
    """Transient per-loop inputs to the reconciler (not durable frontier)."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    cancel_requested: bool = Field(default=False, alias="cancelRequested")
    cleanup_requested: bool = Field(default=False, alias="cleanupRequested")
    intervention_pending: bool = Field(default=False, alias="interventionPending")
    quarantined: bool = Field(default=False, alias="quarantined")
    last_command_condition: OmnigentSessionCommandCondition = Field(
        default=OmnigentSessionCommandCondition.OK, alias="lastCommandCondition"
    )
    seconds_since_last_observation: float = Field(
        default=0.0, ge=0.0, alias="secondsSinceLastObservation"
    )


class OmnigentSessionCommand(BaseModel):
    """A single bounded, fenced, idempotent command authorized by a decision."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    kind: OmnigentSessionCommandKind
    expected_generation: int = Field(ge=1, alias="expectedGeneration")
    idempotency_key: NonBlankStr = Field(alias="idempotencyKey")
    turn_attempt_id: str | None = Field(default=None, alias="turnAttemptId")
    reason: str | None = Field(default=None)


class OmnigentSessionDecision(BaseModel):
    """The reconciler output: status, reason codes, bounded commands, deadline."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    status: OmnigentSessionStatus
    reason_codes: tuple[str, ...] = Field(default=(), alias="reasonCodes")
    commands: tuple[OmnigentSessionCommand, ...] = ()
    next_deadline_seconds: float | None = Field(
        default=None, alias="nextDeadlineSeconds"
    )
    terminal: bool = False

    @property
    def is_terminal(self) -> bool:
        return self.terminal or self.status in TERMINAL_STATUSES


def _command(
    intent: OmnigentSessionIntent,
    frontier: OmnigentSessionFrontier,
    kind: OmnigentSessionCommandKind,
    *,
    turn_attempt_id: str | None = None,
    reason: str | None = None,
) -> OmnigentSessionCommand:
    turn_component = turn_attempt_id or ""
    idempotency_key = (
        f"{intent.canonical_session_id}:{kind.value}:"
        f"{frontier.fencing_generation}:{turn_component}"
    )
    return OmnigentSessionCommand(
        kind=kind,
        expectedGeneration=frontier.fencing_generation,
        idempotencyKey=idempotency_key,
        turnAttemptId=turn_attempt_id,
        reason=reason,
    )


def _terminal_target_status(
    frontier: OmnigentSessionFrontier,
    signals: OmnigentSessionSignals,
    *,
    timed_out: bool,
) -> OmnigentSessionStatus:
    if signals.cancel_requested:
        return OmnigentSessionStatus.CANCELED
    if timed_out and not frontier.terminal_observed:
        return OmnigentSessionStatus.TIMED_OUT
    outcome = (frontier.terminal_outcome or "").strip().lower()
    if outcome == "failed":
        return OmnigentSessionStatus.EXECUTION_FAILED
    if outcome == "ambiguous":
        return OmnigentSessionStatus.DELIVERY_UNKNOWN
    if frontier.terminal_observed:
        return OmnigentSessionStatus.COMPLETED
    # Cleanup requested without a terminal provider outcome.
    return OmnigentSessionStatus.CANCELED


def _descend_to_terminal(
    intent: OmnigentSessionIntent,
    frontier: OmnigentSessionFrontier,
    signals: OmnigentSessionSignals,
    *,
    terminal_status: OmnigentSessionStatus,
    extra_reasons: tuple[str, ...],
) -> OmnigentSessionDecision:
    """Walk the ordered, independently-retryable cleanup phases.

    Ordering guarantees the Provider Profile lease is released *last* and each
    cleanup step is its own durable phase.
    """

    policy = intent.policy
    backoff = float(policy.retry_backoff_seconds)
    cleanup_failed = (
        signals.last_command_condition
        in (
            OmnigentSessionCommandCondition.INTEGRATION_UNAVAILABLE,
            OmnigentSessionCommandCondition.CLEANUP_FAILED,
        )
    )

    # Preserve terminal evidence before tearing down resources.
    if frontier.terminal_observed and not frontier.evidence_harvested:
        return OmnigentSessionDecision(
            status=OmnigentSessionStatus.HARVESTING,
            reasonCodes=extra_reasons + ("harvesting_evidence",),
            commands=(_command(intent, frontier, OmnigentSessionCommandKind.HARVEST_EVIDENCE),),
            nextDeadlineSeconds=backoff,
            terminal=False,
        )
    if frontier.terminal_observed and not frontier.workspace_published:
        return OmnigentSessionDecision(
            status=OmnigentSessionStatus.PUBLISHING,
            reasonCodes=extra_reasons + ("publishing_workspace",),
            commands=(_command(intent, frontier, OmnigentSessionCommandKind.PUBLISH_WORKSPACE),),
            nextDeadlineSeconds=backoff,
            terminal=False,
        )
    if frontier.provider_session_established and not frontier.provider_session_stopped:
        status = (
            OmnigentSessionStatus.CLEANUP_INCOMPLETE
            if cleanup_failed
            else OmnigentSessionStatus.CLEANING_UP
        )
        reasons = extra_reasons + ("stopping_provider_session",)
        if cleanup_failed:
            reasons = reasons + ("cleanup_incomplete",)
        return OmnigentSessionDecision(
            status=status,
            reasonCodes=reasons,
            commands=(_command(intent, frontier, OmnigentSessionCommandKind.STOP_PROVIDER_SESSION),),
            nextDeadlineSeconds=backoff,
            terminal=False,
        )
    if frontier.host_ready and not frontier.host_stopped:
        status = (
            OmnigentSessionStatus.CLEANUP_INCOMPLETE
            if cleanup_failed
            else OmnigentSessionStatus.CLEANING_UP
        )
        reasons = extra_reasons + ("stopping_host",)
        if cleanup_failed:
            reasons = reasons + ("cleanup_incomplete",)
        return OmnigentSessionDecision(
            status=status,
            reasonCodes=reasons,
            commands=(_command(intent, frontier, OmnigentSessionCommandKind.STOP_HOST),),
            nextDeadlineSeconds=backoff,
            terminal=False,
        )
    if frontier.provider_profile_lease_held and not frontier.leases_released:
        status = (
            OmnigentSessionStatus.CLEANUP_INCOMPLETE
            if cleanup_failed
            else OmnigentSessionStatus.RELEASING_LEASES
        )
        reasons = extra_reasons + ("releasing_leases_last",)
        if cleanup_failed:
            reasons = reasons + ("cleanup_incomplete",)
        return OmnigentSessionDecision(
            status=status,
            reasonCodes=reasons,
            commands=(_command(intent, frontier, OmnigentSessionCommandKind.RELEASE_LEASES),),
            nextDeadlineSeconds=backoff,
            terminal=False,
        )

    # All owned resources are cleaned and the lease is released.
    return OmnigentSessionDecision(
        status=terminal_status,
        reasonCodes=extra_reasons + ("cleanup_complete",),
        commands=(),
        nextDeadlineSeconds=None,
        terminal=True,
    )


def reconcile_omnigent_session(
    intent: OmnigentSessionIntent,
    frontier: OmnigentSessionFrontier,
    signals: OmnigentSessionSignals,
    *,
    elapsed_seconds: float,
) -> OmnigentSessionDecision:
    """Pure reducer: map current authority + observation to the next decision.

    The returned decision authorizes at most the bounded commands needed to
    advance one step toward the desired terminal state, and always keeps a
    periodic snapshot deadline active while correctness could otherwise depend
    on a lost provider event.
    """

    policy = intent.policy

    # Operator- or authority-forced quarantine: never invent success.
    if signals.quarantined:
        return OmnigentSessionDecision(
            status=OmnigentSessionStatus.RECONCILIATION_QUARANTINED,
            reasonCodes=("reconciliation_quarantined",),
            commands=(),
            nextDeadlineSeconds=None,
            terminal=True,
        )

    timed_out = elapsed_seconds >= float(policy.max_session_age_seconds)
    cleanup = signals.cancel_requested or signals.cleanup_requested

    # --- Terminal descent: cancel, cleanup, terminal observed, or proven timeout.
    if cleanup or frontier.terminal_observed:
        terminal_status = _terminal_target_status(frontier, signals, timed_out=timed_out)
        extra_reasons: tuple[str, ...] = ()
        if signals.cancel_requested:
            extra_reasons = ("cancel_requested",)
        elif signals.cleanup_requested:
            extra_reasons = ("cleanup_requested",)
        return _descend_to_terminal(
            intent,
            frontier,
            signals,
            terminal_status=terminal_status,
            extra_reasons=extra_reasons,
        )

    # --- Establish / execute path (no cleanup, no terminal yet).
    if not frontier.provider_profile_lease_held:
        return OmnigentSessionDecision(
            status=OmnigentSessionStatus.AWAITING_LEASE,
            reasonCodes=("ensuring_provider_profile_lease",),
            commands=(
                _command(
                    intent, frontier, OmnigentSessionCommandKind.ENSURE_PROVIDER_PROFILE_LEASE
                ),
            ),
            nextDeadlineSeconds=float(policy.retry_backoff_seconds),
            terminal=False,
        )
    if not frontier.host_ready:
        return OmnigentSessionDecision(
            status=OmnigentSessionStatus.LAUNCHING,
            reasonCodes=("ensuring_host",),
            commands=(_command(intent, frontier, OmnigentSessionCommandKind.ENSURE_HOST),),
            nextDeadlineSeconds=float(policy.retry_backoff_seconds),
            terminal=False,
        )
    if not frontier.provider_session_established:
        return OmnigentSessionDecision(
            status=OmnigentSessionStatus.LAUNCHING,
            reasonCodes=("ensuring_provider_session",),
            commands=(
                _command(intent, frontier, OmnigentSessionCommandKind.ENSURE_PROVIDER_SESSION),
            ),
            nextDeadlineSeconds=float(policy.retry_backoff_seconds),
            terminal=False,
        )

    turn_attempt_id = frontier.current_turn_attempt_id or intent.initial_turn_attempt_id
    if not frontier.turn_submitted:
        if frontier.turn_attempts >= policy.max_turn_attempts:
            return _descend_to_terminal(
                intent,
                frontier,
                signals,
                terminal_status=OmnigentSessionStatus.EXECUTION_FAILED,
                extra_reasons=("max_turn_attempts_exhausted",),
            )
        return OmnigentSessionDecision(
            status=OmnigentSessionStatus.EXECUTING,
            reasonCodes=("submitting_turn",),
            commands=(
                _command(
                    intent,
                    frontier,
                    OmnigentSessionCommandKind.SUBMIT_TURN,
                    turn_attempt_id=turn_attempt_id,
                ),
            ),
            nextDeadlineSeconds=float(policy.retry_backoff_seconds),
            terminal=False,
        )

    # --- Turn submitted; awaiting a provable terminal observation.
    if signals.last_command_condition == OmnigentSessionCommandCondition.INTEGRATION_UNAVAILABLE:
        # Do not repeat a mutating command; re-observe authoritative state.
        return OmnigentSessionDecision(
            status=OmnigentSessionStatus.INTEGRATION_UNAVAILABLE,
            reasonCodes=("integration_unavailable", "reobserving_snapshot"),
            commands=(
                _command(
                    intent,
                    frontier,
                    OmnigentSessionCommandKind.OBSERVE_SNAPSHOT,
                    turn_attempt_id=turn_attempt_id,
                ),
            ),
            nextDeadlineSeconds=float(policy.retry_backoff_seconds),
            terminal=False,
        )
    if signals.last_command_condition == OmnigentSessionCommandCondition.DELIVERY_UNKNOWN:
        # Turn delivery is ambiguous; observe rather than blindly resubmit.
        return OmnigentSessionDecision(
            status=OmnigentSessionStatus.DELIVERY_UNKNOWN,
            reasonCodes=("delivery_unknown", "reobserving_snapshot"),
            commands=(
                _command(
                    intent,
                    frontier,
                    OmnigentSessionCommandKind.OBSERVE_SNAPSHOT,
                    turn_attempt_id=turn_attempt_id,
                ),
            ),
            nextDeadlineSeconds=float(policy.retry_backoff_seconds),
            terminal=False,
        )

    fresh_observation = (
        signals.seconds_since_last_observation <= float(policy.snapshot_interval_seconds)
    )
    if timed_out:
        # A workflow-side timeout must reconcile provider/command state before
        # declaring the provider did not complete. Force one fresh authoritative
        # snapshot; only declare timed_out once that snapshot is fresh and still
        # not terminal.
        if fresh_observation and frontier.observation_count > 0:
            return _descend_to_terminal(
                intent,
                frontier,
                signals,
                terminal_status=OmnigentSessionStatus.TIMED_OUT,
                extra_reasons=("session_age_exceeded", "reconciled_before_timeout"),
            )
        return OmnigentSessionDecision(
            status=OmnigentSessionStatus.AWAITING_OBSERVATION,
            reasonCodes=("session_age_exceeded", "reconciling_before_timeout"),
            commands=(
                _command(
                    intent,
                    frontier,
                    OmnigentSessionCommandKind.OBSERVE_SNAPSHOT,
                    turn_attempt_id=turn_attempt_id,
                ),
            ),
            nextDeadlineSeconds=float(policy.retry_backoff_seconds),
            terminal=False,
        )

    # Periodic authoritative snapshot guarantees convergence when every relevant
    # terminal event is lost; between snapshots, bounded event reads wake the
    # workflow as soon as the provider emits.
    if signals.seconds_since_last_observation >= float(policy.snapshot_interval_seconds):
        return OmnigentSessionDecision(
            status=OmnigentSessionStatus.AWAITING_OBSERVATION,
            reasonCodes=("periodic_authoritative_snapshot",),
            commands=(
                _command(
                    intent,
                    frontier,
                    OmnigentSessionCommandKind.OBSERVE_SNAPSHOT,
                    turn_attempt_id=turn_attempt_id,
                ),
            ),
            nextDeadlineSeconds=float(policy.snapshot_interval_seconds),
            terminal=False,
        )
    return OmnigentSessionDecision(
        status=OmnigentSessionStatus.AWAITING_OBSERVATION,
        reasonCodes=("reading_event_batch",),
        commands=(
            _command(
                intent,
                frontier,
                OmnigentSessionCommandKind.READ_EVENT_BATCH,
                turn_attempt_id=turn_attempt_id,
            ),
        ),
        nextDeadlineSeconds=float(policy.snapshot_interval_seconds),
        terminal=False,
    )


def should_continue_as_new(
    intent: OmnigentSessionIntent,
    frontier: OmnigentSessionFrontier,
    *,
    history_length: int,
    is_suggested: bool = False,
) -> bool:
    """Bounded Continue-As-New criteria based on decision/observation/history.

    Never Continue-As-New once the session frontier is terminal (the workflow
    is about to return), so identity and evidence are preserved.
    """

    policy = intent.policy
    if frontier.leases_released:
        return False
    if is_suggested:
        return True
    if frontier.decision_count >= policy.continue_as_new_decision_threshold:
        return True
    return history_length >= policy.continue_as_new_history_threshold


@dataclass(frozen=True, slots=True)
class OmnigentSessionAdmissionPolicy:
    """Deterministic admission gate for newly selected Omnigent sessions.

    Existing sessions and histories keep their legacy owner; new sessions are
    admitted only when the feature is enabled and they fall inside the frozen
    feature generation's canary cohort.
    """

    enabled: bool = False
    admitted_feature_generation: int = 1
    canary_percent: int = 0
    compatibility_version: int = 1

    def admits(self, canonical_session_id: str) -> bool:
        if not self.enabled:
            return False
        if self.canary_percent >= 100:
            return True
        if self.canary_percent <= 0:
            return False
        digest = hashlib.sha256(canonical_session_id.encode("utf-8")).hexdigest()
        bucket = int(digest[:8], 16) % 100
        return bucket < self.canary_percent


def admit_omnigent_session_intent(
    *,
    canonical_session_id: str,
    execution_intent_ref: str,
    execution_intent_digest: str,
    owning_workflow_id: str,
    step_execution_id: str,
    agent_run_id: str,
    execution_profile_ref: str,
    initial_turn_attempt_id: str,
    policy: OmnigentSessionAdmissionPolicy,
    reconcile_policy: OmnigentSessionReconcilePolicy | None = None,
) -> OmnigentSessionIntent | None:
    """Return a compact intent when the session is admitted, else ``None``.

    Deterministic and side-effect free so it is safe to run inside an activity
    whose result is recorded in workflow history.
    """

    if not execution_profile_ref or not str(execution_profile_ref).strip():
        return None
    if not policy.admits(canonical_session_id):
        return None
    return OmnigentSessionIntent(
        canonicalSessionId=canonical_session_id,
        executionIntentRef=execution_intent_ref,
        executionIntentDigest=execution_intent_digest,
        owningWorkflowId=owning_workflow_id,
        stepExecutionId=step_execution_id,
        agentRunId=agent_run_id,
        executionProfileRef=execution_profile_ref,
        initialTurnAttemptId=initial_turn_attempt_id,
        admittedFeatureGeneration=policy.admitted_feature_generation,
        compatibilityVersion=policy.compatibility_version,
        policy=reconcile_policy or OmnigentSessionReconcilePolicy(),
    )


# Resolve the forward reference now that OmnigentSessionFrontier is defined.
OmnigentSessionWorkflowInput.model_rebuild()


__all__ = [
    "ALL_OMNIGENT_SESSION_ACTIVITY_TYPES",
    "TERMINAL_STATUSES",
    "OmnigentSessionAdmissionPolicy",
    "OmnigentSessionCommand",
    "OmnigentSessionCommandCondition",
    "OmnigentSessionCommandKind",
    "OmnigentSessionDecision",
    "OmnigentSessionFrontier",
    "OmnigentSessionIntent",
    "OmnigentSessionReconcilePolicy",
    "OmnigentSessionResult",
    "OmnigentSessionSignals",
    "OmnigentSessionStatus",
    "OmnigentSessionWorkflowInput",
    "admit_omnigent_session_intent",
    "reconcile_omnigent_session",
    "should_continue_as_new",
]
