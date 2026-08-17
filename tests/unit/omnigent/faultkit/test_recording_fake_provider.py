"""AC2: the fake independently records side effects and idempotency identities.

MoonLadderStudios/MoonMind#3709.
"""

from __future__ import annotations

from moonmind.omnigent.faultkit.commands import LogicalCommand
from moonmind.omnigent.faultkit.fake_provider import ProgrammableOmnigentProvider
from moonmind.omnigent.faultkit.recording import ProviderRecorder
from moonmind.omnigent.faultkit.scenario import (
    CANONICAL_SCENARIO_SCHEMA_VERSION,
    ResponseMode,
    Scenario,
    ScenarioStep,
    SideEffectKind,
)


def _scenario(steps: list[ScenarioStep]) -> Scenario:
    return Scenario(
        schema_version=CANONICAL_SCENARIO_SCHEMA_VERSION,
        seed=1,
        steps=tuple(steps),
        name="test",
    )


def test_records_request_side_effect_response_and_observation() -> None:
    scenario = _scenario(
        [
            ScenarioStep(on=LogicalCommand.SUBMIT_TURN, side_effect=SideEffectKind.ACCEPTED),
            ScenarioStep(
                on=LogicalCommand.OBSERVE_SNAPSHOT,
                snapshot={"sessionState": "idle", "turnState": "completed"},
            ),
        ]
    )
    recorder = ProviderRecorder()
    provider = ProgrammableOmnigentProvider(scenario, recorder=recorder)
    provider.execute(scenario.steps[0], idempotency_key="turn:1", payload={"n": 1})
    provider.execute(scenario.steps[1])

    assert recorder.request_count(LogicalCommand.SUBMIT_TURN) == 1
    assert recorder.side_effect_count(SideEffectKind.ACCEPTED) == 1
    assert recorder.side_effects[0].idempotency_key == "turn:1"
    assert recorder.side_effects[0].generation == 1
    assert recorder.requests[0].payload_digest.startswith("sha256:")
    # The snapshot observation was captured independently of MoonMind state.
    assert any(o.kind == "snapshot" for o in recorder.observations)


def test_provider_enforces_at_most_once_accepted_side_effect() -> None:
    scenario = _scenario(
        [
            ScenarioStep(on=LogicalCommand.SUBMIT_TURN, side_effect=SideEffectKind.ACCEPTED),
            ScenarioStep(on=LogicalCommand.SUBMIT_TURN, side_effect=SideEffectKind.ACCEPTED),
        ]
    )
    recorder = ProviderRecorder()
    provider = ProgrammableOmnigentProvider(scenario, recorder=recorder)
    # Two submissions of the same idempotency identity.
    provider.execute(scenario.steps[0], idempotency_key="turn:1")
    provider.execute(scenario.steps[1], idempotency_key="turn:1")

    assert recorder.accepted_side_effect_count("turn:1") == 1
    assert provider.accepted_turn_count == 1
    # The request log still records both attempts independently.
    assert recorder.request_count(LogicalCommand.SUBMIT_TURN) == 2


def test_dropped_response_still_commits_side_effect() -> None:
    scenario = _scenario(
        [
            ScenarioStep(
                on=LogicalCommand.SUBMIT_TURN,
                side_effect=SideEffectKind.ACCEPTED,
                response=ResponseMode.DROP,
            )
        ]
    )
    recorder = ProviderRecorder()
    provider = ProgrammableOmnigentProvider(scenario, recorder=recorder)
    outcome = provider.execute(scenario.steps[0], idempotency_key="turn:1")

    assert outcome.delivered is False
    assert outcome.response_lost is True
    assert recorder.accepted_side_effect_count("turn:1") == 1


def test_stale_generation_write_is_fenced_by_provider() -> None:
    scenario = _scenario(
        [
            ScenarioStep(on=LogicalCommand.HOST_REPLACE, side_effect=SideEffectKind.REPLACED),
            ScenarioStep(
                on=LogicalCommand.SUBMIT_TURN,
                side_effect=SideEffectKind.ACCEPTED,
                generation=1,
            ),
        ]
    )
    recorder = ProviderRecorder()
    provider = ProgrammableOmnigentProvider(scenario, recorder=recorder)
    provider.execute(scenario.steps[0])  # generation -> 2
    outcome = provider.execute(scenario.steps[1], idempotency_key="turn:stale")

    assert outcome.fenced is True
    assert recorder.accepted_side_effect_count("turn:stale") == 0


def test_duplicate_and_reorder_event_observations() -> None:
    scenario = _scenario(
        [
            ScenarioStep(
                on=LogicalCommand.READ_EVENTS,
                emit=(
                    {"type": "a", "id": "1"},
                    {"type": "b", "id": "2"},
                ),
                duplicate=True,
            )
        ]
    )
    provider = ProgrammableOmnigentProvider(scenario)
    outcome = provider.execute(scenario.steps[0])
    # Duplicate appends the last event again.
    assert len(outcome.events) == 3
    assert outcome.events[-1] == outcome.events[-2]


def test_dropped_read_yields_no_observation_to_caller() -> None:
    scenario = _scenario(
        [
            ScenarioStep(
                on=LogicalCommand.OBSERVE_SNAPSHOT,
                snapshot={"sessionState": "idle", "turnState": "completed"},
                response=ResponseMode.TIMEOUT,
            )
        ]
    )
    provider = ProgrammableOmnigentProvider(scenario)
    outcome = provider.execute(scenario.steps[0])
    assert outcome.delivered is False
    assert outcome.snapshot is None
