"""Omnigent control-plane durable aggregates and repositories.

Source: MoonLadderStudios/MoonMind#3703 ([Omnigent control plane 2/11]).

Separates the overloaded ``OmnigentBridgeSession`` lifecycle model into explicit
durable aggregates -- canonical sessions, turn attempts, observations, commands,
and reconciliation decisions -- behind narrow repository interfaces that hide
SQLAlchemy from domain and application code.
"""

from __future__ import annotations

from .backfill import (
    BackfillPlan,
    BackfillReport,
    plan_backfill,
    run_backfill,
)
from .records import (
    ALIAS_STATE_ACTIVE,
    ALIAS_STATE_DIAGNOSTIC,
    ALIAS_STATE_QUARANTINED,
    CURRENT_SCHEMA_VERSION,
    SUPPORTED_SCHEMA_VERSIONS,
    TURN_STATES,
    TURN_STATE_ACCEPTED,
    TURN_STATE_DELIVERY_UNKNOWN,
    TURN_STATE_DISPATCHING,
    TURN_STATE_PREPARED,
    TURN_STATE_RUNNING,
    TURN_STATE_TERMINAL,
    AmbiguousAuthorityError,
    ChatBindingAliasRecord,
    CommandRecord,
    ConflictingSessionAuthorityError,
    DecisionRecord,
    ObservationRecord,
    OmnigentControlPlaneError,
    SessionRecord,
    TerminalSessionOverwriteError,
    TurnAttemptRecord,
    TurnIdempotencyConflictError,
    UnknownSchemaVersionError,
    compute_digest,
)
from .repositories import (
    ChatBindingAliasRepository,
    CommandRepository,
    ControlPlaneRepositories,
    DecisionRepository,
    ObservationRepository,
    OmnigentControlPlaneStore,
    SessionRepository,
    TurnAttemptRepository,
)

__all__ = [
    "ALIAS_STATE_ACTIVE",
    "ALIAS_STATE_DIAGNOSTIC",
    "ALIAS_STATE_QUARANTINED",
    "CURRENT_SCHEMA_VERSION",
    "SUPPORTED_SCHEMA_VERSIONS",
    "TURN_STATES",
    "TURN_STATE_ACCEPTED",
    "TURN_STATE_DELIVERY_UNKNOWN",
    "TURN_STATE_DISPATCHING",
    "TURN_STATE_PREPARED",
    "TURN_STATE_RUNNING",
    "TURN_STATE_TERMINAL",
    "AmbiguousAuthorityError",
    "BackfillPlan",
    "BackfillReport",
    "ChatBindingAliasRecord",
    "ChatBindingAliasRepository",
    "CommandRecord",
    "CommandRepository",
    "ConflictingSessionAuthorityError",
    "ControlPlaneRepositories",
    "DecisionRecord",
    "DecisionRepository",
    "ObservationRecord",
    "ObservationRepository",
    "OmnigentControlPlaneError",
    "OmnigentControlPlaneStore",
    "SessionRecord",
    "SessionRepository",
    "TerminalSessionOverwriteError",
    "TurnAttemptRecord",
    "TurnAttemptRepository",
    "TurnIdempotencyConflictError",
    "UnknownSchemaVersionError",
    "compute_digest",
    "plan_backfill",
    "run_backfill",
]
