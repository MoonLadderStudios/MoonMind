"""Bounded reproduction of game-math conclusions from retained job inputs.

Job membership is checked separately through the persisted Container Job
manifest. Neither these JSON fields nor a specification hash establish that a
job ran, and reproducing a reference model never proves deployed-game behavior.
"""

from __future__ import annotations

import hashlib

from pydantic import ValidationError

from moonmind.security.game_math import (
    GameMathAssessment,
    GameMathJob,
    GameMathSpec,
    RoundRecord,
    SimulationRequest,
    assess_game_math,
    MAX_SETTLEMENT_CASES,
)

MAX_REPLAY_ROUNDS = 100_000
MAX_INPUT_BYTES = 16 * 1024 * 1024


def _rows(body: bytes, limit: int):
    rows = []
    for line in body.decode("utf-8").splitlines():
        if not line.strip():
            continue
        if len(rows) >= limit:
            raise ValueError("game math input exceeds verification bound")
        try:
            rows.append(RoundRecord.model_validate_json(line))
        except ValidationError:
            rows.append(None)
    return rows


def verify_inputs(report: GameMathAssessment, bodies: dict[str, bytes]) -> bool:
    """Return False for a bounded replay omission, never an execution claim.

    Partial model runs replay only their retained prefix; cancellation is
    observed at the engine's existing block boundaries. Large simulations are
    retained without promotion to executed control evidence.
    """
    if "spec" not in bodies or set(bodies) - {"spec", "settlements", "records"}:
        raise ValueError("game math retained inputs are incomplete")
    if sum(map(len, bodies.values())) > MAX_INPUT_BYTES:
        raise ValueError("game math inputs exceed verification byte bound")
    if report.job and report.job.input_digests != {
        name: hashlib.sha256(body).hexdigest() for name, body in bodies.items()
    }:
        raise ValueError("game math input digest differs")
    spec = GameMathSpec.model_validate_json(bodies["spec"])
    settlements = _rows(bodies.get("settlements", b""), MAX_SETTLEMENT_CASES)
    run = report.simulation
    simulation = None
    def stop():
        return False
    records = None
    if run:
        if (run.source == "records") != ("records" in bodies):
            raise ValueError("game math simulation source differs")
        if run.source == "model":
            counts = {row.samples for row in run.results}
            if len(counts) != 1:
                raise ValueError(
                    "game math model strategies have different sample counts"
                )
            samples = next(iter(counts))
        else:
            samples = sum(row.samples for row in run.results) + run.unusable_records
        if samples * len(spec.strategies) > MAX_REPLAY_ROUNDS:
            return False
        if run.source == "records":
            # Bound both parsing and CPU even if a summary understates samples.
            if bodies["records"].count(b"\n") > MAX_REPLAY_ROUNDS:
                return False
            records = _rows(bodies["records"], MAX_REPLAY_ROUNDS)
        simulation = SimulationRequest(
            source=run.source,
            samples=run.requested_samples,
            seed=run.seed,
        )
        if run.stop_reason:
            calls = 0

            def stopped():
                nonlocal calls
                consumed = calls * 1024
                calls += 1
                return consumed >= samples

            stop = stopped
        elif run.source == "model" and run.requested_samples != samples:
            raise ValueError("game math requested sample count differs")
    elif "records" in bodies:
        raise ValueError("game math records have no simulation")
    expected = assess_game_math(
        spec,
        settlements=settlements,
        simulation=simulation,
        records=records,
        should_stop=stop,
        clock=lambda: 0,
    )
    if run and run.stop_reason == "time_budget_exhausted":
        expected.simulation.stop_reason = run.stop_reason
        expected.limitations = [
            run.stop_reason if item == "simulation_cancelled" else item
            for item in expected.limitations
        ]
    if report.job:
        expected = bind_job(expected, report.job)
    if expected != report:
        raise ValueError("game math report differs from retained inputs")
    return True


def bind_job(report: GameMathAssessment, job: GameMathJob) -> GameMathAssessment:
    data = report.model_dump(mode="json", by_alias=True)
    data["job"] = job.model_dump(mode="json", by_alias=True)
    if job.terminal_state != "succeeded" or job.exit_code != 0:
        data["status"] = "partial"
        if data["outcome"] != "violations":
            data["outcome"] = "incomplete"
        data["limitations"] = sorted({*data["limitations"], "job_did_not_succeed"})
    return GameMathAssessment.model_validate(data)


def comparison_basis(bodies: dict[str, bytes]) -> str:
    """Keep targeted case selection stable across corrected observed payouts.

    Dropping a boundary case is not a retest of it. Recorded populations and
    targeted cases also remain separate evidence sources under the same spec.
    """
    from moonmind.security.assessments import content_key

    cases = {
        content_key(
            row.model_dump(mode="json", by_alias=True, exclude={"payouts", "total"})
        )
        for row in _rows(bodies.get("settlements", b""), MAX_SETTLEMENT_CASES)
        if row is not None
    }
    return content_key(["retained-game-math-cases", sorted(cases), "records" in bodies])
