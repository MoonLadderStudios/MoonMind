"""Typed, versioned domain inputs for the Omnigent lifecycle reconciler.

Tracks MoonLadderStudios/MoonMind#3702 ([Omnigent control plane 1/11]).

Three immutable inputs cross the reducer boundary:

* :class:`CompiledSessionIntent` — a compact domain view of the immutable
  execution contract (the full artifact-backed contract is owned by #3701);
* :class:`DurableSessionState` — the authoritative lifecycle state and durable
  authority the reducer trusts;
* :class:`ObservationSet` — independently-sourced, timestamped observations that
  are *evidence*, never authority (invariant 1 / invariant 11).

Every object validates its ``schema_version`` at construction (the fail policy
for unknown envelope versions) and rejects unknown fields structurally because
frozen dataclasses do not accept unexpected keyword arguments. No I/O here.

An **absent** observation (never fetched) is distinguishable from an **observed
negative** (fetched, source-confirmed unavailable) via
:class:`ObservationPresence`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Generic, TypeVar

from moonmind.omnigent.reconciler.versions import (
    DURABLE_STATE_SCHEMA_VERSION,
    INTENT_SCHEMA_VERSION,
    OBSERVATION_SET_SCHEMA_VERSION,
    SUPPORTED_DURABLE_STATE_VERSIONS,
    SUPPORTED_INTENT_VERSIONS,
    SUPPORTED_OBSERVATION_SET_VERSIONS,
    require_supported_version,
)
from moonmind.omnigent.reconciler.vocabulary import (
    DesiredLifecycle,
    DurablePhase,
    TurnSubmissionState,
)

T = TypeVar("T")


# --- Observation envelope ---------------------------------------------------


class ObservationPresence(str, Enum):
    """Whether an observation was taken, and if so what it found."""

    ABSENT = "absent"  # not observed this cycle (no evidence either way)
    PRESENT = "present"  # observed and carries a value
    NEGATIVE = "negative"  # observed, source-confirmed unavailable / errored


@dataclass(frozen=True, slots=True)
class Observation(Generic[T]):
    """A single independently-sourced, timestamped observation.

    ``ABSENT`` (no evidence) is deliberately distinct from ``NEGATIVE`` (source
    was reached and reported the thing is not there / failed transiently).
    """

    presence: ObservationPresence = ObservationPresence.ABSENT
    value: T | None = None
    observed_at: datetime | None = None
    source: str = ""

    @classmethod
    def absent(cls) -> "Observation[T]":
        return cls(presence=ObservationPresence.ABSENT)

    @classmethod
    def present(
        cls, value: T, *, observed_at: datetime | None = None, source: str = ""
    ) -> "Observation[T]":
        return cls(
            presence=ObservationPresence.PRESENT,
            value=value,
            observed_at=observed_at,
            source=source,
        )

    @classmethod
    def negative(
        cls, *, observed_at: datetime | None = None, source: str = ""
    ) -> "Observation[T]":
        return cls(
            presence=ObservationPresence.NEGATIVE,
            observed_at=observed_at,
            source=source,
        )

    @property
    def is_present(self) -> bool:
        return self.presence is ObservationPresence.PRESENT and self.value is not None

    @property
    def is_absent(self) -> bool:
        return self.presence is ObservationPresence.ABSENT

    @property
    def is_negative(self) -> bool:
        return self.presence is ObservationPresence.NEGATIVE


# --- Observation payloads ---------------------------------------------------


@dataclass(frozen=True, slots=True)
class ProviderSessionSnapshot:
    """Authoritative provider-session snapshot (raw, unnormalized status)."""

    provider_session_id: str
    raw_status: str
    snapshot_digest: str = ""
    cursor: int = 0


@dataclass(frozen=True, slots=True)
class ProviderTurnSnapshot:
    """Provider turn / transcript snapshot for the current attempt."""

    attempt_id: str
    raw_status: str
    has_active_tool_call: bool = False
    response_recorded: bool = False
    transcript_digest: str = ""


@dataclass(frozen=True, slots=True)
class EventFrontier:
    """Normalized event frontier derived from the provider stream."""

    last_cursor: int
    terminal_status: str | None = None  # normalized terminal status if seen
    has_pending_tool_call: bool = False


@dataclass(frozen=True, slots=True)
class HostRuntimeState:
    """Host registration and runner readiness."""

    registered: bool
    runner_ready: bool


@dataclass(frozen=True, slots=True)
class LeaseObservation:
    """Observed host and Provider Profile lease state."""

    profile_lease_held: bool
    host_lease_held: bool
    active_consumers: int = 0


@dataclass(frozen=True, slots=True)
class WorkspaceState:
    """Workspace and checkpoint availability."""

    workspace_available: bool
    checkpoint_available: bool


@dataclass(frozen=True, slots=True)
class EvidenceAvailability:
    """Artifact and terminal-evidence availability."""

    artifact_available: bool
    terminal_evidence_available: bool


@dataclass(frozen=True, slots=True)
class RuntimeReadiness:
    """Compatibility and runtime-readiness state (raw compatibility token)."""

    raw_compatibility: str
    ready: bool


@dataclass(frozen=True, slots=True)
class ObservationSet:
    """A bundle of independently-sourced observations for one reconcile cycle."""

    provider_session: Observation[ProviderSessionSnapshot] = field(
        default_factory=Observation.absent
    )
    provider_turn: Observation[ProviderTurnSnapshot] = field(
        default_factory=Observation.absent
    )
    event_frontier: Observation[EventFrontier] = field(
        default_factory=Observation.absent
    )
    host_runtime: Observation[HostRuntimeState] = field(
        default_factory=Observation.absent
    )
    leases: Observation[LeaseObservation] = field(default_factory=Observation.absent)
    workspace: Observation[WorkspaceState] = field(default_factory=Observation.absent)
    evidence: Observation[EvidenceAvailability] = field(
        default_factory=Observation.absent
    )
    runtime_readiness: Observation[RuntimeReadiness] = field(
        default_factory=Observation.absent
    )
    schema_version: str = OBSERVATION_SET_SCHEMA_VERSION

    def __post_init__(self) -> None:
        require_supported_version(
            "ObservationSet",
            self.schema_version,
            SUPPORTED_OBSERVATION_SET_VERSIONS,
        )


# --- Intent -----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CompiledSessionIntent:
    """Compact domain view of the immutable execution contract for reconciliation.

    The full artifact-backed execution-intent contract is owned by #3701; this
    is only the minimal, immutable shape the reducer needs.
    """

    session_id: str
    provider: str
    agent_name: str
    max_turn_attempts: int = 1
    reconcile_interval_seconds: int = 10
    max_reconcile_interval_seconds: int = 60
    requires_cleanup: bool = True
    schema_version: str = INTENT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        require_supported_version(
            "CompiledSessionIntent", self.schema_version, SUPPORTED_INTENT_VERSIONS
        )
        if not str(self.session_id).strip():
            raise ValueError("CompiledSessionIntent.session_id must be non-empty")
        if self.max_turn_attempts < 1:
            raise ValueError("CompiledSessionIntent.max_turn_attempts must be >= 1")
        if self.reconcile_interval_seconds <= 0:
            raise ValueError(
                "CompiledSessionIntent.reconcile_interval_seconds must be positive"
            )
        if self.max_reconcile_interval_seconds < self.reconcile_interval_seconds:
            raise ValueError(
                "max_reconcile_interval_seconds must be >= reconcile_interval_seconds"
            )


# --- Durable state ----------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TurnAttempt:
    """Current turn-attempt identity and durable submission knowledge."""

    attempt_id: str
    attempt_number: int
    submission_state: TurnSubmissionState
    retries_remaining: int = 0


@dataclass(frozen=True, slots=True)
class TerminalEvidence:
    """Durable terminal evidence recorded for the canonical session."""

    status: str  # normalized terminal status
    source: str  # e.g. "provider_event" | "snapshot_synthesis" | "operator"
    recorded: bool = True


@dataclass(frozen=True, slots=True)
class DurableSessionState:
    """Authoritative lifecycle state and durable authority for a decision.

    Every identity the reducer trusts lives here (invariant 11). Observations
    may only corroborate or contradict these values; they never replace them.
    """

    session_id: str
    revision: int
    owner_token: str
    fencing_generation: int
    desired: DesiredLifecycle
    phase: DurablePhase
    provider_session_id: str | None = None
    turn_attempt: TurnAttempt | None = None
    profile_lease_held: bool = False
    host_lease_held: bool = False
    accepted_cursor: int = 0
    snapshot_digest: str | None = None
    terminal_evidence: TerminalEvidence | None = None
    cleanup_complete: bool = False
    next_deadline: datetime | None = None
    last_decision_action: str | None = None
    schema_version: str = DURABLE_STATE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        require_supported_version(
            "DurableSessionState",
            self.schema_version,
            SUPPORTED_DURABLE_STATE_VERSIONS,
        )
        if not str(self.session_id).strip():
            raise ValueError("DurableSessionState.session_id must be non-empty")
        if self.revision < 0:
            raise ValueError("DurableSessionState.revision must be >= 0")
        if self.fencing_generation < 0:
            raise ValueError("DurableSessionState.fencing_generation must be >= 0")


__all__ = [
    "CompiledSessionIntent",
    "DurableSessionState",
    "EventFrontier",
    "EvidenceAvailability",
    "HostRuntimeState",
    "LeaseObservation",
    "Observation",
    "ObservationPresence",
    "ObservationSet",
    "ProviderSessionSnapshot",
    "ProviderTurnSnapshot",
    "RuntimeReadiness",
    "TerminalEvidence",
    "TurnAttempt",
    "WorkspaceState",
]
