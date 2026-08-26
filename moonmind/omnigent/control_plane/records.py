"""Domain records, errors, and helpers for the Omnigent control plane.

Source: MoonLadderStudios/MoonMind#3703 ([Omnigent control plane 2/11]).

These frozen dataclasses are the domain-facing shape of the Omnigent
control-plane aggregates. Repositories translate SQLAlchemy ORM rows into these
records so application and domain code never depends on SQLAlchemy models
directly.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Optional

# Versioned schemas: every aggregate row carries ``schema_version``. Reading or
# writing an unsupported version fails closed (Compatibility Policy) rather than
# silently coercing.
SUPPORTED_SCHEMA_VERSIONS: frozenset[int] = frozenset({1})
CURRENT_SCHEMA_VERSION: int = 1


# --- Session lifecycle vocabulary -------------------------------------------

# Turn-attempt delivery lifecycle (attempt-owned, never session-owned).
TURN_STATE_PREPARED = "prepared"
TURN_STATE_DISPATCHING = "dispatching"
TURN_STATE_DELIVERY_UNKNOWN = "delivery_unknown"
TURN_STATE_ACCEPTED = "accepted"
TURN_STATE_RUNNING = "running"
TURN_STATE_TERMINAL = "terminal"

TURN_STATES: frozenset[str] = frozenset(
    {
        TURN_STATE_PREPARED,
        TURN_STATE_DISPATCHING,
        TURN_STATE_DELIVERY_UNKNOWN,
        TURN_STATE_ACCEPTED,
        TURN_STATE_RUNNING,
        TURN_STATE_TERMINAL,
    }
)

# Monotonic delivery order for the turn lifecycle (#3704). A guarded turn write
# may only advance the state forward or leave it unchanged; an out-of-order
# provider observation must never regress durable attempt authority even when its
# revision and fencing generation are current. ``delivery_unknown`` follows
# ``dispatching`` because it records an ambiguous dispatch that later resolves to
# ``accepted``/``running`` (forward) or a terminal.
TURN_STATE_ORDER: dict[str, int] = {
    TURN_STATE_PREPARED: 0,
    TURN_STATE_DISPATCHING: 1,
    TURN_STATE_DELIVERY_UNKNOWN: 2,
    TURN_STATE_ACCEPTED: 3,
    TURN_STATE_RUNNING: 4,
    TURN_STATE_TERMINAL: 5,
}

# Chat-binding alias resolution states.
ALIAS_STATE_ACTIVE = "active"
ALIAS_STATE_QUARANTINED = "quarantined"
ALIAS_STATE_DIAGNOSTIC = "diagnostic"


# --- Concurrency & fencing vocabulary (MoonLadderStudios/MoonMind#3704) -------


class ControlPlaneOutcome(str, Enum):
    """Stable outcome of an optimistic-concurrency / fencing-guarded write.

    Source: MoonLadderStudios/MoonMind#3704 ([Omnigent control plane 3/11]).

    A conflict is observable and actionable but is not itself an execution
    failure: normal reconciliation reloads current authority and converges. The
    outcome vocabulary is closed and low-cardinality so it can be a metric label
    without leaking workflow/session/host/lease identity.
    """

    #: The guarded mutation was applied and advanced the aggregate's revision.
    APPLIED = "applied"
    #: The mutation's effect was already durably present (idempotent replay);
    #: nothing changed and no conflict occurred.
    ALREADY_APPLIED = "already_applied"
    #: The expected revision did not match current authority (lost update).
    REVISION_CONFLICT = "revision_conflict"
    #: The presented fencing generation is not the current owner generation.
    FENCING_CONFLICT = "fencing_conflict"
    #: A provider side effect may already have occurred; reconcile rather than
    #: issuing a second command blindly.
    DELIVERY_UNKNOWN = "delivery_unknown"
    #: A conflicting immutable authority (identity/terminal) was observed.
    IMMUTABLE_AUTHORITY_CONFLICT = "immutable_authority_conflict"
    #: The caller is not the current owner of the resource it tried to mutate.
    NOT_OWNER = "not_owner"


#: Outcomes that represent a converged (no-conflict) write.
APPLIED_OUTCOMES: frozenset[ControlPlaneOutcome] = frozenset(
    {ControlPlaneOutcome.APPLIED, ControlPlaneOutcome.ALREADY_APPLIED}
)

#: Outcomes that require the caller to reload current authority and reconcile.
CONFLICT_OUTCOMES: frozenset[ControlPlaneOutcome] = frozenset(
    {
        ControlPlaneOutcome.REVISION_CONFLICT,
        ControlPlaneOutcome.FENCING_CONFLICT,
        ControlPlaneOutcome.DELIVERY_UNKNOWN,
        ControlPlaneOutcome.IMMUTABLE_AUTHORITY_CONFLICT,
        ControlPlaneOutcome.NOT_OWNER,
    }
)


class FencingScope(str, Enum):
    """Explicit fencing-generation owner for a side effect.

    Each scope is an independent monotonic generation so a superseded owner of
    one resource cannot mutate durable state after a strictly newer owner is
    acquired. ``SESSION_SUPERVISOR`` guards session-lifecycle and command
    execution; the lease scopes guard the Provider Profile and host leases; and
    ``CLEANUP`` guards durable cleanup/janitor authority.
    """

    SESSION_SUPERVISOR = "session_supervisor"
    PROVIDER_PROFILE_LEASE = "provider_profile_lease"
    HOST_LEASE = "host_lease"
    CLEANUP = "cleanup"


# Command execution / delivery lifecycle. A logical command is claimed once,
# then transitions monotonically toward a terminal (applied/failed) state. The
# ``delivery_unknown`` state records that a provider side effect may already have
# occurred and must be reconciled rather than reissued.
COMMAND_STATE_PENDING = "pending"
COMMAND_STATE_CLAIMED = "claimed"
COMMAND_STATE_DELIVERY_UNKNOWN = "delivery_unknown"
COMMAND_STATE_APPLIED = "applied"
COMMAND_STATE_FAILED = "failed"

COMMAND_STATES: frozenset[str] = frozenset(
    {
        COMMAND_STATE_PENDING,
        COMMAND_STATE_CLAIMED,
        COMMAND_STATE_DELIVERY_UNKNOWN,
        COMMAND_STATE_APPLIED,
        COMMAND_STATE_FAILED,
    }
)

#: Command states past which no further delivery transition is allowed.
COMMAND_TERMINAL_STATES: frozenset[str] = frozenset(
    {COMMAND_STATE_APPLIED, COMMAND_STATE_FAILED}
)


# Durable cleanup-authority lifecycle (fenced against host/provider/lease
# generations). Exactly one janitor may hold ``claimed`` at a time.
CLEANUP_STATE_UNCLAIMED = "unclaimed"
CLEANUP_STATE_CLAIMED = "claimed"
CLEANUP_STATE_COMPLETE = "complete"

CLEANUP_STATES: frozenset[str] = frozenset(
    {CLEANUP_STATE_UNCLAIMED, CLEANUP_STATE_CLAIMED, CLEANUP_STATE_COMPLETE}
)


# --- Errors ------------------------------------------------------------------


class OmnigentControlPlaneError(RuntimeError):
    """Base class for control-plane repository errors."""


class UnknownSchemaVersionError(OmnigentControlPlaneError):
    """Raised when a row's ``schema_version`` is outside the supported set."""

    def __init__(self, aggregate: str, version: int) -> None:
        self.aggregate = aggregate
        self.version = version
        super().__init__(
            f"{aggregate} schema_version {version!r} is not supported "
            f"(supported: {sorted(SUPPORTED_SCHEMA_VERSIONS)})"
        )


