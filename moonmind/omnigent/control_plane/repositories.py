"""Narrow repository interfaces over the Omnigent control-plane aggregates.

Issue MoonLadderStudios/MoonMind#3703. These repositories are the only code
that touches the SQLAlchemy models for the canonical session, turn attempt,
observation, command, decision, and chat-binding alias aggregates. Callers
receive immutable dataclass records (``*Record``) so application and domain
code never depends on the ORM classes.

Invariants that the schema alone cannot express are enforced here at the
earliest shared authority boundary:

* a canonical session's terminal state can never be overwritten by a
  non-terminal update (:class:`TerminalSessionOverwriteError`);
* a turn attempt never carries chat-binding authority (there is no such column,
  and :meth:`TurnAttemptRepository.create` refuses one defensively);
* observations with an unknown schema version fail closed per the repository
  Compatibility Policy (:class:`UnknownSchemaVersionError`).

Database uniqueness invariants (one canonical authority per scope, one chat
authority, unique command/turn idempotency, observation sequence/dedup) are
declared on the models and surface as ``IntegrityError``.
"""

from __future__ import annotations

import secrets
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.db.models import (
    OmnigentChatBindingAlias,
    OmnigentCommand,
    OmnigentObservation,
    OmnigentReconciliationDecision,
    OmnigentSession,
    OmnigentTurnAttempt,
)

# ---------------------------------------------------------------------------
# Vocabulary and compatibility policy
# ---------------------------------------------------------------------------

# One canonical schema version per aggregate today. New versions are added here
# explicitly; unknown versions fail closed instead of silently degrading.
SUPPORTED_SCHEMA_VERSIONS: frozenset[int] = frozenset({1})
CURRENT_SCHEMA_VERSION = 1

# MoonLadderStudios/MoonMind#3703: the chat-binding identity is opaque and
# browser-safe. It is an authorization lookup key, never a bearer capability,
# and is kept distinct from the canonical session id and provider session id.
CHAT_BINDING_ID_PREFIX = "chatb_"

TURN_ATTEMPT_STATES: frozenset[str] = frozenset(
    {
        "prepared",
        "dispatching",
        "delivery_unknown",
        "accepted",
        "running",
        "terminal",
    }
)

# Session lifecycle states that mean the canonical session is terminal.
TERMINAL_SESSION_STATES: frozenset[str] = frozenset(
    {"completed", "failed", "canceled", "timed_out"}
)

OBSERVATION_KINDS: frozenset[str] = frozenset(
    {
        "provider_event_batch",
        "provider_session_snapshot",
        "transcript_snapshot",
        "turn_snapshot",
        "host_state",
        "runner_state",
        "host_lease_state",
        "provider_profile_lease_state",
        "workspace_availability",
        "checkpoint_availability",
        "compatibility_state",
        "runtime_readiness",
        "terminal_evidence",
        "cleanup_evidence",
    }
)

COMMAND_TYPES: frozenset[str] = frozenset(
    {
        "acquire_or_renew_lease",
        "ensure_host",
        "stop_host",
        "ensure_provider_session",
        "stop_provider_session",
        "submit_turn",
        "harvest_evidence",
        "publish_workspace",
        "perform_cleanup",
        "release_capacity",
    }
)

CHAT_BINDING_RESOLUTION_ALIAS = "alias"
CHAT_BINDING_RESOLUTION_FAIL_CLOSED = "fail_closed"


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class OmnigentControlPlaneError(RuntimeError):
    """Base error for the Omnigent control-plane repositories."""


class SessionNotFoundError(OmnigentControlPlaneError):
    """Raised when a canonical session id does not exist."""


class TerminalSessionOverwriteError(OmnigentControlPlaneError):
    """Raised when a non-terminal update would overwrite a terminal session.

    Acceptance criterion (MoonLadderStudios/MoonMind#3703): attempt terminality
    and session terminality are stored separately, and a terminal canonical
    session can never be reopened by a later non-terminal update.
    """


class ChatBindingAuthorityError(OmnigentControlPlaneError):
    """Raised when chat-binding authority would be duplicated or misassigned."""


class ConflictingAuthorityError(OmnigentControlPlaneError):
    """Raised when immutable authority conflicts and must fail closed."""


class UnknownSchemaVersionError(OmnigentControlPlaneError):
    """Raised when an aggregate row declares an unsupported schema version."""


def _require_schema_version(schema_version: int) -> int:
    if schema_version not in SUPPORTED_SCHEMA_VERSIONS:
        raise UnknownSchemaVersionError(
            f"unsupported control-plane schema version: {schema_version!r}"
        )
    return schema_version


def compute_authority_scope(
    *,
    moonmind_workflow_id: str,
    provider: str,
    provider_session_id: str | None,
) -> str:
    """Return the deterministic canonical authority scope key.

    The scope is the sole thing the DB uniqueness invariant keys on, so illegal
    duplicate canonical authority for a Workflow/provider-session pair cannot be
    represented. Sessions that have not yet attached a provider session are
    scoped by their canonical session identity instead, so two pre-attach rows
    never collide before a provider session exists.
    """

    workflow = str(moonmind_workflow_id or "").strip()
    provider_name = str(provider or "").strip()
    provider_session = str(provider_session_id or "").strip()
    if not workflow:
        raise ConflictingAuthorityError("authority scope requires a Workflow id")
    if not provider_name:
        raise ConflictingAuthorityError("authority scope requires a provider")
    if provider_session:
        return f"wf:{workflow}|provider:{provider_name}|session:{provider_session}"
    return f"wf:{workflow}|provider:{provider_name}|session:"


