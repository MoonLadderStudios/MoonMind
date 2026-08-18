"""Versioned domain contracts for the pure Omnigent lifecycle reconciler.

Source issue: MoonLadderStudios/MoonMind#3702
([Omnigent control plane 1/11] Define a pure lifecycle reconciler and canonical
transition contracts).

This module defines the *typed inputs and outputs* of the pure reconciler. It has
**no** infrastructure imports: no database, network, filesystem, Docker, artifact,
logging, telemetry, or Temporal dependency. It depends only on the standard
library and pydantic.

The vocabularies here generalize behavior that currently lives locally across
``moonmind/omnigent/bridge_events.py`` (provider status normalization, the #3683
terminal vocabulary), ``moonmind/omnigent/bridge_store.py`` (status coalescence),
and ``moonmind/omnigent/execute.py`` (the #3698 missed-terminal-edge snapshot
reconciliation). The reconciler consumes compact domain views of that state; it
does not import those modules so it can stay side-effect free.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel

#: The single supported contract version for every reconciler domain object.
RECONCILER_CONTRACT_VERSION = "v1"

#: Known compatibility-observation versions. Anything else fails closed to
#: quarantine (invariant 6).
KNOWN_COMPATIBILITY_VERSIONS: frozenset[str] = frozenset({"v1"})


class _ReconcilerModel(BaseModel):
    """Base for every reconciler contract object.

    ``extra="forbid"`` rejects unknown fields, ``frozen=True`` keeps inputs and
    decisions immutable (which is what makes equal-input determinism testable),
    and the camelCase alias generator matches the repository wire convention
    while ``populate_by_name`` still allows snake_case construction.
    """

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
        frozen=True,
    )


# ---------------------------------------------------------------------------
# Closed vocabularies
# ---------------------------------------------------------------------------


class DesiredLifecycle(str, Enum):
    """Operator/authoring intent for where the session should end up."""

    RUN = "run"
    CANCEL = "cancel"


class SubmissionState(str, Enum):
    """Durable knowledge about the current turn-attempt submission."""

    NOT_SUBMITTED = "not_submitted"
    #: The submit command was issued but durable acceptance is not confirmed.
    #: Delivery is ambiguous, so the command must not be reissued (invariant 7).
    IN_FLIGHT = "in_flight"
    ACCEPTED = "accepted"


class LeaseState(str, Enum):
    """Durable authority over a Provider Profile / host lease."""

    NONE = "none"
    HELD = "held"
    RELEASED = "released"


class TerminalOutcome(str, Enum):
    """Canonical terminal outcome recorded on the durable session."""

    SUCCESS = "success"
    FAILURE = "failure"
    CANCELLED = "cancelled"


class ProviderStatusClass(str, Enum):
    """Classification of a raw provider status string.

    ``UNKNOWN`` is the fail-closed bucket for status vocabulary the reconciler
    does not recognize (invariant 6).
    """

    ACTIVE = "active"
    IDLE = "idle"
    TERMINAL_SUCCESS = "terminal_success"
    TERMINAL_FAILURE = "terminal_failure"
    TERMINAL_CANCELLED = "terminal_cancelled"
    UNKNOWN = "unknown"


class SessionLifecyclePhase(str, Enum):
    """Derived, monotonic view of durable lifecycle progression."""

    INITIALIZING = "initializing"
    PROFILE_LEASE_HELD = "profile_lease_held"
    HOST_READY = "host_ready"
    PROVIDER_SESSION_READY = "provider_session_ready"
    TURN_IN_FLIGHT = "turn_in_flight"
    TERMINAL_RECORDED = "terminal_recorded"
    EVIDENCE_HARVESTED = "evidence_harvested"
    CLEANUP_STARTED = "cleanup_started"
    LEASES_RELEASED = "leases_released"
    CLOSED = "closed"
    FAILED = "failed"
    QUARANTINED = "quarantined"


#: Monotonic ordering for the healthy (non-divergent) lifecycle phases. The
#: divergent terminals ``FAILED``/``QUARANTINED`` are intentionally excluded from
#: the forward ordering.
LINEAR_PHASE_ORDER: dict[SessionLifecyclePhase, int] = {
    SessionLifecyclePhase.INITIALIZING: 0,
    SessionLifecyclePhase.PROFILE_LEASE_HELD: 1,
    SessionLifecyclePhase.HOST_READY: 2,
    SessionLifecyclePhase.PROVIDER_SESSION_READY: 3,
    SessionLifecyclePhase.TURN_IN_FLIGHT: 4,
    SessionLifecyclePhase.TERMINAL_RECORDED: 5,
    SessionLifecyclePhase.EVIDENCE_HARVESTED: 6,
    SessionLifecyclePhase.CLEANUP_STARTED: 7,
    SessionLifecyclePhase.LEASES_RELEASED: 8,
    SessionLifecyclePhase.CLOSED: 9,
}


class DecisionKind(str, Enum):
    """Closed, versioned decision vocabulary (see issue #3702)."""

    NO_OP = "no_op"
    AWAIT_OBSERVATION = "await_observation"
    ENSURE_PROFILE_LEASE = "ensure_profile_lease"
    ENSURE_HOST = "ensure_host"
    ENSURE_PROVIDER_SESSION = "ensure_provider_session"
    SUBMIT_TURN = "submit_turn"
    RECORD_PROVIDER_TERMINAL = "record_provider_terminal"
    SYNTHESIZE_TERMINAL_FROM_SNAPSHOT = "synthesize_terminal_from_snapshot"
    HARVEST_EVIDENCE = "harvest_evidence"
    BEGIN_CLEANUP = "begin_cleanup"
    RELEASE_LEASES = "release_leases"
    RETRY_TRANSIENT_OBSERVATION = "retry_transient_observation"
    QUARANTINE_AMBIGUOUS_STATE = "quarantine_ambiguous_state"
    FAIL_NONRETRYABLE = "fail_nonretryable"


#: Decision kinds that carry a durable side-effect command specification.
COMMAND_DECISION_KINDS: frozenset[DecisionKind] = frozenset(
    {
        DecisionKind.ENSURE_PROFILE_LEASE,
        DecisionKind.ENSURE_HOST,
        DecisionKind.ENSURE_PROVIDER_SESSION,
        DecisionKind.SUBMIT_TURN,
        DecisionKind.RECORD_PROVIDER_TERMINAL,
        DecisionKind.SYNTHESIZE_TERMINAL_FROM_SNAPSHOT,
        DecisionKind.HARVEST_EVIDENCE,
        DecisionKind.BEGIN_CLEANUP,
        DecisionKind.RELEASE_LEASES,
    }
)

#: Decision kinds that represent a settled (non-waiting) end state and therefore
#: do not require a bounded next deadline (invariant 10 is satisfied vacuously).
SETTLED_DECISION_KINDS: frozenset[DecisionKind] = frozenset(
    {
        DecisionKind.NO_OP,
        DecisionKind.QUARANTINE_AMBIGUOUS_STATE,
        DecisionKind.FAIL_NONRETRYABLE,
    }
)


class ReasonCode(str, Enum):
    """Stable reason codes attached to every decision."""

    # Version / compatibility
    UNKNOWN_INPUT_VERSION = "unknown_input_version"
    UNKNOWN_COMPATIBILITY_VERSION = "unknown_compatibility_version"
    RUNTIME_NOT_READY = "runtime_not_ready"

    # Sticky durable meta states
    SESSION_FAILED = "session_failed"
    SESSION_QUARANTINED = "session_quarantined"

    # Forward provisioning path
    DESIRED_CANCELLATION = "desired_cancellation"
    PROFILE_LEASE_REQUIRED = "profile_lease_required"
    HOST_REQUIRED = "host_required"
    PROVIDER_SESSION_REQUIRED = "provider_session_required"
    TURN_SUBMISSION_REQUIRED = "turn_submission_required"
    SUBMISSION_DELIVERY_AMBIGUOUS = "submission_delivery_ambiguous"
    MAX_TURN_ATTEMPTS_EXHAUSTED = "max_turn_attempts_exhausted"

    # Terminal detection
    AWAITING_PROVIDER_SNAPSHOT = "awaiting_provider_snapshot"
    PROVIDER_SESSION_MISSING = "provider_session_missing"
    UNKNOWN_PROVIDER_STATUS = "unknown_provider_status"
    PROVIDER_RUNNING = "provider_running"
    IDLE_WITH_OPEN_TOOL_CALL = "idle_with_open_tool_call"
    IDLE_PENDING_TURN_EVIDENCE = "idle_pending_turn_evidence"
    TERMINAL_EVENT_OBSERVED = "terminal_event_observed"
    TERMINAL_SNAPSHOT_SYNTHESIS = "terminal_snapshot_synthesis"
    TERMINAL_IDLE_SYNTHESIS = "terminal_idle_synthesis"

    # Post-terminal stale / contradiction handling
    CONTRADICTORY_TERMINAL_OUTCOME = "contradictory_terminal_outcome"
    IGNORED_STALE_RUNNING_AFTER_TERMINAL = "ignored_stale_running_after_terminal"

    # Post-terminal harvest / cleanup / release
    EVIDENCE_HARVEST_REQUIRED = "evidence_harvest_required"
    AWAITING_EVIDENCE = "awaiting_evidence"
    EVIDENCE_NOT_YET_AVAILABLE = "evidence_not_yet_available"
    CLEANUP_REQUIRED = "cleanup_required"
    LEASE_RELEASE_REQUIRED = "lease_release_required"
    LEASE_CONSUMERS_ACTIVE = "lease_consumers_active"
    CLEANUP_INCOMPLETE_BEFORE_RELEASE = "cleanup_incomplete_before_release"
    SESSION_CLOSED = "session_closed"


class EvidenceRequirement(str, Enum):
    """Evidence a decision expects the executor to have or produce."""

    PROVIDER_TERMINAL_SNAPSHOT = "provider_terminal_snapshot"
    PROVIDER_TURN_TRANSCRIPT = "provider_turn_transcript"
    TERMINAL_EVIDENCE_ARTIFACT = "terminal_evidence_artifact"
    CLEANUP_EVIDENCE = "cleanup_evidence"
    LEASE_RELEASE_CONFIRMATION = "lease_release_confirmation"


# ---------------------------------------------------------------------------
# Immutable intent
# ---------------------------------------------------------------------------


class CompiledSessionIntent(_ReconcilerModel):
    """Compact domain view of the immutable execution contract.

    The full artifact-backed execution-intent contract is owned by the typed
    intent issue under #3701. This is only the slice the reconciler needs. The
    ``provider`` field is declarative and is **never** used as authority for a
    side effect (invariant 11).
    """

    schema_version: Literal["v1"] = RECONCILER_CONTRACT_VERSION
    session_id: str
    provider: str
    requires_profile_lease: bool = True
    requires_host: bool = True
    requires_cleanup: bool = True
    max_turn_attempts: int = 1
    reconcile_interval_seconds: int = 30
    turn_prompt_digest: str


# ---------------------------------------------------------------------------
# Durable state
# ---------------------------------------------------------------------------


class PriorDecisionSummary(_ReconcilerModel):
    """Bounded summary of the previous decision (no secrets, no free text)."""

    schema_version: Literal["v1"] = RECONCILER_CONTRACT_VERSION
    kind: DecisionKind
    reason_code: ReasonCode
    at_revision: int


class DurableSessionState(_ReconcilerModel):
    """Lifecycle state and durable authority needed for a decision.

    Every authority value (identity, revision, fencing generation, lease and
    attachment state, terminal outcome) is durable; observations never supply
    these (invariant 11). ``terminal_outcome is not None`` means the canonical
    session terminal has been recorded, which is distinct from cleanup
    completion (invariant 9) and from an individual attempt's terminality
    (invariant 4).
    """

    schema_version: Literal["v1"] = RECONCILER_CONTRACT_VERSION
    session_id: str
    revision: int
    owner_token: str
    fencing_generation: int
    desired: DesiredLifecycle = DesiredLifecycle.RUN

    provider_session_attached: bool = False
    provider_session_id: str | None = None

    attempt_id: str | None = None
    submission: SubmissionState = SubmissionState.NOT_SUBMITTED
    turn_attempts: int = 0

    profile_lease: LeaseState = LeaseState.NONE
    host_lease: LeaseState = LeaseState.NONE

    last_cursor: str | None = None
    last_snapshot_digest: str | None = None

    terminal_outcome: TerminalOutcome | None = None
    terminal_evidence_ref: str | None = None
    evidence_harvested: bool = False

    cleanup_started: bool = False
    cleanup_complete: bool = False

    failed: bool = False
    quarantined: bool = False

    next_deadline: datetime | None = None
    prior_decision: PriorDecisionSummary | None = None


# ---------------------------------------------------------------------------
# Observations
# ---------------------------------------------------------------------------


class ProviderSessionObservation(_ReconcilerModel):
    """Independently sourced provider *session* snapshot.

    ``present=False`` is an observed-negative (the provider reports no session);
    an *absent* observation is represented by ``ObservationSet.provider_session``
    being ``None``.
    """

    observed_at: datetime
    present: bool = True
    provider_session_id: str | None = None
    raw_status: str
    open_tool_call: bool = False
    cursor: str | None = None
    snapshot_digest: str | None = None


class ProviderTurnObservation(_ReconcilerModel):
    """Independently sourced provider *turn/transcript* snapshot."""

    observed_at: datetime
    attempt_id: str | None = None
    turn_complete: bool = False
    raw_status: str | None = None
    outcome: TerminalOutcome | None = None


class EventFrontierObservation(_ReconcilerModel):
    """Normalized event-frontier observation.

    ``terminal_event_seen`` distinguishes a normally-observed provider terminal
    edge from a missed one that must be recovered from snapshot evidence (#3698).
    """

    observed_at: datetime
    last_cursor: str | None = None
    terminal_event_seen: bool = False
    running_event_after_cursor: bool = False


class HostObservation(_ReconcilerModel):
    """Host registration and runner state."""

    observed_at: datetime
    registered: bool = False
    runner_ready: bool = False


class LeaseObservation(_ReconcilerModel):
    """Lease state for a Provider Profile lease or host lease.

    ``consumer_active`` reports whether a credential/host consumer is still
    observed using the lease (invariant 8).
    """

    observed_at: datetime
    held: bool = False
    consumer_active: bool = False


class WorkspaceObservation(_ReconcilerModel):
    """Workspace and checkpoint availability."""

    observed_at: datetime
    workspace_available: bool = False
    checkpoint_available: bool = False


class EvidenceObservation(_ReconcilerModel):
    """Artifact and terminal-evidence availability."""

    observed_at: datetime
    terminal_evidence_available: bool = False
    artifacts_available: bool = False


class CompatibilityObservation(_ReconcilerModel):
    """Compatibility and runtime-readiness state."""

    observed_at: datetime
    compatibility_version: str
    runtime_ready: bool = True


class ObservationSet(_ReconcilerModel):
    """Independently sourced, timestamped observations.

    Each field being ``None`` means *not observed*; a present sub-observation
    with a negative flag means *observed negative*. That distinction is required
    by the issue and is load-bearing throughout the reducer.
    """

    schema_version: Literal["v1"] = RECONCILER_CONTRACT_VERSION
    provider_session: ProviderSessionObservation | None = None
    provider_turn: ProviderTurnObservation | None = None
    event_frontier: EventFrontierObservation | None = None
    host: HostObservation | None = None
    profile_lease: LeaseObservation | None = None
    host_lease: LeaseObservation | None = None
    workspace: WorkspaceObservation | None = None
    evidence: EvidenceObservation | None = None
    compatibility: CompatibilityObservation | None = None

    def present_observation_kinds(self) -> tuple[str, ...]:
        """Return the names of present observations in a deterministic order."""

        kinds: list[str] = []
        for name in (
            "provider_session",
            "provider_turn",
            "event_frontier",
            "host",
            "profile_lease",
            "host_lease",
            "workspace",
            "evidence",
            "compatibility",
        ):
            if getattr(self, name) is not None:
                kinds.append(name)
        return tuple(kinds)


# ---------------------------------------------------------------------------
# Decision
# ---------------------------------------------------------------------------


class CommandSpec(_ReconcilerModel):
    """Specification of the side effect a command decision authorizes.

    ``command_id`` is a deterministic idempotency identity so the executor never
    performs the same logical command twice (invariant 7). The provider session
    identity here is copied from durable authority only, never from an
    observation (invariant 11).
    """

    schema_version: Literal["v1"] = RECONCILER_CONTRACT_VERSION
    command_kind: DecisionKind
    command_id: str
    attempt_id: str | None = None
    provider_session_id: str | None = None


class DecisionDiagnostics(_ReconcilerModel):
    """Bounded, non-sensitive diagnostics.

    Only enum codes, booleans, and observation *kind names* are recorded here.
    No workflow, user, provider-session, host, profile, credential, or workspace
    secret is ever included (issue acceptance criterion).
    """

    schema_version: Literal["v1"] = RECONCILER_CONTRACT_VERSION
    present_observations: tuple[str, ...] = ()
    provider_status_class: ProviderStatusClass | None = None


class ReconciliationDecision(_ReconcilerModel):
    """The single output of :func:`moonmind.omnigent.reconciler.reconcile`.

    Carries the stable reason code, the durable revision and fencing generation
    the command must be applied against (so execution cannot ignore concurrency
    authority), an optional command specification, the bounded next
    reconciliation deadline, evidence requirements, and whether the decision
    changes product-visible state.
    """

    schema_version: Literal["v1"] = RECONCILER_CONTRACT_VERSION
    kind: DecisionKind
    reason_code: ReasonCode
    expected_revision: int
    expected_fencing_generation: int
    command: CommandSpec | None = None
    next_deadline: datetime | None = None
    evidence_requirements: tuple[EvidenceRequirement, ...] = ()
    changes_product_visible_state: bool = False
    diagnostics: DecisionDiagnostics = DecisionDiagnostics()


# ---------------------------------------------------------------------------
# Shadow-mode comparison
# ---------------------------------------------------------------------------


class ShadowComparison(_ReconcilerModel):
    """Bounded comparison of a legacy action against a reconciler decision.

    Used to run the reconciler in shadow mode along the existing execution path
    without making it a second orchestration source of truth: it only reports
    agreement/divergence, it never acts.
    """

    schema_version: Literal["v1"] = RECONCILER_CONTRACT_VERSION
    legacy_action: str
    decision_kind: DecisionKind
    agreement: bool
    divergence_reason: str | None = None


__all__ = [
    "RECONCILER_CONTRACT_VERSION",
    "KNOWN_COMPATIBILITY_VERSIONS",
    "DesiredLifecycle",
    "SubmissionState",
    "LeaseState",
    "TerminalOutcome",
    "ProviderStatusClass",
    "SessionLifecyclePhase",
    "LINEAR_PHASE_ORDER",
    "DecisionKind",
    "COMMAND_DECISION_KINDS",
    "SETTLED_DECISION_KINDS",
    "ReasonCode",
    "EvidenceRequirement",
    "CompiledSessionIntent",
    "PriorDecisionSummary",
    "DurableSessionState",
    "ProviderSessionObservation",
    "ProviderTurnObservation",
    "EventFrontierObservation",
    "HostObservation",
    "LeaseObservation",
    "WorkspaceObservation",
    "EvidenceObservation",
    "CompatibilityObservation",
    "ObservationSet",
    "CommandSpec",
    "DecisionDiagnostics",
    "ReconciliationDecision",
    "ShadowComparison",
]
