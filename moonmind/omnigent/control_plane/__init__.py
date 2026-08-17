"""Omnigent control-plane durable aggregates and repository boundaries.

Issue MoonLadderStudios/MoonMind#3703 ([Omnigent control plane 2/11]): the
overloaded ``OmnigentBridgeSession`` lifecycle row is decomposed into explicit
durable aggregates -- canonical sessions, turn attempts, observations,
commands, and reconciliation decisions -- plus a chat-binding alias table.

Application and domain code depends on the repository interfaces in
:mod:`moonmind.omnigent.control_plane.repositories`, never on the SQLAlchemy
models directly. The additive backfill from legacy bridge rows lives in
:mod:`moonmind.omnigent.control_plane.backfill`.
"""

from __future__ import annotations

from moonmind.omnigent.control_plane.repositories import (
    CHAT_BINDING_ID_PREFIX,
    COMMAND_TYPES,
    OBSERVATION_KINDS,
    SUPPORTED_SCHEMA_VERSIONS,
    TERMINAL_SESSION_STATES,
    TURN_ATTEMPT_STATES,
    ChatBindingAliasRepository,
    ChatBindingResolution,
    ChatBindingAuthorityError,
    CommandRecord,
    CommandRepository,
    ConflictingAuthorityError,
    DecisionRecord,
    DecisionRepository,
    ObservationRecord,
    ObservationRepository,
    OmnigentControlPlaneError,
    SessionRecord,
    SessionRepository,
    TerminalSessionOverwriteError,
    TurnAttemptRecord,
    TurnAttemptRepository,
    UnknownSchemaVersionError,
    WorkflowDetailProjection,
    WorkflowDetailProjectionRepository,
    compute_authority_scope,
    create_canonical_session,
)

__all__ = [
    "CHAT_BINDING_ID_PREFIX",
    "COMMAND_TYPES",
    "OBSERVATION_KINDS",
    "SUPPORTED_SCHEMA_VERSIONS",
    "TERMINAL_SESSION_STATES",
    "TURN_ATTEMPT_STATES",
    "ChatBindingAliasRepository",
    "ChatBindingResolution",
    "ChatBindingAuthorityError",
    "CommandRecord",
    "CommandRepository",
    "ConflictingAuthorityError",
    "DecisionRecord",
    "DecisionRepository",
    "ObservationRecord",
    "ObservationRepository",
    "OmnigentControlPlaneError",
    "SessionRecord",
    "SessionRepository",
    "TerminalSessionOverwriteError",
    "TurnAttemptRecord",
    "TurnAttemptRepository",
    "UnknownSchemaVersionError",
    "WorkflowDetailProjection",
    "WorkflowDetailProjectionRepository",
    "compute_authority_scope",
    "create_canonical_session",
]