def _generate_chat_binding_id() -> str:
    return f"{CHAT_BINDING_ID_PREFIX}{secrets.token_urlsafe(24)}"


# ---------------------------------------------------------------------------
# Immutable domain records (no SQLAlchemy leakage past this boundary)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SessionRecord:
    session_id: str
    schema_version: int
    moonmind_workflow_id: str
    moonmind_run_id: str | None
    step_execution_id: str | None
    moonmind_agent_run_id: str | None
    provider: str
    compatibility_profile: str
    provider_session_id: str | None
    authority_scope: str
    chat_binding_id: str | None
    intent_ref: str | None
    intent_digest: str | None
    desired_state: str
    observed_state: str
    reconciled_state: str
    active_turn_attempt_id: str | None
    provider_event_cursor: str | None
    snapshot_frontier: str | None
    provider_profile_id: str | None
    provider_profile_generation: int | None
    host_binding_ref: str | None
    host_lease_ref: str | None
    host_lease_generation: int | None
    credential_generation: int | None
    compatibility_ref: str | None
    image_manifest_ref: str | None
    terminal_state: str | None
    cleanup_state: str
    historical_read_state: str
    revision: int
    fencing_generation: int
    next_reconciliation_deadline: datetime | None
    last_decision_ref: str | None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def is_terminal(self) -> bool:
        return self.terminal_state is not None

    @classmethod
    def from_row(cls, row: OmnigentSession) -> "SessionRecord":
        return cls(
            session_id=row.session_id,
            schema_version=row.schema_version,
            moonmind_workflow_id=row.moonmind_workflow_id,
            moonmind_run_id=row.moonmind_run_id,
            step_execution_id=row.step_execution_id,
            moonmind_agent_run_id=row.moonmind_agent_run_id,
            provider=row.provider,
            compatibility_profile=row.compatibility_profile,
            provider_session_id=row.provider_session_id,
            authority_scope=row.authority_scope,
            chat_binding_id=row.chat_binding_id,
            intent_ref=row.intent_ref,
            intent_digest=row.intent_digest,
            desired_state=row.desired_state,
            observed_state=row.observed_state,
            reconciled_state=row.reconciled_state,
            active_turn_attempt_id=row.active_turn_attempt_id,
            provider_event_cursor=row.provider_event_cursor,
            snapshot_frontier=row.snapshot_frontier,
            provider_profile_id=row.provider_profile_id,
            provider_profile_generation=row.provider_profile_generation,
            host_binding_ref=row.host_binding_ref,
            host_lease_ref=row.host_lease_ref,
            host_lease_generation=row.host_lease_generation,
            credential_generation=row.credential_generation,
            compatibility_ref=row.compatibility_ref,
            image_manifest_ref=row.image_manifest_ref,
            terminal_state=row.terminal_state,
            cleanup_state=row.cleanup_state,
            historical_read_state=row.historical_read_state,
            revision=row.revision,
            fencing_generation=row.fencing_generation,
            next_reconciliation_deadline=row.next_reconciliation_deadline,
            last_decision_ref=row.last_decision_ref,
            metadata=dict(row.metadata_ or {}),
        )


@dataclass(frozen=True)
class TurnAttemptRecord:
    turn_attempt_id: str
    schema_version: int
    session_id: str
    step_execution_id: str | None
    turn_kind: str
    continuation_of_attempt_id: str | None
    remediation_of_attempt_id: str | None
    idempotency_key: str
    instruction_digest: str | None
    provider_marker: str | None
    provider_turn_id: str | None
    provider_item_id: str | None
    state: str
    outcome: str | None
    terminal_evidence_ref: str | None
    revision: int
    fencing_generation: int
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def is_terminal(self) -> bool:
        return self.state == "terminal"

    @classmethod
    def from_row(cls, row: OmnigentTurnAttempt) -> "TurnAttemptRecord":
        return cls(
            turn_attempt_id=row.turn_attempt_id,
            schema_version=row.schema_version,
            session_id=row.session_id,
            step_execution_id=row.step_execution_id,
            turn_kind=row.turn_kind,
            continuation_of_attempt_id=row.continuation_of_attempt_id,
            remediation_of_attempt_id=row.remediation_of_attempt_id,
            idempotency_key=row.idempotency_key,
            instruction_digest=row.instruction_digest,
            provider_marker=row.provider_marker,
            provider_turn_id=row.provider_turn_id,
            provider_item_id=row.provider_item_id,
            state=row.state,
            outcome=row.outcome,
            terminal_evidence_ref=row.terminal_evidence_ref,
            revision=row.revision,
            fencing_generation=row.fencing_generation,
            metadata=dict(row.metadata_ or {}),
        )


