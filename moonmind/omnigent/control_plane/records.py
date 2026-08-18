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

# Chat-binding alias resolution states.
ALIAS_STATE_ACTIVE = "active"
ALIAS_STATE_QUARANTINED = "quarantined"
ALIAS_STATE_DIAGNOSTIC = "diagnostic"


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


class ConflictingSessionAuthorityError(OmnigentControlPlaneError):
    """Raised when creating/binding a canonical session would create a second
    authority for a Workflow/provider-session scope, or bind a chat handle that
    already belongs to a different canonical session.

    Conflicting immutable authority fails closed rather than selecting the
    newest row.
    """


class TerminalSessionOverwriteError(OmnigentControlPlaneError):
    """Raised when a nonterminal update would overwrite a terminal session."""


class TurnIdempotencyConflictError(OmnigentControlPlaneError):
    """Raised when a turn attempt reuses an existing idempotency key for a
    different logical turn."""


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
    status: str = "pending"
    provider_receipt_id: Optional[str] = None
    delivery_ambiguous: bool = False
    result_ref: Optional[str] = None
    retry_policy: dict[str, Any] = field(default_factory=dict)
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


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
