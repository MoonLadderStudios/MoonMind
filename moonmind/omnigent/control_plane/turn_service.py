"""Repository-backed executor for the canonical session/turn contract.

Source: MoonLadderStudios/MoonMind#3707 ([Omnigent control plane 6/11]
Unify continuations, remediation, checkpoints, and chat under canonical session
and turn ownership).

:mod:`moonmind.omnigent.control_plane.turn_contract` is *pure domain*: it decides
what a provider message means (which source kind, whether it reuses the canonical
session, whether an immutable dimension forces a branch, whether cleanup fences
it). This module is the single durable applier of those decisions. Every
same-session continuation, remediation attempt, checkpoint resume, native-chat
message, steering action, and approval response flows through :class:`OmnigentTurnService`
so they all agree on one canonical :class:`SessionRecord` and one chat binding,
and new work is always a new :class:`TurnAttemptRecord` (or an explicit linked
branch) rather than another ambiguous session row.

The service owns only durable orchestration against the #3703 repositories
(:class:`OmnigentControlPlaneStore`). It never reimplements provider data
collection, comment/blocker classification, remediation admission, or completion
rules -- those decisions belong to the reconciler, the remediation controller,
and the resolved Skill. The service simply records the typed command path
atomically:

    resolve canonical session
      -> plan the typed command path (turn_contract.plan_turn_submission)
      -> fence against terminal cleanup (turn_contract.admit_continuation)
      -> validate remediation authority where applicable
      -> allocate one new turn attempt + one fenced submit command
      -> append the reconciliation decision
      -> point the session's active turn at the new attempt

Redelivery is idempotent on the turn attempt's idempotency key, so an ambiguous
delivery never duplicates a continuation.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Optional

from .records import (
    CLEANUP_STATE_CLAIMED,
    CLEANUP_STATE_COMPLETE,
    TURN_STATE_ACCEPTED,
    TURN_STATE_DELIVERY_UNKNOWN,
    CasResult,
    ChatBindingAliasRecord,
    CommandRecord,
    ControlPlaneOutcome,
    DecisionRecord,
    SessionRecord,
    TurnAttemptRecord,
    TurnIdempotencyConflictError,
    compute_digest,
)
from .repositories import ControlPlaneRepositories, OmnigentControlPlaneStore
from .turn_contract import (
    BranchRequiredError,
    CallerAuthorityError,
    ChatCapabilityDecision,
    ImmutableSessionDimensions,
    LifecycleActivity,
    RecoveryDecision,
    RecoveryEvidence,
    TERMINAL_CLEANUP_STATES,
    TurnSourceKind,
    TurnSubmissionPlan,
    TurnSubmissionRequest,
    idempotency_scope_for,
    admit_continuation,
    decide_recovery,
    derive_chat_capability,
    plan_turn_submission,
    validate_remediation_authority,
)

# Command / decision vocabulary the service journals for every turn submission.
# One command type for the whole typed path keeps the idempotency namespace
# small (Simplicity Gate); the source kind is carried on the decision record.
SUBMIT_TURN_COMMAND_TYPE = "submit_turn"
_DECISION_CODE_PREFIX = "turn_submission"


@dataclass(frozen=True)
class TurnSubmissionOutcome:
    """The durable result of applying one turn-submission plan."""

    session: SessionRecord
    turn_attempt: TurnAttemptRecord
    plan: TurnSubmissionPlan
    command: CommandRecord
    claim: CasResult
    #: ``None`` on an idempotent redelivery whose decision was already recorded.
    decision: Optional[DecisionRecord]
    #: True when this call created the attempt; False on idempotent redelivery.
    created: bool


class OmnigentTurnService:
    """Durable applier for the canonical session/turn contract.

    All state changes run inside one :meth:`OmnigentControlPlaneStore.transaction`
    so a partially applied turn submission can never be observed.
    """

    def __init__(self, store: OmnigentControlPlaneStore) -> None:
        self._store = store

    # -- reuse turns --------------------------------------------------------

    async def submit_reuse_turn(
        self,
        request: TurnSubmissionRequest,
        *,
        activity: Optional[LifecycleActivity] = None,
        owner_class: str = "session_workflow",
        claim_token: Optional[str] = None,
    ) -> TurnSubmissionOutcome:
        """Apply a same-session (reuse) turn: continuation, remediation, chat,
        steering, approval response, or checkpoint resume.

        Reuse turns keep the canonical session id, chat binding, immutable
        profile/policy/image/workspace authority, and allocate only a new turn
        attempt. The plan fails closed with ``BranchRequiredError`` when an
        immutable dimension changed and ``SessionTerminalError`` when the
        provider authority is already terminal or cleaned up.

        Raises:
            CallerAuthorityError: ``request.source_kind`` allocates a new
                session (``initial``/``linked_branch``) and must not reuse.
        """

        if request.source_kind in {
            TurnSourceKind.INITIAL,
            TurnSourceKind.LINKED_BRANCH,
        }:
            raise CallerAuthorityError(
                f"{request.source_kind.value} allocates a new canonical session; "
                "use establish_session (initial) or open_linked_branch "
                "(linked_branch) rather than a reuse turn"
            )

        activity = activity or LifecycleActivity()

        async with self._store.transaction() as repos:
            session = await repos.sessions.get(request.session_id)
            # plan_turn_submission fails closed on missing/terminal session,
            # identity mismatch, and changed immutable dimensions.
            plan = plan_turn_submission(request, session)
            assert session is not None  # reuse plan guarantees a live session

            # Terminal-cleanup fence: a continuation submitted at or after
            # terminal cleanup must branch or open a new session, never
            # resurrect deleted provider authority.
            admit_continuation(session=session, activity=activity)

            if request.source_kind is TurnSourceKind.REMEDIATION:
                await self._authorize_remediation(repos, session, request)

            cleanup = await repos.cleanup.get(session.session_id)
            if cleanup is not None and cleanup.state in {
                CLEANUP_STATE_CLAIMED,
                CLEANUP_STATE_COMPLETE,
            }:
                raise CallerAuthorityError(
                    "canonical session cleanup already owns provider authority; "
                    "the request requires an explicit linked branch"
                )

            return await self._apply_plan(
                repos,
                request,
                session,
                plan,
                owner_class=owner_class,
                claim_token=claim_token,
            )

    async def _authorize_remediation(
        self,
        repos: ControlPlaneRepositories,
        session: SessionRecord,
        request: TurnSubmissionRequest,
    ) -> TurnAttemptRecord:
        intent = request.remediation
        if intent is None:
            raise CallerAuthorityError(
                "A remediation turn requires a typed RemediationTurnIntent"
            )
        if request.controller_id != intent.loop_id:
            raise CallerAuthorityError(
                "remediation controller authority must match the typed loop identity"
            )
        if not intent.allow_same_session_reuse:
            raise BranchRequiredError(
                session.session_id, ("remediation_same_session_reuse",)
            )
        predecessor = await repos.turn_attempts.get(intent.of_turn_attempt_id)
        if predecessor is None:
            raise CallerAuthorityError(
                "remediation references an unknown canonical predecessor turn"
            )
        if predecessor.session_id != session.session_id:
            raise CallerAuthorityError(
                "remediation predecessor does not belong to the requested "
                "canonical session"
            )
        if not predecessor.is_terminal:
            raise CallerAuthorityError(
                "remediation predecessor lacks authoritative terminal evidence"
            )
        base_dimensions = ImmutableSessionDimensions.from_session(session)
        base_publication = bool(
            (session.metadata or {}).get("grants_publication_authority", False)
        )
        validate_remediation_authority(
            base_dimensions=base_dimensions,
            intent=intent,
            base_grants_publication_authority=base_publication,
        )
        return predecessor

    async def _apply_plan(
        self,
        repos: ControlPlaneRepositories,
        request: TurnSubmissionRequest,
        session: SessionRecord,
        plan: TurnSubmissionPlan,
        *,
        owner_class: str,
        claim_token: Optional[str],
    ) -> TurnSubmissionOutcome:
        scope = plan.idempotency_scope

        # Idempotent redelivery: the same logical turn (same scope) never
        # allocates a second attempt or a second command.
        existing = await repos.turn_attempts.get_by_idempotency_key(
            request.idempotency_key
        )
        if existing is not None:
            command = await repos.commands.get_by_idempotency_key(
                request.idempotency_key
            )
            if command is None:
                raise CallerAuthorityError(
                    "turn attempt exists without its canonical submit command"
                )
            self._validate_redelivery(
                request=request,
                turn=existing,
                command=command,
                expected_session_id=session.session_id,
            )
            claim = await repos.commands.claim_command(
                command.command_id,
                owner_class=owner_class,
                claim_token=claim_token or _derive_id("claim", scope),
            )
            self._require_command_claim(claim, command.command_id)
            return TurnSubmissionOutcome(
                session=session,
                turn_attempt=existing,
                plan=plan,
                command=command,
                claim=claim,
                decision=None,
                created=False,
            )

        remediation_of = (
            request.remediation.of_turn_attempt_id
            if request.remediation is not None
            else None
        )
        turn = await repos.turn_attempts.create(
            turn_attempt_id=_derive_id("turn", scope),
            session_id=session.session_id,
            idempotency_key=request.idempotency_key,
            lineage_kind=plan.lineage_kind,
            step_execution_id=session.step_execution_id,
            parent_turn_attempt_id=session.active_turn_attempt_id,
            remediation_of_turn_attempt_id=remediation_of,
            instruction_digest=request.instruction_digest,
        )
        command = await repos.commands.record(
            command_id=_derive_id("cmd", scope),
            session_id=session.session_id,
            command_type=SUBMIT_TURN_COMMAND_TYPE,
            idempotency_key=request.idempotency_key,
            payload_digest=request.instruction_digest,
            turn_attempt_id=turn.turn_attempt_id,
            expected_session_revision=session.revision,
            fencing_generation=session.fencing_generation,
        )
        decision = await repos.decisions.append(
            decision_id=_derive_id("dec", scope),
            session_id=session.session_id,
            decision_code=f"{_DECISION_CODE_PREFIX}:{request.source_kind.value}",
            input_state_digest=scope,
            reason_code=(
                "reused_canonical_session"
                if plan.reuses_session
                else "allocated_new_session"
            ),
            resulting_command_id=command.command_id,
            expected_revision=session.revision,
            fencing_generation=session.fencing_generation,
        )
        # Point the canonical session's active turn at the new attempt without
        # touching its terminality: an attempt returning is not session
        # terminality.
        refreshed = await repos.sessions.update_lifecycle(
            session.session_id,
            expected_revision=session.revision,
            expected_fencing_generation=session.fencing_generation,
            active_turn_attempt_id=turn.turn_attempt_id,
        )
        claim = await repos.commands.claim_command(
            command.command_id,
            owner_class=owner_class,
            claim_token=claim_token or _derive_id("claim", scope),
        )
        self._require_command_claim(claim, command.command_id)
        return TurnSubmissionOutcome(
            session=refreshed,
            turn_attempt=turn,
            plan=plan,
            command=command,
            claim=claim,
            decision=decision,
            created=True,
        )

    # -- canonical session allocation --------------------------------------

    async def establish_session(
        self,
        request: TurnSubmissionRequest,
        *,
        moonmind_workflow_id: str,
        provider: str,
        new_session_id: str,
        new_chat_binding_id: Optional[str] = None,
        provider_session_ref: Optional[str] = None,
        moonmind_run_id: Optional[str] = None,
        step_execution_id: Optional[str] = None,
        moonmind_agent_run_id: Optional[str] = None,
        compatibility_profile: Optional[str] = None,
        provider_profile_id: Optional[str] = None,
        host_binding_ref: Optional[str] = None,
        host_lease_ref: Optional[str] = None,
        compatibility_ref: Optional[str] = None,
        image_manifest_ref: Optional[str] = None,
        parent_session_id: Optional[str] = None,
        owner_class: str = "session_workflow",
        claim_token: Optional[str] = None,
    ) -> TurnSubmissionOutcome:
        """Establish one provider-session authority and its first typed turn.

        A remediation or checkpoint branch can be the first message in a newly
        allocated provider session. Its source kind remains visible in the
        command journal, while the first attempt keeps ``lineage_kind=initial``.
        """

        planning_request = request
        if request.source_kind not in {
            TurnSourceKind.INITIAL,
            TurnSourceKind.LINKED_BRANCH,
        }:
            planning_request = replace(request, source_kind=TurnSourceKind.INITIAL)
        base_plan = plan_turn_submission(planning_request, None)
        plan = replace(
            base_plan,
            source_kind=request.source_kind,
            lineage_kind="initial",
            idempotency_scope=idempotency_scope_for(request),
        )
        scope = plan.idempotency_scope
        metadata: dict[str, Any] = dict(request.metadata)
        metadata.update(request.requested_dimensions.as_session_metadata())
        metadata["turn_source_kind"] = request.source_kind.value
        if parent_session_id is not None:
            metadata["branched_from_session_id"] = parent_session_id

        async with self._store.transaction() as repos:
            if request.source_kind is TurnSourceKind.REMEDIATION:
                intent = request.remediation
                if intent is None:
                    raise CallerAuthorityError(
                        "A remediation turn requires a typed RemediationTurnIntent"
                    )
                if request.controller_id != intent.loop_id:
                    raise CallerAuthorityError(
                        "remediation controller authority must match the typed loop "
                        "identity"
                    )
                predecessor = await repos.turn_attempts.get(
                    intent.of_turn_attempt_id
                )
                if predecessor is None:
                    raise CallerAuthorityError(
                        "remediation references an unknown canonical predecessor turn"
                    )
                if not predecessor.is_terminal:
                    raise CallerAuthorityError(
                        "remediation predecessor lacks authoritative terminal evidence"
                    )
                base_session = await repos.sessions.get(predecessor.session_id)
                if base_session is None:
                    raise CallerAuthorityError(
                        "remediation predecessor session authority is missing"
                    )
                if intent.allow_same_session_reuse:
                    raise CallerAuthorityError(
                        "same-session remediation must submit against the prior "
                        "canonical session instead of allocating a new session"
                    )
                if parent_session_id != base_session.session_id:
                    raise CallerAuthorityError(
                        "remediation branch must identify its authoritative prior "
                        "canonical session"
                    )
                validate_remediation_authority(
                    base_dimensions=ImmutableSessionDimensions.from_session(
                        base_session
                    ),
                    intent=intent,
                    base_grants_publication_authority=bool(
                        (base_session.metadata or {}).get(
                            "grants_publication_authority", False
                        )
                    ),
                )
            existing_turn = await repos.turn_attempts.get_by_idempotency_key(
                request.idempotency_key
            )
            if existing_turn is not None:
                existing_session = await repos.sessions.get(existing_turn.session_id)
                command = await repos.commands.get_by_idempotency_key(
                    request.idempotency_key
                )
                if existing_session is None or command is None:
                    raise CallerAuthorityError(
                        "canonical session establishment is only partially durable"
                    )
                self._validate_redelivery(
                    request=request,
                    turn=existing_turn,
                    command=command,
                    expected_session_id=new_session_id,
                )
                claim = await repos.commands.claim_command(
                    command.command_id,
                    owner_class=owner_class,
                    claim_token=claim_token or _derive_id("claim", scope),
                )
                self._require_command_claim(claim, command.command_id)
                return TurnSubmissionOutcome(
                    session=existing_session,
                    turn_attempt=existing_turn,
                    plan=plan,
                    command=command,
                    claim=claim,
                    decision=None,
                    created=False,
                )

            # An explicit branch may start from artifact-backed evidence after
            # its source runtime/session was destroyed. While the parent is
            # still live, however, advance its revision in this transaction so
            # branch admission and parent cleanup cannot both win a race.
            if parent_session_id is not None:
                parent = await repos.sessions.get(parent_session_id)
                if (
                    parent is not None
                    and not parent.is_terminal
                    and parent.cleanup_state not in TERMINAL_CLEANUP_STATES
                ):
                    cleanup = await repos.cleanup.get(parent_session_id)
                    if cleanup is not None and cleanup.state in {
                        CLEANUP_STATE_CLAIMED,
                        CLEANUP_STATE_COMPLETE,
                    }:
                        raise CallerAuthorityError(
                            "linked branch admission lost authority to parent "
                            "cleanup; retry from durable checkpoint evidence"
                        )
                    await repos.sessions.update_lifecycle(
                        parent_session_id,
                        expected_revision=parent.revision,
                        expected_fencing_generation=parent.fencing_generation,
                        active_turn_attempt_id=parent.active_turn_attempt_id,
                    )

            session = await repos.sessions.create(
                session_id=new_session_id,
                moonmind_workflow_id=moonmind_workflow_id,
                provider=provider,
                provider_session_ref=provider_session_ref,
                chat_binding_id=new_chat_binding_id,
                moonmind_run_id=moonmind_run_id,
                step_execution_id=step_execution_id,
                moonmind_agent_run_id=moonmind_agent_run_id,
                compatibility_profile=compatibility_profile,
                provider_profile_id=provider_profile_id,
                host_binding_ref=host_binding_ref,
                host_lease_ref=host_lease_ref,
                compatibility_ref=compatibility_ref,
                image_manifest_ref=image_manifest_ref,
                intent_ref=request.metadata.get("intent_ref"),
                intent_digest=request.requested_dimensions.intent_digest,
                metadata=metadata,
            )
            if new_chat_binding_id is not None:
                await repos.chat_binding_aliases.register(
                    chat_binding_id=new_chat_binding_id,
                    session_id=new_session_id,
                )
            turn = await repos.turn_attempts.create(
                turn_attempt_id=_derive_id("turn", scope),
                session_id=new_session_id,
                idempotency_key=request.idempotency_key,
                lineage_kind="initial",
                step_execution_id=step_execution_id,
                instruction_digest=request.instruction_digest,
            )
            command = await repos.commands.record(
                command_id=_derive_id("cmd", scope),
                session_id=new_session_id,
                command_type=SUBMIT_TURN_COMMAND_TYPE,
                idempotency_key=request.idempotency_key,
                payload_digest=request.instruction_digest,
                turn_attempt_id=turn.turn_attempt_id,
                expected_session_revision=session.revision,
                fencing_generation=session.fencing_generation,
            )
            decision = await repos.decisions.append(
                decision_id=_derive_id("dec", scope),
                session_id=new_session_id,
                decision_code=f"{_DECISION_CODE_PREFIX}:{request.source_kind.value}",
                input_state_digest=scope,
                expected_revision=session.revision,
                fencing_generation=session.fencing_generation,
                reason_code="allocated_new_session",
                resulting_command_id=command.command_id,
            )
            refreshed = await repos.sessions.update_lifecycle(
                new_session_id,
                expected_revision=session.revision,
                expected_fencing_generation=session.fencing_generation,
                active_turn_attempt_id=turn.turn_attempt_id,
            )
            claim = await repos.commands.claim_command(
                command.command_id,
                owner_class=owner_class,
                claim_token=claim_token or _derive_id("claim", scope),
            )
            self._require_command_claim(claim, command.command_id)
        return TurnSubmissionOutcome(
            session=refreshed,
            turn_attempt=turn,
            plan=plan,
            command=command,
            claim=claim,
            decision=decision,
            created=True,
        )

    @staticmethod
    def _validate_redelivery(
        *,
        request: TurnSubmissionRequest,
        turn: TurnAttemptRecord,
        command: CommandRecord,
        expected_session_id: str,
    ) -> None:
        """Reject reuse of a turn key for different immutable command input."""

        scope = idempotency_scope_for(request)
        identity_matches = all(
            (
                turn.session_id == expected_session_id,
                turn.instruction_digest == request.instruction_digest,
                turn.turn_attempt_id == _derive_id("turn", scope),
                command.session_id == expected_session_id,
                command.turn_attempt_id == turn.turn_attempt_id,
                command.payload_digest == request.instruction_digest,
                command.command_id == _derive_id("cmd", scope),
            )
        )
        if not identity_matches:
            raise TurnIdempotencyConflictError(
                "turn idempotency key was reused for a different canonical "
                "session, instruction, or submit command"
            )

    @staticmethod
    def _require_command_claim(claim: CasResult, command_id: str) -> None:
        """Fail closed unless this caller owns the fenced submit command."""

        if not claim.applied:
            raise CallerAuthorityError(
                f"canonical submit command {command_id!r} is owned by another "
                f"claimant ({claim.outcome.value})"
            )

    # -- linked branch allocation ------------------------------------------

    async def open_linked_branch(
        self,
        request: TurnSubmissionRequest,
        *,
        moonmind_workflow_id: str,
        provider: str,
        new_session_id: str,
        new_chat_binding_id: str,
        parent_session_id: Optional[str] = None,
        provider_session_ref: Optional[str] = None,
    ) -> TurnSubmissionOutcome:
        """Allocate a new canonical session for an explicit ``linked_branch``.

        Changed immutable dimensions on a reuse turn surface as
        ``BranchRequiredError``; the caller resolves that by opening a branch
        here, which gets its own canonical session row and its own chat binding
        rather than mutating the parent session.
        """

        if request.source_kind is not TurnSourceKind.LINKED_BRANCH:
            raise CallerAuthorityError(
                "open_linked_branch requires a linked_branch source kind, got "
                f"{request.source_kind.value}"
            )

        return await self.establish_session(
            request,
            moonmind_workflow_id=moonmind_workflow_id,
            provider=provider,
            new_session_id=new_session_id,
            new_chat_binding_id=new_chat_binding_id,
            provider_session_ref=provider_session_ref,
            parent_session_id=parent_session_id,
        )

    # -- command delivery and attempt terminality --------------------------

    async def record_turn_delivery(
        self,
        idempotency_key: str,
        *,
        outcome: ControlPlaneOutcome,
        owner_class: str = "session_workflow",
        claim_token: Optional[str] = None,
        provider_receipt_id: Optional[str] = None,
        result_ref: Optional[str] = None,
    ) -> TurnAttemptRecord:
        """Settle the fenced submit command and advance only its turn attempt."""

        async with self._store.transaction() as repos:
            turn = await repos.turn_attempts.get_by_idempotency_key(idempotency_key)
            command = await repos.commands.get_by_idempotency_key(idempotency_key)
            if turn is None or command is None:
                raise CallerAuthorityError(
                    f"unknown canonical turn command for {idempotency_key!r}"
                )
            token = claim_token or command.claim_token
            if not token:
                raise CallerAuthorityError(
                    f"canonical turn command {command.command_id!r} was not claimed"
                )
            delivered = await repos.commands.record_command_delivery(
                command.command_id,
                owner_class=owner_class,
                claim_token=token,
                outcome=outcome,
                provider_receipt_id=provider_receipt_id,
                result_ref=result_ref,
            )
            if delivered.outcome not in {
                ControlPlaneOutcome.APPLIED,
                ControlPlaneOutcome.ALREADY_APPLIED,
                ControlPlaneOutcome.DELIVERY_UNKNOWN,
            }:
                raise CallerAuthorityError(
                    f"canonical turn command {command.command_id!r} delivery was "
                    f"refused ({delivered.outcome.value})"
                )
            session = await repos.sessions.get(turn.session_id)
            if session is None or turn.is_terminal:
                return turn
            next_state = (
                TURN_STATE_DELIVERY_UNKNOWN
                if outcome is ControlPlaneOutcome.DELIVERY_UNKNOWN
                else TURN_STATE_ACCEPTED
            )
            advanced = await repos.turn_attempts.advance_state(
                turn.turn_attempt_id,
                next_state,
                expected_revision=turn.revision,
                expected_fencing_generation=session.fencing_generation,
                provider_turn_id=provider_receipt_id,
            )
            return advanced

    async def record_turn_terminal(
        self,
        idempotency_key: str,
        *,
        terminal_state: str,
        attempt_outcome: Optional[str] = None,
        terminal_evidence_ref: Optional[str] = None,
    ) -> TurnAttemptRecord:
        """Record attempt terminality without inferring session terminality."""

        async with self._store.transaction() as repos:
            turn = await repos.turn_attempts.get_by_idempotency_key(idempotency_key)
            if turn is None:
                raise CallerAuthorityError(
                    f"unknown canonical turn attempt for {idempotency_key!r}"
                )
            session = await repos.sessions.get(turn.session_id)
            if session is None:
                raise CallerAuthorityError(
                    f"canonical session {turn.session_id!r} is missing"
                )
            return await repos.turn_attempts.mark_terminal(
                turn.turn_attempt_id,
                terminal_state,
                expected_revision=turn.revision,
                expected_fencing_generation=session.fencing_generation,
                attempt_outcome=attempt_outcome,
                terminal_evidence_ref=terminal_evidence_ref,
            )

    async def mark_session_terminal(
        self,
        session_id: str,
        *,
        terminal_state: str,
        terminal_evidence_ref: Optional[str] = None,
    ) -> SessionRecord:
        """Record authoritative provider-session terminality separately."""

        async with self._store.transaction() as repos:
            session = await repos.sessions.get(session_id)
            if session is None:
                raise CallerAuthorityError(
                    f"unknown canonical session {session_id!r}"
                )
            return await repos.sessions.mark_terminal(
                session_id,
                terminal_state,
                expected_revision=session.revision,
                expected_fencing_generation=session.fencing_generation,
                terminal_evidence_ref=terminal_evidence_ref,
            )

    async def claim_cleanup(
        self,
        session_id: str,
        *,
        owner_class: str,
        claim_token: str,
        activity: Optional[LifecycleActivity] = None,
    ) -> CasResult:
        """Claim cleanup only after every accepted turn is terminal."""

        activity = activity or LifecycleActivity()
        async with self._store.transaction() as repos:
            session = await repos.sessions.get(session_id)
            if session is None:
                raise CallerAuthorityError(
                    f"unknown canonical session {session_id!r}"
                )
            session_turns = await repos.turn_attempts.list_for_session(session_id)
            workflow_sessions = await repos.sessions.list_for_workflow(
                session.moonmind_workflow_id,
                moonmind_run_id=session.moonmind_run_id,
            )
            parent_session_id = str(
                (session.metadata or {}).get("branched_from_session_id") or ""
            ).strip()
            related_session_ids = {
                candidate.session_id
                for candidate in workflow_sessions
                if candidate.session_id != session_id
                and (
                    (candidate.metadata or {}).get("branched_from_session_id")
                    == session_id
                    or (
                        parent_session_id
                        and candidate.session_id == parent_session_id
                    )
                )
            }
            related_turn_active = False
            for related_session_id in related_session_ids:
                related_turns = await repos.turn_attempts.list_for_session(
                    related_session_id
                )
                if any(not turn.is_terminal for turn in related_turns):
                    related_turn_active = True
                    break
            effective_activity = replace(
                activity,
                # The session's active-turn pointer is a current projection, not
                # proof that older admitted attempts settled. Cleanup must fence
                # on every non-terminal attempt so overlapping chat/steering or
                # continuation work cannot be hidden by a newer pointer.
                active_turn=activity.active_turn
                or any(not turn.is_terminal for turn in session_turns),
                linked_continuation_active=(
                    activity.linked_continuation_active or related_turn_active
                ),
            )
            from .turn_contract import (
                CleanupDisposition,
                CleanupFenceError,
                CleanupOperation,
                evaluate_cleanup_admission,
            )

            decision = evaluate_cleanup_admission(
                operation=CleanupOperation.HOST_CLEANUP,
                activity=effective_activity,
            )
            if decision.disposition is CleanupDisposition.FENCE:
                raise CleanupFenceError(
                    f"cleanup for {session_id!r} is fenced: {decision.reason}"
                )
            claimed = await repos.cleanup.claim_cleanup(
                session_id,
                owner_class=owner_class,
                claim_token=claim_token,
            )
            if not claimed.applied:
                raise CleanupFenceError(
                    f"cleanup for {session_id!r} is owned by another claimant "
                    f"({claimed.outcome.value})"
                )
            if claimed.applied and session.cleanup_state != "in_progress":
                await repos.sessions.update_lifecycle(
                    session_id,
                    expected_revision=session.revision,
                    expected_fencing_generation=session.fencing_generation,
                    cleanup_state="in_progress",
                )
            return claimed

    async def complete_cleanup(
        self,
        session_id: str,
        *,
        generation: int,
        owner_class: str,
        claim_token: str,
    ) -> CasResult:
        """Complete cleanup under the exact canonical lease/session fences."""

        async with self._store.transaction() as repos:
            result = await repos.cleanup.complete_cleanup(
                session_id,
                generation=generation,
                owner_class=owner_class,
                claim_token=claim_token,
                session_repository=repos.sessions,
            )
            if not result.applied:
                raise CleanupFenceError(
                    f"cleanup completion for {session_id!r} was fenced "
                    f"({result.outcome.value})"
                )
            if result.applied:
                session = await repos.sessions.get(session_id)
                if session is not None and session.cleanup_state != "complete":
                    await repos.sessions.update_lifecycle(
                        session_id,
                        expected_revision=session.revision,
                        expected_fencing_generation=session.fencing_generation,
                        cleanup_state="complete",
                        historical_read_state="archived",
                    )
            return result

    async def release_cleanup_claim(
        self,
        session_id: str,
        *,
        generation: int,
        owner_class: str,
        claim_token: str,
    ) -> CasResult:
        """Release a canonical claim when the host cleanup CAS did not win."""

        async with self._store.transaction() as repos:
            result = await repos.cleanup.release_cleanup_claim(
                session_id,
                generation=generation,
                owner_class=owner_class,
                claim_token=claim_token,
            )
            if not result.applied:
                raise CleanupFenceError(
                    f"cleanup claim release for {session_id!r} was fenced "
                    f"({result.outcome.value})"
                )
            session = await repos.sessions.get(session_id)
            if session is not None and session.cleanup_state == "in_progress":
                await repos.sessions.update_lifecycle(
                    session_id,
                    expected_revision=session.revision,
                    expected_fencing_generation=session.fencing_generation,
                    cleanup_state="pending",
                )
            return result

    # -- chat capability ----------------------------------------------------

    async def resolve_chat_capability(
        self, chat_binding_id: str, *, caller_authorized: bool
    ) -> ChatCapabilityDecision:
        """Derive browser-visible chat capability from canonical authority.

        The chat binding resolves only to the canonical session; an individual
        terminal attempt never downgrades write capability while the session is
        active, and final *session* terminality flips the same binding to
        read-only. Historical diagnostic reads survive provider/host cleanup.
        This is the identical authority the server uses to enforce mutation
        requests, so capability and enforcement can never disagree.
        """

        async with self._store.transaction() as repos:
            alias: Optional[ChatBindingAliasRecord] = (
                await repos.chat_binding_aliases.resolve(chat_binding_id)
            )
            session: Optional[SessionRecord] = None
            active_turn: Optional[TurnAttemptRecord] = None
            if alias is not None and alias.resolves and alias.session_id is not None:
                session = await repos.sessions.get(alias.session_id)
                if session is not None and session.active_turn_attempt_id is not None:
                    active_turn = await repos.turn_attempts.get(
                        session.active_turn_attempt_id
                    )
        return derive_chat_capability(
            alias=alias,
            session=session,
            caller_authorized=caller_authorized,
            active_turn=active_turn,
        )

    # -- checkpoint recovery ------------------------------------------------

    async def decide_session_recovery(
        self,
        session_id: str,
        *,
        recovery_idempotency_key: str,
        intent_dimensions: ImmutableSessionDimensions,
        live_authority: RecoveryEvidence,
    ) -> RecoveryDecision:
        """Resolve the one typed recovery decision for a canonical session.

        The session's own immutable dimensions gate ``branch_required``; the
        supplied ``live_authority`` evidence (profile lease, host, provider
        session, cursor, first-message, credential generation, and
        artifact-backed workspace/session evidence) decides between
        ``live_reattach``, ``cold_restore``, and ``resume_unavailable``. Cold
        restore never trusts a stale host-local path -- only the artifact-backed
        evidence flags on ``live_authority``.
        """

        if not recovery_idempotency_key.strip():
            raise CallerAuthorityError(
                "checkpoint recovery requires a durable idempotency key"
            )
        async with self._store.transaction() as repos:
            # Serialize recovery commands for the canonical session. This makes
            # a concurrent redelivery observe the winner's durable decision
            # instead of racing a second INSERT for the same idempotency key.
            session = await repos.sessions.get_for_update(session_id)
            if session is None:
                raise CallerAuthorityError(
                    f"Cannot recover unknown canonical session {session_id!r}"
                )
            evidence = RecoveryEvidence(
                intent_dimensions=intent_dimensions,
                session_dimensions=ImmutableSessionDimensions.from_session(session),
                provider_profile_lease_current=live_authority.provider_profile_lease_current,
                host_available=live_authority.host_available,
                provider_session_reachable=live_authority.provider_session_reachable,
                cursor_present=live_authority.cursor_present,
                first_message_consistent=live_authority.first_message_consistent,
                credential_generation_current=live_authority.credential_generation_current,
                workspace_artifact_valid=live_authority.workspace_artifact_valid,
                session_evidence_valid=live_authority.session_evidence_valid,
            )
            evidence_digest = compute_digest(
                {
                    "sessionId": session_id,
                    "idempotencyKey": recovery_idempotency_key,
                    "intentDimensions": intent_dimensions.__dict__,
                    "sessionDimensions": evidence.session_dimensions.__dict__,
                    "providerProfileLeaseCurrent": (
                        evidence.provider_profile_lease_current
                    ),
                    "hostAvailable": evidence.host_available,
                    "providerSessionReachable": evidence.provider_session_reachable,
                    "cursorPresent": evidence.cursor_present,
                    "firstMessageConsistent": evidence.first_message_consistent,
                    "credentialGenerationCurrent": (
                        evidence.credential_generation_current
                    ),
                    "workspaceArtifactValid": evidence.workspace_artifact_valid,
                    "sessionEvidenceValid": evidence.session_evidence_valid,
                }
            )
            decision = decide_recovery(evidence)
            # The command identity is stable for the caller-selected retry key,
            # while the input digest fences changed recovery evidence. Deriving
            # the row identity from the evidence itself would admit two
            # different decisions under one idempotency key instead of exposing
            # the conflict.
            command_scope_digest = compute_digest(
                {
                    "sessionId": session_id,
                    "idempotencyKey": recovery_idempotency_key,
                }
            )
            decision_id = _derive_id("recovery", command_scope_digest)
            existing = await repos.decisions.get(decision_id)
            if existing is not None:
                if (
                    existing.session_id != session_id
                    or existing.input_state_digest != evidence_digest
                    or existing.reason_code != decision.reason
                    or existing.product_visible_transition != decision.mode.value
                ):
                    raise TurnIdempotencyConflictError(
                        "checkpoint recovery idempotency key conflicts with its "
                        "durable canonical decision"
                    )
                return decision
            await repos.decisions.append(
                decision_id=decision_id,
                session_id=session_id,
                decision_code=f"checkpoint_recovery:{decision.mode.value}",
                input_state_digest=evidence_digest,
                expected_revision=session.revision,
                fencing_generation=session.fencing_generation,
                reason_code=decision.reason,
                product_visible_transition=decision.mode.value,
            )
            return decision


def _derive_id(prefix: str, scope: str) -> str:
    """Deterministic id derived from the turn's idempotency scope.

    Determinism keeps redelivery and replay stable: the same logical turn always
    maps to the same attempt/command/decision ids.
    """

    return f"{prefix}-{compute_digest(scope)[:40]}"


__all__ = [
    "SUBMIT_TURN_COMMAND_TYPE",
    "TurnSubmissionOutcome",
    "OmnigentTurnService",
]