class ControlPlaneConflictError(OmnigentControlPlaneError):
    """A concurrency/fencing conflict carrying a stable :class:`ControlPlaneOutcome`.

    Source: MoonLadderStudios/MoonMind#3704. Repository methods that fail closed
    on a conflict raise a subclass of this error; the ``outcome`` attribute lets
    reconcilers, telemetry, and callers branch on the stable outcome vocabulary
    instead of parsing messages or collapsing everything into a generic database
    error.
    """

    #: Overridden per subclass; the fail-closed default is the most conservative.
    outcome: ControlPlaneOutcome = ControlPlaneOutcome.IMMUTABLE_AUTHORITY_CONFLICT

    def __init__(
        self, message: str, *, outcome: Optional[ControlPlaneOutcome] = None
    ) -> None:
        if outcome is not None:
            self.outcome = outcome
        super().__init__(message)


class ConflictingSessionAuthorityError(ControlPlaneConflictError):
    """Raised when creating/binding a canonical session would create a second
    authority for a Workflow/provider-session scope, or bind a chat handle that
    already belongs to a different canonical session.

    Conflicting immutable authority fails closed rather than selecting the
    newest row.
    """

    outcome = ControlPlaneOutcome.IMMUTABLE_AUTHORITY_CONFLICT


class TerminalSessionOverwriteError(ControlPlaneConflictError):
    """Raised when a nonterminal update would overwrite a terminal session."""

    outcome = ControlPlaneOutcome.IMMUTABLE_AUTHORITY_CONFLICT


