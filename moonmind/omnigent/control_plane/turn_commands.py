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

from moonmind.omnigent.resume_decision import (
    SessionResumeDecision,
    SessionResumeOutcome,
)

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
    CLEANUP_STATE_COMPLETE,
    ConflictingSessionAuthorityError,
    ControlPlaneOutcome,
    TURN_STATE_ACCEPTED,
    TURN_STATE_DELIVERY_UNKNOWN,
)
from .turn_admission import (
    CanonicalTurnAdmissionRejected,
    IMMUTABLE_AUTHORITY_METADATA_KEY,
    ImmutableTurnAuthority,
    assert_remediation_does_not_broaden,
    evaluate_turn_admission,
)
from .turn_sources import TurnSource, coerce_turn_source


#: Session-metadata key holding the principal that owns this canonical session.
OWNER_PRINCIPAL_METADATA_KEY = "ownerPrincipal"


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
    owner_principal: str | None = None


def _bootstrap_metadata(
    *,
    bootstrap: CanonicalSessionBootstrap,
    requested_authority: ImmutableTurnAuthority | None,
) -> dict[str, Any]:
    """Return the durable, non-sensitive authority a session bootstraps with."""

    metadata: dict[str, Any] = {"canonicalizedOnDemand": True}
    if bootstrap.owner_principal:
        metadata[OWNER_PRINCIPAL_METADATA_KEY] = str(bootstrap.owner_principal)
    if requested_authority is not None:
        metadata[IMMUTABLE_AUTHORITY_METADATA_KEY] = (
            requested_authority.as_metadata()
        )
    return metadata


def _recorded_session_authority(session: Any) -> ImmutableTurnAuthority | None:
    """Return the immutable authority durably recorded for ``session``.

    The session's own plan and runtime-binding pointers win over the metadata
    projection: those columns are advanced under the session's fence, so they
    are the more current statement of the same authority.
    """

    recorded = ImmutableTurnAuthority.from_metadata(
        (session.metadata or {}).get(IMMUTABLE_AUTHORITY_METADATA_KEY)
    )
    if recorded is None:
        return None
    return ImmutableTurnAuthority(
        execution_plan_ref=(session.execution_plan_ref or recorded.execution_plan_ref),
        runtime_binding_ref=(
            session.runtime_binding_ref or recorded.runtime_binding_ref
        ),
        dimensions=recorded.dimensions,
    )