@dataclass(frozen=True)
class ObservationRecord:
    observation_id: str
    schema_version: int
    session_id: str
    observation_kind: str
    source: str
    observed_at: datetime
    source_sequence: int | None
    source_digest: str | None
    dedup_identity: str
    artifact_ref: str | None
    bounded_index: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_row(cls, row: OmnigentObservation) -> "ObservationRecord":
        return cls(
            observation_id=row.observation_id,
            schema_version=row.schema_version,
            session_id=row.session_id,
            observation_kind=row.observation_kind,
            source=row.source,
            observed_at=row.observed_at,
            source_sequence=row.source_sequence,
            source_digest=row.source_digest,
            dedup_identity=row.dedup_identity,
            artifact_ref=row.artifact_ref,
            bounded_index=dict(row.bounded_index or {}),
        )


@dataclass(frozen=True)
class CommandRecord:
    command_id: str
    schema_version: int
    session_id: str
    command_type: str
    command_idempotency_key: str
    expected_session_revision: int | None
    fencing_generation: int
    payload_digest: str | None
    status: str
    provider_receipt_id: str | None
    delivery_ambiguous: bool
    result_ref: str | None
    retry_policy: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_row(cls, row: OmnigentCommand) -> "CommandRecord":
        return cls(
            command_id=row.command_id,
            schema_version=row.schema_version,
            session_id=row.session_id,
            command_type=row.command_type,
            command_idempotency_key=row.command_idempotency_key,
            expected_session_revision=row.expected_session_revision,
            fencing_generation=row.fencing_generation,
            payload_digest=row.payload_digest,
            status=row.status,
            provider_receipt_id=row.provider_receipt_id,
            delivery_ambiguous=row.delivery_ambiguous,
            result_ref=row.result_ref,
            retry_policy=dict(row.retry_policy or {}),
        )


@dataclass(frozen=True)
class DecisionRecord:
    decision_id: str
    schema_version: int
    session_id: str
    input_state_digest: str | None
    observation_frontier_digest: str | None
    expected_revision: int | None
    fencing_generation: int
    decision: str
    reason_code: str | None
    resulting_command_id: str | None
    next_deadline: datetime | None
    product_visible_transition: str | None
    trace_ref: str | None
    diagnostics_ref: str | None
    created_at: datetime | None

    @classmethod
    def from_row(cls, row: OmnigentReconciliationDecision) -> "DecisionRecord":
        return cls(
            decision_id=row.decision_id,
            schema_version=row.schema_version,
            session_id=row.session_id,
            input_state_digest=row.input_state_digest,
            observation_frontier_digest=row.observation_frontier_digest,
            expected_revision=row.expected_revision,
            fencing_generation=row.fencing_generation,
            decision=row.decision,
            reason_code=row.reason_code,
            resulting_command_id=row.resulting_command_id,
            next_deadline=row.next_deadline,
            product_visible_transition=row.product_visible_transition,
            trace_ref=row.trace_ref,
            diagnostics_ref=row.diagnostics_ref,
            created_at=row.created_at,
        )


@dataclass(frozen=True)
class ChatBindingResolution:
    """Browser-safe resolution of a previously issued chat binding.

    Carries only server-owned, browser-safe values: the canonical session id (or
    ``None``) and a diagnostic code. Provider session ids are never exposed.
    """

    chat_binding_id: str
    resolution: str
    canonical_session_id: str | None
    diagnostic_code: str | None


# ---------------------------------------------------------------------------
# Repositories
# ---------------------------------------------------------------------------


