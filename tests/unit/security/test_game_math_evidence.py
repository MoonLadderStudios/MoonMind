"""Retained game-math inputs must reproduce the reported conclusions."""

import json

import pytest

from moonmind.security.game_math import GameMathSpec, assess_game_math
from tests.integration.security.test_game_math_jobs import SPEC


def test_retained_inputs_reject_changed_conclusions():
    from moonmind.security.game_math_evidence import verify_inputs

    spec = GameMathSpec.model_validate(SPEC)
    report = assess_game_math(spec)
    bodies = {"spec": json.dumps(SPEC).encode()}
    assert verify_inputs(report, bodies)
    report.theoretical[0].rtp = "1"
    with pytest.raises(ValueError, match="differs"):
        verify_inputs(report, bodies)


@pytest.mark.parametrize("source", ["model", "records"])
@pytest.mark.parametrize("partial", [False, True])
def test_bounded_replay_preserves_uncertainty_and_cancelled_prefix(source, partial):
    from moonmind.security.game_math import RoundRecord, SimulationRequest
    from moonmind.security.game_math_evidence import verify_inputs

    spec = GameMathSpec.model_validate(SPEC)
    bodies = {"spec": json.dumps(SPEC).encode()}
    records = None
    if source == "model":
        simulation = SimulationRequest(source="model", samples=20, seed=42)
    else:
        records = [RoundRecord(strategy="cash-2", multiplier="2", total="2")]
        bodies["records"] = b' {"strategy":"cash-2","multiplier":"2","total":"2"} '
        simulation = SimulationRequest(source="records")
    report = assess_game_math(
        spec, simulation=simulation, records=records, should_stop=lambda: partial
    )
    assert verify_inputs(report, bodies)
    if partial:
        assert not report.covered and report.outcome == "incomplete"
    else:
        assert not report.covered
    report.simulation.results[0].high += 0.1
    with pytest.raises(ValueError, match="differs"):
        verify_inputs(report, bodies)


def test_replay_ceiling_does_not_reexecute_large_work(monkeypatch):
    from moonmind.security.game_math import SimulationRequest
    from moonmind.security import game_math_evidence as evidence

    spec = GameMathSpec.model_validate(SPEC)
    report = assess_game_math(
        spec, simulation=SimulationRequest(source="model", samples=1, seed=42)
    )
    report.simulation.requested_samples = 100_000_000
    for row in report.simulation.results:
        row.samples = 100_000_000
    monkeypatch.setattr(
        evidence, "assess_game_math", lambda *a, **k: pytest.fail("unbounded replay")
    )
    assert evidence.verify_inputs(report, {"spec": json.dumps(SPEC).encode()}) is False
