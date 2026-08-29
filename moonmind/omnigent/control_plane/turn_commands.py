"""Canonical turn-command boundary for every follow-up instruction source.

MoonLadderStudios/MoonMind#3701 requires Workflow Chat, steering, approval,
continuation, remediation and checkpoint operations to share the same durable
session/turn/command authority.  This application service is deliberately
transport-neutral: HTTP and Temporal adapters supply an already-authorized
session locator and receive a stable claim which must be settled after the
side-effect boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .identities import (
    canonical_followup_turn_attempt_id,
    canonical_omnigent_session_id,
    canonical_omnigent_turn_attempt_id,
    canonical_turn_claim_token,
    canonical_turn_command_id,
    canonical_turn_command_key,
)
from .records import (
    APPLIED_OUTCOMES,
    ConflictingSessionAuthorityError,
    ControlPlaneOutcome,
    TURN_STATE_ACCEPTED,
    TURN_STATE_DELIVERY_UNKNOWN,
)
from .turn_sources import (
    TURN_SOURCE_KINDS,
    TURN_SOURCE_SCHEMA,
    TURN_SOURCE_VERSION,
    normalize_turn_source,
    turn_source_for_command_type,
    validate_turn_source,
)


class CanonicalTurnAuthorityUnavailable(RuntimeError):
    """The supplied transport binding cannot resolve one canonical session."""


@dataclass(frozen=True, slots=True)
class CanonicalTurnCommandClaim:
    session_id: str
    turn_attempt_id: str
    command_id: str
    idempotency_key: str
    claim_token: str
    outcome: ControlPlaneOutcome
    expected_session_revision: int
    fencing_generation: int

    @property
    def owns_delivery(self) -> bool:
        return self.outcome is ControlPlaneOutcome.APPLIED


@dataclass(frozen=True, slots=True)
class CanonicalSessionBootstrap:
    """Verified legacy/source authority sufficient for on-demand convergence."""

    provider: str
    step_execution_id: str
    agent_run_id: str
    source_idempotency_key: str
    execution_plan_ref: str | None = None


def _lineage_kind(command_type: str, *, explicit_source: str | None = None) -> str:
    """Map a command or explicit source to the closed turn-source vocabulary.

    When an explicit source is supplied it is validated strictly against the
    versioned vocabulary (#3707).  Otherwise the migration heuristic derives a
    closed kind from the free-form command type so that historical callers
    cannot invent a new authority outside the vocabulary.  The function never
    returns an open-ended string — every branch resolves to one of
    :data:`TURN_SOURCE_KINDS`.
    """

    if explicit_source is not None:
        candidate = str(explicit_source).strip()
        if candidate:
            return validate_turn_source(candidate)
    # Fallback heuristic for callers that have not yet migrated to an explicit
    # source.  The derived value is always one of the closed kinds and is
    # versioned by :data:`TURN_SOURCE_SCHEMA`.
    derived = turn_source_for_command_type(command_type)
    # Normalize aliases (e.g. legacy "continuation" / "approval") to their
    # canonical closed forms before persisting.
    return normalize_turn_source(derived) if derived else "repository_continuation"


class CanonicalTurnCommandService:
    """Record, fence, claim and settle one instruction-bearing side effect."""

    OWNER_CLASS = "canonical_turn_command"

    def __init__(self, store: Any) -> None:
        # Persistence is an injected capability; this application service does
        # not import or construct a SQL-backed repository implementation.
        self._store = store

    async def claim(
        self,
        *,
        workflow_id: str,
        provider_session_ref: str,
        chat_binding_id: str | None,
        command_type: str,
        idempotency_key: str,
        payload_digest: str,
        step_execution_id: str | None = None,
        bootstrap: CanonicalSessionBootstrap | None = None,
        turn_source: str | None = None,
    ) -> CanonicalTurnCommandClaim:
        """Claim current fenced authority before an external side effect."""

        async with self._store.transaction() as repos:
            return await self.claim_with_repositories(
                repos,
                workflow_id=workflow_id,
                provider_session_ref=provider_session_ref,
                chat_binding_id=chat_binding_id,
                command_type=command_type,
                idempotency_key=idempotency_key,
                payload_digest=payload_digest,
                step_execution_id=step_execution_id,
                bootstrap=bootstrap,
                turn_source=turn_source,
            )

    async def claim_with_repositories(
        self,
        repos: Any,
        *,
        workflow_id: str,
        provider_session_ref: str,
        chat_binding_id: str | None,
        command_type: str,
        idempotency_key: str,
        payload_digest: str,
        step_execution_id: str | None = None,
        bootstrap: CanonicalSessionBootstrap | None = None,
        turn_source: str | None = None,
    ) -> CanonicalTurnCommandClaim:
        """Claim within an existing application transaction."""

        canonical_key = canonical_turn_command_key(workflow_id, idempotency_key)
        claim_token = canonical_turn_claim_token(canonical_key)
        session = None
        bootstrap_turn = None
        if chat_binding_id:
            alias = await repos.chat_binding_aliases.resolve(chat_binding_id)
            if alias is not None and alias.resolves:
                session = await repos.sessions.get(str(alias.session_id))
            if session is None:
                session = await repos.sessions.get_by_chat_binding(chat_binding_id)
        if session is None and provider_session_ref:
            session = await repos.sessions.get_by_scope(
                workflow_id, provider_session_ref
            )
        if session is None and bootstrap is not None:
            session_id = canonical_omnigent_session_id(
                workflow_id=workflow_id,
                step_execution_id=bootstrap.step_execution_id,
                agent_run_id=bootstrap.agent_run_id,
            )
            existing_session = await repos.sessions.get(session_id)
            if existing_session is not None:
                if (
                    existing_session.moonmind_workflow_id != workflow_id
                    or existing_session.step_execution_id != bootstrap.step_execution_id
                    or existing_session.moonmind_agent_run_id != bootstrap.agent_run_id
                    or existing_session.execution_plan_ref
                    != bootstrap.execution_plan_ref
                ):
                    raise CanonicalTurnAuthorityUnavailable(
                        "deterministic session identity conflicts with its "
                        "bootstrap authority"
                    )
                session = existing_session
            else:
                first_turn_id = canonical_omnigent_turn_attempt_id(session_id)
                try:
                    await repos.sessions.create(
                        session_id=session_id,
                        moonmind_workflow_id=workflow_id,
                        provider=bootstrap.provider,
                        provider_session_ref=provider_session_ref or None,
                        chat_binding_id=chat_binding_id,
                        step_execution_id=bootstrap.step_execution_id,
                        moonmind_agent_run_id=bootstrap.agent_run_id,
                        execution_plan_ref=bootstrap.execution_plan_ref,
                        metadata={"canonicalizedOnDemand": True},
                    )
                except ConflictingSessionAuthorityError:
                    # A concurrent identical bootstrap may win the deterministic
                    # primary key while this transaction waits on PostgreSQL's
                    # unique index. The savepoint leaves this transaction usable;
                    # converge on the winner and verify it below.
                    session = await repos.sessions.get(session_id)
                    if session is None:
                        raise
                    if (
                        session.moonmind_workflow_id != workflow_id
                        or session.step_execution_id != bootstrap.step_execution_id
                        or session.moonmind_agent_run_id != bootstrap.agent_run_id
                        or session.execution_plan_ref != bootstrap.execution_plan_ref
                    ):
                        raise CanonicalTurnAuthorityUnavailable(
                            "concurrent deterministic session bootstrap has "
                            "different authority"
                        )
                else:
                    if chat_binding_id:
                        await repos.chat_binding_aliases.register(
                            chat_binding_id=chat_binding_id,
                            session_id=session_id,
                        )
                    bootstrap_turn = await repos.turn_attempts.create(
                        turn_attempt_id=first_turn_id,
                        session_id=session_id,
                        idempotency_key=(
                            "omnigent-bootstrap:"
                            + canonical_turn_command_key(
                                workflow_id, bootstrap.source_idempotency_key
                            )
                        ),
                        lineage_kind="initial",
                        step_execution_id=bootstrap.step_execution_id,
                        instruction_digest=(
                            payload_digest
                            if bootstrap.source_idempotency_key == idempotency_key
                            else None
                        ),
                    )
                    session = await repos.sessions.update_lifecycle(
                        session_id,
                        expected_revision=1,
                        expected_fencing_generation=0,
                        active_turn_attempt_id=first_turn_id,
                    )
        if session is None:
            raise CanonicalTurnAuthorityUnavailable(
                "Instruction authority does not resolve a canonical OmnigentSession"
            )
        if session.moonmind_workflow_id != workflow_id:
            raise CanonicalTurnAuthorityUnavailable(
                "Instruction binding conflicts with canonical workflow authority"
            )
        if chat_binding_id:
            await repos.chat_binding_aliases.register(
                chat_binding_id=chat_binding_id,
                session_id=session.session_id,
            )

        existing_command = await repos.commands.get_by_idempotency_key(canonical_key)
        if existing_command is None:
            if (
                bootstrap_turn is not None
                and bootstrap is not None
                and bootstrap.source_idempotency_key == idempotency_key
            ):
                # The admitted initial instruction owns the bootstrap attempt;
                # do not manufacture a second "continuation" for the same
                # provider-facing command.
                turn = bootstrap_turn
            else:
                turn_id = canonical_followup_turn_attempt_id(
                    session.session_id, canonical_key
                )
                turn = await repos.turn_attempts.create(
                    turn_attempt_id=turn_id,
                    session_id=session.session_id,
                    idempotency_key=f"omnigent-turn:{idempotency_key}",
                    lineage_kind=_lineage_kind(
                        command_type, explicit_source=turn_source
                    ),
                    step_execution_id=step_execution_id,
                    parent_turn_attempt_id=session.active_turn_attempt_id,
                    instruction_digest=payload_digest,
                )
                session = await repos.sessions.update_lifecycle(
                    session.session_id,
                    expected_revision=session.revision,
                    expected_fencing_generation=session.fencing_generation,
                    active_turn_attempt_id=turn.turn_attempt_id,
                )
            command_id = canonical_turn_command_id(
                session.session_id, canonical_key, command_type
            )
            command = await repos.commands.record(
                command_id=command_id,
                session_id=session.session_id,
                command_type=command_type,
                idempotency_key=canonical_key,
                payload_digest=payload_digest,
                turn_attempt_id=turn.turn_attempt_id,
                expected_session_revision=session.revision,
                fencing_generation=session.fencing_generation,
                owner_class=self.OWNER_CLASS,
            )
        else:
            command = await repos.commands.record(
                command_id=existing_command.command_id,
                session_id=session.session_id,
                command_type=command_type,
                idempotency_key=canonical_key,
                payload_digest=payload_digest,
                turn_attempt_id=existing_command.turn_attempt_id,
                expected_session_revision=existing_command.expected_session_revision,
                fencing_generation=existing_command.fencing_generation,
                owner_class=self.OWNER_CLASS,
            )
        claimed = await repos.commands.claim_command(
            command.command_id,
            owner_class=self.OWNER_CLASS,
            claim_token=claim_token,
        )
        return CanonicalTurnCommandClaim(
            session_id=session.session_id,
            turn_attempt_id=str(command.turn_attempt_id),
            command_id=command.command_id,
            idempotency_key=canonical_key,
            claim_token=claim_token,
            outcome=claimed.outcome,
            expected_session_revision=command.expected_session_revision,
            fencing_generation=command.fencing_generation,
        )

    async def settle(
        self,
        *,
        workflow_id: str,
        idempotency_key: str,
        outcome: ControlPlaneOutcome,
        provider_receipt_id: str | None = None,
        result_ref: str | None = None,
    ) -> ControlPlaneOutcome:
        """Settle the command and advance its turn without session terminality."""

        canonical_key = canonical_turn_command_key(workflow_id, idempotency_key)
        claim_token = canonical_turn_claim_token(canonical_key)
        async with self._store.transaction() as repos:
            command = await repos.commands.get_by_idempotency_key(canonical_key)
            if command is None or not command.turn_attempt_id:
                raise CanonicalTurnAuthorityUnavailable(
                    "canonical turn command was not claimed before settlement"
                )
            delivered = await repos.commands.record_command_delivery(
                command.command_id,
                owner_class=self.OWNER_CLASS,
                claim_token=claim_token,
                outcome=outcome,
                provider_receipt_id=provider_receipt_id,
                result_ref=result_ref,
            )
            turn = await repos.turn_attempts.get(command.turn_attempt_id)
            session = await repos.sessions.get(command.session_id)
            if turn is None or session is None:
                raise CanonicalTurnAuthorityUnavailable(
                    "canonical turn command lost its owning aggregate"
                )
            if not turn.is_terminal:
                turn_state = (
                    TURN_STATE_ACCEPTED
                    if outcome in APPLIED_OUTCOMES
                    else (
                        TURN_STATE_DELIVERY_UNKNOWN
                        if outcome is ControlPlaneOutcome.DELIVERY_UNKNOWN
                        else None
                    )
                )
                if turn_state is not None:
                    if turn.state != turn_state:
                        await repos.turn_attempts.advance_state(
                            turn.turn_attempt_id,
                            turn_state,
                            expected_revision=turn.revision,
                            expected_fencing_generation=session.fencing_generation,
                        )
            return delivered.outcome

    async def bind_runtime_authority(
        self,
        *,
        session_id: str,
        execution_plan_ref: str,
        runtime_binding_ref: str,
    ) -> None:
        """Advance the session's pointer to a binding stage under its fence."""

        async with self._store.transaction() as repos:
            session = await repos.sessions.get(session_id)
            if session is None:
                raise CanonicalTurnAuthorityUnavailable(
                    "runtime binding lost its canonical session authority"
                )
            await repos.sessions.bind_runtime_authority(
                session_id,
                expected_revision=session.revision,
                expected_fencing_generation=session.fencing_generation,
                execution_plan_ref=execution_plan_ref,
                runtime_binding_ref=runtime_binding_ref,
            )


__all__ = [
    "CanonicalTurnAuthorityUnavailable",
    "CanonicalTurnCommandClaim",
    "CanonicalTurnCommandService",
    "CanonicalSessionBootstrap",
]
