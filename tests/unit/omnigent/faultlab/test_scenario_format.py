"""Versioned declarative fault-scenario format and compatibility policy.

Source issue: MoonLadderStudios/MoonMind#3709 (acceptance criteria 1 and 10).
"""

from __future__ import annotations

import pytest

from moonmind.omnigent.faultlab.scenario import (
    FAULT_SCENARIO_SCHEMA_VERSION,
    CommandWindow,
    FaultScenario,
    LogicalOperation,
    ResponseBehavior,
    ScenarioStep,
    SideEffect,
    UnknownScenarioSchemaVersionError,
    dumps_scenario,
    load_scenario,
    loads_scenario,
)


def test_declarative_scenario_from_issue_example_loads():
    """The YAML shape from the issue parses into a typed scenario."""

    text = """
schemaVersion: moonmind.omnigent-fault-scenario/v1
seed: 12345
steps:
  - on: ensure_session
    sideEffect: created
    response: success
  - on: submit_turn
    sideEffect: accepted
    response: drop
  - on: read_events
    emit:
      - type: turn.running
    disconnect: true
  - on: observe_snapshot
    return:
      sessionState: idle
      turnState: completed
      unfinishedToolCalls: 0
  - on: read_events
    emit: []
"""
    scenario = loads_scenario(text)
    assert scenario is not None
    assert scenario.schema_version == FAULT_SCENARIO_SCHEMA_VERSION
    assert scenario.seed == 12345
    submit = scenario.steps[1]
    assert submit.on == LogicalOperation.SUBMIT_TURN
    assert submit.side_effect == SideEffect.ACCEPTED
    assert submit.response == ResponseBehavior.DROP
    assert scenario.steps[2].disconnect is True
    assert scenario.steps[3].ret.turn_state == "completed"


def test_scenario_controls_all_required_layers():
    """One scenario controls provider, host, lease, workspace, transport, activity."""

    scenario = FaultScenario(
        steps=(
            ScenarioStep(on=LogicalOperation.ENSURE_PROFILE_LEASE),
            ScenarioStep(on=LogicalOperation.ENSURE_HOST),
            ScenarioStep(
                on=LogicalOperation.SUBMIT_TURN,
                side_effect=SideEffect.ACCEPTED,
                response=ResponseBehavior.TIMEOUT,
            ),
            ScenarioStep(
                on=LogicalOperation.BEGIN_CLEANUP,
                crash_at=CommandWindow.AFTER_SIDE_EFFECT_BEFORE_RECEIPT,
            ),
            ScenarioStep(on=LogicalOperation.RELEASE_LEASES),
        ),
        requires_cleanup=False,
    )
    ops = {step.on for step in scenario.steps}
    assert LogicalOperation.ENSURE_PROFILE_LEASE in ops
    assert LogicalOperation.ENSURE_HOST in ops
    assert LogicalOperation.RELEASE_LEASES in ops
    assert scenario.requires_cleanup is False


def test_unknown_schema_version_fails_by_default():
    """Compatibility safety: an unknown version fails closed under the fail policy."""

    with pytest.raises(UnknownScenarioSchemaVersionError):
        load_scenario({"schemaVersion": "moonmind.omnigent-fault-scenario/v99"})


def test_unknown_schema_version_quarantines_under_policy():
    """The declared quarantine policy skips an unknown scenario without crashing."""

    assert (
        load_scenario(
            {"schemaVersion": "something-else/v1"}, on_unknown="quarantine"
        )
        is None
    )


def test_extra_fields_are_rejected():
    """extra='forbid' keeps the format from silently accepting stray keys."""

    with pytest.raises(Exception):
        load_scenario(
            {
                "schemaVersion": FAULT_SCENARIO_SCHEMA_VERSION,
                "notARealField": 1,
            }
        )


def test_scenario_yaml_round_trip_is_stable():
    scenario = FaultScenario(
        seed=7,
        scenario_id="x",
        steps=(ScenarioStep(on=LogicalOperation.SUBMIT_TURN),),
    )
    reloaded = loads_scenario(dumps_scenario(scenario))
    assert reloaded == scenario


def test_scenario_to_plan_rejects_unrepresentable_observation_step():
    """A declarative fault a FaultPlan cannot encode fails conversion, not silently
    replays fault-free."""

    from moonmind.omnigent.faultlab.conversions import (
        UnrepresentableScenarioStepError,
        scenario_to_plan,
    )

    # A dropped ensure_session response has no FaultPlan representation.
    scenario = FaultScenario(
        seed=1,
        steps=(
            ScenarioStep(
                on=LogicalOperation.ENSURE_SESSION,
                response=ResponseBehavior.DROP,
            ),
        ),
    )
    with pytest.raises(UnrepresentableScenarioStepError):
        scenario_to_plan(scenario)


def test_scenario_to_plan_rejects_snapshot_fault_with_extra_transport_disorder():
    """A canonical observation fault carrying extra duplicate/reorder disorder is
    not silently decoded as the plain fault."""

    from moonmind.omnigent.faultlab.conversions import (
        UnrepresentableScenarioStepError,
        scenario_to_plan,
    )
    from moonmind.omnigent.faultlab.scenario import SnapshotReturn

    scenario = FaultScenario(
        seed=1,
        steps=(
            ScenarioStep(
                on=LogicalOperation.OBSERVE_SNAPSHOT,
                ret=SnapshotReturn(session_state="running"),
                duplicate=True,
            ),
        ),
    )
    with pytest.raises(UnrepresentableScenarioStepError):
        scenario_to_plan(scenario)


def test_scenario_to_plan_rejects_unrepresentable_submit_step():
    from moonmind.omnigent.faultlab.conversions import (
        UnrepresentableScenarioStepError,
        scenario_to_plan,
    )

    scenario = FaultScenario(
        seed=1,
        steps=(
            ScenarioStep(
                on=LogicalOperation.SUBMIT_TURN,
                response=ResponseBehavior.DROP,
                disconnect=True,
            ),
        ),
    )
    with pytest.raises(UnrepresentableScenarioStepError):
        scenario_to_plan(scenario)
