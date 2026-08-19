"""Round-trippable conversion between executable plans and declarative scenarios.

Source issue: MoonLadderStudios/MoonMind#3709.

The :class:`~moonmind.omnigent.faultlab.harness.FaultPlan` is the executable
form; the :class:`~moonmind.omnigent.faultlab.scenario.FaultScenario` is the
declarative, versioned, storable form. Minimized failing plans are serialized to
scenarios for the corpus, and stored scenarios are converted back to plans to be
replayed. The mapping is deterministic and total for generated faults, so a
``plan -> scenario -> plan`` round trip executes identically.
"""

from __future__ import annotations

from .harness import FaultPlan, ObservationFault
from .scenario import (
    LOGICAL_OPERATION_ORDER,
    CommandWindow,
    EmittedEvent,
    FaultScenario,
    LogicalOperation,
    ResponseBehavior,
    ScenarioStep,
    SideEffect,
    SnapshotReturn,
)


class UnrepresentableScenarioStepError(ValueError):
    """A declarative scenario step has no executable :class:`FaultPlan` form.

    ``FaultPlan`` can only express the faults the generator produces: a scripted
    submit response, per-command crash windows, and the canonical observation
    faults in ``_FAULT_TO_STEP``. A declarative scenario can encode strictly more
    (an ``ensure_session`` dropped response, a malformed/auth failure on an
    arbitrary operation, a custom snapshot return, a duplicate/reordered event
    frontier). Converting such a step by silently dropping it would replay the
    scenario *as if the fault were absent*, weakening the fixture without warning,
    so conversion fails fast instead.
    """

# Each observation fault has one canonical declarative encoding and its inverse.
_FAULT_TO_STEP: dict[ObservationFault, ScenarioStep] = {
    ObservationFault.DROP_SNAPSHOT: ScenarioStep(
        on=LogicalOperation.OBSERVE_SNAPSHOT, response=ResponseBehavior.DROP
    ),
    ObservationFault.STALE_SESSION: ScenarioStep(
        on=LogicalOperation.OBSERVE_SNAPSHOT,
        ret=SnapshotReturn(session_state="completed", provider_session_id="other"),
    ),
    ObservationFault.UNKNOWN_VOCAB: ScenarioStep(
        on=LogicalOperation.OBSERVE_SNAPSHOT,
        response=ResponseBehavior.UNKNOWN_SCHEMA,
        ret=SnapshotReturn(session_state="frobnicate"),
    ),
    ObservationFault.RUNNING: ScenarioStep(
        on=LogicalOperation.OBSERVE_SNAPSHOT,
        ret=SnapshotReturn(session_state="running"),
    ),
    ObservationFault.MISSED_EDGE: ScenarioStep(
        on=LogicalOperation.READ_EVENTS,
        emit=(EmittedEvent(type="turn.running"),),
        disconnect=True,
    ),
    ObservationFault.EVIDENCE_DELAY: ScenarioStep(
        on=LogicalOperation.HARVEST_EVIDENCE, response=ResponseBehavior.LATENCY
    ),
    ObservationFault.CONSUMER_ACTIVE: ScenarioStep(
        on=LogicalOperation.RELEASE_LEASES, response=ResponseBehavior.LATENCY
    ),
}


def _decode_observation_fault(step: ScenarioStep) -> ObservationFault | None:
    for fault, encoded in _FAULT_TO_STEP.items():
        if (
            step.on == encoded.on
            and step.side_effect == encoded.side_effect
            and step.response == encoded.response
            and step.ret == encoded.ret
            and step.emit == encoded.emit
            and step.disconnect == encoded.disconnect
            and step.duplicate == encoded.duplicate
            and step.reorder == encoded.reorder
        ):
            return fault
    return None


def _is_representable_submit(step: ScenarioStep) -> bool:
    """Whether a ``submit_turn`` step is one ``FaultPlan`` can round-trip.

    ``FaultPlan`` only carries the submit *response*; any other scripted behavior
    on the submit step (an emitted frontier, a snapshot return, a disconnect, a
    duplicate/reorder) has no executable representation and must not be dropped.
    """

    return (
        step.emit == ()
        and step.ret is None
        and step.disconnect is False
        and step.duplicate is False
        and step.reorder is False
    )


def plan_to_scenario(
    plan: FaultPlan,
    *,
    scenario_id: str | None = None,
    source_ref: str | None = None,
    invariant: str | None = None,
) -> FaultScenario:
    """Project an executable plan into a declarative, storable scenario."""

    steps: list[ScenarioStep] = []

    # Provisioning + submission with its scripted transport behavior.
    steps.append(
        ScenarioStep(
            on=LogicalOperation.SUBMIT_TURN,
            side_effect=SideEffect.ACCEPTED,
            response=plan.submit_response,
        )
    )

    # Crash windows, in a stable operation order.
    for operation in LOGICAL_OPERATION_ORDER:
        window = plan.command_crashes.get(operation)
        if window is not None:
            steps.append(ScenarioStep(on=operation, crash_at=window))

    # Observation faults, in order.
    for fault in plan.observation_faults:
        steps.append(_FAULT_TO_STEP[fault])

    return FaultScenario(
        seed=plan.seed,
        scenario_id=scenario_id,
        source_ref=source_ref,
        invariant=invariant,
        steps=tuple(steps),
        ground_truth_terminal=plan.ground_truth_terminal,
        recovery_round=plan.recovery_round,
        requires_profile_lease=plan.requires_profile_lease,
        requires_host=plan.requires_host,
        requires_cleanup=plan.requires_cleanup,
        desired_cancel=plan.desired_cancel,
        max_turn_attempts=plan.max_turn_attempts,
    )


def scenario_to_plan(scenario: FaultScenario) -> FaultPlan:
    """Rebuild an executable plan from a declarative scenario."""

    submit_response = ResponseBehavior.SUCCESS
    command_crashes: dict[LogicalOperation, CommandWindow] = {}
    observation_faults: list[ObservationFault] = []

    for step in scenario.steps:
        if step.crash_at is not None:
            command_crashes[step.on] = step.crash_at
            continue
        if step.on == LogicalOperation.SUBMIT_TURN:
            if not _is_representable_submit(step):
                raise UnrepresentableScenarioStepError(
                    "submit_turn step carries behavior a FaultPlan cannot "
                    "represent (only the submit response round-trips)"
                )
            submit_response = step.response
            continue
        decoded = _decode_observation_fault(step)
        if decoded is None:
            raise UnrepresentableScenarioStepError(
                f"scenario step on {step.on.value!r} has no executable FaultPlan "
                "representation; represent it in FaultPlan or reject the scenario "
                "rather than replaying it as if the fault were absent"
            )
        observation_faults.append(decoded)

    return FaultPlan(
        seed=scenario.seed,
        requires_profile_lease=scenario.requires_profile_lease,
        requires_host=scenario.requires_host,
        requires_cleanup=scenario.requires_cleanup,
        desired_cancel=scenario.desired_cancel,
        ground_truth_terminal=scenario.ground_truth_terminal,
        max_turn_attempts=scenario.max_turn_attempts,
        recovery_round=scenario.recovery_round,
        submit_response=submit_response,
        observation_faults=tuple(observation_faults),
        command_crashes=command_crashes,
    )


__all__ = [
    "UnrepresentableScenarioStepError",
    "plan_to_scenario",
    "scenario_to_plan",
]