class RevisionConflictError(ControlPlaneConflictError):
    """Raised when a guarded write's expected revision != current revision.

    A stale writer observed an older revision than durable authority; the write
    is refused so a lost update cannot overwrite the winner. The caller must
    reload current state and reconcile rather than blindly retrying.
    """

    outcome = ControlPlaneOutcome.REVISION_CONFLICT


class FencingConflictError(ControlPlaneConflictError):
    """Raised when a guarded write presents a superseded fencing generation.

    A former owner (activity, host, lease holder, janitor) attempted to mutate
    durable state after a strictly newer generation was acquired; the write is
    fenced out.
    """

    outcome = ControlPlaneOutcome.FENCING_CONFLICT


class NotCommandOwnerError(ControlPlaneConflictError):
    """Raised when a caller that does not own a claimed command tries to
    record its delivery or result.

    Ownership is the per-claim ``claim_token`` the winning claimant received, not
    the low-cardinality ``owner_class`` metric label: a racing loser that shares
    an ``owner_class`` is refused so it cannot settle a command it never won."""

    outcome = ControlPlaneOutcome.NOT_OWNER


class TurnIdempotencyConflictError(OmnigentControlPlaneError):
    """Raised when a turn attempt reuses an existing idempotency key for a
    different logical turn."""


class CommandIdempotencyConflictError(OmnigentControlPlaneError):
    """Raised when a command reuses an existing idempotency key for a different
    logical command.

    Command identity is the immutable tuple ``(session_id, command_type,
    turn_attempt_id, payload_digest)``. Reusing a key after changing any of those
    fields fails closed rather than silently returning a receipt/status for
    unrelated input."""


class AmbiguousAuthorityError(OmnigentControlPlaneError):
    """Raised by the backfill when a duplicate group carries conflicting
    immutable authority and cannot be resolved deterministically."""


# --- Records -----------------------------------------------------------------


@dataclass(frozen=True)
class SessionRecord:
    """Canonical provider-session authority (one per Workflow/provider-session
    scope)."""

    session_id: str
    moonmind_workflow_id: str
    provider: str
    schema_version: int = CURRENT_SCHEMA_VERSION
    moonmind_run_id: Optional[str] = None
    step_execution_id: Optional[str] = None
    moonmind_agent_run_id: Optional[str] = None
    compatibility_profile: Optional[str] = None
    provider_session_ref: Optional[str] = None
    chat_binding_id: Optional[str] = None
    intent_ref: Optional[str] = None
    intent_digest: Optional[str] = None
    execution_plan_ref: Optional[str] = None
    runtime_binding_ref: Optional[str] = None
    desired_state: str = "pending"
    observed_state: Optional[str] = None
    reconciled_state: Optional[str] = None
    active_turn_attempt_id: Optional[str] = None
    provider_event_cursor: Optional[str] = None
    snapshot_frontier: Optional[str] = None
    provider_profile_id: Optional[str] = None
    host_binding_ref: Optional[str] = None
    host_lease_ref: Optional[str] = None
    provider_profile_generation: Optional[int] = None
    host_lease_generation: Optional[int] = None
    credential_generation: Optional[int] = None
    compatibility_ref: Optional[str] = None
    image_manifest_ref: Optional[str] = None
    terminal_state: Optional[str] = None
    terminal_evidence_ref: Optional[str] = None
    cleanup_state: str = "pending"
    historical_read_state: str = "live"
    revision: int = 1
    fencing_generation: int = 0
    next_reconciliation_deadline: Optional[datetime] = None
    last_decision_ref: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    @property
    def is_terminal(self) -> bool:
        return self.terminal_state is not None


@dataclass(frozen=True)
class TurnAttemptRecord:
    """One logical turn: instruction, initial message, continuation, steering,
    or remediation. Owns request idempotency; never owns chat-binding
    authority."""

    turn_attempt_id: str
    session_id: str
    idempotency_key: str
    schema_version: int = CURRENT_SCHEMA_VERSION
    step_execution_id: Optional[str] = None
    lineage_kind: str = "instruction"
    parent_turn_attempt_id: Optional[str] = None
    remediation_of_turn_attempt_id: Optional[str] = None
    instruction_digest: Optional[str] = None
    provider_marker: Optional[str] = None
    provider_turn_id: Optional[str] = None
    provider_item_id: Optional[str] = None
    state: str = TURN_STATE_PREPARED
    terminal_state: Optional[str] = None
    attempt_outcome: Optional[str] = None
    terminal_evidence_ref: Optional[str] = None
    revision: int = 1
    fencing_generation: int = 0
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    @property
    def is_terminal(self) -> bool:
        return self.terminal_state is not None


