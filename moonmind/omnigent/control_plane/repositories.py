"""Narrow repository interfaces for the Omnigent control-plane aggregates.

Source: MoonLadderStudios/MoonMind#3703 ([Omnigent control plane 2/11]).

Application and domain code depends on these repositories and the frozen
records in :mod:`moonmind.omnigent.control_plane.records`, never on the
SQLAlchemy ORM models directly. Each repository is bound to a single
``AsyncSession`` so that a create-session / allocate-chat-binding /
bind-immutable-authority / establish-first-turn sequence can run atomically in
one transaction (see :class:`OmnigentControlPlaneStore`).
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, AsyncIterator, Callable, Optional, Sequence

from sqlalchemy import case, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.db.models import (
    OmnigentChatBindingAlias,
    OmnigentCleanupAuthority,
    OmnigentCommand,
    OmnigentObservation,
    OmnigentReconciliationDecision,
    OmnigentRuntimeBindingRecord,
    OmnigentSession,
    OmnigentTurnAttempt,
)

from . import metrics, spans, telemetry
from .records import (
    ALIAS_STATE_ACTIVE,
    ALIAS_STATE_QUARANTINED,
    CLEANUP_STATE_CLAIMED,
    CLEANUP_STATE_COMPLETE,
    CLEANUP_STATE_UNCLAIMED,
    COMMAND_STATE_APPLIED,
    COMMAND_STATE_CLAIMED,
    COMMAND_STATE_DELIVERY_UNKNOWN,
    COMMAND_STATE_FAILED,
    COMMAND_STATE_PENDING,
    COMMAND_TERMINAL_STATES,
    TURN_STATE_ORDER,
    TURN_STATE_PREPARED,
    TURN_STATE_TERMINAL,
    TURN_STATES,
    CasResult,
    ChatBindingAliasRecord,
    CleanupAuthorityRecord,
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
    ensure_supported_schema_version,
)
from .turn_sources import TurnSource, coerce_turn_source

_UNSET: Any = object()

# Fields an ordinary lifecycle update may still advance after a session has
# reached a terminal state. The normal terminal-then-cleanup/archive journey has
# no separate writer, so cleanup/archive progress must remain recordable while
# any attempt to mutate nonterminal session state still fails closed.
_POST_TERMINAL_MUTABLE_FIELDS: frozenset[str] = frozenset(
    {"cleanup_state", "historical_read_state"}
)


def _raise_for_session_conflict(session_id: str, result: CasResult) -> None:
    """Translate a fail-closed session CAS outcome into a typed exception.

    Convenience for callers of the raising wrappers (:meth:`update_lifecycle`);
    the returned-outcome path stays available for reconcilers that converge on a
    conflict rather than raising.
    """

    if result.outcome is ControlPlaneOutcome.REVISION_CONFLICT:
        raise RevisionConflictError(
            f"Session {session_id!r} lost update: expected revision did not "
            f"match current authority (revision {result.record.revision})"
        )
    if result.outcome is ControlPlaneOutcome.FENCING_CONFLICT:
        raise FencingConflictError(
            f"Session {session_id!r} write fenced: presented fencing generation "
            f"is superseded (current {result.record.fencing_generation})"
        )


def _raise_for_turn_conflict(turn_attempt_id: str, result: CasResult) -> None:
    """Translate a fail-closed turn CAS outcome into a typed exception."""

    if result.outcome is ControlPlaneOutcome.REVISION_CONFLICT:
        raise RevisionConflictError(
            f"Turn attempt {turn_attempt_id!r} lost update: expected revision "
            f"did not match current authority (revision {result.record.revision})"
        )
    if result.outcome is ControlPlaneOutcome.FENCING_CONFLICT:
        raise FencingConflictError(
            f"Turn attempt {turn_attempt_id!r} write fenced: presented fencing "
            f"generation is superseded (current {result.record.fencing_generation})"
        )


# --- ORM -> record converters ------------------------------------------------


def _session_record(row: OmnigentSession) -> SessionRecord:
    ensure_supported_schema_version("OmnigentSession", row.schema_version)
    return SessionRecord(
        session_id=row.session_id,
        moonmind_workflow_id=row.moonmind_workflow_id,
        provider=row.provider,
        schema_version=row.schema_version,
        moonmind_run_id=row.moonmind_run_id,
        step_execution_id=row.step_execution_id,
        moonmind_agent_run_id=row.moonmind_agent_run_id,
        compatibility_profile=row.compatibility_profile,
        provider_session_ref=row.provider_session_ref,
        chat_binding_id=row.chat_binding_id,
        intent_ref=row.intent_ref,
        intent_digest=row.intent_digest,
        execution_plan_ref=row.execution_plan_ref,
        runtime_binding_ref=row.runtime_binding_ref,
        desired_state=row.desired_state,
        observed_state=row.observed_state,
        reconciled_state=row.reconciled_state,
        active_turn_attempt_id=row.active_turn_attempt_id,
        provider_event_cursor=row.provider_event_cursor,
        snapshot_frontier=row.snapshot_frontier,
        provider_profile_id=row.provider_profile_id,
        host_binding_ref=row.host_binding_ref,
        host_lease_ref=row.host_lease_ref,
        provider_profile_generation=row.provider_profile_generation,
        host_lease_generation=row.host_lease_generation,
        credential_generation=row.credential_generation,
        compatibility_ref=row.compatibility_ref,
        image_manifest_ref=row.image_manifest_ref,
        terminal_state=row.terminal_state,
        terminal_evidence_ref=row.terminal_evidence_ref,
        cleanup_state=row.cleanup_state,
        historical_read_state=row.historical_read_state,
        revision=row.revision,
        fencing_generation=row.fencing_generation,
        next_reconciliation_deadline=row.next_reconciliation_deadline,
        last_decision_ref=row.last_decision_ref,
        metadata=dict(row.metadata_ or {}),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _turn_record(row: OmnigentTurnAttempt) -> TurnAttemptRecord:
    ensure_supported_schema_version("OmnigentTurnAttempt", row.schema_version)
    return TurnAttemptRecord(
        turn_attempt_id=row.turn_attempt_id,
        session_id=row.session_id,
        idempotency_key=row.idempotency_key,
        schema_version=row.schema_version,
        step_execution_id=row.step_execution_id,
        lineage_kind=row.lineage_kind,
        parent_turn_attempt_id=row.parent_turn_attempt_id,
        remediation_of_turn_attempt_id=row.remediation_of_turn_attempt_id,
        instruction_digest=row.instruction_digest,
        provider_marker=row.provider_marker,
        provider_turn_id=row.provider_turn_id,
        provider_item_id=row.provider_item_id,
        state=row.state,
        terminal_state=row.terminal_state,
        attempt_outcome=row.attempt_outcome,
        terminal_evidence_ref=row.terminal_evidence_ref,
        revision=row.revision,
        fencing_generation=row.fencing_generation,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _observation_record(row: OmnigentObservation) -> ObservationRecord:
    ensure_supported_schema_version("OmnigentObservation", row.schema_version)
    return ObservationRecord(
        observation_id=row.observation_id,
        session_id=row.session_id,
        observation_type=row.observation_type,
        source=row.source,
        observed_at=row.observed_at,
        deduplication_key=row.deduplication_key,
        schema_version=row.schema_version,
        source_sequence=row.source_sequence,
        source_digest=row.source_digest,
        payload_ref=row.payload_ref,
        bounded_index=dict(row.bounded_index_ or {}),
        created_at=row.created_at,
    )


def _command_record(row: OmnigentCommand) -> CommandRecord:
    ensure_supported_schema_version("OmnigentCommand", row.schema_version)
    return CommandRecord(
        command_id=row.command_id,
        session_id=row.session_id,
        command_type=row.command_type,
        idempotency_key=row.idempotency_key,
        payload_digest=row.payload_digest,
        schema_version=row.schema_version,
        turn_attempt_id=row.turn_attempt_id,
        expected_session_revision=row.expected_session_revision,
        fencing_generation=row.fencing_generation,
        status=row.status,
        owner_class=row.owner_class,
        claim_token=row.claim_token,
        provider_receipt_id=row.provider_receipt_id,
        delivery_ambiguous=row.delivery_ambiguous,
        result_ref=row.result_ref,
        revision=row.revision,
        retry_policy=dict(row.retry_policy_ or {}),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _cleanup_record(row: OmnigentCleanupAuthority) -> CleanupAuthorityRecord:
    ensure_supported_schema_version("OmnigentCleanupAuthority", row.schema_version)
    return CleanupAuthorityRecord(
        session_id=row.session_id,
        generation=row.generation,
        state=row.state,
        owner_class=row.owner_class,
        claim_token=row.claim_token,
        fenced_host_generation=row.fenced_host_generation,
        fenced_profile_generation=row.fenced_profile_generation,
        fenced_provider_epoch=row.fenced_provider_epoch,
        revision=row.revision,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _decision_record(row: OmnigentReconciliationDecision) -> DecisionRecord:
    ensure_supported_schema_version("OmnigentReconciliationDecision", row.schema_version)
    return DecisionRecord(
        decision_id=row.decision_id,
        session_id=row.session_id,
        decision_code=row.decision_code,
        schema_version=row.schema_version,
        input_state_digest=row.input_state_digest,
        observation_frontier_digest=row.observation_frontier_digest,
        expected_revision=row.expected_revision,
        fencing_generation=row.fencing_generation,
        reason_code=row.reason_code,
        resulting_command_id=row.resulting_command_id,
        next_deadline=row.next_deadline,
        product_visible_transition=row.product_visible_transition,
        trace_ref=row.trace_ref,
        diagnostics_ref=row.diagnostics_ref,
        created_at=row.created_at,
    )


def _alias_record(row: OmnigentChatBindingAlias) -> ChatBindingAliasRecord:
    return ChatBindingAliasRecord(
        chat_binding_id=row.chat_binding_id,
        session_id=row.session_id,
        alias_state=row.alias_state,
        diagnostic_reason=row.diagnostic_reason,
        created_at=row.created_at,
    )


class _RepositoryBase:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def _insert(self, obj: Any, *, on_conflict: Callable[[IntegrityError], Exception]):
        """Insert a row, translating an integrity conflict to a domain error.

        The row is added *inside* the savepoint so the INSERT is only ever
        emitted after the SAVEPOINT is established: a losing unique-key insert
        then rolls back to the savepoint and leaves the surrounding transaction
        usable (fail closed, don't poison the txn) instead of aborting the outer
        PostgreSQL transaction (#3704).
        """

        try:
            async with self._session.begin_nested():
                self._session.add(obj)
                await self._session.flush()
        except IntegrityError as exc:  # pragma: no cover - exercised via tests
            raise on_conflict(exc) from exc
        await self._session.refresh(obj)
        return obj


# --- SessionRepository -------------------------------------------------------


class SessionRepository(_RepositoryBase):
    """Canonical provider-session authority repository."""

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
        execution_plan_ref: Optional[str] = None,
        runtime_binding_ref: Optional[str] = None,
        desired_state: str = "pending",
        provider_profile_id: Optional[str] = None,
        host_binding_ref: Optional[str] = None,
        host_lease_ref: Optional[str] = None,
        compatibility_ref: Optional[str] = None,
        image_manifest_ref: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> SessionRecord:
        row = OmnigentSession(
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
            execution_plan_ref=execution_plan_ref,
            runtime_binding_ref=runtime_binding_ref,
            desired_state=desired_state,
            provider_profile_id=provider_profile_id,
            host_binding_ref=host_binding_ref,
            host_lease_ref=host_lease_ref,
            compatibility_ref=compatibility_ref,
            image_manifest_ref=image_manifest_ref,
            metadata_=dict(metadata or {}),
        )
        await self._insert(
            row,
            on_conflict=lambda exc: ConflictingSessionAuthorityError(
                "Refusing to create a second canonical session authority for "
                f"workflow={moonmind_workflow_id!r} "
                f"provider_session_ref={provider_session_ref!r} "
                f"chat_binding_id={chat_binding_id!r}"
            ),
        )
        return _session_record(row)

    async def _load(
        self, session_id: str, *, for_update: bool = False
    ) -> Optional[OmnigentSession]:
        # Revision-fenced writers load ``for_update`` so the load/check/increment
        # sequence is atomic: a concurrent writer blocks on the row lock and then
        # observes the incremented revision, so stale work cannot overwrite the
        # winner. (SQLite serializes writes and ignores ``FOR UPDATE``; PostgreSQL
        # provides the real row lock that proves ownership.)
        return await self._session.get(
            OmnigentSession, session_id, with_for_update=for_update or None
        )

    async def get(self, session_id: str) -> Optional[SessionRecord]:
        row = await self._load(session_id)
        return _session_record(row) if row is not None else None

    async def list_reconciliation_candidates(
        self, *, limit: int = 100, offset: int = 0
    ) -> list[SessionRecord]:
        """Return a bounded batch whose lifecycle may still require convergence.

        Quarantined sessions remain readable but interactive mutation is disabled,
        so the operational stuck-state sweep must not repeatedly act on them.
        Terminal sessions stay eligible until cleanup is durably complete.

        A bounded ``offset`` rotates the stable ordering so a large eligible
        population does not permanently monopolize the first ``limit`` rows when
        healthy active rows never advance ``updated_at``.
        """

        bounded_limit = max(1, min(int(limit), 500))
        bounded_offset = max(0, min(int(offset), 10_000))
        stmt = (
            select(OmnigentSession)
            .where(
                OmnigentSession.historical_read_state != "quarantined",
                or_(
                    OmnigentSession.terminal_state.is_(None),
                    OmnigentSession.cleanup_state.is_(None),
                    OmnigentSession.cleanup_state.notin_(("complete", "closed")),
                ),
            )
            .order_by(OmnigentSession.updated_at, OmnigentSession.session_id)
            .offset(bounded_offset)
            .limit(bounded_limit)
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [_session_record(row) for row in rows]

    async def load_for_update(self, session_id: str) -> Optional[SessionRecord]:
        """Load a session under a row lock for a read-decide-write sequence.

        The returned record is a durable snapshot the caller uses to compute the
        expected revision / fencing generation for a subsequent
        :meth:`compare_and_swap_session` in the *same* transaction. On PostgreSQL
        the lock serializes concurrent reconcilers on this session (#3704).
        """

        row = await self._load(session_id, for_update=True)
        return _session_record(row) if row is not None else None

    async def get_by_scope(
        self, moonmind_workflow_id: str, provider_session_ref: str
    ) -> Optional[SessionRecord]:
        # The schema deliberately admits multiple unattached sessions with a NULL
        # provider_session_ref in one workflow, so a NULL lookup is ambiguous:
        # ``scalar_one_or_none`` would raise ``MultipleResultsFound`` rather than
        # return a usable result. Require a concrete discriminator and fail closed
        # with an actionable error instead of a lookup crash.
        if provider_session_ref is None:
            raise ConflictingSessionAuthorityError(
                "get_by_scope requires a non-null provider_session_ref: "
                f"workflow={moonmind_workflow_id!r} admits multiple unattached "
                "sessions, so a NULL scope lookup is ambiguous"
            )
        stmt = select(OmnigentSession).where(
            OmnigentSession.moonmind_workflow_id == moonmind_workflow_id,
            OmnigentSession.provider_session_ref == provider_session_ref,
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return _session_record(row) if row is not None else None

    async def get_by_provider_session(
        self, provider_session_ref: str
    ) -> Optional[SessionRecord]:
        """Return the one canonical session holding this provider attachment.

        Recovery owners know the provider session but not the workflow scope, so
        this resolves the aggregate the turn boundary attached. Provider session
        identifiers are provider-generated and unique; more than one match is
        ambiguous authority and fails closed rather than picking a session.
        """

        if not provider_session_ref:
            return None
        stmt = select(OmnigentSession).where(
            OmnigentSession.provider_session_ref == provider_session_ref
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        if not rows:
            return None
        if len(rows) > 1:
            raise ConflictingSessionAuthorityError(
                "provider session "
                f"{provider_session_ref!r} resolves multiple canonical sessions"
            )
        return _session_record(rows[0])

    async def get_by_step_execution(
        self, moonmind_workflow_id: str, step_execution_id: str
    ) -> Optional[SessionRecord]:
        """Return the canonical session a prior Step Execution established.

        A remediation turn is bounded by the authority of the attempt it
        repairs, and that attempt is named by its Step Execution identity rather
        than by a session id the instruction could forge. A Step Execution may
        legitimately own more than one canonical session (a later agent run for
        the same execution), so the *earliest* row is returned: the authority a
        remediation may not broaden is the one first established, not whichever
        row happens to sort last.
        """

        if not moonmind_workflow_id or not step_execution_id:
            return None
        stmt = (
            select(OmnigentSession)
            .where(
                OmnigentSession.moonmind_workflow_id == moonmind_workflow_id,
                OmnigentSession.step_execution_id == step_execution_id,
            )
            .order_by(OmnigentSession.created_at, OmnigentSession.session_id)
            .limit(1)
        )
        row = (await self._session.execute(stmt)).scalars().first()
        return _session_record(row) if row is not None else None

    async def get_by_chat_binding(self, chat_binding_id: str) -> Optional[SessionRecord]:
        stmt = select(OmnigentSession).where(
            OmnigentSession.chat_binding_id == chat_binding_id
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return _session_record(row) if row is not None else None

    async def allocate_chat_binding(
        self, session_id: str, chat_binding_id: str
    ) -> SessionRecord:
        """Bind the single opaque chat handle to this canonical session.

        Fails closed if the session already owns a different binding or the
        handle already belongs to another session.
        """

        row = await self._load(session_id)
        if row is None:
            raise ConflictingSessionAuthorityError(
                f"Unknown canonical session {session_id!r}"
            )
        if row.chat_binding_id is not None and row.chat_binding_id != chat_binding_id:
            raise ConflictingSessionAuthorityError(
                f"Session {session_id!r} already owns chat binding "
                f"{row.chat_binding_id!r}; refusing to rebind"
            )
        if row.chat_binding_id == chat_binding_id:
            return _session_record(row)
        row.chat_binding_id = chat_binding_id
        try:
            async with self._session.begin_nested():
                await self._session.flush()
        except IntegrityError as exc:
            raise ConflictingSessionAuthorityError(
                f"Chat binding {chat_binding_id!r} already bound to another "
                "canonical session"
            ) from exc
        await self._session.refresh(row)
        return _session_record(row)

    async def attach_provider_session(
        self,
        session_id: str,
        provider_session_ref: str,
        *,
        expected_revision: int,
        expected_fencing_generation: int,
    ) -> SessionRecord:
        """Attach provider authority under the active supervisor fence.

        Source: MoonLadderStudios/MoonMind#3705. Provider session creation is an
        external side effect, so a delayed Activity result must not attach its
        receipt after canonical revision or supervisor ownership has advanced.
        An exact replay is safe and remains idempotent.
        """

        row = await self._load(session_id, for_update=True)
        if row is None:
            raise ConflictingSessionAuthorityError(
                f"Unknown canonical session {session_id!r}"
            )
        if (
            row.provider_session_ref is not None
            and row.provider_session_ref != provider_session_ref
        ):
            raise ConflictingSessionAuthorityError(
                f"Session {session_id!r} already attached to provider session "
                f"{row.provider_session_ref!r}"
            )
        if row.provider_session_ref == provider_session_ref:
            return _session_record(row)
        conflict = self._check_session_fence(
            row,
            expected_revision=expected_revision,
            expected_fencing_generation=expected_fencing_generation,
            expected_provider_profile_generation=None,
            expected_host_lease_generation=None,
        )
        if conflict is ControlPlaneOutcome.FENCING_CONFLICT:
            raise FencingConflictError(
                f"Session {session_id!r} supervisor fence changed before "
                "provider attachment"
            )
        if conflict is ControlPlaneOutcome.REVISION_CONFLICT:
            raise RevisionConflictError(
                f"Session {session_id!r} revision changed before provider "
                "attachment"
            )
        row.provider_session_ref = provider_session_ref
        row.revision = row.revision + 1
        try:
            async with self._session.begin_nested():
                await self._session.flush()
        except IntegrityError as exc:
            raise ConflictingSessionAuthorityError(
                "Refusing to attach provider session "
                f"{provider_session_ref!r}: scope already owned"
            ) from exc
        await self._session.refresh(row)
        return _session_record(row)

    async def bind_runtime_authority(
        self,
        session_id: str,
        *,
        expected_revision: int,
        expected_fencing_generation: int,
        provider_profile_id: Any = _UNSET,
        host_binding_ref: Any = _UNSET,
        host_lease_ref: Any = _UNSET,
        credential_generation: Any = _UNSET,
        provider_profile_generation: Any = _UNSET,
        host_lease_generation: Any = _UNSET,
        execution_plan_ref: Any = _UNSET,
        runtime_binding_ref: Any = _UNSET,
        metadata_patch: Optional[dict[str, Any]] = None,
    ) -> SessionRecord:
        """Bind safe runtime refs under the supervisor revision/fence.

        Source: MoonLadderStudios/MoonMind#3705. Bounded side-effect Activities
        persist their provider/profile/host receipts here before returning. An
        exact replay is idempotent even when it presents the pre-write revision;
        a different stale write still fails with the normal revision/fencing
        outcome.
        """

        row = await self._load(session_id, for_update=True)
        if row is None:
            raise ConflictingSessionAuthorityError(
                f"Unknown canonical session {session_id!r}"
            )
        if runtime_binding_ref is not _UNSET and runtime_binding_ref is not None:
            binding = (
                await self._session.execute(
                    select(OmnigentRuntimeBindingRecord).where(
                        OmnigentRuntimeBindingRecord.binding_id
                        == runtime_binding_ref
                    )
                )
            ).scalar_one_or_none()
            if binding is None:
                raise ConflictingSessionAuthorityError(
                    f"Session {session_id!r} cannot bind unknown runtime authority"
                )
            intended_plan_ref = (
                execution_plan_ref
                if execution_plan_ref is not _UNSET
                else row.execution_plan_ref
            )
            if (
                intended_plan_ref is None
                or binding.execution_plan_ref != intended_plan_ref
            ):
                raise ConflictingSessionAuthorityError(
                    f"Session {session_id!r} runtime binding does not belong to "
                    "its immutable execution plan"
                )
            if row.runtime_binding_ref not in {None, runtime_binding_ref}:
                raise ConflictingSessionAuthorityError(
                    f"Session {session_id!r} cannot replace its stable runtime binding"
                )
        provided = {
            name: value
            for name, value in (
                ("provider_profile_id", provider_profile_id),
                ("host_binding_ref", host_binding_ref),
                ("host_lease_ref", host_lease_ref),
                ("credential_generation", credential_generation),
                ("provider_profile_generation", provider_profile_generation),
                ("host_lease_generation", host_lease_generation),
                ("execution_plan_ref", execution_plan_ref),
            )
            if value is not _UNSET
        }
        metadata_patch = dict(metadata_patch or {})
        already_applied = all(getattr(row, name) == value for name, value in provided.items())
        if metadata_patch:
            already_applied = already_applied and all(
                (row.metadata_ or {}).get(key) == value
                for key, value in metadata_patch.items()
            )
        if runtime_binding_ref is not _UNSET:
            already_applied = (
                already_applied and row.runtime_binding_ref == runtime_binding_ref
            )
        if already_applied and (
            provided or metadata_patch or runtime_binding_ref is not _UNSET
        ):
            return _session_record(row)
        conflict = self._check_session_fence(
            row,
            expected_revision=expected_revision,
            expected_fencing_generation=expected_fencing_generation,
            expected_provider_profile_generation=None,
            expected_host_lease_generation=None,
        )
        if conflict is ControlPlaneOutcome.FENCING_CONFLICT:
            raise FencingConflictError(
                f"Session {session_id!r} supervisor fence changed"
            )
        if conflict is ControlPlaneOutcome.REVISION_CONFLICT:
            raise RevisionConflictError(
                f"Session {session_id!r} revision changed before authority bind"
            )
        for name, value in provided.items():
            existing = getattr(row, name)
            if existing is not None and value is not None and existing != value:
                raise ConflictingSessionAuthorityError(
                    f"Session {session_id!r} already binds {name}={existing!r}; "
                    f"refusing {value!r}"
                )
            setattr(row, name, value)
        if runtime_binding_ref is not _UNSET:
            # Runtime binding envelopes are immutable, digest-addressed stages;
            # the session points at the latest stage for the same immutable plan.
            row.runtime_binding_ref = runtime_binding_ref
        if metadata_patch:
            metadata = dict(row.metadata_ or {})
            next_binding_revision = metadata_patch.get("runtimeBindingRevision")
            current_binding_revision = metadata.get("runtimeBindingRevision")
            if next_binding_revision is not None and current_binding_revision is not None:
                if int(next_binding_revision) < int(current_binding_revision):
                    raise FencingConflictError(
                        f"Session {session_id!r} received a stale runtime binding revision"
                    )
            for key, value in metadata_patch.items():
                existing = metadata.get(key)
                mutable_binding_projection = key in {
                    "runtimeBindingRef",
                    "runtimeBindingRevision",
                    "runtimeBindingFencingGeneration",
                    "runtimeBindingState",
                }
                if (
                    existing is not None
                    and value is not None
                    and existing != value
                    and not mutable_binding_projection
                ):
                    raise ConflictingSessionAuthorityError(
                        f"Session {session_id!r} metadata {key!r} already has "
                        "different immutable authority"
                    )
                metadata[key] = value
            row.metadata_ = metadata
        row.revision = row.revision + 1
        await self._session.flush()
        await self._session.refresh(row)
        return _session_record(row)

    async def replace_provider_runtime_authority(
        self,
        session_id: str,
        *,
        expected_revision: int,
        expected_fencing_generation: int,
        expected_provider_profile_generation: int,
        provider_profile_id: str,
        credential_generation: int,
        metadata_patch: dict[str, Any],
    ) -> SessionRecord:
        """Project one already-fenced Provider Profile lease replacement.

        The runtime-binding aggregate owns the replacement decision. This
        projection is permitted only before host/session realization and only
        after both the binding fence and Provider Profile lease fence advanced.
        """

        row = await self._load(session_id, for_update=True)
        if row is None:
            raise ConflictingSessionAuthorityError(
                f"Unknown canonical session {session_id!r}"
            )
        conflict = self._check_session_fence(
            row,
            expected_revision=expected_revision,
            expected_fencing_generation=expected_fencing_generation,
            expected_provider_profile_generation=(
                expected_provider_profile_generation
            ),
            expected_host_lease_generation=None,
        )
        if conflict is ControlPlaneOutcome.FENCING_CONFLICT:
            raise FencingConflictError(
                f"Session {session_id!r} Provider Profile fence changed"
            )
        if conflict is ControlPlaneOutcome.REVISION_CONFLICT:
            raise RevisionConflictError(
                f"Session {session_id!r} revision changed before Provider "
                "Profile replacement"
            )
        if row.provider_profile_id not in {None, provider_profile_id}:
            raise ConflictingSessionAuthorityError(
                f"Session {session_id!r} cannot replace its Provider Profile"
            )
        if any(
            value is not None
            for value in (
                row.host_binding_ref,
                row.host_lease_ref,
                row.provider_session_ref,
            )
        ):
            raise ConflictingSessionAuthorityError(
                f"Session {session_id!r} must drain live host/session authority "
                "before Provider Profile replacement"
            )

        metadata = dict(row.metadata_ or {})
        current_binding_revision = int(
            metadata.get("runtimeBindingRevision") or 0
        )
        current_binding_fence = int(
            metadata.get("runtimeBindingFencingGeneration") or 0
        )
        next_binding_revision = int(
            metadata_patch.get("runtimeBindingRevision") or 0
        )
        next_binding_fence = int(
            metadata_patch.get("runtimeBindingFencingGeneration") or 0
        )
        if (
            next_binding_revision <= current_binding_revision
            or next_binding_fence <= current_binding_fence
        ):
            raise FencingConflictError(
                f"Session {session_id!r} Provider Profile replacement lacks "
                "a newer runtime-binding revision and fence"
            )
        for immutable_key in (
            "executionPlanRef",
            "providerLeaseOwnerId",
            "providerRuntimeId",
        ):
            current_value = metadata.get(immutable_key)
            next_value = metadata_patch.get(immutable_key)
            if (
                current_value is not None
                and next_value is not None
                and current_value != next_value
            ):
                raise ConflictingSessionAuthorityError(
                    f"Session {session_id!r} cannot replace immutable "
                    f"metadata {immutable_key!r}"
                )

        row.provider_profile_id = provider_profile_id
        row.credential_generation = credential_generation
        metadata.update(metadata_patch)
        row.metadata_ = metadata
        row.revision = row.revision + 1
        await self._session.flush()
        await self._session.refresh(row)
        return _session_record(row)

    @staticmethod
    def _check_session_fence(
        row: OmnigentSession,
        *,
        expected_revision: int,
        expected_fencing_generation: int,
        expected_provider_profile_generation: Optional[int],
        expected_host_lease_generation: Optional[int],
    ) -> Optional[ControlPlaneOutcome]:
        """Validate optimistic-concurrency and fencing authority for a write.

        Returns ``None`` when the caller holds current authority, or the stable
        conflict outcome otherwise. Fencing generations are checked before the
        revision so a superseded owner is fenced out even if its revision happens
        to match, and each generation is attributed to its own fencing scope for
        telemetry. Emitting the counter here keeps every fenced write path
        observable from one place (#3704).
        """

        if row.fencing_generation != expected_fencing_generation:
            telemetry.record_fencing_conflict(scope=FencingScope.SESSION_SUPERVISOR)
            return ControlPlaneOutcome.FENCING_CONFLICT
        if (
            expected_provider_profile_generation is not None
            and row.provider_profile_generation != expected_provider_profile_generation
        ):
            telemetry.record_fencing_conflict(scope=FencingScope.PROVIDER_PROFILE_LEASE)
            return ControlPlaneOutcome.FENCING_CONFLICT
        if (
            expected_host_lease_generation is not None
            and row.host_lease_generation != expected_host_lease_generation
        ):
            telemetry.record_fencing_conflict(scope=FencingScope.HOST_LEASE)
            return ControlPlaneOutcome.FENCING_CONFLICT
        if row.revision != expected_revision:
            telemetry.record_revision_conflict(scope=FencingScope.SESSION_SUPERVISOR)
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
        """Fenced lifecycle write returning a stable :class:`ControlPlaneOutcome`.

        The caller declares the revision and fencing generations it observed;
        lease-owner writers additionally declare the lease generation relevant to
        their side effect. A revision/fencing conflict is a benign convergence
        signal (returned as a :class:`CasResult`, not raised) so the reconciler
        reloads and retries against fresh authority. A terminal-authority
        violation is not a benign convergence case and still fails closed
        (raised), because immutable authority must never be silently regressed.
        """

        row = await self._load(session_id, for_update=True)
        if row is None:
            raise ConflictingSessionAuthorityError(
                f"Unknown canonical session {session_id!r}"
            )
        conflict = self._check_session_fence(
            row,
            expected_revision=expected_revision,
            expected_fencing_generation=expected_fencing_generation,
            expected_provider_profile_generation=expected_provider_profile_generation,
            expected_host_lease_generation=expected_host_lease_generation,
        )
        if conflict is not None:
            return CasResult(conflict, _session_record(row))
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
        if row.terminal_state is not None:
            disallowed = [
                name
                for name, _ in provided
                if name not in _POST_TERMINAL_MUTABLE_FIELDS
            ]
            if disallowed:
                raise TerminalSessionOverwriteError(
                    f"Session {session_id!r} is terminal ({row.terminal_state!r}); "
                    "refusing nonterminal lifecycle update to "
                    f"{sorted(disallowed)} (only "
                    f"{sorted(_POST_TERMINAL_MUTABLE_FIELDS)} may advance)"
                )
        for name, value in provided:
            setattr(row, name, value)
        row.revision = row.revision + 1
        await self._session.flush()
        await self._session.refresh(row)
        return CasResult(ControlPlaneOutcome.APPLIED, _session_record(row))

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
        """Update mutable lifecycle fields under a mandatory revision/fencing guard.

        Convenience wrapper over :meth:`compare_and_swap_session` for callers that
        want fail-closed exceptions instead of a returned outcome. The expected
        revision and session-supervisor fencing generation are mandatory (#3704):
        a lifecycle-changing write must always declare the authority it observed.
        Never clears or rewrites session terminality; once terminal, only the
        cleanup/archive fields in :data:`_POST_TERMINAL_MUTABLE_FIELDS` may
        advance so the normal terminal-then-cleanup journey can record completion.
        """

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
        _raise_for_session_conflict(session_id, result)
        return result.record

    async def acquire_fencing_generation(
        self,
        session_id: str,
        scope: FencingScope,
        *,
        expected_revision: int,
    ) -> SessionRecord:
        """Acquire a strictly newer fencing generation for ``scope``.

        A newly acquired owner (session supervisor, Provider Profile lease, or
        host lease) receives ``current + 1`` so every former owner of that scope
        is fenced out of subsequent writes. The acquisition compare-and-swaps on
        the session revision so two racing replacements cannot both win: exactly
        one advances the generation and the loser observes a revision conflict and
        reloads. ``CLEANUP`` authority is durable in its own aggregate; acquire it
        through :class:`CleanupAuthorityRepository` instead.
        """

        if scope is FencingScope.CLEANUP:
            raise ValueError(
                "cleanup fencing is owned by CleanupAuthorityRepository, not "
                "acquire_fencing_generation"
            )
        row = await self._load(session_id, for_update=True)
        if row is None:
            raise ConflictingSessionAuthorityError(
                f"Unknown canonical session {session_id!r}"
            )
        if row.revision != expected_revision:
            telemetry.record_revision_conflict(scope=scope)
            raise RevisionConflictError(
                f"Session {session_id!r} revision {row.revision} != expected "
                f"{expected_revision}; cannot acquire {scope.value} generation"
            )
        if scope is FencingScope.SESSION_SUPERVISOR:
            row.fencing_generation = row.fencing_generation + 1
        elif scope is FencingScope.PROVIDER_PROFILE_LEASE:
            row.provider_profile_generation = (row.provider_profile_generation or 0) + 1
        elif scope is FencingScope.HOST_LEASE:
            row.host_lease_generation = (row.host_lease_generation or 0) + 1
        row.revision = row.revision + 1
        await self._session.flush()
        await self._session.refresh(row)
        return _session_record(row)

    async def recover_from_quarantine(
        self,
        session_id: str,
        *,
        expected_revision: int,
        expected_fencing_generation: int,
    ) -> SessionRecord:
        """Restore a quarantined session to live under current fencing.

        Quarantine is an operator-visible parked state, not a terminal
        deletion. After the underlying provider, lease, or evidence
        problem is repaired, an authorized operator may restore
        interaction by revalidating the repaired authority and resetting
        ``historical_read_state`` to ``live``. The recovery is fenced
        against the session-supervisor generation so a stale operator
        cannot resurrect a session that a newer supervisor has moved.
        """

        row = await self._load(session_id, for_update=True)
        if row is None:
            raise ConflictingSessionAuthorityError(
                f"Unknown canonical session {session_id!r}"
            )
        if row.historical_read_state != "quarantined":
            return _session_record(row)
        conflict = self._check_session_fence(
            row,
            expected_revision=expected_revision,
            expected_fencing_generation=expected_fencing_generation,
            expected_provider_profile_generation=None,
            expected_host_lease_generation=None,
        )
        if conflict is ControlPlaneOutcome.FENCING_CONFLICT:
            raise FencingConflictError(
                f"Session {session_id!r} supervisor fence changed; refusing quarantine recovery"
            )
        if conflict is ControlPlaneOutcome.REVISION_CONFLICT:
            raise RevisionConflictError(
                f"Session {session_id!r} revision changed; refusing quarantine recovery"
            )
        row.historical_read_state = "live"
        row.revision = row.revision + 1
        await self._session.flush()
        await self._session.refresh(row)
        return _session_record(row)

    async def advance_observation_frontier(
        self,
        session_id: str,
        *,
        expected_revision: int,
        expected_fencing_generation: int,
        provider_event_cursor: Any = _UNSET,
        snapshot_frontier: Any = _UNSET,
    ) -> CasResult:
        """Advance the provider event / snapshot frontier under a fencing guard.

        A delayed event or callback must prove it belongs to the current provider
        epoch (the session-supervisor fencing generation) before it advances the
        durable frontier. A stale-epoch write is fenced (``fencing_conflict``) and
        the caller retains it as an append-only observation without regressing
        current lifecycle state; a lost update returns ``revision_conflict``. The
        stale-retention counter is emitted so operators can see how often delayed
        results arrive after supersession (#3704).
        """

        row = await self._load(session_id, for_update=True)
        if row is None:
            raise ConflictingSessionAuthorityError(
                f"Unknown canonical session {session_id!r}"
            )
        conflict = self._check_session_fence(
            row,
            expected_revision=expected_revision,
            expected_fencing_generation=expected_fencing_generation,
            expected_provider_profile_generation=None,
            expected_host_lease_generation=None,
        )
        if conflict is ControlPlaneOutcome.FENCING_CONFLICT:
            telemetry.record_stale_observation_retained()
            return CasResult(conflict, _session_record(row))
        if conflict is not None:
            return CasResult(conflict, _session_record(row))
        if provider_event_cursor is not _UNSET:
            row.provider_event_cursor = provider_event_cursor
        if snapshot_frontier is not _UNSET:
            row.snapshot_frontier = snapshot_frontier
        row.revision = row.revision + 1
        await self._session.flush()
        await self._session.refresh(row)
        return CasResult(ControlPlaneOutcome.APPLIED, _session_record(row))

    async def mark_terminal(
        self,
        session_id: str,
        terminal_state: str,
        *,
        expected_revision: int,
        expected_fencing_generation: int,
        terminal_evidence_ref: Optional[str] = None,
    ) -> SessionRecord:
        """Record the canonical session terminal under a mandatory fencing guard.

        Recording the same terminal twice is idempotent (``already_applied``); a
        contradictory terminal fails closed. A stale writer that presents an old
        revision or a superseded fencing generation is refused so a delayed
        activity cannot terminalize a session that a newer owner has moved on.
        """

        row = await self._load(session_id, for_update=True)
        if row is None:
            raise ConflictingSessionAuthorityError(
                f"Unknown canonical session {session_id!r}"
            )
        if row.terminal_state is not None:
            if row.terminal_state == terminal_state:
                return _session_record(row)
            raise TerminalSessionOverwriteError(
                f"Session {session_id!r} already terminal "
                f"({row.terminal_state!r}); refusing to overwrite with "
                f"{terminal_state!r}"
            )
        conflict = self._check_session_fence(
            row,
            expected_revision=expected_revision,
            expected_fencing_generation=expected_fencing_generation,
            expected_provider_profile_generation=None,
            expected_host_lease_generation=None,
        )
        if conflict is ControlPlaneOutcome.FENCING_CONFLICT:
            raise FencingConflictError(
                f"Session {session_id!r} fencing generation "
                f"{row.fencing_generation} != expected "
                f"{expected_fencing_generation}; refusing terminal write"
            )
        if conflict is ControlPlaneOutcome.REVISION_CONFLICT:
            raise RevisionConflictError(
                f"Session {session_id!r} revision {row.revision} != expected "
                f"{expected_revision}; refusing terminal write"
            )
        terminal_observed_at = (
            await self._session.execute(
                select(func.max(OmnigentObservation.observed_at)).where(
                    OmnigentObservation.session_id == session_id,
                    OmnigentObservation.observation_type.in_(
                        ("snapshot", "provider_snapshot")
                    ),
                )
            )
        ).scalar_one_or_none()
        row.terminal_state = terminal_state
        if terminal_evidence_ref is not None:
            row.terminal_evidence_ref = terminal_evidence_ref
        row.revision = row.revision + 1
        await self._session.flush()
        await self._session.refresh(row)
        if terminal_observed_at is not None:
            observed = (
                terminal_observed_at.replace(tzinfo=UTC)
                if terminal_observed_at.tzinfo is None
                else terminal_observed_at
            )
            try:
                metrics.observe(
                    metrics.PROVIDER_TERMINAL_TO_MOONMIND_TERMINAL_LATENCY,
                    max(0.0, (datetime.now(UTC) - observed).total_seconds()),
                )
            except Exception:
                pass  # Telemetry failures must not affect lifecycle authority
        return _session_record(row)

    async def attach_terminal_evidence(
        self,
        session_id: str,
        *,
        terminal_evidence_ref: str,
        expected_revision: int,
        expected_fencing_generation: int,
    ) -> SessionRecord:
        """Attach immutable evidence under current revision and ownership."""

        row = await self._load(session_id, for_update=True)
        if row is None:
            raise ConflictingSessionAuthorityError(
                f"Unknown canonical session {session_id!r}"
            )
        if row.terminal_state is None:
            raise TerminalSessionOverwriteError(
                f"Session {session_id!r} is not terminal; evidence cannot attach"
            )
        if row.fencing_generation != expected_fencing_generation:
            raise FencingConflictError(
                f"Session {session_id!r} supervisor fence changed"
            )
        if row.terminal_evidence_ref is not None:
            if row.terminal_evidence_ref == terminal_evidence_ref:
                return _session_record(row)
            raise TerminalSessionOverwriteError(
                f"Session {session_id!r} already owns different terminal evidence"
            )
        conflict = self._check_session_fence(
            row,
            expected_revision=expected_revision,
            expected_fencing_generation=expected_fencing_generation,
            expected_provider_profile_generation=None,
            expected_host_lease_generation=None,
        )
        if conflict is ControlPlaneOutcome.FENCING_CONFLICT:
            raise FencingConflictError(
                f"Session {session_id!r} supervisor fence changed before "
                "terminal evidence attachment"
            )
        if conflict is ControlPlaneOutcome.REVISION_CONFLICT:
            raise RevisionConflictError(
                f"Session {session_id!r} revision changed before terminal "
                "evidence attachment"
            )
        row.terminal_evidence_ref = terminal_evidence_ref
        row.revision = row.revision + 1
        await self._session.flush()
        await self._session.refresh(row)
        return _session_record(row)


# --- TurnAttemptRepository ---------------------------------------------------


class TurnAttemptRepository(_RepositoryBase):
    """Turn-attempt repository. Owns request idempotency; never chat binding.

    Note there is intentionally no way to write a ``chat_binding_id`` through
    this repository -- the underlying model has no such column, so a continuation
    or remediation turn cannot acquire chat-binding authority.
    """

    async def create(
        self,
        *,
        turn_attempt_id: str,
        session_id: str,
        idempotency_key: str,
        lineage_kind: Any = TurnSource.INITIAL,
        step_execution_id: Optional[str] = None,
        parent_turn_attempt_id: Optional[str] = None,
        remediation_of_turn_attempt_id: Optional[str] = None,
        instruction_digest: Optional[str] = None,
        provider_marker: Optional[str] = None,
        state: str = TURN_STATE_PREPARED,
    ) -> TurnAttemptRecord:
        # The turn source is a closed, versioned vocabulary (#3707): an
        # unrecognized value fails closed instead of being coerced.
        source = coerce_turn_source(lineage_kind)
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
        row = OmnigentTurnAttempt(
            turn_attempt_id=turn_attempt_id,
            session_id=session_id,
            idempotency_key=idempotency_key,
            lineage_kind=source.value,
            step_execution_id=step_execution_id,
            parent_turn_attempt_id=parent_turn_attempt_id,
            remediation_of_turn_attempt_id=remediation_of_turn_attempt_id,
            instruction_digest=instruction_digest,
            provider_marker=provider_marker,
            state=state,
        )
        await self._insert(
            row,
            on_conflict=lambda exc: TurnIdempotencyConflictError(
                f"Idempotency key {idempotency_key!r} already exists"
            ),
        )
        return _turn_record(row)

    async def get(self, turn_attempt_id: str) -> Optional[TurnAttemptRecord]:
        row = await self._session.get(OmnigentTurnAttempt, turn_attempt_id)
        return _turn_record(row) if row is not None else None

    async def get_by_idempotency_key(
        self, idempotency_key: str
    ) -> Optional[TurnAttemptRecord]:
        stmt = select(OmnigentTurnAttempt).where(
            OmnigentTurnAttempt.idempotency_key == idempotency_key
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return _turn_record(row) if row is not None else None

    async def list_for_session(
        self,
        session_id: str,
        *,
        limit: Optional[int] = None,
        latest: bool = False,
    ) -> list[TurnAttemptRecord]:
        order = (
            (
                OmnigentTurnAttempt.created_at.desc(),
                OmnigentTurnAttempt.turn_attempt_id.desc(),
            )
            if latest
            else (
                OmnigentTurnAttempt.created_at,
                OmnigentTurnAttempt.turn_attempt_id,
            )
        )
        stmt = (
            select(OmnigentTurnAttempt)
            .where(OmnigentTurnAttempt.session_id == session_id)
            .order_by(*order)
        )
        if limit is not None:
            stmt = stmt.limit(limit)
        rows = (await self._session.execute(stmt)).scalars().all()
        if latest:
            rows = list(reversed(rows))
        return [_turn_record(r) for r in rows]

    async def count_for_session(self, session_id: str) -> int:
        """Return the number of turn attempts without materializing the rows."""

        stmt = (
            select(func.count())
            .select_from(OmnigentTurnAttempt)
            .where(OmnigentTurnAttempt.session_id == session_id)
        )
        return int((await self._session.execute(stmt)).scalar_one())

    async def _load_turn_for_update(
        self, turn_attempt_id: str
    ) -> OmnigentTurnAttempt:
        row = await self._session.get(
            OmnigentTurnAttempt, turn_attempt_id, with_for_update=True
        )
        if row is None:
            raise TurnIdempotencyConflictError(
                f"Unknown turn attempt {turn_attempt_id!r}"
            )
        return row

    @staticmethod
    def _check_turn_fence(
        row: OmnigentTurnAttempt,
        *,
        session_fencing_generation: int,
        expected_revision: int,
        expected_fencing_generation: int,
    ) -> Optional[ControlPlaneOutcome]:
        # Turn writes are guarded by the owning session's SESSION_SUPERVISOR
        # generation, not the turn's creation-time value. A turn row has no way to
        # bind the current session generation at creation, so comparing against
        # the turn's own stored generation would fence the new supervisor out
        # while letting a superseded one keep mutating the attempt. Validate
        # against the live session generation instead, then stamp it on apply so
        # the record reflects the guarding authority (#3704).
        if session_fencing_generation != expected_fencing_generation:
            telemetry.record_fencing_conflict(scope=FencingScope.SESSION_SUPERVISOR)
            return ControlPlaneOutcome.FENCING_CONFLICT
        if row.revision != expected_revision:
            telemetry.record_revision_conflict(scope=FencingScope.SESSION_SUPERVISOR)
            return ControlPlaneOutcome.REVISION_CONFLICT
        return None

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
        """Fenced turn-attempt write returning a stable outcome.

        Turn-attempt submission and terminal state are independently mutable
        surfaces (#3704): a delayed activity result must present the revision and
        fencing generation it observed or be refused. A conflict is a benign
        convergence signal; a terminal attempt cannot be moved back to a
        nonterminal state or overwritten with a contradictory terminal.
        """

        row = await self._load_turn_for_update(turn_attempt_id)
        session_row = await self._session.get(OmnigentSession, row.session_id)
        session_generation = (
            session_row.fencing_generation
            if session_row is not None
            else row.fencing_generation
        )
        wants_terminal = terminal_state is not _UNSET
        if row.terminal_state is not None:
            if wants_terminal and terminal_state == row.terminal_state:
                return CasResult(
                    ControlPlaneOutcome.ALREADY_APPLIED, _turn_record(row)
                )
            raise TurnIdempotencyConflictError(
                f"Turn attempt {turn_attempt_id!r} already terminal "
                f"({row.terminal_state!r}); refusing to overwrite"
            )
        conflict = self._check_turn_fence(
            row,
            session_fencing_generation=session_generation,
            expected_revision=expected_revision,
            expected_fencing_generation=expected_fencing_generation,
        )
        if conflict is not None:
            return CasResult(conflict, _turn_record(row))
        if wants_terminal:
            row.state = TURN_STATE_TERMINAL
            row.terminal_state = terminal_state
        elif state is not _UNSET:
            if state not in TURN_STATES:
                raise ValueError(
                    f"Unknown turn state {state!r}; must be one of "
                    f"{sorted(TURN_STATES)}"
                )
            # Enforce the documented monotonic delivery order (#3704): an
            # out-of-order provider observation must never regress durable
            # attempt authority even when its revision and fence are current. A
            # regressive state is an idempotent no-op that leaves the
            # already-advanced authority intact (re-asserting the current state
            # is still allowed to refresh provider markers).
            if TURN_STATE_ORDER[state] < TURN_STATE_ORDER.get(row.state, -1):
                return CasResult(
                    ControlPlaneOutcome.ALREADY_APPLIED, _turn_record(row)
                )
            row.state = state
        # A guarded turn write always advances against the live session
        # generation; stamp it so the record reflects the guarding authority.
        row.fencing_generation = session_generation
        if attempt_outcome is not _UNSET:
            row.attempt_outcome = attempt_outcome
        if provider_turn_id is not _UNSET:
            row.provider_turn_id = provider_turn_id
        if provider_item_id is not _UNSET:
            row.provider_item_id = provider_item_id
        if terminal_evidence_ref is not _UNSET:
            row.terminal_evidence_ref = terminal_evidence_ref
        row.revision = row.revision + 1
        await self._session.flush()
        await self._session.refresh(row)
        return CasResult(ControlPlaneOutcome.APPLIED, _turn_record(row))

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
        """Advance the turn delivery state under a mandatory fencing guard."""

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
        _raise_for_turn_conflict(turn_attempt_id, result)
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
        """Record the attempt terminal under a mandatory fencing guard.

        A terminal attempt never terminalizes the canonical session (that
        authority lives on :class:`OmnigentSession`); recording the same attempt
        terminal twice is idempotent.
        """

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
        _raise_for_turn_conflict(turn_attempt_id, result)
        return result.record


# --- ObservationRepository ---------------------------------------------------


class ObservationRepository(_RepositoryBase):
    """Append-only bounded observation index repository."""

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
        span_name = (
            spans.PROVIDER_OBSERVE_SNAPSHOT
            if observation_type in {"snapshot", "provider_snapshot"}
            else spans.PROVIDER_READ_EVENT_BATCH
            if observation_type in {
                "event",
                "event_frontier",
                "event_batch",
                "provider_event",
                "provider_event_batch",
            }
            else spans.OBSERVATION_LOAD
        )
        schema_value = (bounded_index or {}).get("schemaVersion")
        if schema_value not in (None, 1, "1"):
            try:
                metrics.increment(metrics.UNKNOWN_SCHEMA_VALUE)
            except Exception:
                pass  # Telemetry failures must not affect lifecycle authority
        with spans.omnigent_span(
            span_name,
            observation_source=source,
            observation_schema_version=1,
        ):
            return await self._append(
                observation_id=observation_id,
                session_id=session_id,
                observation_type=observation_type,
                source=source,
                observed_at=observed_at,
                deduplication_key=deduplication_key,
                source_sequence=source_sequence,
                source_digest=source_digest,
                payload_ref=payload_ref,
                bounded_index=bounded_index,
            )

    async def _append(
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
        """Append an observation. Idempotent on ``(session_id, dedup_key)``."""

        existing = await self._by_dedup(session_id, deduplication_key)
        if existing is not None:
            return _observation_record(existing)
        row = OmnigentObservation(
            observation_id=observation_id,
            session_id=session_id,
            observation_type=observation_type,
            source=source,
            observed_at=observed_at,
            deduplication_key=deduplication_key,
            source_sequence=source_sequence,
            source_digest=source_digest,
            payload_ref=payload_ref,
            bounded_index_=dict(bounded_index or {}),
        )
        try:
            async with self._session.begin_nested():
                self._session.add(row)
                await self._session.flush()
        except IntegrityError:
            # Concurrent append with the same dedup identity: return the winner.
            existing = await self._by_dedup(session_id, deduplication_key)
            if existing is not None:
                return _observation_record(existing)
            raise
        await self._session.refresh(row)
        return _observation_record(row)

    async def _by_dedup(
        self, session_id: str, deduplication_key: str
    ) -> Optional[OmnigentObservation]:
        stmt = select(OmnigentObservation).where(
            OmnigentObservation.session_id == session_id,
            OmnigentObservation.deduplication_key == deduplication_key,
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def list_for_session(
        self,
        session_id: str,
        *,
        observation_type: Optional[str] = None,
        limit: Optional[int] = None,
        latest: bool = False,
    ) -> list[ObservationRecord]:
        stmt = select(OmnigentObservation).where(
            OmnigentObservation.session_id == session_id
        )
        if observation_type is not None:
            stmt = stmt.where(OmnigentObservation.observation_type == observation_type)
        order = (
            (
                OmnigentObservation.observed_at.desc(),
                OmnigentObservation.observation_id.desc(),
            )
            if latest
            else (
                OmnigentObservation.observed_at,
                OmnigentObservation.observation_id,
            )
        )
        stmt = stmt.order_by(*order)
        if limit is not None:
            stmt = stmt.limit(limit)
        rows = (await self._session.execute(stmt)).scalars().all()
        if latest:
            rows = list(reversed(rows))
        return [_observation_record(r) for r in rows]

    async def latest_for_session(
        self,
        session_id: str,
        *,
        observation_types: Optional[Sequence[str]] = None,
    ) -> Optional[ObservationRecord]:
        """Return the most recent observation, optionally filtered by type.

        Bounded (``LIMIT 1``) so an operator diagnostic never materializes the
        full append-only observation history for a long-running session.
        """

        stmt = select(OmnigentObservation).where(
            OmnigentObservation.session_id == session_id
        )
        if observation_types is not None:
            stmt = stmt.where(
                OmnigentObservation.observation_type.in_(list(observation_types))
            )
        stmt = stmt.order_by(
            OmnigentObservation.observed_at.desc(),
            OmnigentObservation.observation_id.desc(),
        ).limit(1)
        row = (await self._session.execute(stmt)).scalars().first()
        return _observation_record(row) if row is not None else None


# --- CommandRepository -------------------------------------------------------


class CommandRepository(_RepositoryBase):
    """Durable command / idempotency journal repository."""

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
        with spans.omnigent_span(
            spans.COMMAND_EXECUTE,
            command_class=command_type,
            expected_revision=expected_session_revision,
            fencing_generation_ordinal=fencing_generation,
        ):
            return await self._record(
                command_id=command_id,
                session_id=session_id,
                command_type=command_type,
                idempotency_key=idempotency_key,
                payload_digest=payload_digest,
                turn_attempt_id=turn_attempt_id,
                expected_session_revision=expected_session_revision,
                fencing_generation=fencing_generation,
                owner_class=owner_class,
                retry_policy=retry_policy,
            )

    async def _record(
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
        """Record a logical command. Idempotent on ``idempotency_key``.

        A reused key must describe the same logical command. If the immutable
        identity ``(session_id, command_type, turn_attempt_id, payload_digest)``
        differs from the stored command, fail closed rather than returning a
        receipt/status for unrelated input. A reuse that matches an existing
        command is a suppressed duplicate dispatch (telemetry counter), not a new
        journal row.
        """

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
            telemetry.record_duplicate_command_suppressed()
            return existing
        row = OmnigentCommand(
            command_id=command_id,
            session_id=session_id,
            command_type=command_type,
            idempotency_key=idempotency_key,
            payload_digest=payload_digest,
            turn_attempt_id=turn_attempt_id,
            expected_session_revision=expected_session_revision,
            fencing_generation=fencing_generation,
            owner_class=owner_class,
            retry_policy_=dict(retry_policy or {}),
        )
        try:
            async with self._session.begin_nested():
                self._session.add(row)
                await self._session.flush()
        except IntegrityError:
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
                telemetry.record_duplicate_command_suppressed()
                return existing
            raise
        await self._session.refresh(row)
        return _command_record(row)

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
        row = await self._session.get(OmnigentCommand, command_id)
        return _command_record(row) if row is not None else None

    async def get_by_idempotency_key(
        self, idempotency_key: str
    ) -> Optional[CommandRecord]:
        stmt = select(OmnigentCommand).where(
            OmnigentCommand.idempotency_key == idempotency_key
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return _command_record(row) if row is not None else None

    async def list_for_session(
        self,
        session_id: str,
        *,
        limit: Optional[int] = None,
        latest: bool = False,
    ) -> list[CommandRecord]:
        """Return this session's commands in creation order (read-only).

        Used by the operator session-timeline projection (#3708) to surface the
        active or delivery-unknown command; it never claims or mutates a command.
        ``limit``/``latest`` bound the read to the most recent window while still
        returning creation order.
        """

        order = (
            (
                OmnigentCommand.created_at.desc(),
                OmnigentCommand.command_id.desc(),
            )
            if latest
            else (OmnigentCommand.created_at, OmnigentCommand.command_id)
        )
        stmt = (
            select(OmnigentCommand)
            .where(OmnigentCommand.session_id == session_id)
            .order_by(*order)
        )
        if limit is not None:
            stmt = stmt.limit(limit)
        rows = (await self._session.execute(stmt)).scalars().all()
        if latest:
            rows = list(reversed(rows))
        return [_command_record(row) for row in rows]

    async def active_for_session(self, session_id: str) -> Optional[CommandRecord]:
        """Return the single active command, delivery-ambiguity taking precedence.

        Bounded (``LIMIT 1``): a ``delivery_unknown`` command outranks a merely
        ``claimed`` one, matching the timeline projection's precedence, without
        loading the full command journal.
        """

        precedence = case(
            (OmnigentCommand.status == COMMAND_STATE_DELIVERY_UNKNOWN, 0),
            else_=1,
        )
        stmt = (
            select(OmnigentCommand)
            .where(
                OmnigentCommand.session_id == session_id,
                OmnigentCommand.status.in_(
                    [COMMAND_STATE_DELIVERY_UNKNOWN, COMMAND_STATE_CLAIMED]
                ),
            )
            .order_by(precedence, OmnigentCommand.updated_at.desc(), OmnigentCommand.command_id)
            .limit(1)
        )
        row = (await self._session.execute(stmt)).scalars().first()
        return _command_record(row) if row is not None else None

    async def _load_command_for_update(self, command_id: str) -> OmnigentCommand:
        row = await self._session.get(
            OmnigentCommand, command_id, with_for_update=True
        )
        if row is None:
            raise KeyError(f"Unknown command {command_id!r}")
        return row

    async def claim_command(
        self,
        command_id: str,
        *,
        owner_class: str,
        claim_token: str,
    ) -> CasResult:
        """Claim exclusive execution authority for a logical command.

        ``claim_token`` is the caller's durable claimant identity (for example a
        worker/activity identity), not a per-call nonce; ``owner_class`` remains a
        low-cardinality metric label. Outcomes (#3704):

        * ``pending`` and the command still targets current session authority ->
          the caller wins ``pending -> claimed`` and receives :attr:`APPLIED`,
          recording its ``claim_token``.
        * ``pending`` but the command's recorded session-supervisor generation is
          superseded -> :attr:`FENCING_CONFLICT`; a stale command must not be
          executed after ownership changed.
        * already ``claimed`` by the *same* ``claim_token`` -> :attr:`APPLIED`
          again, so a durable owner whose worker crashed after claiming but
          before delivering can safely resume rather than stranding a side effect
          that is known not to have run.
        * already ``claimed`` by a *different* ``claim_token`` -> :attr:`NOT_OWNER`;
          a racing claimant did not win execution authority and must reconcile.
        * settled or parked (terminal / delivery-unknown) -> :attr:`ALREADY_APPLIED`.

        The claim compare-and-swaps under a real row lock so the win is atomic.
        """

        row = await self._load_command_for_update(command_id)
        if row.status == COMMAND_STATE_PENDING:
            session_row = await self._session.get(OmnigentSession, row.session_id)
            # A command records the session-supervisor generation it was created
            # under. That generation only advances when a supervisor is replaced,
            # so a command whose generation is *older* than the live session was
            # authored by a superseded supervisor and must not be executed after
            # ownership changed (#3704).
            if (
                session_row is not None
                and row.fencing_generation < session_row.fencing_generation
            ):
                telemetry.record_fencing_conflict(
                    scope=FencingScope.SESSION_SUPERVISOR
                )
                return CasResult(
                    ControlPlaneOutcome.FENCING_CONFLICT, _command_record(row)
                )
            row.status = COMMAND_STATE_CLAIMED
            row.owner_class = owner_class
            row.claim_token = claim_token
            row.revision = row.revision + 1
            await self._session.flush()
            await self._session.refresh(row)
            return CasResult(ControlPlaneOutcome.APPLIED, _command_record(row))
        if row.status == COMMAND_STATE_CLAIMED:
            if row.claim_token == claim_token:
                # Same durable claimant resuming its own outstanding claim.
                return CasResult(ControlPlaneOutcome.APPLIED, _command_record(row))
            # A different claimant races a live claim: it did not win authority.
            telemetry.record_duplicate_command_suppressed()
            return CasResult(ControlPlaneOutcome.NOT_OWNER, _command_record(row))
        # Settled (applied/failed) or parked delivery-unknown: do not re-execute.
        telemetry.record_duplicate_command_suppressed()
        return CasResult(ControlPlaneOutcome.ALREADY_APPLIED, _command_record(row))

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
        existing = await self.get(command_id)
        with spans.omnigent_span(
            spans.COMMAND_EXECUTE,
            command_class=existing.command_type if existing is not None else "unknown",
            fencing_generation_ordinal=(
                existing.fencing_generation if existing is not None else None
            ),
            delivery_unknown_outcome=(
                "created"
                if outcome is ControlPlaneOutcome.DELIVERY_UNKNOWN
                else "reconciled"
                if existing is not None and existing.delivery_ambiguous
                else "none"
            ),
        ):
            return await self._record_command_delivery(
                command_id,
                owner_class=owner_class,
                claim_token=claim_token,
                outcome=outcome,
                provider_receipt_id=provider_receipt_id,
                result_ref=result_ref,
            )

    async def _record_command_delivery(
        self,
        command_id: str,
        *,
        owner_class: str,
        claim_token: str,
        outcome: ControlPlaneOutcome,
        provider_receipt_id: Optional[str] = None,
        result_ref: Optional[str] = None,
    ) -> CasResult:
        """Record the delivery result of a claimed command's side effect.

        ``outcome`` is the caller's interpretation of the side effect:

        * :attr:`APPLIED` — the side effect is confirmed; the command settles.
        * :attr:`DELIVERY_UNKNOWN` — the provider side effect may already have
          occurred; the command is parked as delivery-ambiguous so the reconciler
          reconciles instead of blindly reissuing it.
        * :attr:`REVISION_CONFLICT` / :attr:`FENCING_CONFLICT` — the side effect
          did not land against current authority; the command fails and is
          retried against fresh state.

        Only the winning claimant may record delivery: both the ``owner_class``
        and the per-claim ``claim_token`` must match, so a racing loser that
        shares an ``owner_class`` cannot settle the command
        (:class:`NotCommandOwnerError`). A command parked as delivery-unknown may
        still be reconciled by its owner. Recording a delivery that *matches* an
        already-settled terminal is idempotent (:attr:`ALREADY_APPLIED`); a
        delivery that *contradicts* the settled terminal (for example ``APPLIED``
        reported after the command already failed) is refused as an
        immutable-authority conflict and never reported as success (#3704).
        """

        row = await self._load_command_for_update(command_id)
        if row.status in COMMAND_TERMINAL_STATES:
            settled_applied = row.status == COMMAND_STATE_APPLIED
            confirms_apply = outcome in (
                ControlPlaneOutcome.APPLIED,
                ControlPlaneOutcome.DELIVERY_UNKNOWN,
            )
            # Idempotent replay only when the new outcome agrees with the settled
            # terminal; a contradictory terminal delivery is a hard conflict.
            if settled_applied == confirms_apply:
                return CasResult(
                    ControlPlaneOutcome.ALREADY_APPLIED, _command_record(row)
                )
            return CasResult(
                ControlPlaneOutcome.IMMUTABLE_AUTHORITY_CONFLICT,
                _command_record(row),
            )
        if (
            row.status not in (COMMAND_STATE_CLAIMED, COMMAND_STATE_DELIVERY_UNKNOWN)
            or row.owner_class != owner_class
            or row.claim_token != claim_token
        ):
            raise NotCommandOwnerError(
                f"Command {command_id!r} is not claimed by this claimant "
                f"(status={row.status!r}, owner={row.owner_class!r})"
            )
        was_delivery_unknown = row.status == COMMAND_STATE_DELIVERY_UNKNOWN
        if provider_receipt_id is not None:
            row.provider_receipt_id = provider_receipt_id
        if result_ref is not None:
            row.result_ref = result_ref
        if outcome is ControlPlaneOutcome.APPLIED:
            row.status = COMMAND_STATE_APPLIED
            row.delivery_ambiguous = False
            if was_delivery_unknown:
                # A previously parked delivery-ambiguous command is now confirmed
                # at the authoritative delivery boundary: this is the reconciled
                # event, counted separately from its creation (#3704).
                telemetry.record_delivery_unknown_reconciled()
            result_outcome = ControlPlaneOutcome.APPLIED
        elif outcome is ControlPlaneOutcome.DELIVERY_UNKNOWN:
            row.status = COMMAND_STATE_DELIVERY_UNKNOWN
            row.delivery_ambiguous = True
            if not was_delivery_unknown:
                telemetry.record_delivery_unknown_created()
            result_outcome = ControlPlaneOutcome.DELIVERY_UNKNOWN
        else:
            # REVISION_CONFLICT / FENCING_CONFLICT (or any non-delivery outcome):
            # the side effect did not land against current authority. Settle the
            # command failed and route the conflict through bounded telemetry so
            # the command-execution surface is observable (#3704).
            row.status = COMMAND_STATE_FAILED
            telemetry.record_outcome(outcome, scope=FencingScope.SESSION_SUPERVISOR)
            result_outcome = outcome
        row.revision = row.revision + 1
        await self._session.flush()
        await self._session.refresh(row)
        return CasResult(result_outcome, _command_record(row))

    async def record_command_failure(
        self,
        command_id: str,
        *,
        owner_class: str,
        claim_token: str,
        result_ref: str,
    ) -> CasResult:
        """Park an exhausted command with durable, reference-only evidence.

        A delivery-unknown command remains delivery-unknown so terminalization
        cannot manufacture proof that the provider side effect did not occur.
        Pending commands may be failed by the session supervisor because they
        never crossed the claim boundary; claimed commands still require the
        exact deterministic claimant token used by the bounded Activity.
        """

        row = await self._load_command_for_update(command_id)
        if row.status == COMMAND_STATE_APPLIED:
            return CasResult(
                ControlPlaneOutcome.ALREADY_APPLIED, _command_record(row)
            )
        if row.status == COMMAND_STATE_FAILED:
            if row.result_ref not in {None, result_ref}:
                return CasResult(
                    ControlPlaneOutcome.IMMUTABLE_AUTHORITY_CONFLICT,
                    _command_record(row),
                )
            if row.result_ref is None:
                row.result_ref = result_ref
                row.revision = row.revision + 1
                await self._session.flush()
                await self._session.refresh(row)
            return CasResult(
                ControlPlaneOutcome.ALREADY_APPLIED, _command_record(row)
            )
        if row.status == COMMAND_STATE_DELIVERY_UNKNOWN:
            if row.result_ref is None:
                row.result_ref = result_ref
                row.revision = row.revision + 1
                await self._session.flush()
                await self._session.refresh(row)
            return CasResult(
                ControlPlaneOutcome.DELIVERY_UNKNOWN, _command_record(row)
            )
        if row.status == COMMAND_STATE_CLAIMED and (
            row.owner_class != owner_class or row.claim_token != claim_token
        ):
            raise NotCommandOwnerError(
                f"Command {command_id!r} is not claimed by this claimant"
            )
        if row.status == COMMAND_STATE_PENDING:
            session_row = await self._session.get(OmnigentSession, row.session_id)
            if (
                session_row is not None
                and row.fencing_generation != session_row.fencing_generation
            ):
                return CasResult(
                    ControlPlaneOutcome.FENCING_CONFLICT, _command_record(row)
                )
        row.status = COMMAND_STATE_FAILED
        row.delivery_ambiguous = False
        row.result_ref = result_ref
        row.revision = row.revision + 1
        await self._session.flush()
        await self._session.refresh(row)
        return CasResult(ControlPlaneOutcome.APPLIED, _command_record(row))


# --- DecisionRepository ------------------------------------------------------


class DecisionRepository(_RepositoryBase):
    """Append-only reconciliation decision repository."""

    async def get(self, decision_id: str) -> Optional[DecisionRecord]:
        row = await self._session.get(OmnigentReconciliationDecision, decision_id)
        return _decision_record(row) if row is not None else None

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
        reason_class = (
            "ambiguous"
            if "quarantine" in decision_code or "ambiguous" in str(reason_code or "")
            else "cleanup"
            if "cleanup" in decision_code or "release" in decision_code
            else "terminal"
            if "terminal" in decision_code
            else "failed"
            if "fail" in decision_code
            else "compatibility"
            if "compatibility" in decision_code
            else "provisioning"
            if decision_code.startswith("ensure_")
            else "awaiting"
        )
        metric_decision_class = (
            decision_code
            if decision_code
            in metrics.BOUNDED_LABEL_VALUES["decision_class"]
            else "await_observation"
        )
        with spans.omnigent_span(
            spans.SESSION_RECONCILE,
            decision_class=decision_code,
            reason_code=reason_code,
            expected_revision=expected_revision,
            fencing_generation_ordinal=fencing_generation,
        ):
            record = await self._append(
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
            )
        try:
            metrics.increment(
                metrics.RECONCILIATION_DECISIONS,
                decision_class=metric_decision_class,
                reason_class=reason_class,
            )
            session = await self._session.get(OmnigentSession, session_id)
            if session is not None and session.updated_at is not None:
                updated = (
                    session.updated_at.replace(tzinfo=UTC)
                    if session.updated_at.tzinfo is None
                    else session.updated_at
                )
                metrics.observe(
                    metrics.RECONCILIATION_CONVERGENCE_LATENCY,
                    max(0.0, (datetime.now(UTC) - updated).total_seconds()),
                )
            if decision_code == "synthesize_terminal_from_snapshot":
                metrics.increment(metrics.SNAPSHOT_RECOVERED_TERMINAL)
        except Exception:
            # Metrics are auxiliary; a recorder/exporter outage must not change
            # the durable reconciliation decision.
            pass
        return record

    async def _append(
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
        existing = await self.get(decision_id)
        if existing is not None:
            incoming_identity = (
                session_id,
                decision_code,
                expected_revision,
                fencing_generation,
                reason_code,
            )
            stored_identity = (
                existing.session_id,
                existing.decision_code,
                existing.expected_revision,
                existing.fencing_generation,
                existing.reason_code,
            )
            if incoming_identity != stored_identity:
                raise ConflictingSessionAuthorityError(
                    f"Decision {decision_id!r} already records different "
                    "reconciliation authority"
                )
            return existing
        row = OmnigentReconciliationDecision(
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
        )
        self._session.add(row)
        await self._session.flush()
        await self._session.refresh(row)
        return _decision_record(row)

    async def recent_for_session(
        self, session_id: str, *, limit: int = 10
    ) -> list[DecisionRecord]:
        """Return newest decisions first with a strict operational bound."""

        bounded_limit = max(1, min(int(limit), 100))
        stmt = (
            select(OmnigentReconciliationDecision)
            .where(OmnigentReconciliationDecision.session_id == session_id)
            .order_by(
                OmnigentReconciliationDecision.created_at.desc(),
                OmnigentReconciliationDecision.decision_id.desc(),
            )
            .limit(bounded_limit)
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [_decision_record(row) for row in rows]

    async def list_for_session(
        self,
        session_id: str,
        *,
        limit: Optional[int] = None,
        latest: bool = False,
    ) -> list[DecisionRecord]:
        order = (
            (
                OmnigentReconciliationDecision.created_at.desc(),
                OmnigentReconciliationDecision.decision_id.desc(),
            )
            if latest
            else (
                OmnigentReconciliationDecision.created_at,
                OmnigentReconciliationDecision.decision_id,
            )
        )
        stmt = (
            select(OmnigentReconciliationDecision)
            .where(OmnigentReconciliationDecision.session_id == session_id)
            .order_by(*order)
        )
        if limit is not None:
            stmt = stmt.limit(limit)
        rows = (await self._session.execute(stmt)).scalars().all()
        if latest:
            rows = list(reversed(rows))
        return [_decision_record(r) for r in rows]

    async def latest_for_session(self, session_id: str) -> Optional[DecisionRecord]:
        """Return the most recent reconciliation decision (bounded ``LIMIT 1``)."""

        stmt = (
            select(OmnigentReconciliationDecision)
            .where(OmnigentReconciliationDecision.session_id == session_id)
            .order_by(
                OmnigentReconciliationDecision.created_at.desc(),
                OmnigentReconciliationDecision.decision_id.desc(),
            )
            .limit(1)
        )
        row = (await self._session.execute(stmt)).scalars().first()
        return _decision_record(row) if row is not None else None

    async def for_resulting_command(
        self, command_id: str
    ) -> Optional[DecisionRecord]:
        """Return the unique decision that authorized a durable command."""

        stmt = (
            select(OmnigentReconciliationDecision)
            .where(
                OmnigentReconciliationDecision.resulting_command_id == command_id
            )
            .limit(1)
        )
        row = (await self._session.execute(stmt)).scalars().first()
        return _decision_record(row) if row is not None else None

    async def count_for_session_reason(self, session_id: str, reason_code: str) -> int:
        """Count durable decisions recorded for a session under one reason code.

        This is the per-session/per-reason detection count the stuck-state
        response policy uses to escalate persistent ambiguity to quarantine; the
        durable decision journal *is* the persistence, so no separate counter is
        introduced.
        """

        stmt = (
            select(func.count())
            .select_from(OmnigentReconciliationDecision)
            .where(
                OmnigentReconciliationDecision.session_id == session_id,
                OmnigentReconciliationDecision.reason_code == reason_code,
            )
        )
        return int((await self._session.execute(stmt)).scalar_one())


# --- ChatBindingAliasRepository ----------------------------------------------


class ChatBindingAliasRepository(_RepositoryBase):
    """Resolves previously issued chat-binding handles to canonical sessions.

    Aliases never expose provider session IDs; they resolve only to the
    canonical ``session_id`` (or fail closed as a stable diagnostic).
    """

    async def register(
        self,
        *,
        chat_binding_id: str,
        session_id: Optional[str],
        alias_state: str = ALIAS_STATE_ACTIVE,
        diagnostic_reason: Optional[str] = None,
    ) -> ChatBindingAliasRecord:
        row = await self._session.get(OmnigentChatBindingAlias, chat_binding_id)
        if row is None:
            row = OmnigentChatBindingAlias(
                chat_binding_id=chat_binding_id,
                session_id=session_id,
                alias_state=alias_state,
                diagnostic_reason=diagnostic_reason,
            )
            self._session.add(row)
        elif row.session_id == session_id and row.alias_state == alias_state:
            # Identical registration is preserved (idempotent); only refresh a
            # newly supplied diagnostic reason.
            if diagnostic_reason is not None:
                row.diagnostic_reason = diagnostic_reason
        elif alias_state == ALIAS_STATE_QUARANTINED:
            # An explicit quarantine transition always fails closed onto the
            # existing handle regardless of prior binding.
            row.session_id = session_id
            row.alias_state = alias_state
            row.diagnostic_reason = diagnostic_reason
        elif (
            row.alias_state == ALIAS_STATE_ACTIVE
            and row.session_id is not None
            and row.session_id != session_id
        ):
            # A browser-safe handle that already actively resolves to one
            # canonical session must never be silently repointed at another.
            raise ConflictingSessionAuthorityError(
                f"Chat binding {chat_binding_id!r} already actively bound to "
                f"canonical session {row.session_id!r}; refusing to reassign to "
                f"{session_id!r}"
            )
        else:
            row.session_id = session_id
            row.alias_state = alias_state
            row.diagnostic_reason = diagnostic_reason
        await self._session.flush()
        await self._session.refresh(row)
        return _alias_record(row)

    async def quarantine(
        self, chat_binding_id: str, *, diagnostic_reason: str
    ) -> ChatBindingAliasRecord:
        return await self.register(
            chat_binding_id=chat_binding_id,
            session_id=None,
            alias_state=ALIAS_STATE_QUARANTINED,
            diagnostic_reason=diagnostic_reason,
        )

    async def resolve(self, chat_binding_id: str) -> Optional[ChatBindingAliasRecord]:
        """Resolve a handle to its alias record.

        Callers that get a record with ``resolves`` True may look up the
        canonical session by ``session_id``; otherwise the record is a
        fail-closed diagnostic and no provider identity is exposed.
        """

        row = await self._session.get(OmnigentChatBindingAlias, chat_binding_id)
        return _alias_record(row) if row is not None else None


# --- CleanupAuthorityRepository ----------------------------------------------


class CleanupAuthorityRepository(_RepositoryBase):
    """Durable cleanup / janitor authority repository.

    Source: MoonLadderStudios/MoonMind#3704. Cleanup is fenced against the host,
    Provider Profile lease, and provider-session generations it was claimed
    against so a former janitor cannot stop or release resources that now belong
    to a replacement generation. Exactly one owner may hold ``claimed``.
    """

    async def get(self, session_id: str) -> Optional[CleanupAuthorityRecord]:
        row = await self._session.get(OmnigentCleanupAuthority, session_id)
        return _cleanup_record(row) if row is not None else None

    async def _load_for_update(self, session_id: str) -> OmnigentCleanupAuthority:
        """Load (or lazily create) the cleanup-authority row under a row lock."""

        row = await self._session.get(
            OmnigentCleanupAuthority, session_id, with_for_update=True
        )
        if row is not None:
            return row
        row = OmnigentCleanupAuthority(session_id=session_id)
        try:
            # Add inside the savepoint so the INSERT is only emitted after the
            # SAVEPOINT exists: a losing concurrent lazy-create rolls back to the
            # savepoint instead of aborting the outer transaction (#3704).
            async with self._session.begin_nested():
                self._session.add(row)
                await self._session.flush()
        except IntegrityError:
            # Concurrent lazy-create: reload the winner under the row lock.
            row = await self._session.get(
                OmnigentCleanupAuthority, session_id, with_for_update=True
            )
            assert row is not None
            return row
        return row

    async def claim_cleanup(
        self,
        session_id: str,
        *,
        owner_class: str,
        claim_token: str,
    ) -> CasResult:
        """Claim exclusive cleanup authority for a session.

        ``claim_token`` is the winning janitor's durable identity; ``owner_class``
        stays a low-cardinality metric label. The claim locks the session row in
        the same transaction and derives the host / Provider Profile lease
        generations and provider-session epoch it is fenced against directly from
        that live authority, so a claim can never silently record ``None`` fences
        while authority-bearing leases exist and thereby skip fencing at
        completion (#3704). Outcomes:

        * ``unclaimed`` -> the janitor wins and receives :attr:`APPLIED`.
        * ``claimed`` by the same ``claim_token`` -> :attr:`APPLIED` (idempotent
          resume of the janitor's own outstanding claim).
        * ``claimed`` by a different janitor whose recorded fences match current
          authority (a live claim) -> :attr:`NOT_OWNER` (a cleanup-claim conflict
          counter is emitted); exactly one owner may hold a live claim.
        * ``claimed`` by a different janitor whose recorded fences are *stale*
          (superseded by a newer host/profile/provider generation) -> a fenced
          takeover that advances the generation and re-owns the claim, so a
          cleanup fenced out at completion cannot strand the resource forever.
        * ``complete`` -> idempotent (:attr:`ALREADY_APPLIED`).
        """

        row = await self._load_for_update(session_id)
        if row.state == CLEANUP_STATE_COMPLETE:
            return CasResult(ControlPlaneOutcome.ALREADY_APPLIED, _cleanup_record(row))
        session_row = await self._session.get(
            OmnigentSession, session_id, with_for_update=True
        )
        cur_host = session_row.host_lease_generation if session_row else None
        cur_profile = (
            session_row.provider_profile_generation if session_row else None
        )
        cur_epoch = session_row.provider_session_ref if session_row else None
        if row.state == CLEANUP_STATE_CLAIMED:
            if row.claim_token == claim_token:
                return CasResult(ControlPlaneOutcome.APPLIED, _cleanup_record(row))
            stale = (
                (
                    row.fenced_host_generation is not None
                    and row.fenced_host_generation != cur_host
                )
                or (
                    row.fenced_profile_generation is not None
                    and row.fenced_profile_generation != cur_profile
                )
                or (
                    row.fenced_provider_epoch is not None
                    and row.fenced_provider_epoch != cur_epoch
                )
            )
            if not stale:
                telemetry.record_cleanup_claim_conflict()
                return CasResult(
                    ControlPlaneOutcome.NOT_OWNER, _cleanup_record(row)
                )
            # else: the prior claim was fenced out by a newer generation; fall
            # through to re-own it as a fenced takeover (advances generation).
        row.state = CLEANUP_STATE_CLAIMED
        row.owner_class = owner_class
        row.claim_token = claim_token
        row.fenced_host_generation = cur_host
        row.fenced_profile_generation = cur_profile
        row.fenced_provider_epoch = cur_epoch
        row.generation = row.generation + 1
        row.revision = row.revision + 1
        await self._session.flush()
        await self._session.refresh(row)
        return CasResult(ControlPlaneOutcome.APPLIED, _cleanup_record(row))

    async def fence_for_turn(
        self,
        session_id: str,
        *,
        owner_class: str,
    ) -> CasResult:
        """Fence incompatible cleanup before a turn mutates the provider.

        Source: MoonLadderStudios/MoonMind#3707 §4. Continuation, remediation,
        and chat admission all race host cleanup, credential-materializer
        cleanup, Provider Profile release, and janitor recovery. The race is
        resolved deterministically in one direction: an *admitted* turn always
        advances the cleanup generation, so an outstanding janitor claim is
        fenced out at completion (it can no longer delete the replacement
        generation) while a janitor that has not yet claimed simply claims the
        newer generation after the turn.

        Completed cleanup is a distinct terminal meaning and is never reopened:
        the turn is refused with :attr:`IMMUTABLE_AUTHORITY_CONFLICT` so the
        caller cold-restores or branches instead of consuming released
        credentials.
        """

        row = await self._load_for_update(session_id)
        if row.state == CLEANUP_STATE_COMPLETE:
            return CasResult(
                ControlPlaneOutcome.IMMUTABLE_AUTHORITY_CONFLICT,
                _cleanup_record(row),
            )
        row.state = CLEANUP_STATE_UNCLAIMED
        row.owner_class = owner_class
        row.claim_token = None
        row.fenced_host_generation = None
        row.fenced_profile_generation = None
        row.fenced_provider_epoch = None
        row.generation = row.generation + 1
        row.revision = row.revision + 1
        await self._session.flush()
        await self._session.refresh(row)
        return CasResult(ControlPlaneOutcome.APPLIED, _cleanup_record(row))

    async def record_janitor_handoff(
        self,
        session_id: str,
        *,
        owner_class: str,
    ) -> CleanupAuthorityRecord:
        """Make exhausted cleanup discoverable without claiming janitor fences.

        The evidence reference lives on the canonical session metadata. This
        aggregate records the durable owner intent while remaining unclaimed,
        so a real janitor can subsequently win the normal fenced claim.
        """

        row = await self._load_for_update(session_id)
        if row.state != CLEANUP_STATE_UNCLAIMED:
            return _cleanup_record(row)
        if row.owner_class == owner_class and row.claim_token is None:
            return _cleanup_record(row)
        row.owner_class = owner_class
        row.claim_token = None
        row.revision = row.revision + 1
        await self._session.flush()
        await self._session.refresh(row)
        return _cleanup_record(row)

    async def complete_cleanup(
        self,
        session_id: str,
        *,
        generation: int,
        owner_class: str,
        claim_token: str,
        session_repository: "SessionRepository",
    ) -> CasResult:
        """Complete a claimed cleanup, re-validating lease fencing.

        Only the exact winning claimant may complete cleanup: the claimed
        ``generation``, ``owner_class``, and per-claim ``claim_token`` must all
        match, so a racing janitor that shares an ``owner_class`` (and therefore
        received the winner's record on its losing claim) cannot complete a
        cleanup it never won (:attr:`NOT_OWNER`). The session is loaded *for
        update* in the same transaction so its authority cannot advance between
        this check and completion (consistent lock order: cleanup row, then
        session). Before settling, the recorded host / Provider Profile lease
        generations and provider-session epoch are re-checked against the locked
        session: if any was superseded since the claim, completion is fenced
        (:attr:`FENCING_CONFLICT`) so a former owner cannot release a resource
        that now belongs to a newer generation (#3704).
        """

        row = await self._load_for_update(session_id)
        if row.state == CLEANUP_STATE_COMPLETE and row.generation == generation:
            return CasResult(ControlPlaneOutcome.ALREADY_APPLIED, _cleanup_record(row))
        if (
            row.state != CLEANUP_STATE_CLAIMED
            or row.generation != generation
            or row.owner_class != owner_class
            or row.claim_token != claim_token
        ):
            telemetry.record_cleanup_claim_conflict()
            return CasResult(ControlPlaneOutcome.NOT_OWNER, _cleanup_record(row))
        session = await session_repository.load_for_update(session_id)
        if session is not None:
            if (
                row.fenced_profile_generation is not None
                and session.provider_profile_generation != row.fenced_profile_generation
            ):
                telemetry.record_fencing_conflict(
                    scope=FencingScope.PROVIDER_PROFILE_LEASE
                )
                return CasResult(
                    ControlPlaneOutcome.FENCING_CONFLICT, _cleanup_record(row)
                )
            if (
                row.fenced_host_generation is not None
                and session.host_lease_generation != row.fenced_host_generation
            ):
                telemetry.record_fencing_conflict(scope=FencingScope.HOST_LEASE)
                return CasResult(
                    ControlPlaneOutcome.FENCING_CONFLICT, _cleanup_record(row)
                )
            if (
                row.fenced_provider_epoch is not None
                and session.provider_session_ref != row.fenced_provider_epoch
            ):
                # The provider session captured at claim time was replaced; a new
                # provider epoch owns cleanup and the former janitor is fenced.
                telemetry.record_fencing_conflict(
                    scope=FencingScope.SESSION_SUPERVISOR
                )
                return CasResult(
                    ControlPlaneOutcome.FENCING_CONFLICT, _cleanup_record(row)
                )
        row.state = CLEANUP_STATE_COMPLETE
        row.revision = row.revision + 1
        await self._session.flush()
        await self._session.refresh(row)
        return CasResult(ControlPlaneOutcome.APPLIED, _cleanup_record(row))


# --- Unit of work ------------------------------------------------------------


@dataclass
class ControlPlaneRepositories:
    """Bundle of control-plane repositories bound to one transaction."""

    sessions: SessionRepository
    turn_attempts: TurnAttemptRepository
    observations: ObservationRepository
    commands: CommandRepository
    decisions: DecisionRepository
    chat_binding_aliases: ChatBindingAliasRepository
    cleanup: CleanupAuthorityRepository

    @classmethod
    def bind(cls, session: AsyncSession) -> "ControlPlaneRepositories":
        return cls(
            sessions=SessionRepository(session),
            turn_attempts=TurnAttemptRepository(session),
            observations=ObservationRepository(session),
            commands=CommandRepository(session),
            decisions=DecisionRepository(session),
            chat_binding_aliases=ChatBindingAliasRepository(session),
            cleanup=CleanupAuthorityRepository(session),
        )


class OmnigentControlPlaneStore:
    """Session-factory-bound entry point for the control-plane repositories.

    Provides an atomic :meth:`transaction` unit of work and a convenience
    :meth:`establish_session` that creates a canonical session, allocates its
    single chat binding, and establishes the first turn attempt in one
    transaction.
    """

    def __init__(self, session_factory: Callable[[], Any]) -> None:
        self._session_factory = session_factory

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[ControlPlaneRepositories]:
        async with self._session_factory() as session:
            repos = ControlPlaneRepositories.bind(session)
            try:
                yield repos
            except Exception:
                await session.rollback()
                raise
            else:
                await session.commit()

    async def establish_session(
        self,
        *,
        session_id: str,
        moonmind_workflow_id: str,
        provider: str,
        chat_binding_id: str,
        first_turn_attempt_id: str,
        first_turn_idempotency_key: str,
        provider_session_ref: Optional[str] = None,
        moonmind_run_id: Optional[str] = None,
        step_execution_id: Optional[str] = None,
        moonmind_agent_run_id: Optional[str] = None,
        compatibility_profile: Optional[str] = None,
        intent_ref: Optional[str] = None,
        intent_digest: Optional[str] = None,
        execution_plan_ref: Optional[str] = None,
        instruction_digest: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> tuple[SessionRecord, TurnAttemptRecord]:
        async with self.transaction() as repos:
            await repos.sessions.create(
                session_id=session_id,
                moonmind_workflow_id=moonmind_workflow_id,
                provider=provider,
                provider_session_ref=provider_session_ref,
                chat_binding_id=chat_binding_id,
                moonmind_run_id=moonmind_run_id,
                step_execution_id=step_execution_id,
                moonmind_agent_run_id=moonmind_agent_run_id,
                compatibility_profile=compatibility_profile,
                intent_ref=intent_ref,
                intent_digest=intent_digest,
                execution_plan_ref=execution_plan_ref,
                metadata=metadata,
            )
            await repos.chat_binding_aliases.register(
                chat_binding_id=chat_binding_id, session_id=session_id
            )
            turn = await repos.turn_attempts.create(
                turn_attempt_id=first_turn_attempt_id,
                session_id=session_id,
                idempotency_key=first_turn_idempotency_key,
                lineage_kind=TurnSource.INITIAL,
                step_execution_id=step_execution_id,
                instruction_digest=instruction_digest,
            )
            # The freshly created session is at revision 1, fencing generation 0;
            # the establishing owner declares that authority to bind the first
            # active turn attempt (#3704 lifecycle writes are always fenced).
            await repos.sessions.update_lifecycle(
                session_id,
                expected_revision=1,
                expected_fencing_generation=0,
                active_turn_attempt_id=first_turn_attempt_id,
            )
            refreshed = await repos.sessions.get(session_id)
        assert refreshed is not None
        return refreshed, turn
