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

from dataclasses import dataclass
from typing import Any, Optional

from .records import (
    ChatBindingAliasRecord,
    CommandRecord,
    DecisionRecord,
    SessionRecord,
    TurnAttemptRecord,
    compute_digest,
)
from .repositories import ControlPlaneRepositories, OmnigentControlPlaneStore
from .turn_contract import (
    CallerAuthorityError,
    ChatCapabilityDecision,
    ImmutableSessionDimensions,
    LifecycleActivity,
    RecoveryDecision,
    RecoveryEvidence,
    TurnSourceKind,
    TurnSubmissionPlan,
    TurnSubmissionRequest,
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
                self._authorize_remediation(session, request)

            return await self._apply_plan(repos, request, session, plan)

    def _authorize_remediation(
        self, session: SessionRecord, request: TurnSubmissionRequest
    ) -> None:
        intent = request.remediation
        if intent is None:
            raise CallerAuthorityError(
                "A remediation turn requires a typed RemediationTurnIntent"
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

    async def _apply_plan(
        self,
        repos: ControlPlaneRepositories,
        request: TurnSubmissionRequest,
        session: SessionRecord,
        plan: TurnSubmissionPlan,
    ) -> TurnSubmissionOutcome:
        scope = plan.idempotency_scope

        # Idempotent redelivery: the same logical turn (same scope) never
        # allocates a second attempt or a second command.
        existing = await repos.turn_attempts.get_by_idempotency_key(scope)
        if existing is not None:
            command = await repos.commands.get_by_idempotency_key(scope)
            assert command is not None
            return TurnSubmissionOutcome(
                session=session,
                turn_attempt=existing,
                plan=plan,
                command=command,
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
            idempotency_key=scope,
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
            idempotency_key=scope,
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
        )
        # Point the canonical session's active turn at the new attempt without
        # touching its terminality: an attempt returning is not session
        # terminality.
        refreshed = await repos.sessions.update_lifecycle(
            session.session_id,
            active_turn_attempt_id=turn.turn_attempt_id,
        )
        return TurnSubmissionOutcome(
            session=refreshed,
            turn_attempt=turn,
            plan=plan,
            command=command,
            decision=decision,
            created=True,
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

        plan = plan_turn_submission(request, None)
        scope = plan.idempotency_scope
        metadata: dict[str, Any] = dict(request.metadata)
        metadata.update(request.requested_dimensions.as_session_metadata())
        if parent_session_id is not None:
            metadata["branched_from_session_id"] = parent_session_id

        async with self._store.transaction() as repos:
            await repos.sessions.create(
                session_id=new_session_id,
                moonmind_workflow_id=moonmind_workflow_id,
                provider=provider,
                provider_session_ref=provider_session_ref,
                chat_binding_id=new_chat_binding_id,
                intent_digest=request.requested_dimensions.intent_digest,
                metadata=metadata,
            )
            await repos.chat_binding_aliases.register(
                chat_binding_id=new_chat_binding_id, session_id=new_session_id
            )
            turn = await repos.turn_attempts.create(
                turn_attempt_id=_derive_id("turn", scope),
                session_id=new_session_id,
                idempotency_key=scope,
                lineage_kind=plan.lineage_kind,
                instruction_digest=request.instruction_digest,
            )
            command = await repos.commands.record(
                command_id=_derive_id("cmd", scope),
                session_id=new_session_id,
                command_type=SUBMIT_TURN_COMMAND_TYPE,
                idempotency_key=scope,
                payload_digest=request.instruction_digest,
                turn_attempt_id=turn.turn_attempt_id,
            )
            decision = await repos.decisions.append(
                decision_id=_derive_id("dec", scope),
                session_id=new_session_id,
                decision_code=f"{_DECISION_CODE_PREFIX}:{request.source_kind.value}",
                input_state_digest=scope,
                reason_code="allocated_new_session",
                resulting_command_id=command.command_id,
            )
            refreshed = await repos.sessions.update_lifecycle(
                new_session_id, active_turn_attempt_id=turn.turn_attempt_id
            )
        return TurnSubmissionOutcome(
            session=refreshed,
            turn_attempt=turn,
            plan=plan,
            command=command,
            decision=decision,
            created=True,
        )

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

        async with self._store.transaction() as repos:
            session = await repos.sessions.get(session_id)
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
        return decide_recovery(evidence)


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