@dataclass(frozen=True)
class ObservationRecord:
    """Append-only bounded observation index entry."""

    observation_id: str
    session_id: str
    observation_type: str
    source: str
    observed_at: datetime
    deduplication_key: str
    schema_version: int = CURRENT_SCHEMA_VERSION
    source_sequence: Optional[int] = None
    source_digest: Optional[str] = None
    payload_ref: Optional[str] = None
    bounded_index: dict[str, Any] = field(default_factory=dict)
    created_at: Optional[datetime] = None


@dataclass(frozen=True)
class CommandRecord:
    """Durable logical-side-effect / idempotency journal entry."""

    command_id: str
    session_id: str
    command_type: str
    idempotency_key: str
    payload_digest: str
    schema_version: int = CURRENT_SCHEMA_VERSION
    turn_attempt_id: Optional[str] = None
    expected_session_revision: Optional[int] = None
    fencing_generation: int = 0
    status: str = COMMAND_STATE_PENDING
    owner_class: Optional[str] = None
    claim_token: Optional[str] = None
    provider_receipt_id: Optional[str] = None
    delivery_ambiguous: bool = False
    result_ref: Optional[str] = None
    revision: int = 1
    retry_policy: dict[str, Any] = field(default_factory=dict)
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    @property
    def is_terminal(self) -> bool:
        return self.status in COMMAND_TERMINAL_STATES


@dataclass(frozen=True)
class DecisionRecord:
    """Append-only reconciliation decision record."""

    decision_id: str
    session_id: str
    decision_code: str
    schema_version: int = CURRENT_SCHEMA_VERSION
    input_state_digest: Optional[str] = None
    observation_frontier_digest: Optional[str] = None
    expected_revision: Optional[int] = None
    fencing_generation: int = 0
    reason_code: Optional[str] = None
    resulting_command_id: Optional[str] = None
    next_deadline: Optional[datetime] = None
    product_visible_transition: Optional[str] = None
    trace_ref: Optional[str] = None
    diagnostics_ref: Optional[str] = None
    created_at: Optional[datetime] = None


@dataclass(frozen=True)
class ChatBindingAliasRecord:
    """Resolution record for a previously issued chat-binding handle."""

    chat_binding_id: str
    session_id: Optional[str]
    alias_state: str = ALIAS_STATE_ACTIVE
    diagnostic_reason: Optional[str] = None
    created_at: Optional[datetime] = None

    @property
    def resolves(self) -> bool:
        """True when the alias safely resolves to a canonical session."""

        return self.alias_state == ALIAS_STATE_ACTIVE and self.session_id is not None


@dataclass(frozen=True)
class CleanupAuthorityRecord:
    """Durable cleanup/janitor authority for one canonical session.

    Source: MoonLadderStudios/MoonMind#3704. Cleanup is fenced against the host,
    Provider Profile lease, and provider-session generations it was claimed
    against so a janitor cannot stop or release resources that now belong to a
    replacement generation. Exactly one owner may hold ``claimed`` at a time; a
    former owner cannot complete cleanup after a strictly newer generation is
    claimed.
    """

    session_id: str
    generation: int = 0
    state: str = CLEANUP_STATE_UNCLAIMED
    owner_class: Optional[str] = None
    claim_token: Optional[str] = None
    fenced_host_generation: Optional[int] = None
    fenced_profile_generation: Optional[int] = None
    fenced_provider_epoch: Optional[str] = None
    revision: int = 1
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


@dataclass(frozen=True)
class CasResult:
    """Result of a compare-and-swap / fencing-guarded repository operation.

    ``outcome`` is the stable :class:`ControlPlaneOutcome`; ``record`` is the
    current durable record (the freshly applied record on success, or the
    unchanged current authority on a conflict) so a reconciler can converge
    without a second read.
    """

    outcome: ControlPlaneOutcome
    record: Any = None

    @property
    def applied(self) -> bool:
        """True when the write converged (applied or already applied)."""

        return self.outcome in APPLIED_OUTCOMES

    @property
    def conflicted(self) -> bool:
        return self.outcome in CONFLICT_OUTCOMES


def ensure_supported_schema_version(aggregate: str, version: int) -> int:
    """Fail closed on unsupported schema versions (Compatibility Policy)."""

    if version not in SUPPORTED_SCHEMA_VERSIONS:
        raise UnknownSchemaVersionError(aggregate, version)
    return version


def compute_digest(payload: Any) -> str:
    """Deterministic sha256 digest of a JSON-serializable payload.

    Used for instruction/intent/command payload digests so identical logical
    inputs collapse to one idempotent identity.
    """

    if isinstance(payload, (bytes, bytearray)):
        raw = bytes(payload)
    elif isinstance(payload, str):
        raw = payload.encode("utf-8")
    else:
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()