def _verify_owner_principal(session: Any, actor_principal: str | None) -> None:
    """Fail closed before mutation when another principal owns this session."""

    if not actor_principal:
        return
    recorded = str(
        (session.metadata or {}).get(OWNER_PRINCIPAL_METADATA_KEY) or ""
    ).strip()
    if recorded and recorded != str(actor_principal).strip():
        raise CanonicalTurnAuthorityUnavailable(
            "Instruction actor does not own this canonical OmnigentSession"
        )


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
        turn_source: Any,
        idempotency_key: str,
        payload_digest: str,
        step_execution_id: str | None = None,
        base_step_execution_id: str | None = None,
        bootstrap: CanonicalSessionBootstrap | None = None,
        requested_authority: ImmutableTurnAuthority | None = None,
        actor_principal: str | None = None,
        runtime_authority_current: bool = True,
        branch_capable: bool = True,
        fence_cleanup: bool = True,
    ) -> CanonicalTurnCommandClaim:
        """Claim current fenced authority before an external side effect."""

        async with self._store.transaction() as repos:
            return await self.claim_with_repositories(
                repos,
                workflow_id=workflow_id,
                provider_session_ref=provider_session_ref,
                chat_binding_id=chat_binding_id,
                command_type=command_type,
                turn_source=turn_source,
                idempotency_key=idempotency_key,
                payload_digest=payload_digest,
                step_execution_id=step_execution_id,
                base_step_execution_id=base_step_execution_id,
                bootstrap=bootstrap,
                requested_authority=requested_authority,
                actor_principal=actor_principal,
                runtime_authority_current=runtime_authority_current,
                branch_capable=branch_capable,
                fence_cleanup=fence_cleanup,
            )

    async def claim_with_repositories(
        self,
        repos: Any,
        *,
        workflow_id: str,
        provider_session_ref: str,
        chat_binding_id: str | None,
        command_type: str,
        turn_source: Any,
        idempotency_key: str,
        payload_digest: str,
        step_execution_id: str | None = None,
        base_step_execution_id: str | None = None,
        bootstrap: CanonicalSessionBootstrap | None = None,
        requested_authority: ImmutableTurnAuthority | None = None,
        actor_principal: str | None = None,
        runtime_authority_current: bool = True,
        branch_capable: bool = True,
        fence_cleanup: bool = True,
    ) -> CanonicalTurnCommandClaim:
        """Claim within an existing application transaction."""

        source = coerce_turn_source(turn_source)
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
                        metadata=_bootstrap_metadata(
                            bootstrap=bootstrap,
                            requested_authority=requested_authority,
                        ),
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
                    bootstrapping_instruction = (
                        bootstrap.source_idempotency_key == idempotency_key
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
                        # When this claim *is* the instruction that bootstraps
                        # the session, the bootstrap attempt is that
                        # instruction's turn and must journal its real source
                        # (#3707 AC3) -- a remediation attempt that opens its own
                        # canonical session is not an ``initial`` turn. When the
                        # claim is a later follow-up canonicalizing an older
                        # session on demand, it cannot attest the earlier
                        # instruction's source, so the bootstrap attempt keeps
                        # the one source that establishes a session.
                        lineage_kind=(
                            source if bootstrapping_instruction else TurnSource.INITIAL
                        ),
                        step_execution_id=bootstrap.step_execution_id,
                        instruction_digest=(
                            payload_digest if bootstrapping_instruction else None
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
        _verify_owner_principal(session, actor_principal)
        cleanup = await repos.cleanup.get(session.session_id)
        cleanup_complete = (
            cleanup is not None and cleanup.state == CLEANUP_STATE_COMPLETE
        )
        if cleanup_complete:
            # Completed cleanup is a distinct terminal meaning and is never
            # reopened: the host, credential lease, and workspace this session
            # owned are gone. Refuse before any write so the turn cannot consume
            # a released credential lease (#3707 §4).
            raise CanonicalTurnAdmissionRejected(
                SessionResumeOutcome(
                    decision=SessionResumeDecision.COLD_RESTORE,
                    reason_codes=("cleanup_complete",),
                )
            )
        admission = self._admit(
            session,
            source=source,
            requested_authority=requested_authority,
            base_authority=await self._base_authority(
                repos,
                source=source,
                workflow_id=workflow_id,
                base_step_execution_id=base_step_execution_id,
                session=session,
            ),
            bootstrapped_by_this_claim=bootstrap_turn is not None,
            runtime_authority_current=runtime_authority_current,
            branch_capable=branch_capable,
            cleanup_complete=cleanup_complete,
        )
        if admission is not None and not admission.same_session:
            # Immutable authority changed, or the prior session is no longer
            # safely reusable: return the explicit typed decision *before* any
            # provider mutation instead of silently rewriting the old session.
            raise CanonicalTurnAdmissionRejected(admission)
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
                    lineage_kind=source,
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
        if fence_cleanup and claimed.outcome is ControlPlaneOutcome.APPLIED:
            # An accepted turn fences incompatible cleanup before the provider
            # mutation: a janitor holding an older cleanup generation can no
            # longer complete against the replacement generation (#3707 §4).
            fenced = await repos.cleanup.fence_for_turn(
                session.session_id, owner_class=self.OWNER_CLASS
            )
            if not fenced.applied:
                # Cleanup completed between the admission read and the fence;
                # the transaction rolls back so nothing was published.
                raise CanonicalTurnAdmissionRejected(
                    SessionResumeOutcome(
                        decision=SessionResumeDecision.COLD_RESTORE,
                        reason_codes=("cleanup_complete",),
                    )
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

    async def _base_authority(
        self,
        repos: Any,
        *,
        source: TurnSource,
        workflow_id: str,
        base_step_execution_id: str | None,
        session: Any,
    ) -> ImmutableTurnAuthority | None:
        """Load the durable authority of the Step Execution a turn repairs.

        A remediation attempt runs as its own Step Execution and therefore
        bootstraps its own canonical session; comparing its requested authority
        against that session's metadata would compare the claim with the copy it
        just wrote. The bound must come from the *other* durable aggregate the
        controller names, so this resolves the base Step Execution's canonical
        session and refuses to treat the claiming session as its own base.

        The instruction names the base identity; it never attests the base
        authority, which is read from durable session state.
        """

        if source is not TurnSource.REMEDIATION or not base_step_execution_id:
            return None
        base_session = await repos.sessions.get_by_step_execution(
            workflow_id, base_step_execution_id
        )
        if base_session is None or base_session.session_id == session.session_id:
            return None
        return _recorded_session_authority(base_session)

    def _admit(
        self,
        session: Any,
        *,
        source: TurnSource,
        requested_authority: ImmutableTurnAuthority | None,
        base_authority: ImmutableTurnAuthority | None = None,
        bootstrapped_by_this_claim: bool = False,
        runtime_authority_current: bool,
        branch_capable: bool,
        cleanup_complete: bool,
    ) -> SessionResumeOutcome | None:
        """Return the typed admission decision for reusing ``session``.

        The recorded execution plan and runtime binding come from durable
        session authority (#3706), never from the caller: an instruction may
        request authority but cannot attest it. Instructions that do not assert
        an immutable authority set (``requested_authority is None``) skip the
        comparison and keep the session's recorded authority unchanged.
        """

        if requested_authority is None:
            return None
        recorded = _recorded_session_authority(session)
        if source is TurnSource.REMEDIATION:
            # Remediation is bounded by the authority of the attempt it repairs
            # (#3707 AC6). Broadening is a policy violation, not a branch.
            #
            # A claim that just bootstrapped this session wrote ``recorded``
            # itself, so comparing against it would compare the claim with its
            # own copy. Such a turn is bounded only by the base Step Execution
            # the controller named; a turn that joins an existing session is
            # bounded by that session's durable record.
            bound = base_authority
            if bound is None and not bootstrapped_by_this_claim:
                bound = recorded
            assert_remediation_does_not_broaden(
                recorded=bound, requested=requested_authority
            )
        if recorded is None:
            # First instruction to assert authority for a session that predates
            # the record converges onto it rather than failing an admitted run.
            return None
        return evaluate_turn_admission(
            recorded=recorded,
            requested=requested_authority,
            session_terminal=session.is_terminal,
            # Final session terminality is durable: no source may reopen it.
            # A checkpoint resume of a terminal session branches instead.
            session_resumable=False,
            runtime_authority_current=runtime_authority_current,
            branch_capable=branch_capable,
            cleanup_complete=cleanup_complete,
            require_complete_authority=False,
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
    "OWNER_PRINCIPAL_METADATA_KEY",
    "CanonicalTurnAdmissionRejected",
    "CanonicalTurnAuthorityUnavailable",
    "CanonicalTurnCommandClaim",
    "CanonicalTurnCommandService",
    "CanonicalSessionBootstrap",
    "ImmutableTurnAuthority",
    "TurnSource",
]
