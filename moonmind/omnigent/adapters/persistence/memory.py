"""In-memory persistence adapters for the append-only control-plane aggregates.

Source issue: MoonLadderStudios/MoonMind#3711
([Omnigent control plane 10/11]).

These adapters implement :class:`~moonmind.omnigent.ports.ObservationRepositoryPort`
and :class:`~moonmind.omnigent.ports.DecisionRepositoryPort` with the same
observable behaviour (append idempotency, bounded reads, ordering, per-reason
counting) as the production SQLAlchemy repositories in
:mod:`moonmind.omnigent.control_plane.repositories`. Both families are exercised
by the same shared port-contract suite
(``tests/helpers/omnigent_port_contracts.py``) so an in-memory test double and
the PostgreSQL adapter are proven interchangeable behind one interface.

Ordering mirrors the production repositories: observations order by
``(observed_at, observation_id)`` and decisions by ``(created_at, decision_id)``.
Each appended record is assigned a strictly increasing synthetic ``created_at``
so append order and the ``(timestamp, id)`` sort order agree without depending
on wall-clock time.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from itertools import count
from typing import Any, Optional, Sequence

from moonmind.omnigent.control_plane.records import (
    COMMAND_STATE_APPLIED,
    COMMAND_STATE_CLAIMED,
    COMMAND_STATE_DELIVERY_UNKNOWN,
    COMMAND_STATE_FAILED,
    COMMAND_STATE_PENDING,
    COMMAND_TERMINAL_STATES,
    POST_TERMINAL_MUTABLE_FIELDS,
    TURN_STATE_ORDER,
    TURN_STATE_PREPARED,
    TURN_STATE_TERMINAL,
    TURN_STATES,
    CasResult,
    CommandIdempotencyConflictError,
    CommandRecord,
    ConflictingSessionAuthorityError,
    ControlPlaneOutcome,
    DecisionRecord,
    FencingConflictError,
    FencingScope,
    NotCommandOwnerError,
    ObservationRecord,
    RevisionConflictError,
    SessionRecord,
    TerminalSessionOverwriteError,
    TurnAttemptRecord,
    TurnIdempotencyConflictError,
)

_UNSET: Any = object()

# Synthetic monotonic clock base for ``created_at`` assignment. A strictly
# increasing per-append offset guarantees append order equals the
# ``(created_at, id)`` sort order the production repositories use.
_EPOCH = datetime(2020, 1, 1, tzinfo=timezone.utc)


class InMemoryObservationRepository:
    """In-memory append-only observation index.

    Appends are idempotent on ``(session_id, deduplication_key)``: a duplicate
    append returns the previously stored record unchanged.
    """

    def __init__(self) -> None:
        self._by_id: dict[str, ObservationRecord] = {}
        self._dedup: dict[tuple[str, str], str] = {}
        self._seq = count()

    async def append(
        self,
        *,
        observation_id: str,
        session_id: str,
        observation_type: str,
        source: str,
        observed_at: datetime,
        deduplication_key: str,
        source_sequence: Optional[int] = None,
        source_digest: Optional[str] = None,
        payload_ref: Optional[str] = None,
        bounded_index: Optional[dict[str, Any]] = None,
    ) -> ObservationRecord:
        dedup_key = (session_id, deduplication_key)
        existing_id = self._dedup.get(dedup_key)
        if existing_id is not None:
            return self._by_id[existing_id]
        record = ObservationRecord(
            observation_id=observation_id,
            session_id=session_id,
            observation_type=observation_type,
            source=source,
            observed_at=observed_at,
            deduplication_key=deduplication_key,
            source_sequence=source_sequence,
            source_digest=source_digest,
            payload_ref=payload_ref,
            bounded_index=dict(bounded_index or {}),
            created_at=_EPOCH + timedelta(microseconds=next(self._seq)),
        )
        self._by_id[observation_id] = record
        self._dedup[dedup_key] = observation_id
        return record

    async def list_for_session(
        self,
        session_id: str,
        *,
        observation_type: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> list[ObservationRecord]:
        rows = [
            r
            for r in self._by_id.values()
            if r.session_id == session_id
            and (observation_type is None or r.observation_type == observation_type)
        ]
        rows.sort(key=lambda r: (r.observed_at, r.observation_id))
        if limit is not None:
            rows = rows[:limit]
        return rows

    async def latest_for_session(
        self,
        session_id: str,
        *,
        observation_types: Optional[Sequence[str]] = None,
    ) -> Optional[ObservationRecord]:
        type_filter = set(observation_types) if observation_types is not None else None
        rows = [
            r
            for r in self._by_id.values()
            if r.session_id == session_id
            and (type_filter is None or r.observation_type in type_filter)
        ]
        if not rows:
            return None
        rows.sort(key=lambda r: (r.observed_at, r.observation_id), reverse=True)
        return rows[0]


class InMemoryDecisionRepository:
    """In-memory append-only reconciliation-decision journal."""

    def __init__(self) -> None:
        self._records: list[DecisionRecord] = []
        self._seq = count()

    async def append(
        self,
        *,
        decision_id: str,
        session_id: str,
        decision_code: str,
        input_state_digest: Optional[str] = None,
        observation_frontier_digest: Optional[str] = None,
        expected_revision: Optional[int] = None,
        fencing_generation: int = 0,
        reason_code: Optional[str] = None,
        resulting_command_id: Optional[str] = None,
        next_deadline: Optional[datetime] = None,
        product_visible_transition: Optional[str] = None,
        trace_ref: Optional[str] = None,
        diagnostics_ref: Optional[str] = None,
    ) -> DecisionRecord:
        record = DecisionRecord(
            decision_id=decision_id,
            session_id=session_id,
            decision_code=decision_code,
            input_state_digest=input_state_digest,
            observation_frontier_digest=observation_frontier_digest,
            expected_revision=expected_revision,
            fencing_generation=fencing_generation,
            reason_code=reason_code,
            resulting_command_id=resulting_command_id,
            next_deadline=next_deadline,
            product_visible_transition=product_visible_transition,
            trace_ref=trace_ref,
            diagnostics_ref=diagnostics_ref,
            created_at=_EPOCH + timedelta(microseconds=next(self._seq)),
        )
        self._records.append(record)
        return record

    async def list_for_session(self, session_id: str) -> list[DecisionRecord]:
        rows = [r for r in self._records if r.session_id == session_id]
        rows.sort(key=lambda r: (r.created_at, r.decision_id))
        return rows

    async def latest_for_session(
        self, session_id: str
    ) -> Optional[DecisionRecord]:
        rows = [r for r in self._records if r.session_id == session_id]
        if not rows:
            return None
        rows.sort(key=lambda r: (r.created_at, r.decision_id), reverse=True)
        return rows[0]

    async def count_for_session_reason(
        self, session_id: str, reason_code: str
    ) -> int:
        return sum(
            1
            for r in self._records
            if r.session_id == session_id and r.reason_code == reason_code
        )


class _ControlPlaneState:
    """Shared backing state for the cooperating in-memory adapters.

    The production session, turn, and command repositories share one
    ``AsyncSession`` inside a transaction, so a turn or command write can read the
    owning session's live fencing generation. The in-memory family reproduces
    that by sharing one state object: turn/command adapters read
    ``self._state.sessions`` exactly as the SQLAlchemy repositories query
    ``OmnigentSession`` in the same transaction. A single monotonic sequence
    stamps ``created_at``/``updated_at`` so append and ``(timestamp, id)`` sort
    order agree without depending on wall-clock time.
    """

    def __init__(self) -> None:
        self.sessions: dict[str, SessionRecord] = {}
        self.turns: dict[str, TurnAttemptRecord] = {}
        self.turns_by_idempotency: dict[str, str] = {}
        self.commands: dict[str, CommandRecord] = {}
        self.commands_by_idempotency: dict[str, str] = {}
        self._seq = count()

    def tick(self) -> datetime:
        return _EPOCH + timedelta(microseconds=next(self._seq))


class InMemorySessionRepository:
    """In-memory canonical provider-session authority.

    Reproduces the observable revision/fencing outcomes of
    :class:`moonmind.omnigent.control_plane.repositories.SessionRepository`:
    fail-closed scope uniqueness, fencing-before-revision conflict ordering,
    monotonic revision on every applied write, fenced/lost-update
    :class:`CasResult` convergence signals, and immutable terminal authority
    (with only :data:`POST_TERMINAL_MUTABLE_FIELDS` advancing post-terminal).
    """

    def __init__(self, state: Optional[_ControlPlaneState] = None) -> None:
        self._state = state if state is not None else _ControlPlaneState()

    async def create(
        self,
        *,
        session_id: str,
        moonmind_workflow_id: str,
        provider: str,
        moonmind_run_id: Optional[str] = None,
        step_execution_id: Optional[str] = None,
        moonmind_agent_run_id: Optional[str] = None,
        compatibility_profile: Optional[str] = None,
        provider_session_ref: Optional[str] = None,
        chat_binding_id: Optional[str] = None,
        intent_ref: Optional[str] = None,
        intent_digest: Optional[str] = None,
        desired_state: str = "pending",
        provider_profile_id: Optional[str] = None,
        host_binding_ref: Optional[str] = None,
        host_lease_ref: Optional[str] = None,
        compatibility_ref: Optional[str] = None,
        image_manifest_ref: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> SessionRecord:
        sessions = self._state.sessions
        if session_id in sessions:
            raise ConflictingSessionAuthorityError(
                f"Refusing to create a second canonical session {session_id!r}"
            )
        if provider_session_ref is not None:
            for rec in sessions.values():
                if (
                    rec.moonmind_workflow_id == moonmind_workflow_id
                    and rec.provider_session_ref == provider_session_ref
                ):
                    raise ConflictingSessionAuthorityError(
                        "Refusing to create a second canonical session authority "
                        f"for workflow={moonmind_workflow_id!r} "
                        f"provider_session_ref={provider_session_ref!r}"
                    )
        if chat_binding_id is not None:
            for rec in sessions.values():
                if rec.chat_binding_id == chat_binding_id:
                    raise ConflictingSessionAuthorityError(
                        f"Chat binding {chat_binding_id!r} already bound to "
                        "another canonical session"
                    )
        now = self._state.tick()
        record = SessionRecord(
            session_id=session_id,
            moonmind_workflow_id=moonmind_workflow_id,
            provider=provider,
            moonmind_run_id=moonmind_run_id,
            step_execution_id=step_execution_id,
            moonmind_agent_run_id=moonmind_agent_run_id,
            compatibility_profile=compatibility_profile,
            provider_session_ref=provider_session_ref,
            chat_binding_id=chat_binding_id,
            intent_ref=intent_ref,
            intent_digest=intent_digest,
            desired_state=desired_state,
            provider_profile_id=provider_profile_id,
            host_binding_ref=host_binding_ref,
            host_lease_ref=host_lease_ref,
            compatibility_ref=compatibility_ref,
            image_manifest_ref=image_manifest_ref,
            metadata=dict(metadata or {}),
            created_at=now,
            updated_at=now,
        )
        sessions[session_id] = record
        return record

    async def get(self, session_id: str) -> Optional[SessionRecord]:
        return self._state.sessions.get(session_id)

    async def load_for_update(self, session_id: str) -> Optional[SessionRecord]:
        # No cross-task locking in the single-threaded reference adapter; the
        # observable read is identical to ``get``.
        return self._state.sessions.get(session_id)

    async def get_by_scope(
        self, moonmind_workflow_id: str, provider_session_ref: str
    ) -> Optional[SessionRecord]:
        if provider_session_ref is None:
            raise ConflictingSessionAuthorityError(
                "get_by_scope requires a non-null provider_session_ref: "
                f"workflow={moonmind_workflow_id!r} admits multiple unattached "
                "sessions, so a NULL scope lookup is ambiguous"
            )
        for rec in self._state.sessions.values():
            if (
                rec.moonmind_workflow_id == moonmind_workflow_id
                and rec.provider_session_ref == provider_session_ref
            ):
                return rec
        return None

    async def get_by_chat_binding(
        self, chat_binding_id: str
    ) -> Optional[SessionRecord]:
        for rec in self._state.sessions.values():
            if rec.chat_binding_id == chat_binding_id:
                return rec
        return None

    @staticmethod
    def _check_fence(
        record: SessionRecord,
        *,
        expected_revision: int,
        expected_fencing_generation: int,
        expected_provider_profile_generation: Optional[int],
        expected_host_lease_generation: Optional[int],
    ) -> Optional[ControlPlaneOutcome]:
        # Fencing generations are checked before the revision so a superseded
        # owner is fenced out even when its revision happens to match.
        if record.fencing_generation != expected_fencing_generation:
            return ControlPlaneOutcome.FENCING_CONFLICT
        if (
            expected_provider_profile_generation is not None
            and record.provider_profile_generation
            != expected_provider_profile_generation
        ):
            return ControlPlaneOutcome.FENCING_CONFLICT
        if (
            expected_host_lease_generation is not None
            and record.host_lease_generation != expected_host_lease_generation
        ):
            return ControlPlaneOutcome.FENCING_CONFLICT
        if record.revision != expected_revision:
            return ControlPlaneOutcome.REVISION_CONFLICT
        return None

    async def compare_and_swap_session(
        self,
        session_id: str,
        *,
        expected_revision: int,
        expected_fencing_generation: int,
        expected_provider_profile_generation: Optional[int] = None,
        expected_host_lease_generation: Optional[int] = None,
        desired_state: Any = _UNSET,
        observed_state: Any = _UNSET,
        reconciled_state: Any = _UNSET,
        active_turn_attempt_id: Any = _UNSET,
        provider_event_cursor: Any = _UNSET,
        snapshot_frontier: Any = _UNSET,
        cleanup_state: Any = _UNSET,
        historical_read_state: Any = _UNSET,
        next_reconciliation_deadline: Any = _UNSET,
        last_decision_ref: Any = _UNSET,
    ) -> CasResult:
        record = self._state.sessions.get(session_id)
        if record is None:
            raise ConflictingSessionAuthorityError(
                f"Unknown canonical session {session_id!r}"
            )
        conflict = self._check_fence(
            record,
            expected_revision=expected_revision,
            expected_fencing_generation=expected_fencing_generation,
            expected_provider_profile_generation=expected_provider_profile_generation,
            expected_host_lease_generation=expected_host_lease_generation,
        )
        if conflict is not None:
            return CasResult(conflict, record)
        provided = [
            (name, value)
            for name, value in (
                ("desired_state", desired_state),
                ("observed_state", observed_state),
                ("reconciled_state", reconciled_state),
                ("active_turn_attempt_id", active_turn_attempt_id),
                ("provider_event_cursor", provider_event_cursor),
                ("snapshot_frontier", snapshot_frontier),
                ("cleanup_state", cleanup_state),
                ("historical_read_state", historical_read_state),
                ("next_reconciliation_deadline", next_reconciliation_deadline),
                ("last_decision_ref", last_decision_ref),
            )
            if value is not _UNSET
        ]
        if record.terminal_state is not None:
            disallowed = [
                name
                for name, _ in provided
                if name not in POST_TERMINAL_MUTABLE_FIELDS
            ]
            if disallowed:
                raise TerminalSessionOverwriteError(
                    f"Session {session_id!r} is terminal "
                    f"({record.terminal_state!r}); refusing nonterminal lifecycle "
                    f"update to {sorted(disallowed)} (only "
                    f"{sorted(POST_TERMINAL_MUTABLE_FIELDS)} may advance)"
                )
        updates: dict[str, Any] = {name: value for name, value in provided}
        updates["revision"] = record.revision + 1
        updates["updated_at"] = self._state.tick()
        new_record = replace(record, **updates)
        self._state.sessions[session_id] = new_record
        return CasResult(ControlPlaneOutcome.APPLIED, new_record)

    @staticmethod
    def _raise_for_conflict(session_id: str, result: CasResult) -> None:
        if result.outcome is ControlPlaneOutcome.REVISION_CONFLICT:
            raise RevisionConflictError(
                f"Session {session_id!r} lost update: expected revision did not "
                f"match current authority (revision {result.record.revision})"
            )
        if result.outcome is ControlPlaneOutcome.FENCING_CONFLICT:
            raise FencingConflictError(
                f"Session {session_id!r} write fenced: presented fencing "
                "generation is superseded (current "
                f"{result.record.fencing_generation})"
            )

    async def update_lifecycle(
        self,
        session_id: str,
        *,
        expected_revision: int,
        expected_fencing_generation: int,
        expected_provider_profile_generation: Optional[int] = None,
        expected_host_lease_generation: Optional[int] = None,
        desired_state: Any = _UNSET,
        observed_state: Any = _UNSET,
        reconciled_state: Any = _UNSET,
        active_turn_attempt_id: Any = _UNSET,
        provider_event_cursor: Any = _UNSET,
        snapshot_frontier: Any = _UNSET,
        cleanup_state: Any = _UNSET,
        historical_read_state: Any = _UNSET,
        next_reconciliation_deadline: Any = _UNSET,
        last_decision_ref: Any = _UNSET,
    ) -> SessionRecord:
        result = await self.compare_and_swap_session(
            session_id,
            expected_revision=expected_revision,
            expected_fencing_generation=expected_fencing_generation,
            expected_provider_profile_generation=expected_provider_profile_generation,
            expected_host_lease_generation=expected_host_lease_generation,
            desired_state=desired_state,
            observed_state=observed_state,
            reconciled_state=reconciled_state,
            active_turn_attempt_id=active_turn_attempt_id,
            provider_event_cursor=provider_event_cursor,
            snapshot_frontier=snapshot_frontier,
            cleanup_state=cleanup_state,
            historical_read_state=historical_read_state,
            next_reconciliation_deadline=next_reconciliation_deadline,
            last_decision_ref=last_decision_ref,
        )
        self._raise_for_conflict(session_id, result)
        return result.record

    async def acquire_fencing_generation(
        self,
        session_id: str,
        scope: FencingScope,
        *,
        expected_revision: int,
    ) -> SessionRecord:
        if scope is FencingScope.CLEANUP:
            raise ValueError(
                "cleanup fencing is owned by CleanupAuthorityRepository, not "
                "acquire_fencing_generation"
            )
        record = self._state.sessions.get(session_id)
        if record is None:
            raise ConflictingSessionAuthorityError(
                f"Unknown canonical session {session_id!r}"
            )
        if record.revision != expected_revision:
            raise RevisionConflictError(
                f"Session {session_id!r} revision {record.revision} != expected "
                f"{expected_revision}; cannot acquire {scope.value} generation"
            )
        updates: dict[str, Any] = {}
        if scope is FencingScope.SESSION_SUPERVISOR:
            updates["fencing_generation"] = record.fencing_generation + 1
        elif scope is FencingScope.PROVIDER_PROFILE_LEASE:
            updates["provider_profile_generation"] = (
                record.provider_profile_generation or 0
            ) + 1
        elif scope is FencingScope.HOST_LEASE:
            updates["host_lease_generation"] = (record.host_lease_generation or 0) + 1
        updates["revision"] = record.revision + 1
        updates["updated_at"] = self._state.tick()
        new_record = replace(record, **updates)
        self._state.sessions[session_id] = new_record
        return new_record

    async def advance_observation_frontier(
        self,
        session_id: str,
        *,
        expected_revision: int,
        expected_fencing_generation: int,
        provider_event_cursor: Any = _UNSET,
        snapshot_frontier: Any = _UNSET,
    ) -> CasResult:
        record = self._state.sessions.get(session_id)
        if record is None:
            raise ConflictingSessionAuthorityError(
                f"Unknown canonical session {session_id!r}"
            )
        conflict = self._check_fence(
            record,
            expected_revision=expected_revision,
            expected_fencing_generation=expected_fencing_generation,
            expected_provider_profile_generation=None,
            expected_host_lease_generation=None,
        )
        if conflict is not None:
            return CasResult(conflict, record)
        updates: dict[str, Any] = {}
        if provider_event_cursor is not _UNSET:
            updates["provider_event_cursor"] = provider_event_cursor
        if snapshot_frontier is not _UNSET:
            updates["snapshot_frontier"] = snapshot_frontier
        updates["revision"] = record.revision + 1
        updates["updated_at"] = self._state.tick()
        new_record = replace(record, **updates)
        self._state.sessions[session_id] = new_record
        return CasResult(ControlPlaneOutcome.APPLIED, new_record)

    async def mark_terminal(
        self,
        session_id: str,
        terminal_state: str,
        *,
        expected_revision: int,
        expected_fencing_generation: int,
        terminal_evidence_ref: Optional[str] = None,
    ) -> SessionRecord:
        record = self._state.sessions.get(session_id)
        if record is None:
            raise ConflictingSessionAuthorityError(
                f"Unknown canonical session {session_id!r}"
            )
        if record.terminal_state is not None:
            if record.terminal_state == terminal_state:
                return record
            raise TerminalSessionOverwriteError(
                f"Session {session_id!r} already terminal "
                f"({record.terminal_state!r}); refusing to overwrite with "
                f"{terminal_state!r}"
            )
        conflict = self._check_fence(
            record,
            expected_revision=expected_revision,
            expected_fencing_generation=expected_fencing_generation,
            expected_provider_profile_generation=None,
            expected_host_lease_generation=None,
        )
        if conflict is ControlPlaneOutcome.FENCING_CONFLICT:
            raise FencingConflictError(
                f"Session {session_id!r} fencing generation "
                f"{record.fencing_generation} != expected "
                f"{expected_fencing_generation}; refusing terminal write"
            )
        if conflict is ControlPlaneOutcome.REVISION_CONFLICT:
            raise RevisionConflictError(
                f"Session {session_id!r} revision {record.revision} != expected "
                f"{expected_revision}; refusing terminal write"
            )
        updates: dict[str, Any] = {
            "terminal_state": terminal_state,
            "revision": record.revision + 1,
            "updated_at": self._state.tick(),
        }
        if terminal_evidence_ref is not None:
            updates["terminal_evidence_ref"] = terminal_evidence_ref
        new_record = replace(record, **updates)
        self._state.sessions[session_id] = new_record
        return new_record


class InMemoryTurnAttemptRepository:
    """In-memory turn-attempt aggregate.

    Reproduces the turn state machine of
    :class:`moonmind.omnigent.control_plane.repositories.TurnAttemptRepository`:
    request idempotency, guarding against the *owning session's* live
    session-supervisor fencing generation (not the turn's stored value), the
    documented monotonic delivery order (a regressive state is an idempotent
    no-op), and immutable terminal authority.
    """

    def __init__(self, state: Optional[_ControlPlaneState] = None) -> None:
        self._state = state if state is not None else _ControlPlaneState()

    async def create(
        self,
        *,
        turn_attempt_id: str,
        session_id: str,
        idempotency_key: str,
        lineage_kind: str = "instruction",
        step_execution_id: Optional[str] = None,
        parent_turn_attempt_id: Optional[str] = None,
        remediation_of_turn_attempt_id: Optional[str] = None,
        instruction_digest: Optional[str] = None,
        provider_marker: Optional[str] = None,
        state: str = TURN_STATE_PREPARED,
    ) -> TurnAttemptRecord:
        existing = await self.get_by_idempotency_key(idempotency_key)
        if existing is not None:
            if (
                existing.session_id == session_id
                and existing.instruction_digest == instruction_digest
            ):
                return existing
            raise TurnIdempotencyConflictError(
                f"Idempotency key {idempotency_key!r} already bound to turn "
                f"{existing.turn_attempt_id!r} on session "
                f"{existing.session_id!r}"
            )
        now = self._state.tick()
        record = TurnAttemptRecord(
            turn_attempt_id=turn_attempt_id,
            session_id=session_id,
            idempotency_key=idempotency_key,
            step_execution_id=step_execution_id,
            lineage_kind=lineage_kind,
            parent_turn_attempt_id=parent_turn_attempt_id,
            remediation_of_turn_attempt_id=remediation_of_turn_attempt_id,
            instruction_digest=instruction_digest,
            provider_marker=provider_marker,
            state=state,
            created_at=now,
            updated_at=now,
        )
        self._state.turns[turn_attempt_id] = record
        self._state.turns_by_idempotency[idempotency_key] = turn_attempt_id
        return record

    async def get(self, turn_attempt_id: str) -> Optional[TurnAttemptRecord]:
        return self._state.turns.get(turn_attempt_id)

    async def get_by_idempotency_key(
        self, idempotency_key: str
    ) -> Optional[TurnAttemptRecord]:
        turn_id = self._state.turns_by_idempotency.get(idempotency_key)
        return self._state.turns.get(turn_id) if turn_id is not None else None

    async def list_for_session(
        self, session_id: str
    ) -> list[TurnAttemptRecord]:
        rows = [r for r in self._state.turns.values() if r.session_id == session_id]
        rows.sort(key=lambda r: (r.created_at, r.turn_attempt_id))
        return rows

    async def count_for_session(self, session_id: str) -> int:
        return sum(
            1 for r in self._state.turns.values() if r.session_id == session_id
        )

    async def compare_and_swap_turn(
        self,
        turn_attempt_id: str,
        *,
        expected_revision: int,
        expected_fencing_generation: int,
        state: Any = _UNSET,
        provider_turn_id: Any = _UNSET,
        provider_item_id: Any = _UNSET,
        terminal_state: Any = _UNSET,
        attempt_outcome: Any = _UNSET,
        terminal_evidence_ref: Any = _UNSET,
    ) -> CasResult:
        row = self._state.turns.get(turn_attempt_id)
        if row is None:
            raise TurnIdempotencyConflictError(
                f"Unknown turn attempt {turn_attempt_id!r}"
            )
        session = self._state.sessions.get(row.session_id)
        session_generation = (
            session.fencing_generation
            if session is not None
            else row.fencing_generation
        )
        wants_terminal = terminal_state is not _UNSET
        if row.terminal_state is not None:
            if wants_terminal and terminal_state == row.terminal_state:
                return CasResult(ControlPlaneOutcome.ALREADY_APPLIED, row)
            raise TurnIdempotencyConflictError(
                f"Turn attempt {turn_attempt_id!r} already terminal "
                f"({row.terminal_state!r}); refusing to overwrite"
            )
        if session_generation != expected_fencing_generation:
            return CasResult(ControlPlaneOutcome.FENCING_CONFLICT, row)
        if row.revision != expected_revision:
            return CasResult(ControlPlaneOutcome.REVISION_CONFLICT, row)
        updates: dict[str, Any] = {}
        if wants_terminal:
            updates["state"] = TURN_STATE_TERMINAL
            updates["terminal_state"] = terminal_state
        elif state is not _UNSET:
            if state not in TURN_STATES:
                raise ValueError(
                    f"Unknown turn state {state!r}; must be one of "
                    f"{sorted(TURN_STATES)}"
                )
            # Enforce the documented monotonic delivery order: a regressive state
            # is an idempotent no-op that leaves already-advanced authority
            # intact.
            if TURN_STATE_ORDER[state] < TURN_STATE_ORDER.get(row.state, -1):
                return CasResult(ControlPlaneOutcome.ALREADY_APPLIED, row)
            updates["state"] = state
        updates["fencing_generation"] = session_generation
        if attempt_outcome is not _UNSET:
            updates["attempt_outcome"] = attempt_outcome
        if provider_turn_id is not _UNSET:
            updates["provider_turn_id"] = provider_turn_id
        if provider_item_id is not _UNSET:
            updates["provider_item_id"] = provider_item_id
        if terminal_evidence_ref is not _UNSET:
            updates["terminal_evidence_ref"] = terminal_evidence_ref
        updates["revision"] = row.revision + 1
        updates["updated_at"] = self._state.tick()
        new_record = replace(row, **updates)
        self._state.turns[turn_attempt_id] = new_record
        return CasResult(ControlPlaneOutcome.APPLIED, new_record)

    @staticmethod
    def _raise_for_conflict(turn_attempt_id: str, result: CasResult) -> None:
        if result.outcome is ControlPlaneOutcome.REVISION_CONFLICT:
            raise RevisionConflictError(
                f"Turn attempt {turn_attempt_id!r} lost update: expected revision "
                f"did not match current authority (revision "
                f"{result.record.revision})"
            )
        if result.outcome is ControlPlaneOutcome.FENCING_CONFLICT:
            raise FencingConflictError(
                f"Turn attempt {turn_attempt_id!r} write fenced: presented "
                "fencing generation is superseded (current "
                f"{result.record.fencing_generation})"
            )

    async def advance_state(
        self,
        turn_attempt_id: str,
        state: str,
        *,
        expected_revision: int,
        expected_fencing_generation: int,
        provider_turn_id: Optional[str] = None,
        provider_item_id: Optional[str] = None,
    ) -> TurnAttemptRecord:
        result = await self.compare_and_swap_turn(
            turn_attempt_id,
            expected_revision=expected_revision,
            expected_fencing_generation=expected_fencing_generation,
            state=state,
            provider_turn_id=(
                provider_turn_id if provider_turn_id is not None else _UNSET
            ),
            provider_item_id=(
                provider_item_id if provider_item_id is not None else _UNSET
            ),
        )
        self._raise_for_conflict(turn_attempt_id, result)
        return result.record

    async def mark_terminal(
        self,
        turn_attempt_id: str,
        terminal_state: str,
        *,
        expected_revision: int,
        expected_fencing_generation: int,
        attempt_outcome: Optional[str] = None,
        terminal_evidence_ref: Optional[str] = None,
    ) -> TurnAttemptRecord:
        result = await self.compare_and_swap_turn(
            turn_attempt_id,
            expected_revision=expected_revision,
            expected_fencing_generation=expected_fencing_generation,
            terminal_state=terminal_state,
            attempt_outcome=(
                attempt_outcome if attempt_outcome is not None else _UNSET
            ),
            terminal_evidence_ref=(
                terminal_evidence_ref if terminal_evidence_ref is not None else _UNSET
            ),
        )
        self._raise_for_conflict(turn_attempt_id, result)
        return result.record


class InMemoryCommandRepository:
    """In-memory durable command / idempotency journal.

    Reproduces the delivery semantics of
    :class:`moonmind.omnigent.control_plane.repositories.CommandRepository`:
    idempotency on ``idempotency_key`` with fail-closed identity checks, exclusive
    single-claimant authority (a racing loser sharing an ``owner_class`` is
    refused), session-generation fencing of a stale claim, and idempotent /
    fail-closed delivery recording against a settled terminal.
    """

    def __init__(self, state: Optional[_ControlPlaneState] = None) -> None:
        self._state = state if state is not None else _ControlPlaneState()

    async def record(
        self,
        *,
        command_id: str,
        session_id: str,
        command_type: str,
        idempotency_key: str,
        payload_digest: str,
        turn_attempt_id: Optional[str] = None,
        expected_session_revision: Optional[int] = None,
        fencing_generation: int = 0,
        owner_class: Optional[str] = None,
        retry_policy: Optional[dict[str, Any]] = None,
    ) -> CommandRecord:
        existing = await self.get_by_idempotency_key(idempotency_key)
        if existing is not None:
            self._ensure_same_command_identity(
                existing,
                idempotency_key=idempotency_key,
                session_id=session_id,
                command_type=command_type,
                turn_attempt_id=turn_attempt_id,
                payload_digest=payload_digest,
            )
            return existing
        now = self._state.tick()
        record = CommandRecord(
            command_id=command_id,
            session_id=session_id,
            command_type=command_type,
            idempotency_key=idempotency_key,
            payload_digest=payload_digest,
            turn_attempt_id=turn_attempt_id,
            expected_session_revision=expected_session_revision,
            fencing_generation=fencing_generation,
            owner_class=owner_class,
            retry_policy=dict(retry_policy or {}),
            created_at=now,
            updated_at=now,
        )
        self._state.commands[command_id] = record
        self._state.commands_by_idempotency[idempotency_key] = command_id
        return record

    @staticmethod
    def _ensure_same_command_identity(
        existing: CommandRecord,
        *,
        idempotency_key: str,
        session_id: str,
        command_type: str,
        turn_attempt_id: Optional[str],
        payload_digest: str,
    ) -> None:
        incoming = (session_id, command_type, turn_attempt_id, payload_digest)
        stored = (
            existing.session_id,
            existing.command_type,
            existing.turn_attempt_id,
            existing.payload_digest,
        )
        if incoming != stored:
            raise CommandIdempotencyConflictError(
                f"Idempotency key {idempotency_key!r} already bound to command "
                f"{existing.command_id!r} with identity {stored!r}; refusing to "
                f"reuse it for {incoming!r}"
            )

    async def get(self, command_id: str) -> Optional[CommandRecord]:
        return self._state.commands.get(command_id)

    async def get_by_idempotency_key(
        self, idempotency_key: str
    ) -> Optional[CommandRecord]:
        command_id = self._state.commands_by_idempotency.get(idempotency_key)
        return self._state.commands.get(command_id) if command_id is not None else None

    async def list_for_session(self, session_id: str) -> list[CommandRecord]:
        rows = [
            c for c in self._state.commands.values() if c.session_id == session_id
        ]
        rows.sort(key=lambda c: (c.created_at, c.command_id))
        return rows

    async def active_for_session(
        self, session_id: str
    ) -> Optional[CommandRecord]:
        candidates = [
            c
            for c in self._state.commands.values()
            if c.session_id == session_id
            and c.status in (COMMAND_STATE_DELIVERY_UNKNOWN, COMMAND_STATE_CLAIMED)
        ]
        if not candidates:
            return None
        # Precedence matches the production ``LIMIT 1`` ordering:
        # delivery-unknown outranks claimed, then most-recent update, then id.
        # Successive stable sorts compose the ordering with the last as primary.
        candidates.sort(key=lambda c: c.command_id)
        candidates.sort(key=lambda c: c.updated_at, reverse=True)
        candidates.sort(
            key=lambda c: 0 if c.status == COMMAND_STATE_DELIVERY_UNKNOWN else 1
        )
        return candidates[0]

    async def claim_command(
        self,
        command_id: str,
        *,
        owner_class: str,
        claim_token: str,
    ) -> CasResult:
        row = self._state.commands.get(command_id)
        if row is None:
            raise KeyError(f"Unknown command {command_id!r}")
        if row.status == COMMAND_STATE_PENDING:
            session = self._state.sessions.get(row.session_id)
            if (
                session is not None
                and row.fencing_generation < session.fencing_generation
            ):
                return CasResult(ControlPlaneOutcome.FENCING_CONFLICT, row)
            new_record = replace(
                row,
                status=COMMAND_STATE_CLAIMED,
                owner_class=owner_class,
                claim_token=claim_token,
                revision=row.revision + 1,
                updated_at=self._state.tick(),
            )
            self._state.commands[command_id] = new_record
            return CasResult(ControlPlaneOutcome.APPLIED, new_record)
        if row.status == COMMAND_STATE_CLAIMED:
            if row.claim_token == claim_token:
                return CasResult(ControlPlaneOutcome.APPLIED, row)
            return CasResult(ControlPlaneOutcome.NOT_OWNER, row)
        return CasResult(ControlPlaneOutcome.ALREADY_APPLIED, row)

    async def record_command_delivery(
        self,
        command_id: str,
        *,
        owner_class: str,
        claim_token: str,
        outcome: ControlPlaneOutcome,
        provider_receipt_id: Optional[str] = None,
        result_ref: Optional[str] = None,
    ) -> CasResult:
        row = self._state.commands.get(command_id)
        if row is None:
            raise KeyError(f"Unknown command {command_id!r}")
        if row.status in COMMAND_TERMINAL_STATES:
            settled_applied = row.status == COMMAND_STATE_APPLIED
            confirms_apply = outcome in (
                ControlPlaneOutcome.APPLIED,
                ControlPlaneOutcome.DELIVERY_UNKNOWN,
            )
            if settled_applied == confirms_apply:
                return CasResult(ControlPlaneOutcome.ALREADY_APPLIED, row)
            return CasResult(ControlPlaneOutcome.IMMUTABLE_AUTHORITY_CONFLICT, row)
        if (
            row.status
            not in (COMMAND_STATE_CLAIMED, COMMAND_STATE_DELIVERY_UNKNOWN)
            or row.owner_class != owner_class
            or row.claim_token != claim_token
        ):
            raise NotCommandOwnerError(
                f"Command {command_id!r} is not claimed by this claimant "
                f"(status={row.status!r}, owner={row.owner_class!r})"
            )
        updates: dict[str, Any] = {}
        if provider_receipt_id is not None:
            updates["provider_receipt_id"] = provider_receipt_id
        if result_ref is not None:
            updates["result_ref"] = result_ref
        if outcome is ControlPlaneOutcome.APPLIED:
            updates["status"] = COMMAND_STATE_APPLIED
            updates["delivery_ambiguous"] = False
            result_outcome = ControlPlaneOutcome.APPLIED
        elif outcome is ControlPlaneOutcome.DELIVERY_UNKNOWN:
            updates["status"] = COMMAND_STATE_DELIVERY_UNKNOWN
            updates["delivery_ambiguous"] = True
            result_outcome = ControlPlaneOutcome.DELIVERY_UNKNOWN
        else:
            updates["status"] = COMMAND_STATE_FAILED
            result_outcome = outcome
        updates["revision"] = row.revision + 1
        updates["updated_at"] = self._state.tick()
        new_record = replace(row, **updates)
        self._state.commands[command_id] = new_record
        return CasResult(result_outcome, new_record)


class InMemoryControlPlaneStore:
    """Cooperating in-memory adapters sharing one backing state.

    Mirrors :class:`moonmind.omnigent.control_plane.OmnigentControlPlaneStore`'s
    ``repos`` surface so a turn/command write reads the owning session's live
    fencing generation, exactly as the production repositories share one
    transaction. Provided so the shared port-contract suite can exercise the
    cross-aggregate session/turn/command behaviour, not only per-aggregate
    reads.
    """

    def __init__(self) -> None:
        state = _ControlPlaneState()
        self._state = state
        self.sessions = InMemorySessionRepository(state)
        # Mirrors ``ControlPlaneRepositories`` attribute names so the shared
        # port-contract suite treats the two stores identically.
        self.turn_attempts = InMemoryTurnAttemptRepository(state)
        self.commands = InMemoryCommandRepository(state)
        self.observations = InMemoryObservationRepository()
        self.decisions = InMemoryDecisionRepository()


__all__ = [
    "InMemoryCommandRepository",
    "InMemoryControlPlaneStore",
    "InMemoryDecisionRepository",
    "InMemoryObservationRepository",
    "InMemorySessionRepository",
    "InMemoryTurnAttemptRepository",
]
