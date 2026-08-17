"""The pure Omnigent lifecycle reducer.

Tracks MoonLadderStudios/MoonMind#3702 ([Omnigent control plane 1/11]).

:func:`reconcile` converts immutable intent, current durable state, and
authoritative observations into a single explicit
:class:`~moonmind.omnigent.reconciler.decision.ReconciliationDecision`.

It is the common decision boundary for provider events, snapshots, retries,
reconnects, host and lease state, terminal evidence, cleanup, and ambiguous
recovery. It performs **no** database, network, filesystem, Docker, artifact,
logging, telemetry, or Temporal calls, and is deterministic for equal inputs
(invariant 12). ``now`` is supplied by the caller; nothing here reads the clock.

The reducer never trusts caller-supplied identity carried on an observation
(invariant 11): identity always comes from :class:`DurableSessionState`, and a
contradicting observation identity quarantines rather than being adopted.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from moonmind.omnigent.reconciler.decision import (
    CommandSpec,
    EvidenceRequirement,
    ReconciliationDecision,
)
from moonmind.omnigent.reconciler.models import (
    CompiledSessionIntent,
    DurableSessionState,
    ObservationSet,
)
from moonmind.omnigent.reconciler.versions import ReconcilerContractError
from moonmind.omnigent.reconciler.vocabulary import (
    DecisionAction,
    DesiredLifecycle,
    DurablePhase,
    ReasonCode,
    StatusKind,
    TurnSubmissionState,
    classify_compatibility,
    is_terminal_recorded_or_beyond,
    normalize_provider_status,
    normalize_turn_status,
)

# External wait authority label used when the reducer hands control to an
# operator/other authority (quarantine) rather than a timed re-reconcile.
_OPERATOR_REVIEW = "operator_review"


def reconcile(
    *,
    intent: CompiledSessionIntent,
    durable: DurableSessionState,
    observations: ObservationSet,
    now: datetime,
) -> ReconciliationDecision:
    """Return the single explicit decision for the given inputs.

    Pure and deterministic: equal inputs always produce an equal decision.
    """

    # Authority contract: the intent and durable state must agree on canonical
    # identity. This is a structural caller error, not runtime ambiguity.
    if intent.session_id != durable.session_id:
        raise ReconcilerContractError(
            "intent.session_id does not match durable.session_id; "
            "the reducer never reconciles across canonical identities"
        )

    ctx = _Context(intent=intent, durable=durable, observations=observations, now=now)

    # Off-ladder holding / closed states first.
    if durable.phase is DurablePhase.FAILED:
        return ctx.terminal_no_op(ReasonCode.SESSION_ALREADY_FAILED)
    if durable.phase is DurablePhase.CLOSED:
        return ctx.terminal_no_op(ReasonCode.SESSION_ALREADY_CLOSED)
    if durable.phase is DurablePhase.QUARANTINED:
        # Remain quarantined until an external authority resolves the ambiguity.
        return ctx.quarantine(ReasonCode.QUARANTINE_UNRESOLVED)

    # Invariant 11: never adopt a provider-session identity from an observation
    # that contradicts durable authority.
    if ctx.provider_identity_conflict():
        return ctx.quarantine(ReasonCode.OBSERVATION_IDENTITY_MISMATCH)

    # Invariant 6: unknown provider status / compatibility vocabulary fails
    # closed to quarantine rather than being silently mapped to success.
    unknown = ctx.unknown_vocabulary_quarantine()
    if unknown is not None:
        return unknown
    incompatible = ctx.runtime_incompatible()
    if incompatible is not None:
        return incompatible

    # Operator abort short-circuit: drive toward terminal + cleanup.
    if durable.desired is DesiredLifecycle.TERMINATED and not is_terminal_recorded_or_beyond(
        durable.phase
    ):
        return ctx.record_terminal(
            action=DecisionAction.RECORD_PROVIDER_TERMINAL,
            status="canceled",
            reason=ReasonCode.OPERATOR_REQUESTED_TERMINATION,
        )

    phase = durable.phase
    if phase is DurablePhase.PENDING:
        return ctx.ensure(
            DecisionAction.ENSURE_PROFILE_LEASE, ReasonCode.NEED_PROFILE_LEASE
        )
    if phase is DurablePhase.PROFILE_LEASED:
        return ctx.ensure(DecisionAction.ENSURE_HOST, ReasonCode.NEED_HOST)
    if phase is DurablePhase.HOST_READY:
        return ctx.ensure(
            DecisionAction.ENSURE_PROVIDER_SESSION, ReasonCode.NEED_PROVIDER_SESSION
        )
    if phase is DurablePhase.PROVIDER_SESSION_OPEN:
        return ctx.decide_submission()
    if phase is DurablePhase.TURN_IN_FLIGHT:
        return ctx.decide_in_flight()
    if phase is DurablePhase.TERMINAL_RECORDED:
        return ctx.decide_after_terminal()
    if phase is DurablePhase.EVIDENCE_HARVESTED:
        return ctx.decide_cleanup_start()
    if phase is DurablePhase.CLEANUP_STARTED:
        return ctx.decide_release()
    if phase is DurablePhase.LEASES_RELEASED:
        return ctx.terminal_no_op(ReasonCode.SESSION_CLOSED_AFTER_RELEASE)

    # Unreachable: every DurablePhase is handled above.
    raise ReconcilerContractError(f"Unhandled durable phase {phase!r}")


class _Context:
    """Bundles the inputs and the decision-construction helpers.

    Keeping helpers on a small context object avoids threading four parameters
    through every branch while keeping the module a pure function of its inputs.
    """

    __slots__ = ("intent", "durable", "obs", "now")

    def __init__(
        self,
        *,
        intent: CompiledSessionIntent,
        durable: DurableSessionState,
        observations: ObservationSet,
        now: datetime,
    ) -> None:
        self.intent = intent
        self.durable = durable
        self.obs = observations
        self.now = now

    # --- deadline / identity helpers ---------------------------------------

    def _deadline(self, *, factor: int = 1) -> datetime:
        seconds = min(
            self.intent.reconcile_interval_seconds * factor,
            self.intent.max_reconcile_interval_seconds,
        )
        return self.now + timedelta(seconds=seconds)

    def _command_id(self, action: DecisionAction) -> str:
        attempt = self.durable.turn_attempt
        attempt_key = attempt.attempt_id if attempt is not None else "-"
        return ":".join(
            (
                self.durable.session_id,
                str(self.durable.fencing_generation),
                action.value,
                self.durable.phase.value,
                attempt_key,
            )
        )

    def provider_identity_conflict(self) -> bool:
        ps = self.obs.provider_session
        if ps.is_present and self.durable.provider_session_id:
            return ps.value.provider_session_id != self.durable.provider_session_id
        return False

    # --- decision constructors ---------------------------------------------

    def _decision(
        self,
        action: DecisionAction,
        reasons: tuple[ReasonCode, ...],
        *,
        terminal: bool,
        product_visible: bool,
        command: CommandSpec | None = None,
        next_deadline: datetime | None = None,
        wait_authority: str | None = None,
        evidence: tuple[EvidenceRequirement, ...] = (),
    ) -> ReconciliationDecision:
        return ReconciliationDecision(
            action=action,
            reason_codes=reasons,
            expected_revision=self.durable.revision,
            expected_fencing_generation=self.durable.fencing_generation,
            changes_product_visible_state=product_visible,
            terminal=terminal,
            command=command,
            next_deadline=next_deadline,
            wait_authority=wait_authority,
            evidence_requirements=evidence,
        )

    def terminal_no_op(self, reason: ReasonCode) -> ReconciliationDecision:
        return self._decision(
            DecisionAction.NO_OP,
            (reason,),
            terminal=True,
            product_visible=False,
        )

    def quarantine(self, *reasons: ReasonCode) -> ReconciliationDecision:
        # Nonterminal: control is handed to an external authority to resolve.
        return self._decision(
            DecisionAction.QUARANTINE_AMBIGUOUS_STATE,
            reasons,
            terminal=False,
            product_visible=False,
            next_deadline=self._deadline(),
            wait_authority=_OPERATOR_REVIEW,
        )

    def await_observation(self, *reasons: ReasonCode) -> ReconciliationDecision:
        return self._decision(
            DecisionAction.AWAIT_OBSERVATION,
            reasons,
            terminal=False,
            product_visible=False,
            next_deadline=self._deadline(),
        )

    def retry_transient(self, *reasons: ReasonCode) -> ReconciliationDecision:
        return self._decision(
            DecisionAction.RETRY_TRANSIENT_OBSERVATION,
            reasons,
            terminal=False,
            product_visible=False,
            next_deadline=self._deadline(),
        )

    def ensure(
        self, action: DecisionAction, reason: ReasonCode
    ) -> ReconciliationDecision:
        return self._decision(
            action,
            (reason,),
            terminal=False,
            product_visible=False,
            command=CommandSpec(kind=action.value, command_id=self._command_id(action)),
            next_deadline=self._deadline(),
        )

    def fail_nonretryable(self, *reasons: ReasonCode) -> ReconciliationDecision:
        return self._decision(
            DecisionAction.FAIL_NONRETRYABLE,
            reasons,
            terminal=True,
            product_visible=True,
        )

    def submit_turn(self, reason: ReasonCode) -> ReconciliationDecision:
        action = DecisionAction.SUBMIT_TURN
        return self._decision(
            action,
            (reason,),
            terminal=False,
            product_visible=True,
            command=CommandSpec(kind=action.value, command_id=self._command_id(action)),
            next_deadline=self._deadline(),
        )

    def record_terminal(
        self,
        *,
        action: DecisionAction,
        status: str,
        reason: ReasonCode,
        extra_reasons: tuple[ReasonCode, ...] = (),
    ) -> ReconciliationDecision:
        return self._decision(
            action,
            (reason, *extra_reasons),
            terminal=False,  # session becomes terminal only once durably recorded
            product_visible=True,
            command=CommandSpec(
                kind=action.value,
                command_id=self._command_id(action),
                parameters=(("terminal_status", status),),
            ),
            next_deadline=self._deadline(),
            evidence=(EvidenceRequirement(name="terminal_status", satisfied=True),),
        )

    # --- vocabulary / compatibility gates ----------------------------------

    def unknown_vocabulary_quarantine(self) -> ReconciliationDecision | None:
        ps = self.obs.provider_session
        if ps.is_present:
            kind, _ = normalize_provider_status(ps.value.raw_status)
            if kind is StatusKind.UNKNOWN:
                return self.quarantine(ReasonCode.UNKNOWN_PROVIDER_STATUS)
        turn = self.obs.provider_turn
        if turn.is_present:
            kind, _ = normalize_turn_status(turn.value.raw_status)
            if kind is StatusKind.UNKNOWN:
                return self.quarantine(ReasonCode.UNKNOWN_PROVIDER_STATUS)
        rr = self.obs.runtime_readiness
        if rr.is_present and classify_compatibility(rr.value.raw_compatibility) == "unknown":
            return self.quarantine(ReasonCode.UNKNOWN_COMPATIBILITY_VOCABULARY)
        return None

    def runtime_incompatible(self) -> ReconciliationDecision | None:
        rr = self.obs.runtime_readiness
        if rr.is_present and classify_compatibility(rr.value.raw_compatibility) == (
            "incompatible"
        ):
            return self.fail_nonretryable(ReasonCode.RUNTIME_INCOMPATIBLE)
        return None

    # --- phase branches ----------------------------------------------------

    def decide_submission(self) -> ReconciliationDecision:
        attempt = self.durable.turn_attempt
        if attempt is None or attempt.submission_state is TurnSubmissionState.NOT_SUBMITTED:
            if attempt is not None and attempt.attempt_number > self.intent.max_turn_attempts:
                return self.fail_nonretryable(ReasonCode.ATTEMPTS_EXHAUSTED)
            return self.submit_turn(ReasonCode.SUBMIT_FIRST_TURN)
        if attempt.submission_state is TurnSubmissionState.AMBIGUOUS:
            # Invariant 7: a durably-ambiguous submission is never re-issued;
            # the reducer waits for a snapshot to disambiguate delivery.
            return self.await_observation(ReasonCode.AMBIGUOUS_SUBMISSION_AWAIT)
        # SUBMITTED / ATTEMPT_* at this phase: evaluate as in-flight.
        return self.decide_in_flight()

    def decide_in_flight(self) -> ReconciliationDecision:
        obs = self.obs
        turn = obs.provider_turn
        frontier = obs.event_frontier
        session = obs.provider_session

        active_tool = turn.is_present and turn.value.has_active_tool_call
        pending_tool = frontier.is_present and frontier.value.has_pending_tool_call

        # 1) An explicit terminal event was observed on the frontier.
        terminal_event = (
            frontier.value.terminal_status if frontier.is_present else None
        )
        if terminal_event is not None:
            if active_tool or pending_tool:
                # Contradiction: provider signalled terminal while a tool call
                # is still open. Do not record terminal yet (invariant 3).
                return self.await_observation(
                    ReasonCode.TERMINAL_EVENT_PENDING_TOOL_CALL
                )
            return self.record_terminal(
                action=DecisionAction.RECORD_PROVIDER_TERMINAL,
                status=terminal_event,
                reason=ReasonCode.PROVIDER_TERMINAL_EVENT,
            )

        # Invariant 3: provider idle / incomplete work with an active or pending
        # tool call is never sufficient terminal evidence.
        if active_tool or pending_tool:
            return self.await_observation(ReasonCode.ACTIVE_TOOL_CALL_NOT_TERMINAL)

        # 2) Recover a lost terminal edge from snapshot + transcript evidence
        #    (invariant 2; reproduces #3698 and #3683).
        if turn.is_present:
            status, extra = self._synthesizable_status(turn.value)
            if status is not None:
                if status == "failed":
                    retry = self._maybe_retry_attempt()
                    if retry is not None:
                        return retry
                return self.record_terminal(
                    action=DecisionAction.SYNTHESIZE_TERMINAL_FROM_SNAPSHOT,
                    status=status,
                    reason=ReasonCode.SNAPSHOT_TERMINAL_EVIDENCE,
                    extra_reasons=extra,
                )
            if not turn.value.response_recorded:
                return self.await_observation(ReasonCode.AWAITING_RESPONSE_RECORD)
            # Response recorded but no terminal status derivable yet.
            return self.await_observation(ReasonCode.AWAITING_RESPONSE_RECORD)

        # 3) No turn snapshot: distinguish "not fetched" from "fetch failed".
        if turn.is_negative:
            return self.retry_transient(ReasonCode.TURN_SNAPSHOT_UNAVAILABLE)
        # session snapshot present & terminal but transcript absent -> still wait
        if session.is_present:
            kind, _ = normalize_provider_status(session.value.raw_status)
            if kind is StatusKind.TERMINAL:
                return self.await_observation(
                    ReasonCode.TURN_SNAPSHOT_ABSENT,
                    ReasonCode.AWAITING_RESPONSE_RECORD,
                )
        return self.await_observation(ReasonCode.TURN_SNAPSHOT_ABSENT)

    def _synthesizable_status(self, turn) -> tuple[str | None, tuple[ReasonCode, ...]]:
        """Derive a terminal status from snapshot + transcript, if sufficient.

        Returns ``(None, ())`` when evidence is insufficient. Requires a recorded
        response and no open tool call (already checked by the caller).
        """

        if not turn.response_recorded:
            return None, ()
        session = self.obs.provider_session
        if session.is_present:
            kind, norm = normalize_provider_status(session.value.raw_status)
            if kind is StatusKind.TERMINAL:
                return norm, ()
            # Provider idle after completed, recorded work with no open tool
            # call: treat as completed (invariant 3 inverse; reproduces #3683).
            if norm == "idle":
                return "completed", (ReasonCode.IDLE_WITH_COMPLETED_WORK,)
        tkind, tnorm = normalize_turn_status(turn.raw_status)
        if tkind is StatusKind.TERMINAL:
            mapped = "completed" if tnorm == "completed" else "failed"
            return mapped, ()
        return None, ()

    def _maybe_retry_attempt(self) -> ReconciliationDecision | None:
        """Retry a failed *attempt* without recording *session* terminal.

        Invariant 4: attempt terminality is distinct from session terminality.
        A confirmed attempt failure with retries remaining submits a *new*
        attempt rather than sealing the canonical session.
        """

        attempt = self.durable.turn_attempt
        if attempt is None or attempt.retries_remaining <= 0:
            return None
        if attempt.attempt_number >= self.intent.max_turn_attempts:
            return None
        return self.submit_turn(ReasonCode.SUBMIT_RETRY_ATTEMPT)

    def decide_after_terminal(self) -> ReconciliationDecision:
        # Invariant 5: a late nonterminal observation cannot move a terminal
        # session backward; we note it but keep driving forward.
        extra = self._late_nonterminal_reasons()
        # Invariant 9: harvest evidence before any cleanup can begin.
        return self._decision(
            DecisionAction.HARVEST_EVIDENCE,
            (ReasonCode.HARVEST_TERMINAL_EVIDENCE, *extra),
            terminal=False,
            product_visible=False,
            command=CommandSpec(
                kind=DecisionAction.HARVEST_EVIDENCE.value,
                command_id=self._command_id(DecisionAction.HARVEST_EVIDENCE),
            ),
            next_deadline=self._deadline(),
            evidence=(
                EvidenceRequirement(
                    name="terminal_evidence",
                    satisfied=self._terminal_evidence_available(),
                ),
            ),
        )

    def decide_cleanup_start(self) -> ReconciliationDecision:
        extra = self._late_nonterminal_reasons()
        if not self.intent.requires_cleanup:
            return self._decision(
                DecisionAction.RELEASE_LEASES,
                (ReasonCode.RELEASE_IDLE_LEASES, *extra),
                terminal=False,
                product_visible=False,
                command=CommandSpec(
                    kind=DecisionAction.RELEASE_LEASES.value,
                    command_id=self._command_id(DecisionAction.RELEASE_LEASES),
                ),
                next_deadline=self._deadline(),
            )
        return self._decision(
            DecisionAction.BEGIN_CLEANUP,
            (ReasonCode.CLEANUP_AFTER_EVIDENCE, *extra),
            terminal=False,
            product_visible=False,
            command=CommandSpec(
                kind=DecisionAction.BEGIN_CLEANUP.value,
                command_id=self._command_id(DecisionAction.BEGIN_CLEANUP),
            ),
            next_deadline=self._deadline(),
        )

    def decide_release(self) -> ReconciliationDecision:
        # Invariant 8: leases cannot be released while any credential or host
        # consumer is still observed or durably owned.
        if self._lease_consumers_active():
            return self.await_observation(ReasonCode.LEASE_CONSUMERS_ACTIVE)
        return self._decision(
            DecisionAction.RELEASE_LEASES,
            (ReasonCode.RELEASE_IDLE_LEASES,),
            terminal=False,
            product_visible=False,
            command=CommandSpec(
                kind=DecisionAction.RELEASE_LEASES.value,
                command_id=self._command_id(DecisionAction.RELEASE_LEASES),
            ),
            next_deadline=self._deadline(),
        )

    # --- observation predicates --------------------------------------------

    def _late_nonterminal_reasons(self) -> tuple[ReasonCode, ...]:
        frontier = self.obs.event_frontier
        session = self.obs.provider_session
        late = False
        if frontier.is_present and frontier.value.terminal_status is None:
            late = True
        if session.is_present:
            kind, _ = normalize_provider_status(session.value.raw_status)
            if kind is StatusKind.NONTERMINAL:
                late = True
        return (ReasonCode.LATE_NONTERMINAL_AFTER_TERMINAL,) if late else ()

    def _terminal_evidence_available(self) -> bool:
        ev = self.obs.evidence
        return bool(ev.is_present and ev.value.terminal_evidence_available)

    def _lease_consumers_active(self) -> bool:
        # An observed active consumer (live lease consumer or a ready host
        # runner) blocks release outright.
        leases = self.obs.leases
        if leases.is_present and leases.value.active_consumers > 0:
            return True
        host = self.obs.host_runtime
        if host.is_present and host.value.registered and host.value.runner_ready:
            return True
        # No observation proving the consumer is gone: stay safe while a lease is
        # still durably owned but unobserved this cycle.
        if leases.is_absent and self.durable.host_lease_held:
            return True
        return False


__all__ = ["reconcile"]