class SessionRepository:
    """Repository for the canonical :class:`OmnigentSession` authority."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def _row(self, session_id: str) -> OmnigentSession:
        row = await self._session.get(OmnigentSession, session_id)
        if row is None:
            raise SessionNotFoundError(f"unknown canonical session: {session_id!r}")
        return row

    async def create(
        self,
        *,
        moonmind_workflow_id: str,
        provider: str,
        compatibility_profile: str,
        session_id: str | None = None,
        moonmind_run_id: str | None = None,
        step_execution_id: str | None = None,
        moonmind_agent_run_id: str | None = None,
        provider_session_id: str | None = None,
        intent_ref: str | None = None,
        intent_digest: str | None = None,
        desired_state: str = "active",
        provider_profile_id: str | None = None,
        provider_profile_generation: int | None = None,
        host_binding_ref: str | None = None,
        host_lease_ref: str | None = None,
        host_lease_generation: int | None = None,
        credential_generation: int | None = None,
        compatibility_ref: str | None = None,
        image_manifest_ref: str | None = None,
        schema_version: int = CURRENT_SCHEMA_VERSION,
        metadata: Mapping[str, Any] | None = None,
    ) -> SessionRecord:
        _require_schema_version(schema_version)
        canonical_id = session_id or f"oms_{uuid4().hex}"
        scope = compute_authority_scope(
            moonmind_workflow_id=moonmind_workflow_id,
            provider=provider,
            provider_session_id=provider_session_id,
        )
        # Pre-attach rows scope by canonical identity so two sessions can be
        # created before a provider session exists without a spurious collision.
        if not str(provider_session_id or "").strip():
            scope = f"{scope}{canonical_id}"
        row = OmnigentSession(
            session_id=canonical_id,
            schema_version=schema_version,
            moonmind_workflow_id=str(moonmind_workflow_id),
            moonmind_run_id=moonmind_run_id,
            step_execution_id=step_execution_id,
            moonmind_agent_run_id=moonmind_agent_run_id,
            provider=str(provider),
            compatibility_profile=str(compatibility_profile),
            provider_session_id=provider_session_id,
            authority_scope=scope,
            intent_ref=intent_ref,
            intent_digest=intent_digest,
            desired_state=desired_state,
            provider_profile_id=provider_profile_id,
            provider_profile_generation=provider_profile_generation,
            host_binding_ref=host_binding_ref,
            host_lease_ref=host_lease_ref,
            host_lease_generation=host_lease_generation,
            credential_generation=credential_generation,
            compatibility_ref=compatibility_ref,
            image_manifest_ref=image_manifest_ref,
            metadata_=dict(metadata or {}),
        )
        self._session.add(row)
        await self._session.flush()
        return SessionRecord.from_row(row)

    async def get(self, session_id: str) -> SessionRecord | None:
        row = await self._session.get(OmnigentSession, session_id)
        return SessionRecord.from_row(row) if row is not None else None

    async def get_by_authority_scope(self, authority_scope: str) -> SessionRecord | None:
        result = await self._session.execute(
            select(OmnigentSession).where(
                OmnigentSession.authority_scope == authority_scope
            )
        )
        row = result.scalars().one_or_none()
        return SessionRecord.from_row(row) if row is not None else None

    async def get_by_chat_binding(self, chat_binding_id: str) -> SessionRecord | None:
        result = await self._session.execute(
            select(OmnigentSession).where(
                OmnigentSession.chat_binding_id == chat_binding_id
            )
        )
        row = result.scalars().one_or_none()
        return SessionRecord.from_row(row) if row is not None else None

    async def get_by_provider_session(
        self, provider_session_id: str
    ) -> list[SessionRecord]:
        result = await self._session.execute(
            select(OmnigentSession).where(
                OmnigentSession.provider_session_id == provider_session_id
            )
        )
        return [SessionRecord.from_row(row) for row in result.scalars().all()]

    async def allocate_chat_binding(
        self, session_id: str, *, chat_binding_id: str | None = None
    ) -> SessionRecord:
        """Allocate the one opaque chat-binding authority for a session.

        Idempotent when the same id is requested; refuses to rebind an existing
        chat authority (a continuation/remediation turn cannot allocate a second
        binding for the same canonical session).
        """

        row = await self._row(session_id)
        new_id = chat_binding_id or _generate_chat_binding_id()
        if row.chat_binding_id is not None:
            if chat_binding_id is not None and chat_binding_id != row.chat_binding_id:
                raise ChatBindingAuthorityError(
                    "canonical session already owns a different chat binding"
                )
            return SessionRecord.from_row(row)
        row.chat_binding_id = new_id
        row.revision = int(row.revision) + 1
        await self._session.flush()
        return SessionRecord.from_row(row)

    async def attach_provider_session(
        self, session_id: str, *, provider_session_id: str
    ) -> SessionRecord:
        """Bind the provider session and recompute the canonical scope.

        Refuses to rebind an already-attached provider session; conflicting
        immutable authority fails closed rather than silently rebinding.
        """

        row = await self._row(session_id)
        provider_session = str(provider_session_id or "").strip()
        if not provider_session:
            raise ConflictingAuthorityError("provider session id is required")
        if row.provider_session_id is not None:
            if row.provider_session_id != provider_session:
                raise ConflictingAuthorityError(
                    "canonical session already attached to a different provider session"
                )
            return SessionRecord.from_row(row)
        row.provider_session_id = provider_session
        row.authority_scope = compute_authority_scope(
            moonmind_workflow_id=row.moonmind_workflow_id,
            provider=row.provider,
            provider_session_id=provider_session,
        )
        row.revision = int(row.revision) + 1
        await self._session.flush()
        return SessionRecord.from_row(row)

    async def update_states(
        self,
        session_id: str,
        *,
        desired_state: str | None = None,
        observed_state: str | None = None,
        reconciled_state: str | None = None,
        provider_event_cursor: str | None = None,
        snapshot_frontier: str | None = None,
        active_turn_attempt_id: str | None = None,
        next_reconciliation_deadline: datetime | None = None,
        last_decision_ref: str | None = None,
    ) -> SessionRecord:
        """Apply a non-terminal reconciliation update to the canonical session.

        Fails closed if the session is already terminal: a terminal canonical
        session's lifecycle can never be reopened by a non-terminal update.
        """

        row = await self._row(session_id)
        if row.terminal_state is not None:
            raise TerminalSessionOverwriteError(
                "cannot apply a non-terminal update to a terminal canonical session"
            )
        if desired_state is not None:
            row.desired_state = desired_state
        if observed_state is not None:
            row.observed_state = observed_state
        if reconciled_state is not None:
            row.reconciled_state = reconciled_state
        if provider_event_cursor is not None:
            row.provider_event_cursor = provider_event_cursor
        if snapshot_frontier is not None:
            row.snapshot_frontier = snapshot_frontier
        if active_turn_attempt_id is not None:
            row.active_turn_attempt_id = active_turn_attempt_id
        if next_reconciliation_deadline is not None:
            row.next_reconciliation_deadline = next_reconciliation_deadline
        if last_decision_ref is not None:
            row.last_decision_ref = last_decision_ref
        row.revision = int(row.revision) + 1
        await self._session.flush()
        return SessionRecord.from_row(row)

    async def mark_terminal(
        self,
        session_id: str,
        *,
        terminal_state: str,
        observed_state: str | None = None,
        reconciled_state: str | None = None,
    ) -> SessionRecord:
        """Terminalize the canonical session.

        Idempotent for the same terminal value; a different terminal state fails
        closed rather than overwriting an existing terminal authority.
        """

        terminal = str(terminal_state or "").strip()
        if terminal not in TERMINAL_SESSION_STATES:
            raise OmnigentControlPlaneError(
                f"invalid terminal session state: {terminal_state!r}"
            )
        row = await self._row(session_id)
        if row.terminal_state is not None:
            if row.terminal_state != terminal:
                raise TerminalSessionOverwriteError(
                    "canonical session already terminal with a different state"
                )
            return SessionRecord.from_row(row)
        row.terminal_state = terminal
        row.observed_state = observed_state or terminal
        row.reconciled_state = reconciled_state or terminal
        row.revision = int(row.revision) + 1
        await self._session.flush()
        return SessionRecord.from_row(row)

    async def mark_cleanup_state(
        self, session_id: str, *, cleanup_state: str
    ) -> SessionRecord:
        row = await self._row(session_id)
        row.cleanup_state = str(cleanup_state)
        row.revision = int(row.revision) + 1
        await self._session.flush()
        return SessionRecord.from_row(row)

    async def mark_historical_read(
        self, session_id: str, *, historical_read_state: str
    ) -> SessionRecord:
        """Move a session to a historical-read projection after cleanup.

        Preserves durable read access after provider/host resources are removed.
        """

        row = await self._row(session_id)
        row.historical_read_state = str(historical_read_state)
        row.revision = int(row.revision) + 1
        await self._session.flush()
        return SessionRecord.from_row(row)

    async def bump_fencing_generation(self, session_id: str) -> SessionRecord:
        row = await self._row(session_id)
        row.fencing_generation = int(row.fencing_generation) + 1
        row.revision = int(row.revision) + 1
        await self._session.flush()
        return SessionRecord.from_row(row)


class TurnAttemptRepository:
    """Repository for :class:`OmnigentTurnAttempt` request-idempotency rows."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        session_id: str,
        idempotency_key: str,
        turn_attempt_id: str | None = None,
        turn_kind: str = "instruction",
        step_execution_id: str | None = None,
        continuation_of_attempt_id: str | None = None,
        remediation_of_attempt_id: str | None = None,
        instruction_digest: str | None = None,
        provider_marker: str | None = None,
        state: str = "prepared",
        schema_version: int = CURRENT_SCHEMA_VERSION,
        metadata: Mapping[str, Any] | None = None,
    ) -> TurnAttemptRecord:
        _require_schema_version(schema_version)
        if state not in TURN_ATTEMPT_STATES:
            raise OmnigentControlPlaneError(f"invalid turn attempt state: {state!r}")
        payload = dict(metadata or {})
        # A turn attempt never carries chat-binding authority. Guard defensively
        # so a caller cannot smuggle it through metadata.
        if "chat_binding_id" in payload:
            raise ChatBindingAuthorityError(
                "turn attempts cannot carry chat-binding authority"
            )
        row = OmnigentTurnAttempt(
            turn_attempt_id=turn_attempt_id or f"omt_{uuid4().hex}",
            schema_version=schema_version,
            session_id=session_id,
            step_execution_id=step_execution_id,
            turn_kind=turn_kind,
            continuation_of_attempt_id=continuation_of_attempt_id,
            remediation_of_attempt_id=remediation_of_attempt_id,
            idempotency_key=idempotency_key,
            instruction_digest=instruction_digest,
            provider_marker=provider_marker,
            state=state,
            metadata_=payload,
        )
        self._session.add(row)
        await self._session.flush()
        return TurnAttemptRecord.from_row(row)

    async def get(self, turn_attempt_id: str) -> TurnAttemptRecord | None:
        row = await self._session.get(OmnigentTurnAttempt, turn_attempt_id)
        return TurnAttemptRecord.from_row(row) if row is not None else None

    async def get_by_idempotency_key(
        self, idempotency_key: str
    ) -> TurnAttemptRecord | None:
        result = await self._session.execute(
            select(OmnigentTurnAttempt).where(
                OmnigentTurnAttempt.idempotency_key == idempotency_key
            )
        )
        row = result.scalars().one_or_none()
        return TurnAttemptRecord.from_row(row) if row is not None else None

    async def list_for_session(self, session_id: str) -> list[TurnAttemptRecord]:
        result = await self._session.execute(
            select(OmnigentTurnAttempt)
            .where(OmnigentTurnAttempt.session_id == session_id)
            .order_by(OmnigentTurnAttempt.created_at)
        )
        return [TurnAttemptRecord.from_row(row) for row in result.scalars().all()]

    async def update_state(
        self,
        turn_attempt_id: str,
        *,
        state: str,
        outcome: str | None = None,
        provider_turn_id: str | None = None,
        provider_item_id: str | None = None,
        terminal_evidence_ref: str | None = None,
    ) -> TurnAttemptRecord:
        if state not in TURN_ATTEMPT_STATES:
            raise OmnigentControlPlaneError(f"invalid turn attempt state: {state!r}")
        row = await self._session.get(OmnigentTurnAttempt, turn_attempt_id)
        if row is None:
            raise OmnigentControlPlaneError(
                f"unknown turn attempt: {turn_attempt_id!r}"
            )
        row.state = state
        if outcome is not None:
            row.outcome = outcome
        if provider_turn_id is not None:
            row.provider_turn_id = provider_turn_id
        if provider_item_id is not None:
            row.provider_item_id = provider_item_id
        if terminal_evidence_ref is not None:
            row.terminal_evidence_ref = terminal_evidence_ref
        row.revision = int(row.revision) + 1
        await self._session.flush()
        return TurnAttemptRecord.from_row(row)


