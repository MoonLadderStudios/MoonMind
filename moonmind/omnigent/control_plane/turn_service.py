"""The one canonical Omnigent turn-command boundary.

Source issue: MoonLadderStudios/MoonMind#3707.

:class:`CanonicalTurnService` is the only application service permitted to submit
new work to an existing Omnigent session. Repository-output continuations,
remediation controllers, Workflow Chat HTTP and WebSocket mutations, steering and
approval actions, checkpoint resume, edit-and-rerun reconstruction, linked-branch
workflows, and execution realizers all enter here. Each of them names itself
through :data:`~moonmind.omnigent.turn_contracts.TURN_PRODUCER_SOURCES`, so no
producer can allocate a second canonical session, a second chat binding, or an
independent bridge authority for same-session work.

What the service owns, once, for every source kind:

* turn-attempt identity and idempotency (one logical turn per instruction)
* the immutable execution authority the turn was admitted against
* the fenced ``omnigent.submit_turn`` command journal entry
* a durable admission decision record, including for *refused* submissions
* cleanup fencing before any provider mutation

What the service deliberately does not own: harness-specific message or resume
behavior. There is no harness, provider, or runtime branch anywhere in this
module -- the harness appears only as one immutable authority dimension compared
against the recorded execution plan. Harness-specific behavior belongs behind the
selected Omnigent adapter or realizer, invoked by the session supervisor after
this service admits the turn.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Mapping, Optional

from moonmind.omnigent.turn_contracts import (
    CANONICAL_SUBMIT_COMMAND_TYPE,
    SESSION_NOT_FOUND,
    ImmutableExecutionAuthority,
    OmnigentTurnSource,
    RuntimeAuthorityEvidence,
    TurnAdmissionDecision,
    TurnAdmissionRequest,
    TurnDisposition,
    evaluate_turn_admission,
    resolve_producer_source,
    resolve_turn_source,
    turn_source_policy,
)

from .records import (
    CLEANUP_STATE_CLAIMED,
    CLEANUP_STATE_COMPLETE,
    CLEANUP_STATE_UNCLAIMED,
    CommandRecord,
    SessionRecord,
    TurnAttemptRecord,
    compute_digest,
)
from .repositories import ControlPlaneRepositories, OmnigentControlPlaneStore

#: Session metadata key holding the immutable execution authority recorded for
#: the session. Written once when the plan and runtime binding are committed;
#: this service only ever reads it.
IMMUTABLE_AUTHORITY_METADATA_KEY = "omnigentImmutableAuthority"

#: Prefix for the per-turn instruction ref recorded in session metadata so the
#: supervisor can resolve the artifact-backed instruction for a turn.
TURN_INSTRUCTION_METADATA_PREFIX = "turnInstructionRef:"

#: Low-cardinality owner class for the submit command. The producer name is
#: high-cardinality relative to metrics labels, so it is journalled on the
#: decision record instead.
SUBMIT_OWNER_CLASS = "canonical_turn_service"

#: Decision codes appended to the durable decision journal.
DECISION_TURN_ADMITTED = "submit_turn"
DECISION_TURN_REFUSED = "await_observation"


class CanonicalTurnServiceError(RuntimeError):
    """Base error for the canonical turn boundary."""


class TurnProducerNotRegisteredError(CanonicalTurnServiceError):
    """Raised when an unregistered production path tries to submit a turn."""


@dataclass(frozen=True)
class CanonicalTurnRequest:
    """One turn submission, expressed only in compact, non-sensitive authority.

    ``instruction_ref`` is an artifact reference; instruction *text* never
    crosses this boundary. ``requested_authority`` carries only the immutable
    dimensions the producer believes it is running under -- leaving a dimension
    unset means "whatever the recorded plan says", which is what a same-session
    follow-up normally wants.
    """

    producer: str
    session_id: str
    turn_attempt_id: str
    idempotency_key: str
    instruction_ref: str
    source: OmnigentTurnSource | str
    actor_id: Optional[str] = None
    chat_binding_id: Optional[str] = None
    parent_turn_attempt_id: Optional[str] = None
    remediation_of_turn_attempt_id: Optional[str] = None
    remediation_gate_ref: Optional[str] = None
    checkpoint_ref: Optional[str] = None
    step_execution_id: Optional[str] = None
    expected_session_revision: Optional[int] = None
    expected_fencing_generation: Optional[int] = None
    requested_authority: ImmutableExecutionAuthority = ImmutableExecutionAuthority()
    evidence: RuntimeAuthorityEvidence = RuntimeAuthorityEvidence()


@dataclass(frozen=True)
class CanonicalTurnResult:
    """Outcome of one submission through the canonical boundary."""

    decision: TurnAdmissionDecision
    session: Optional[SessionRecord] = None
    turn_attempt: Optional[TurnAttemptRecord] = None
    command: Optional[CommandRecord] = None
    decision_ref: Optional[str] = None
    #: Artifact-backed instruction reference for an admitted turn.
    instruction_ref: str = ""

    @property
    def admitted(self) -> bool:
        return self.decision.admitted

    @property
    def disposition(self) -> TurnDisposition:
        return self.decision.disposition

    def dispatch_payload(self) -> dict[str, Any]:
        """Compact supervisor signal payload for an admitted turn.

        Raises when called on a refused submission: a refused turn has no
        provider-visible effect and must never be dispatched.
        """

        if not self.admitted or self.turn_attempt is None:
            raise CanonicalTurnServiceError(
                "refused turn submissions are never dispatched "
                f"(reason {self.decision.reason_code!r})"
            )
        return {
            "requestId": self.turn_attempt.idempotency_key,
            "turnAttemptId": self.turn_attempt.turn_attempt_id,
            "instructionRef": self.instruction_ref,
            "turnSource": self.decision.source.value,
            "reasonCode": self.decision.reason_code,
        }


def recorded_authority_from_session(session: SessionRecord) -> ImmutableExecutionAuthority:
    """Project the session's recorded immutable execution authority.

    Missing metadata yields an empty authority, which the admission gates treat
    as "no execution plan recorded" -- a new session is required rather than a
    silent reuse of unknown authority.
    """

    raw = session.metadata.get(IMMUTABLE_AUTHORITY_METADATA_KEY)
    if not isinstance(raw, Mapping):
        return ImmutableExecutionAuthority()
    return ImmutableExecutionAuthority.model_validate(dict(raw))


class CanonicalTurnService:
    """Create or resolve a canonical turn attempt and its fenced submit command.

    ``dispatcher`` is the supervisor signal boundary. It is invoked only after
    the durable turn attempt and command journal entry are committed, so a lost
    dispatch is recoverable from the journal rather than producing an untracked
    provider side effect.
    """

    def __init__(
        self,
        store: OmnigentControlPlaneStore,
        *,
        dispatcher: Callable[[CanonicalTurnResult], Awaitable[None]] | None = None,
    ) -> None:
        self._store = store
        self._dispatcher = dispatcher

    async def submit_turn(
        self, request: CanonicalTurnRequest
    ) -> CanonicalTurnResult:
        """Admit and journal one turn, or refuse it without any mutation.

        The whole admission runs inside one transaction against the locked
        session row, so continuation admission, remediation admission, chat
        submission, session stop, cleanup claiming, and janitor recovery
        serialize deterministically instead of racing.
        """

        source = resolve_turn_source(request.source)
        registered = resolve_producer_source(request.producer)
        if registered is not source:
            raise TurnProducerNotRegisteredError(
                f"producer {request.producer!r} is registered for "
                f"{registered.value!r}, not {source.value!r}"
            )
        policy = turn_source_policy(source)
        if not (request.instruction_ref or "").strip():
            raise CanonicalTurnServiceError(
                "a canonical turn requires a durable instructionRef"
            )

        async with self._store.transaction() as repos:
            session = await repos.sessions.load_for_update(request.session_id)
            if session is None:
                decision = _refused_unknown_session(request, source)
                return CanonicalTurnResult(decision=decision)

            evidence = await self._resolve_evidence(
                repos, session=session, requested=request.evidence
            )
            chat_binding_id = await self._resolve_chat_binding(
                repos, session=session, request=request, required=policy.requires_chat_binding
            )
            recorded = recorded_authority_from_session(session)
            expected_revision = (
                session.revision
                if request.expected_session_revision is None
                else request.expected_session_revision
            )
            expected_fence = (
                session.fencing_generation
                if request.expected_fencing_generation is None
                else request.expected_fencing_generation
            )
            admission = TurnAdmissionRequest(
                producer=request.producer,
                source=source,
                sessionId=request.session_id,
                actorId=request.actor_id,
                sessionActorId=_session_actor_id(session),
                chatBindingId=request.chat_binding_id,
                sessionChatBindingId=chat_binding_id,
                parentTurnAttemptId=(
                    request.parent_turn_attempt_id or session.active_turn_attempt_id
                ),
                remediationOfTurnAttemptId=request.remediation_of_turn_attempt_id,
                remediationGateRef=request.remediation_gate_ref,
                checkpointRef=request.checkpoint_ref,
                expectedSessionRevision=expected_revision,
                currentSessionRevision=session.revision,
                expectedFencingGeneration=expected_fence,
                currentFencingGeneration=session.fencing_generation,
                requestedAuthority=request.requested_authority,
                recordedAuthority=recorded,
                evidence=evidence,
            )
            decision = evaluate_turn_admission(admission)
            decision_ref = await self._journal_decision(
                repos,
                request=request,
                decision=decision,
                session=session,
                admission=admission,
            )
            if not decision.admitted:
                # No mutation beyond the append-only decision journal: a refused
                # submission never touches the turn, command, or session rows.
                return CanonicalTurnResult(
                    decision=decision, session=session, decision_ref=decision_ref
                )

            instruction_digest = compute_digest(request.instruction_ref)
            turn = await repos.turn_attempts.create(
                turn_attempt_id=request.turn_attempt_id,
                session_id=request.session_id,
                idempotency_key=request.idempotency_key,
                turn_source=source.value,
                step_execution_id=(
                    request.step_execution_id or session.step_execution_id
                ),
                parent_turn_attempt_id=admission.parent_turn_attempt_id,
                remediation_of_turn_attempt_id=(
                    request.remediation_of_turn_attempt_id
                ),
                instruction_digest=instruction_digest,
                execution_plan_ref=recorded.execution_plan_ref,
                runtime_binding_ref=recorded.runtime_binding_ref,
                authority_digest=recorded.authority_digest,
                expected_session_revision=session.revision,
            )
            command = await repos.commands.record(
                command_id=f"cmd-{turn.turn_attempt_id}",
                session_id=request.session_id,
                command_type=CANONICAL_SUBMIT_COMMAND_TYPE,
                idempotency_key=f"{CANONICAL_SUBMIT_COMMAND_TYPE}:{request.idempotency_key}",
                payload_digest=compute_digest(
                    {
                        "turnAttemptId": turn.turn_attempt_id,
                        "turnSource": source.value,
                        "instructionDigest": instruction_digest,
                        "authorityDigest": recorded.authority_digest,
                    }
                ),
                turn_attempt_id=turn.turn_attempt_id,
                expected_session_revision=session.revision,
                fencing_generation=session.fencing_generation,
                owner_class=SUBMIT_OWNER_CLASS,
            )
            if session.active_turn_attempt_id != turn.turn_attempt_id:
                session = await repos.sessions.update_lifecycle(
                    request.session_id,
                    expected_revision=session.revision,
                    expected_fencing_generation=session.fencing_generation,
                    active_turn_attempt_id=turn.turn_attempt_id,
                    last_decision_ref=decision_ref,
                )
            session = await repos.sessions.bind_runtime_authority(
                request.session_id,
                expected_revision=session.revision,
                expected_fencing_generation=session.fencing_generation,
                metadata_patch={
                    f"{TURN_INSTRUCTION_METADATA_PREFIX}{turn.turn_attempt_id}": (
                        request.instruction_ref
                    ),
                },
            )

        result = CanonicalTurnResult(
            decision=decision,
            session=session,
            turn_attempt=turn,
            command=command,
            decision_ref=decision_ref,
            instruction_ref=request.instruction_ref,
        )
        if self._dispatcher is not None:
            await self._dispatcher(result)
        return result

    async def _resolve_evidence(
        self,
        repos: ControlPlaneRepositories,
        *,
        session: SessionRecord,
        requested: RuntimeAuthorityEvidence,
    ) -> RuntimeAuthorityEvidence:
        """Overlay durable session/cleanup authority onto caller evidence.

        Terminality, historical-read authority, and cleanup state are owned by
        the control plane, never by the submitting producer, so they are always
        taken from durable rows. Provider/host/workspace liveness is genuinely
        observed by the caller and passed through.
        """

        cleanup = await repos.cleanup.get(session.session_id)
        cleanup_state = cleanup.state if cleanup is not None else CLEANUP_STATE_UNCLAIMED
        if cleanup_state not in {
            CLEANUP_STATE_UNCLAIMED,
            CLEANUP_STATE_CLAIMED,
            CLEANUP_STATE_COMPLETE,
        }:
            cleanup_state = CLEANUP_STATE_CLAIMED
        # ``cleanup_state`` on the session advances independently of the janitor
        # claim; a finished session-level cleanup is just as disqualifying. The
        # terminal values match the repository's reconciliation-candidate filter.
        if session.cleanup_state in {"complete", "closed"}:
            cleanup_state = CLEANUP_STATE_COMPLETE
        return requested.model_copy(
            update={
                "session_terminal": session.is_terminal,
                "historical_read_state": session.historical_read_state,
                "cleanup_state": cleanup_state,
            }
        )

    async def _resolve_chat_binding(
        self,
        repos: ControlPlaneRepositories,
        *,
        session: SessionRecord,
        request: CanonicalTurnRequest,
        required: bool,
    ) -> Optional[str]:
        """Resolve the canonical chat binding for the session.

        A caller-supplied handle is resolved through the alias repository so a
        previously issued handle still lands on the one canonical session. The
        resolved value is compared against the session's own binding by the
        admission gates, so a cross-binding submission is refused before any
        mutation instead of acquiring a second binding authority.
        """

        if not required:
            return session.chat_binding_id
        handle = (request.chat_binding_id or "").strip()
        if not handle:
            return session.chat_binding_id
        alias = await repos.chat_binding_aliases.resolve(handle)
        if alias is not None and alias.resolves and alias.session_id == session.session_id:
            # The handle is an alias of this session's canonical binding.
            return handle
        return session.chat_binding_id

    async def _journal_decision(
        self,
        repos: ControlPlaneRepositories,
        *,
        request: CanonicalTurnRequest,
        decision: TurnAdmissionDecision,
        session: SessionRecord,
        admission: TurnAdmissionRequest,
    ) -> str:
        """Append the admission decision, admitted or refused.

        Refusals are durable evidence too: ``branch_required`` and
        ``new_session_required`` are the authoritative record of why a producer
        was told to allocate new canonical authority.
        """

        # The decision id includes the observed revision so an idempotent
        # resubmission at a later revision records its own observation instead
        # of colliding with the earlier one, which the append-only journal
        # correctly refuses as conflicting authority.
        decision_id = compute_digest(
            {
                "sessionId": session.session_id,
                "idempotencyKey": request.idempotency_key,
                "reasonCode": decision.reason_code,
                "disposition": decision.disposition.value,
                "expectedRevision": admission.expected_session_revision,
                "fencingGeneration": session.fencing_generation,
            }
        )[:40]
        record = await repos.decisions.append(
            decision_id=f"turn-{decision_id}",
            session_id=session.session_id,
            decision_code=(
                DECISION_TURN_ADMITTED if decision.admitted else DECISION_TURN_REFUSED
            ),
            input_state_digest=decision.authority_digest,
            expected_revision=admission.expected_session_revision,
            fencing_generation=session.fencing_generation,
            reason_code=decision.reason_code,
            product_visible_transition=decision.disposition.value,
        )
        return record.decision_id


def _session_actor_id(session: SessionRecord) -> Optional[str]:
    """Return the end user the session belongs to, when recorded."""

    value = session.metadata.get("actorId") or session.metadata.get("ownerId")
    return str(value) if value else None


def _refused_unknown_session(
    request: CanonicalTurnRequest, source: OmnigentTurnSource
) -> TurnAdmissionDecision:
    """Refuse a submission whose canonical session does not exist.

    A missing session is not permission to create one here: session
    establishment is a separate authority (``establish_session``), so the
    producer is told a new session is required.
    """

    return TurnAdmissionDecision(
        admitted=False,
        disposition=TurnDisposition.NEW_SESSION_REQUIRED,
        reasonCode=SESSION_NOT_FOUND,
        source=source,
        producer=request.producer,
        sessionId=request.session_id,
        authorityDigest=ImmutableExecutionAuthority().authority_digest,
    )


__all__ = [
    "CanonicalTurnRequest",
    "CanonicalTurnResult",
    "CanonicalTurnService",
    "CanonicalTurnServiceError",
    "DECISION_TURN_ADMITTED",
    "DECISION_TURN_REFUSED",
    "IMMUTABLE_AUTHORITY_METADATA_KEY",
    "SUBMIT_OWNER_CLASS",
    "TURN_INSTRUCTION_METADATA_PREFIX",
    "TurnProducerNotRegisteredError",
    "recorded_authority_from_session",
]
