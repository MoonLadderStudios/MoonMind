"""AC1 / AC10: the versioned declarative scenario format and command windows.

MoonLadderStudios/MoonMind#3709.
"""

from __future__ import annotations

import pytest

from moonmind.omnigent.faultkit.commands import COMMAND_WINDOWS, CommandWindow
from moonmind.omnigent.faultkit.injectors import InfraFault, parse_infra_fault
from moonmind.omnigent.faultkit.scenario import (
    CANONICAL_SCENARIO_SCHEMA_VERSION,
    ResponseMode,
    ScenarioSchemaError,
    SideEffectKind,
    load_scenario,
    load_scenario_yaml,
)

_EXAMPLE_YAML = """
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


def test_canonical_example_parses_declaratively() -> None:
    scenario = load_scenario_yaml(_EXAMPLE_YAML)
    assert scenario.schema_version == CANONICAL_SCENARIO_SCHEMA_VERSION
    assert scenario.seed == 12345
    assert len(scenario.steps) == 5
    submit = scenario.steps[1]
    assert submit.response is ResponseMode.DROP
    assert submit.side_effect is SideEffectKind.ACCEPTED
    read = scenario.steps[2]
    assert read.disconnect is True
    assert read.emit[0]["type"] == "turn.running"
    snapshot = scenario.steps[3]
    assert snapshot.snapshot == {
        "sessionState": "idle",
        "turnState": "completed",
        "unfinishedToolCalls": 0,
    }


def test_scenario_round_trips_through_mapping() -> None:
    scenario = load_scenario_yaml(_EXAMPLE_YAML)
    reparsed = load_scenario(scenario.to_mapping())
    assert reparsed.steps == scenario.steps
    assert reparsed.seed == scenario.seed


def test_unknown_schema_version_fails_fast() -> None:
    with pytest.raises(ScenarioSchemaError):
        load_scenario({"schemaVersion": "moonmind.omnigent-fault-scenario/v999", "steps": []})


def test_unknown_schema_version_quarantines_when_requested() -> None:
    scenario = load_scenario(
        {"schemaVersion": "future/v9", "steps": []}, quarantine=True
    )
    assert scenario.quarantined is True
    assert scenario.supported is False
    with pytest.raises(ScenarioSchemaError):
        scenario.require_executable()


def test_missing_steps_is_rejected() -> None:
    with pytest.raises(ScenarioSchemaError):
        load_scenario({"schemaVersion": CANONICAL_SCENARIO_SCHEMA_VERSION})


def test_all_five_command_windows_are_declarable() -> None:
    assert COMMAND_WINDOWS == (
        CommandWindow.BEFORE_CLAIM,
        CommandWindow.AFTER_CLAIM_BEFORE_SIDE_EFFECT,
        CommandWindow.AFTER_SIDE_EFFECT_BEFORE_RECEIPT,
        CommandWindow.AFTER_RECEIPT_BEFORE_STATE_TRANSITION,
        CommandWindow.AFTER_TRANSITION_BEFORE_ACTIVITY_RESPONSE,
    )
    scenario = load_scenario(
        {
            "schemaVersion": CANONICAL_SCENARIO_SCHEMA_VERSION,
            "seed": 1,
            "steps": [
                {"on": "submit_turn", "crashAt": "after_side_effect_before_receipt"}
            ],
        }
    )
    assert scenario.steps[0].crash_at is CommandWindow.AFTER_SIDE_EFFECT_BEFORE_RECEIPT


def test_unknown_command_window_is_rejected() -> None:
    with pytest.raises(ScenarioSchemaError):
        load_scenario(
            {
                "schemaVersion": CANONICAL_SCENARIO_SCHEMA_VERSION,
                "seed": 1,
                "steps": [{"on": "submit_turn", "crashAt": "no_such_window"}],
            }
        )


def test_named_infra_faults_parse_and_reject_unknown() -> None:
    assert parse_infra_fault("lease_expired") is InfraFault.LEASE_EXPIRED
    assert parse_infra_fault(None) is None
    with pytest.raises(ValueError):
        parse_infra_fault("not_a_fault")