class ObservationRepository:
    """Append-only repository for :class:`OmnigentObservation` index rows."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def append(
        self,
        *,
        session_id: str,
        observation_kind: str,
        source: str,
        observed_at: datetime,
        dedup_identity: str,
        observation_id: str | None = None,
        source_sequence: int | None = None,
        source_digest: str | None = None,
        artifact_ref: str | None = None,
        bounded_index: Mapping[str, Any] | None = None,
        schema_version: int = CURRENT_SCHEMA_VERSION,
    ) -> ObservationRecord:
        _require_schema_version(schema_version)
        if observation_kind not in OBSERVATION_KINDS:
            raise OmnigentControlPlaneError(
                f"unknown observation kind: {observation_kind!r}"
            )
        row = OmnigentObservation(
            observation_id=observation_id or f"omo_{uuid4().hex}",
            schema_version=schema_version,
            session_id=session_id,
            observation_kind=observation_kind,
            source=str(source),
            observed_at=observed_at,
            source_sequence=source_sequence,
            source_digest=source_digest,
            dedup_identity=str(dedup_identity),
            artifact_ref=artifact_ref,
            bounded_index=dict(bounded_index or {}),
        )
        self._session.add(row)
        await self._session.flush()
        return ObservationRecord.from_row(row)

    async def list_for_session(
        self, session_id: str, *, observation_kind: str | None = None
    ) -> list[ObservationRecord]:
        query = select(OmnigentObservation).where(
            OmnigentObservation.session_id == session_id
        )
        if observation_kind is not None:
            query = query.where(
                OmnigentObservation.observation_kind == observation_kind
            )
        query = query.order_by(
            OmnigentObservation.source_sequence, OmnigentObservation.created_at
        )
        result = await self._session.execute(query)
        return [ObservationRecord.from_row(row) for row in result.scalars().all()]


class CommandRepository:
    """Durable command / idempotency journal repository."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record(
        self,
        *,
        session_id: str,
        command_type: str,
        command_idempotency_key: str,
        command_id: str | None = None,
        expected_session_revision: int | None = None,
        fencing_generation: int = 0,
        payload_digest: str | None = None,
        status: str = "pending",
        provider_receipt_id: str | None = None,
        delivery_ambiguous: bool = False,
        result_ref: str | None = None,
        retry_policy: Mapping[str, Any] | None = None,
        schema_version: int = CURRENT_SCHEMA_VERSION,
    ) -> CommandRecord:
        _require_schema_version(schema_version)
        if command_type not in COMMAND_TYPES:
            raise OmnigentControlPlaneError(f"unknown command type: {command_type!r}")
        row = OmnigentCommand(
            command_id=command_id or f"omc_{uuid4().hex}",
            schema_version=schema_version,
            session_id=session_id,
            command_type=command_type,
            command_idempotency_key=command_idempotency_key,
            expected_session_revision=expected_session_revision,
            fencing_generation=fencing_generation,
            payload_digest=payload_digest,
            status=status,
            provider_receipt_id=provider_receipt_id,
            delivery_ambiguous=delivery_ambiguous,
            result_ref=result_ref,
            retry_policy=dict(retry_policy or {}),
        )
        self._session.add(row)
        await self._session.flush()
        return CommandRecord.from_row(row)

    async def get_by_idempotency_key(
        self, command_idempotency_key: str
    ) -> CommandRecord | None:
        result = await self._session.execute(
            select(OmnigentCommand).where(
                OmnigentCommand.command_idempotency_key == command_idempotency_key
            )
        )
        row = result.scalars().one_or_none()
        return CommandRecord.from_row(row) if row is not None else None

    async def update_status(
        self,
        command_id: str,
        *,
        status: str,
        provider_receipt_id: str | None = None,
        delivery_ambiguous: bool | None = None,
        result_ref: str | None = None,
    ) -> CommandRecord:
        row = await self._session.get(OmnigentCommand, command_id)
        if row is None:
            raise OmnigentControlPlaneError(f"unknown command: {command_id!r}")
        row.status = str(status)
        if provider_receipt_id is not None:
            row.provider_receipt_id = provider_receipt_id
        if delivery_ambiguous is not None:
            row.delivery_ambiguous = bool(delivery_ambiguous)
        if result_ref is not None:
            row.result_ref = result_ref
        await self._session.flush()
        return CommandRecord.from_row(row)


