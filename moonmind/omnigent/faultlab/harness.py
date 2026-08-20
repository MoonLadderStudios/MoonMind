"""Execution harness that drives the production reducer under injected faults.

Source issue: MoonLadderStudios/MoonMind#3709.

The harness plays a :class:`FaultPlan` against the *production* reconciler
(``moonmind.omnigent.reconciler.reconcile``) using the programmable fake provider
as the world. It injects transport, observation, and crash faults, applies each
decision like a deterministic executor, records a decision journal and the
provider ledger, and cross-checks the emitted commands against the independent
:class:`ReferenceModel`.

Faults are bounded: after ``recovery_round`` the world reports the ground truth
honestly, which is the precondition that makes eventual convergence decidable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum

from moonmind.omnigent.reconciler import (
    CompiledSessionIntent,
    DecisionKind,
    DurableSessionState,
    EventFrontierObservation,
    EvidenceObservation,
    HostObservation,
    LeaseObservation,
    LeaseState,
    ObservationSet,
    ProviderSessionObservation,
    ReconciliationDecision,
    SessionLifecyclePhase,
    SubmissionState,
    current_phase,
    reconcile,
)

from .provider import ProgrammableFakeProvider
from .reference_model import ReferenceCommand, ReferenceModel
from .scenario import CommandWindow, LogicalOperation, ResponseBehavior, SideEffect

_EPOCH = datetime(2026, 8, 18, 12, 0, 0, tzinfo=timezone.utc)

_TERMINAL_STATUS = {
    "success": "completed",
    "failure": "failed",
    "cancelled": "cancelled",
}


class ObservationFault(str, Enum):
    """A fault applied to the observations surfaced in a poll round."""

    HONEST = "honest"
    DROP_SNAPSHOT = "drop_snapshot"
    STALE_SESSION = "stale_session"
    UNKNOWN_VOCAB = "unknown_vocab"
    RUNNING = "running"
    MISSED_EDGE = "missed_edge"
    EVIDENCE_DELAY = "evidence_delay"
    CONSUMER_ACTIVE = "consumer_active"


#: Maps production decision kinds onto the reference model's command vocabulary.
_DECISION_TO_REFERENCE: dict[DecisionKind, ReferenceCommand] = {
    DecisionKind.ENSURE_PROFILE_LEASE: ReferenceCommand.ENSURE_PROFILE_LEASE,
    DecisionKind.ENSURE_HOST: ReferenceCommand.ENSURE_HOST,
    DecisionKind.ENSURE_PROVIDER_SESSION: ReferenceCommand.ENSURE_SESSION,
    DecisionKind.SUBMIT_TURN: ReferenceCommand.SUBMIT_TURN,
    DecisionKind.RECORD_PROVIDER_TERMINAL: ReferenceCommand.RECORD_TERMINAL,
    DecisionKind.SYNTHESIZE_TERMINAL_FROM_SNAPSHOT: ReferenceCommand.RECORD_TERMINAL,
    DecisionKind.HARVEST_EVIDENCE: ReferenceCommand.HARVEST_EVIDENCE,
    DecisionKind.BEGIN_CLEANUP: ReferenceCommand.BEGIN_CLEANUP,
    DecisionKind.RELEASE_LEASES: ReferenceCommand.RELEASE_LEASES,
}

_DECISION_TO_OPERATION: dict[DecisionKind, LogicalOperation] = {
    DecisionKind.ENSURE_PROFILE_LEASE: LogicalOperation.ENSURE_PROFILE_LEASE,
    DecisionKind.ENSURE_HOST: LogicalOperation.ENSURE_HOST,
    DecisionKind.ENSURE_PROVIDER_SESSION: LogicalOperation.ENSURE_SESSION,
    DecisionKind.SUBMIT_TURN: LogicalOperation.SUBMIT_TURN,
    DecisionKind.HARVEST_EVIDENCE: LogicalOperation.HARVEST_EVIDENCE,
    DecisionKind.BEGIN_CLEANUP: LogicalOperation.BEGIN_CLEANUP,
    DecisionKind.RELEASE_LEASES: LogicalOperation.RELEASE_LEASES,
}

_NO_TRANSITION_WINDOWS = frozenset(
    {CommandWindow.BEFORE_CLAIM, CommandWindow.AFTER_CLAIM_BEFORE_SIDE_EFFECT}
)
_SIDE_EFFECT_ONLY_WINDOWS = frozenset(
    {
        CommandWindow.AFTER_SIDE_EFFECT_BEFORE_RECEIPT,
        CommandWindow.AFTER_RECEIPT_BEFORE_STATE_TRANSITION,
    }
)


@dataclass
class FaultPlan:
    """A concrete, executable fault plan (the harness's primary input).

    A plan is the programmatic sibling of the declarative
    :class:`~moonmind.omnigent.faultlab.scenario.FaultScenario`; the two round-trip
    via ``plan_to_scenario`` / ``scenario_to_plan`` so a failing plan can be
    serialized, minimized, and replayed from a seed.
    """

    seed: int = 0
    requires_profile_lease: bool = True
    requires_host: bool = True
    requires_cleanup: bool = True
    desired_cancel: bool = False
    ground_truth_terminal: str = "success"
    max_turn_attempts: int = 3
    recovery_round: int = 8
    submit_response: ResponseBehavior = ResponseBehavior.SUCCESS
    observation_faults: tuple[ObservationFault, ...] = ()
    #: Crash the *first* time each logical command runs, at the named window.
    command_crashes: dict[LogicalOperation, CommandWindow] = field(default_factory=dict)


@dataclass
class JournalEntry:
    round_index: int
    decision_kind: DecisionKind
    reason_code: str
    command_id: str | None
    fenced: bool = False
    crash_window: CommandWindow | None = None
    #: The observation fault the world injected in this round. Retaining it on the
    #: journal is what lets an invariant correlate a decision with the fault that
    #: was active when the reducer made it (e.g. a terminal recorded while unknown
    #: vocabulary was being injected, or a lease released while a consumer was
    #: still observed active) rather than only inspecting the settled final state.
    observation_fault: ObservationFault = ObservationFault.HONEST


@dataclass
class ExecutionTrace:
    """The full, bounded result of running a plan."""

    plan: FaultPlan
    journal: list[JournalEntry]
    phases: list[SessionLifecyclePhase]
    final: DurableSessionState
    provider: ProgrammableFakeProvider
    reference: ReferenceModel
    distinct_commands: list[tuple[str, DecisionKind]]
    converged: bool
    settled_kind: DecisionKind | None
    reference_violation: str | None
    crashes_fired: list[tuple[str, CommandWindow]]
    #: Every performed cleanup (``begin_cleanup``) side effect paired with the
    #: fencing generation it executed under. This is the explicit generation
    #: authority a cleanup-safety check needs to prove that a cleanup effect
    #: targeted only the currently authorized generation and not a superseded one
    #: (a stale cleanup deleting a replacement continuation's resource).
    cleanup_effects: list[tuple[str, int]] = field(default_factory=list)

    @property
    def ledger(self):
        return self.provider.ledger


def _initial_durable(plan: FaultPlan) -> DurableSessionState:
    from moonmind.omnigent.reconciler import DesiredLifecycle

    return DurableSessionState(
        session_id="session-1",
        revision=0,
        owner_token="owner-token",
        fencing_generation=1,
        desired=DesiredLifecycle.CANCEL if plan.desired_cancel else DesiredLifecycle.RUN,
    )


def _intent(plan: FaultPlan) -> CompiledSessionIntent:
    return CompiledSessionIntent(
        session_id="session-1",
        provider="omnigent",
        turn_prompt_digest="sha256:prompt",
        requires_profile_lease=plan.requires_profile_lease,
        requires_host=plan.requires_host,
        requires_cleanup=plan.requires_cleanup,
        max_turn_attempts=plan.max_turn_attempts,
        reconcile_interval_seconds=30,
    )


def _fault_for_round(plan: FaultPlan, round_index: int) -> ObservationFault:
    if round_index >= plan.recovery_round or not plan.observation_faults:
        return ObservationFault.HONEST
    return plan.observation_faults[round_index % len(plan.observation_faults)]


def _observe(
    plan: FaultPlan,
    durable: DurableSessionState,
    round_index: int,
    now: datetime,
) -> tuple[ObservationSet, ObservationFault]:
    """Build the authoritative observations for the current durable state.

    Returns the observation set and the fault that was *actually* injected this
    round. The scheduled fault only takes effect in the lifecycle phase it models
    (session-observation faults while awaiting the terminal; evidence/lease faults
    once a terminal is recorded); in any other phase no fault is applied and the
    effective fault is ``HONEST``. Callers that correlate a decision with the
    fault active in its round must use this effective fault, not the schedule, so
    e.g. a cancellation recorded at round 0 is never mistaken for acting on an
    unknown-vocabulary snapshot that was never observed.
    """

    fault = _fault_for_round(plan, round_index)
    truth_status = _TERMINAL_STATUS[plan.ground_truth_terminal]
    kwargs: dict = {}
    effective_fault = ObservationFault.HONEST

    awaiting_terminal = (
        durable.terminal_outcome is None
        and durable.submission in (SubmissionState.ACCEPTED, SubmissionState.IN_FLIGHT)
    )
    if awaiting_terminal:
        effective_fault = fault
        sid = durable.provider_session_id
        if fault == ObservationFault.DROP_SNAPSHOT:
            pass  # not observed
        elif fault == ObservationFault.STALE_SESSION:
            kwargs["provider_session"] = ProviderSessionObservation(
                observed_at=now, raw_status=truth_status, provider_session_id="other"
            )
        elif fault == ObservationFault.UNKNOWN_VOCAB:
            kwargs["provider_session"] = ProviderSessionObservation(
                observed_at=now, raw_status="frobnicate", provider_session_id=sid
            )
        elif fault == ObservationFault.RUNNING:
            kwargs["provider_session"] = ProviderSessionObservation(
                observed_at=now, raw_status="running", provider_session_id=sid
            )
        elif fault == ObservationFault.MISSED_EDGE:
            kwargs["provider_session"] = ProviderSessionObservation(
                observed_at=now, raw_status=truth_status, provider_session_id=sid
            )
            kwargs["event_frontier"] = EventFrontierObservation(
                observed_at=now, terminal_event_seen=False
            )
        else:  # honest / post-recovery
            kwargs["provider_session"] = ProviderSessionObservation(
                observed_at=now, raw_status=truth_status, provider_session_id=sid
            )
            kwargs["event_frontier"] = EventFrontierObservation(
                observed_at=now, terminal_event_seen=True
            )

    if durable.terminal_outcome is not None:
        if fault in (ObservationFault.EVIDENCE_DELAY, ObservationFault.CONSUMER_ACTIVE):
            effective_fault = fault
        if not durable.evidence_harvested or durable.terminal_evidence_ref is None:
            available = fault != ObservationFault.EVIDENCE_DELAY
            kwargs["evidence"] = EvidenceObservation(
                observed_at=now,
                terminal_evidence_available=available,
                artifacts_available=available,
            )
        consumer = fault == ObservationFault.CONSUMER_ACTIVE
        kwargs["profile_lease"] = LeaseObservation(
            observed_at=now,
            held=durable.profile_lease == LeaseState.HELD,
            consumer_active=consumer,
        )
        kwargs["host_lease"] = LeaseObservation(
            observed_at=now,
            held=durable.host_lease == LeaseState.HELD,
            consumer_active=consumer,
        )
        kwargs["host"] = HostObservation(
            observed_at=now, registered=True, runner_ready=consumer
        )

    return ObservationSet(**kwargs), effective_fault


def apply_decision(
    durable: DurableSessionState,
    decision: ReconciliationDecision,
    provider: ProgrammableFakeProvider,
    *,
    submit_response: ResponseBehavior = ResponseBehavior.SUCCESS,
    crash_window: CommandWindow | None = None,
) -> tuple[DurableSessionState, bool]:
    """Apply one command decision like a deterministic, fencing-aware executor.

    Returns ``(next_durable, fenced)``. ``fenced`` is ``True`` when the decision's
    fencing generation no longer matches durable authority, in which case no side
    effect and no durable transition occurs (fencing safety). A crash at a
    no-transition window performs no side effect and leaves durable state
    unchanged; a crash at a side-effect-only window performs the provider side
    effect but records no durable transition, so a retry must dedup it.
    """

    command = decision.command
    if command is None:
        return durable, False

    # Fencing safety (invariant 4): a decision computed against a superseded
    # generation must never mutate current authority.
    if decision.expected_fencing_generation != durable.fencing_generation:
        return durable, True

    if crash_window in _NO_TRANSITION_WINDOWS:
        return durable, False

    kind = decision.kind
    operation = _DECISION_TO_OPERATION.get(kind)
    if operation is not None:
        response = (
            submit_response
            if kind == DecisionKind.SUBMIT_TURN
            else ResponseBehavior.SUCCESS
        )
        side_effect = {
            DecisionKind.ENSURE_PROFILE_LEASE: SideEffect.CREATED,
            DecisionKind.ENSURE_HOST: SideEffect.CREATED,
            DecisionKind.ENSURE_PROVIDER_SESSION: SideEffect.CREATED,
            DecisionKind.SUBMIT_TURN: SideEffect.ACCEPTED,
            DecisionKind.HARVEST_EVIDENCE: SideEffect.RECORDED,
            DecisionKind.BEGIN_CLEANUP: SideEffect.REMOVED,
            DecisionKind.RELEASE_LEASES: SideEffect.RELEASED,
        }[kind]
        result = provider.call(
            operation,
            idempotency_key=command.command_id,
            payload={"kind": kind.value},
            side_effect=side_effect,
            response=response,
        )
    else:
        # Terminal recording/synthesis is a MoonMind durable op; record it in the
        # ledger under the command id for at-most-once evidence too.
        result = provider.call(
            LogicalOperation.OBSERVE_SNAPSHOT,
            idempotency_key=command.command_id,
            payload={"kind": kind.value},
            side_effect=SideEffect.RECORDED,
            response=ResponseBehavior.SUCCESS,
        )

    if crash_window in _SIDE_EFFECT_ONLY_WINDOWS:
        # Side effect performed but no durable transition recorded.
        return durable, False

    update: dict = {"revision": durable.revision + 1}
    if kind == DecisionKind.ENSURE_PROFILE_LEASE:
        update["profile_lease"] = LeaseState.HELD
    elif kind == DecisionKind.ENSURE_HOST:
        update["host_lease"] = LeaseState.HELD
    elif kind == DecisionKind.ENSURE_PROVIDER_SESSION:
        update["provider_session_attached"] = True
        update["provider_session_id"] = "provider-session-1"
        update["attempt_id"] = "attempt-1"
    elif kind == DecisionKind.SUBMIT_TURN:
        update["turn_attempts"] = durable.turn_attempts + 1
        update["submission"] = (
            SubmissionState.ACCEPTED if result.delivered else SubmissionState.IN_FLIGHT
        )
    elif kind in (
        DecisionKind.RECORD_PROVIDER_TERMINAL,
        DecisionKind.SYNTHESIZE_TERMINAL_FROM_SNAPSHOT,
    ):
        # The reducer carries the observed outcome on the command so the executor
        # records the correct durable terminal without re-deriving it. If the
        # command carries no outcome, record nothing durable rather than guessing.
        if command.terminal_outcome is not None:
            update["terminal_outcome"] = command.terminal_outcome
    elif kind == DecisionKind.HARVEST_EVIDENCE:
        update["evidence_harvested"] = True
        update["terminal_evidence_ref"] = "evref-1"
    elif kind == DecisionKind.BEGIN_CLEANUP:
        update["cleanup_started"] = True
        update["cleanup_complete"] = True
    elif kind == DecisionKind.RELEASE_LEASES:
        update["profile_lease"] = LeaseState.RELEASED
        update["host_lease"] = LeaseState.RELEASED

    return durable.model_copy(update=update), False


def run_plan(plan: FaultPlan, *, max_rounds: int | None = None) -> ExecutionTrace:
    """Play a fault plan against the production reducer to a bounded horizon."""

    intent = _intent(plan)
    durable = _initial_durable(plan)
    provider = ProgrammableFakeProvider()
    reference = ReferenceModel(
        requires_profile_lease=plan.requires_profile_lease,
        requires_host=plan.requires_host,
        requires_cleanup=plan.requires_cleanup,
        desired_cancel=plan.desired_cancel,
        ground_truth_terminal=plan.ground_truth_terminal,
    )

    journal: list[JournalEntry] = []
    phases: list[SessionLifecyclePhase] = [current_phase(durable)]
    distinct_commands: list[tuple[str, DecisionKind]] = []
    applied_command_ids: set[str] = set()
    fired_crashes: set[tuple[str, CommandWindow]] = set()
    crashes_fired: list[tuple[str, CommandWindow]] = []
    cleanup_effects: list[tuple[str, int]] = []
    reference_violation: str | None = None

    budget = max_rounds if max_rounds is not None else plan.recovery_round + 80
    converged = False
    settled_kind: DecisionKind | None = None

    for round_index in range(budget):
        now = _EPOCH + timedelta(seconds=30 * round_index)
        observations, round_fault = _observe(plan, durable, round_index, now)
        decision = reconcile(
            intent=intent, durable=durable, observations=observations, now=now
        )

        command = decision.command
        command_id = command.command_id if command is not None else None

        crash_window: CommandWindow | None = None
        if command is not None:
            operation = _DECISION_TO_OPERATION.get(decision.kind)
            if operation is not None:
                candidate = plan.command_crashes.get(operation)
                if candidate is not None and (command_id, candidate) not in fired_crashes:
                    crash_window = candidate
                    fired_crashes.add((command_id, candidate))
                    crashes_fired.append((command_id, candidate))

        next_durable, fenced = apply_decision(
            durable,
            decision,
            provider,
            submit_response=plan.submit_response,
            crash_window=crash_window,
        )

        journal.append(
            JournalEntry(
                round_index=round_index,
                decision_kind=decision.kind,
                reason_code=decision.reason_code.value,
                command_id=command_id,
                fenced=fenced,
                crash_window=crash_window,
                observation_fault=round_fault,
            )
        )

        # Record the fencing generation every performed cleanup side effect ran
        # under. ``apply_decision`` performs the provider side effect whenever the
        # command was not fenced and the crash window is not a pre-side-effect
        # one, so mirror that condition here. Because the reducer scopes a
        # command id by ``g<fencing_generation>``, a cleanup retried after a
        # replacement continuation bumped authority carries a fresh id and lands
        # under the new generation, while a stale generation's cleanup is
        # recorded under the superseded number — which cleanup safety rejects.
        if (
            command_id is not None
            and decision.kind == DecisionKind.BEGIN_CLEANUP
            and not fenced
            and crash_window not in _NO_TRANSITION_WINDOWS
        ):
            cleanup_effects.append((command_id, durable.fencing_generation))

        # Feed the reference model only when a durable transition actually
        # happens for a not-yet-seen logical command.
        if (
            command_id is not None
            and next_durable.revision != durable.revision
            and command_id not in applied_command_ids
            and decision.kind in _DECISION_TO_REFERENCE
        ):
            applied_command_ids.add(command_id)
            distinct_commands.append((command_id, decision.kind))
            try:
                reference.apply(_DECISION_TO_REFERENCE[decision.kind])
            except AssertionError as exc:  # IllegalTransitionError
                if reference_violation is None:
                    reference_violation = str(exc)

        durable = next_durable
        phases.append(current_phase(durable))

        if decision.kind == DecisionKind.NO_OP:
            converged = True
            settled_kind = decision.kind
            break
        if decision.kind in (
            DecisionKind.QUARANTINE_AMBIGUOUS_STATE,
            DecisionKind.FAIL_NONRETRYABLE,
        ):
            settled_kind = decision.kind
            # A settled non-NO_OP state that never changes again ends the run.
            if crash_window is None and not fenced:
                break

    return ExecutionTrace(
        plan=plan,
        journal=journal,
        phases=phases,
        final=durable,
        provider=provider,
        reference=reference,
        distinct_commands=distinct_commands,
        converged=converged,
        settled_kind=settled_kind,
        reference_violation=reference_violation,
        crashes_fired=crashes_fired,
        cleanup_effects=cleanup_effects,
    )


__all__ = [
    "ObservationFault",
    "FaultPlan",
    "JournalEntry",
    "ExecutionTrace",
    "apply_decision",
    "run_plan",
]