class DecisionRepository:
    """Append-only repository for reconciliation decisions."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def append(
        self,
        *,
        session_id: str,
        decision: str,
        decision_id: str | None = None,
        input_state_digest: str | None = None,
        observation_frontier_digest: str | None = None,
        expected_revision: int | None = None,
        fencing_generation: int = 0,
        reason_code: str | None = None,
        resulting_command_id: str | None = None,
        next_deadline: datetime | None = None,
        product_visible_transition: str | None = None,
        trace_ref: str | None = None,
        diagnostics_ref: str | None = None,
        schema_version: int = CURRENT_SCHEMA_VERSION,
    ) -> DecisionRecord:
        _require_schema_version(schema_version)
        row = OmnigentReconciliationDecision(
            decision_id=decision_id or f"omd_{uuid4().hex}",
            schema_version=schema_version,
            session_id=session_id,
            input_state_digest=input_state_digest,
            observation_frontier_digest=observation_frontier_digest,
            expected_revision=expected_revision,
            fencing_generation=fencing_generation,
            decision=decision,
            reason_code=reason_code,
            resulting_command_id=resulting_command_id,
            next_deadline=next_deadline,
            product_visible_transition=product_visible_transition,
            trace_ref=trace_ref,
            diagnostics_ref=diagnostics_ref,
        )
        self._session.add(row)
        await self._session.flush()
        return DecisionRecord.from_row(row)

    async def list_for_session(self, session_id: str) -> list[DecisionRecord]:
        result = await self._session.execute(
            select(OmnigentReconciliationDecision)
            .where(OmnigentReconciliationDecision.session_id == session_id)
            .order_by(OmnigentReconciliationDecision.created_at)
        )
        return [DecisionRecord.from_row(row) for row in result.scalars().all()]


class ChatBindingAliasRepository:
    """Repository resolving previously issued chat-binding URLs safely."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add_alias(
        self,
        *,
        chat_binding_id: str,
        canonical_session_id: str,
        source_bridge_session_id: str | None = None,
    ) -> None:
        row = await self._session.get(OmnigentChatBindingAlias, chat_binding_id)
        if row is None:
            self._session.add(
                OmnigentChatBindingAlias(
                    chat_binding_id=chat_binding_id,
                    canonical_session_id=canonical_session_id,
                    resolution=CHAT_BINDING_RESOLUTION_ALIAS,
                    source_bridge_session_id=source_bridge_session_id,
                )
            )
        else:
            row.canonical_session_id = canonical_session_id
            row.resolution = CHAT_BINDING_RESOLUTION_ALIAS
            row.diagnostic_code = None
            if source_bridge_session_id is not None:
                row.source_bridge_session_id = source_bridge_session_id
        await self._session.flush()

    async def add_fail_closed(
        self,
        *,
        chat_binding_id: str,
        diagnostic_code: str,
        source_bridge_session_id: str | None = None,
    ) -> None:
        row = await self._session.get(OmnigentChatBindingAlias, chat_binding_id)
        if row is None:
            self._session.add(
                OmnigentChatBindingAlias(
                    chat_binding_id=chat_binding_id,
                    canonical_session_id=None,
                    resolution=CHAT_BINDING_RESOLUTION_FAIL_CLOSED,
                    diagnostic_code=diagnostic_code,
                    source_bridge_session_id=source_bridge_session_id,
                )
            )
        else:
            row.canonical_session_id = None
            row.resolution = CHAT_BINDING_RESOLUTION_FAIL_CLOSED
            row.diagnostic_code = diagnostic_code
            if source_bridge_session_id is not None:
                row.source_bridge_session_id = source_bridge_session_id
        await self._session.flush()

    async def resolve(self, chat_binding_id: str) -> ChatBindingResolution:
        """Resolve a chat-binding id to its canonical authority or a diagnostic.

        The canonical session table is authoritative: a currently bound session
        wins. Otherwise the alias table resolves a previously issued (possibly
        duplicate) binding to the canonical session or to a stable fail-closed
        diagnostic. Provider session ids are never returned.
        """

        session_result = await self._session.execute(
            select(OmnigentSession.session_id).where(
                OmnigentSession.chat_binding_id == chat_binding_id
            )
        )
        canonical_session_id = session_result.scalars().one_or_none()
        if canonical_session_id is not None:
            return ChatBindingResolution(
                chat_binding_id=chat_binding_id,
                resolution=CHAT_BINDING_RESOLUTION_ALIAS,
                canonical_session_id=canonical_session_id,
                diagnostic_code=None,
            )
        alias = await self._session.get(OmnigentChatBindingAlias, chat_binding_id)
        if alias is None:
            return ChatBindingResolution(
                chat_binding_id=chat_binding_id,
                resolution=CHAT_BINDING_RESOLUTION_FAIL_CLOSED,
                canonical_session_id=None,
                diagnostic_code="unknown_chat_binding",
            )
        return ChatBindingResolution(
            chat_binding_id=chat_binding_id,
            resolution=alias.resolution,
            canonical_session_id=alias.canonical_session_id,
            diagnostic_code=alias.diagnostic_code,
        )


@dataclass(frozen=True)
class WorkflowDetailProjection:
    """Browser-safe projection of a canonical session for Workflow Detail reads.

    Carries only server-owned, browser-safe values. Provider session ids, host
    ids, lease refs, and credential generations are never exposed. The
    projection is derived from the canonical aggregate and remains readable
    after provider/host resources are removed (``historical_read_state``).
    """

    session_id: str
    moonmind_workflow_id: str
    moonmind_run_id: str | None
    step_execution_id: str | None
    chat_binding_id: str | None
    desired_state: str
    observed_state: str
    reconciled_state: str
    terminal_state: str | None
    cleanup_state: str
    historical_read_state: str
    active_turn_attempt_id: str | None
    active_turn_state: str | None
    turn_attempt_count: int
    diagnostics_refs: tuple[str, ...]


class WorkflowDetailProjectionRepository:
    """Read-only projection over the canonical control-plane aggregate.

    Workflow Detail, diagnostic reads, and chat-binding resolution all read the
    canonical :class:`OmnigentSession` aggregate through this projection rather
    than the legacy bridge row, and preserve historical access after provider
    and host resources are removed.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._sessions = SessionRepository(session)
        self._turns = TurnAttemptRepository(session)

    async def _project(self, record: SessionRecord) -> WorkflowDetailProjection:
        attempts = await self._turns.list_for_session(record.session_id)
        active_state: str | None = None
        if record.active_turn_attempt_id is not None:
            for attempt in attempts:
                if attempt.turn_attempt_id == record.active_turn_attempt_id:
                    active_state = attempt.state
                    break
        preserved = record.metadata.get("backfill", {}) if record.metadata else {}
        diagnostics: list[str] = []
        for entry in (preserved.get("preservedRefs") or {}).values():
            ref = (entry.get("refs") or {}).get("diagnosticsRef")
            if ref:
                diagnostics.append(str(ref))
        return WorkflowDetailProjection(
            session_id=record.session_id,
            moonmind_workflow_id=record.moonmind_workflow_id,
            moonmind_run_id=record.moonmind_run_id,
            step_execution_id=record.step_execution_id,
            chat_binding_id=record.chat_binding_id,
            desired_state=record.desired_state,
            observed_state=record.observed_state,
            reconciled_state=record.reconciled_state,
            terminal_state=record.terminal_state,
            cleanup_state=record.cleanup_state,
            historical_read_state=record.historical_read_state,
            active_turn_attempt_id=record.active_turn_attempt_id,
            active_turn_state=active_state,
            turn_attempt_count=len(attempts),
            diagnostics_refs=tuple(diagnostics),
        )

    async def by_session_id(
        self, session_id: str
    ) -> WorkflowDetailProjection | None:
        record = await self._sessions.get(session_id)
        return await self._project(record) if record is not None else None

    async def by_chat_binding(
        self, chat_binding_id: str
    ) -> WorkflowDetailProjection | None:
        record = await self._sessions.get_by_chat_binding(chat_binding_id)
        return await self._project(record) if record is not None else None


async def create_canonical_session(
    session: AsyncSession,
    *,
    moonmind_workflow_id: str,
    provider: str,
    compatibility_profile: str,
    idempotency_key: str,
    provider_session_id: str | None = None,
    allocate_chat_binding: bool = True,
    chat_binding_id: str | None = None,
    first_turn_kind: str = "instruction",
    intent_ref: str | None = None,
    intent_digest: str | None = None,
    instruction_digest: str | None = None,
    step_execution_id: str | None = None,
    moonmind_run_id: str | None = None,
    moonmind_agent_run_id: str | None = None,
) -> tuple[SessionRecord, TurnAttemptRecord]:
    """Atomically create a canonical session, allocate its chat binding, and
    establish the first turn attempt.

    The three writes participate in the caller's ``session`` transaction so the
    canonical authority, its single chat binding, and its first turn are
    established together or not at all (MoonLadderStudios/MoonMind#3703).
    """

    sessions = SessionRepository(session)
    turns = TurnAttemptRepository(session)
    record = await sessions.create(
        moonmind_workflow_id=moonmind_workflow_id,
        provider=provider,
        compatibility_profile=compatibility_profile,
        provider_session_id=provider_session_id,
        intent_ref=intent_ref,
        intent_digest=intent_digest,
        step_execution_id=step_execution_id,
        moonmind_run_id=moonmind_run_id,
        moonmind_agent_run_id=moonmind_agent_run_id,
    )
    if allocate_chat_binding:
        record = await sessions.allocate_chat_binding(
            record.session_id, chat_binding_id=chat_binding_id
        )
    attempt = await turns.create(
        session_id=record.session_id,
        idempotency_key=idempotency_key,
        turn_kind=first_turn_kind,
        step_execution_id=step_execution_id,
        instruction_digest=instruction_digest,
    )
    await sessions.update_states(
        record.session_id, active_turn_attempt_id=attempt.turn_attempt_id
    )
    refreshed = await sessions.get(record.session_id)
    assert refreshed is not None
    return refreshed, attempt
