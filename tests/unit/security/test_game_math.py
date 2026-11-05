"""Game math and RTP assessment against explicit specifications (#5199).

Every expected value is a literal derived by hand from the fixture's closed
form, never from the evaluator under test.
"""

from __future__ import annotations

import json
from fractions import Fraction
from functools import cache
from itertools import count

import pytest
from pydantic import ValidationError

from moonmind.security.game_math import (
    GameMathAssessment,
    GameMathSpec,
    RoundRecord,
    SimulationRequest,
    assess_game_math,
    main,
)

DIGEST = "sha256:" + "a" * 64
CRITERIA = {"tolerance": "1/50", "confidence": "99/100"}


def spec(game, wagers, strategies, *, settlement=None, declared=(), **extra):
    return GameMathSpec.model_validate(
        {
            "schemaVersion": "game-math-spec/v1",
            "gameRef": "game:fixture",
            "provenance": {"sourceRef": "par:fixture", "contentDigest": DIGEST},
            "game": game,
            "wagers": wagers,
            "strategies": strategies,
            "settlement": {
                "quantum": "0.01",
                "rounding": "floor",
                **(settlement or {}),
            },
            "declared": list(declared),
            "criteria": CRITERIA,
            **extra,
        }
    )


def table(*outcomes):
    return {"kind": "outcome-table", "outcomes": list(outcomes)}


def one_bet(stake="1", **wager):
    return [{"id": "bet", "stake": stake, **wager}], [{"id": "play", "bets": ["bet"]}]


COIN = table(
    {"id": "heads", "probability": "1/2", "multiplier": "2"},
    {"id": "tails", "probability": "1/2", "multiplier": "0"},
)
# Face 6 pays 4x, face 5 pays 1.7x: RTP = (4 + 1.7) / 6 = 19/20.
DIE = table(
    *(
        {"id": f"face{n}", "probability": "1/6", "multiplier": m}
        for n, m in zip(range(1, 7), ["0", "0", "0", "0", "1.7", "4"])
    )
)
# A 1-in-1000 jackpot paying 960x: RTP = 960 / 1000 = 24/25.
JACKPOT = table(
    {"id": "jackpot", "probability": "1/1000", "multiplier": "960"},
    {"id": "miss", "probability": "999/1000", "multiplier": "0"},
)
# A 1-in-1000 double among even-money returns: RTP = 0.999 + 0.002.
DOUBLE = table(
    {"id": "steady", "probability": "999/1000", "multiplier": "1"},
    {"id": "double", "probability": "1/1000", "multiplier": "2"},
)
# Twelve draws over ten widely spaced values exceed the enumeration bound.
FEATURE = table(
    {"id": "base", "probability": "99/100", "multiplier": "0"},
    {
        "id": "feature",
        "probability": "1/100",
        "multiplier": "0",
        "bonus": {
            "draws": 12,
            "outcomes": [
                {"probability": "1/10", "multiplier": str(10**k)} for k in range(10)
            ],
        },
    },
)


def crash(survival, *, boundary="inclusive", **extra):
    return {
        "kind": "crash",
        "precision": "1/100",
        "boundary": boundary,
        "minTarget": "1.01",
        "survival": survival,
        **extra,
    }


# P(M >= t) = 0.99 / t: RTP(t) = t * 0.99 / t = 99/100 at every target.
CONSTANT = {"kind": "inverse", "houseEdge": "1/100"}
# Half the rounds bust at 1.00x; RTP(t) = t * P(M >= t) varies with t.
STEPPED = {
    "kind": "table",
    "points": [
        {"multiplier": "1", "probability": "1/2"},
        {"multiplier": "1.8", "probability": "1/4"},
        {"multiplier": "3.6", "probability": "1/8"},
        {"multiplier": "7.2", "probability": "1/8"},
    ],
}


def targets(*values, stake="1"):
    wagers = [{"id": f"t{v}", "stake": stake, "target": v} for v in values]
    return wagers, [{"id": f"at{v}", "bets": [f"t{v}"]} for v in values]


def theory(result):
    return {row.strategy: row for row in result.theoretical}


def record(strategy="play", **values):
    return RoundRecord.model_validate({"strategy": strategy, **values})


def test_fair_coin_and_die_have_exact_reference_rtp():
    coin = assess_game_math(
        spec(COIN, *one_bet(), declared=[{"strategy": "play", "rtp": "1"}])
    )
    (row,) = coin.theoretical
    assert (row.status, row.method, row.rtp, row.consistent) == (
        "exact",
        "enumeration",
        Fraction(1),
        True,
    )
    die = assess_game_math(
        spec(DIE, *one_bet(), declared=[{"strategy": "play", "rtp": "0.95"}])
    )
    assert die.theoretical[0].rtp == Fraction(19, 20)
    assert die.theoretical[0].return_bound == Fraction(4)
    assert (die.status, die.outcome) == ("complete", "consistent")


def test_specification_provenance_and_approval_reference_are_kept_verbatim():
    approved = spec(
        COIN,
        *one_bet(),
        provenance={
            "sourceRef": "par:coin-v3",
            "contentDigest": DIGEST,
            "approvalRef": "regulator-filing-77",
        },
    )
    result = assess_game_math(approved)
    assert result.provenance.approval_ref == "regulator-filing-77"
    assert result.provenance.source_ref == "par:coin-v3"
    assert result.spec_digest.startswith("sha256:")
    # An approval reference is the operator's record, not MoonMind approval.
    assert result.certification == "not_performed"
    assert "approval_reference_absent" not in result.limitations
    assert (
        "approval_reference_absent"
        in assess_game_math(spec(COIN, *one_bet())).limitations
    )


@pytest.mark.parametrize(
    "change",
    [
        lambda s: s["game"]["outcomes"][0].update(probability="1/3"),
        lambda s: s["game"]["outcomes"][0].update(probability=0.5),
        lambda s: s["wagers"][0].update(stake="1.005"),
        lambda s: s["strategies"][0].update(bets=["missing"]),
        lambda s: s["declared"].append(
            {"strategy": "play", "rtp": "1", "immediateBust": "0"}
        ),
        lambda s: s.pop("criteria"),
        lambda s: s["criteria"].update(tolerance="0"),
        lambda s: s["provenance"].pop("contentDigest"),
    ],
)
def test_malformed_specifications_are_refused_before_evaluation(change):
    raw = spec(COIN, *one_bet()).model_dump(mode="json", by_alias=True)
    change(raw)
    with pytest.raises(ValidationError):
        GameMathSpec.model_validate(raw)


@pytest.mark.parametrize(
    "wager",
    [{"target": "2.005"}, {"target": "1"}, {}, {"target": "1001"}],
)
def test_crash_wagers_need_a_grid_target_within_the_declared_range(wager):
    with pytest.raises(ValidationError):
        spec(crash(CONSTANT, maxTarget="1000"), *one_bet(**wager))


def test_precision_criteria_cannot_be_supplied_after_the_specification():
    with pytest.raises(ValidationError):
        SimulationRequest.model_validate(
            {"source": "model", "samples": 10, "seed": 1, "tolerance": "1/2"}
        )
    with pytest.raises(ValidationError):
        SimulationRequest.model_validate({"source": "model", "samples": 10})


def test_rounding_mode_is_checked_without_rejecting_a_different_valid_mode():
    # 2.5x on a 0.25 stake is 0.625: floor pays 0.62, half-up pays 0.63.
    game = table(
        {"id": "win", "probability": "2/5", "multiplier": "2.5"},
        {"id": "lose", "probability": "3/5", "multiplier": "0"},
    )
    floor = spec(game, *one_bet("0.25"))
    half_up = spec(game, *one_bet("0.25"), settlement={"rounding": "half_up"})
    paid_up = [record(outcome="win", payouts=["0.63"], total="0.63")]
    paid_down = [record(outcome="win", payouts=["0.62"], total="0.62")]

    assert assess_game_math(floor).theoretical[0].rtp == Fraction(124, 125)
    assert assess_game_math(half_up).theoretical[0].rtp == Fraction(126, 125)
    assert assess_game_math(half_up, settlements=paid_up).outcome == "consistent"
    assert assess_game_math(floor, settlements=paid_down).outcome == "consistent"
    result = assess_game_math(floor, settlements=paid_up)
    (violation,) = result.violations
    assert (violation.rule, violation.expected, violation.observed) == (
        "settlement",
        "31/50",
        "63/100",
    )
    assert "rounding" in violation.engaged


def test_constant_rtp_crash_model_holds_at_every_declared_target():
    wagers, strategies = targets("1.01", "2", "10", "1000")
    result = assess_game_math(
        spec(
            crash(CONSTANT),
            wagers,
            strategies,
            declared=[
                {"strategy": s["id"], "rtp": "0.99", "immediateBust": "2/101"}
                for s in strategies
            ],
        )
    )
    rows = theory(result)
    assert {row.rtp for row in rows.values()} == {Fraction(99, 100)}
    assert {row.method for row in rows.values()} == {"survival-function"}
    # Immediate bust: 1 - P(M >= 1.01) = 1 - 0.99 / 1.01 = 2/101.
    assert {row.immediate_bust for row in rows.values()} == {Fraction(2, 101)}
    assert result.outcome == "consistent"


def test_varying_rtp_crash_model_is_judged_by_its_own_specification():
    wagers, strategies = targets("1.8", "2", "7.2")
    declared = {"at1.8": "0.9", "at2": "0.5", "at7.2": "0.9"}
    result = assess_game_math(
        spec(
            crash(STEPPED),
            wagers,
            strategies,
            declared=[
                {"strategy": key, "rtp": value, "immediateBust": "1/2"}
                for key, value in declared.items()
            ],
        )
    )
    rows = theory(result)
    # RTP(1.8) = 1.8 * 1/2, RTP(2) = 2 * 1/4, RTP(7.2) = 7.2 * 1/8.
    assert rows["at1.8"].rtp == Fraction(9, 10)
    assert rows["at2"].rtp == Fraction(1, 2)
    assert rows["at7.2"].rtp == Fraction(9, 10)
    assert result.outcome == "consistent" and not result.violations

    # A declaration copied from a flat-RTP assumption is what gets flagged.
    flat = assess_game_math(
        spec(
            crash(STEPPED),
            wagers,
            strategies,
            declared=[{"strategy": key, "rtp": "0.9"} for key in declared],
        )
    )
    assert [(v.rule, v.strategy) for v in flat.violations] == [("declared-rtp", "at2")]


def test_exclusive_boundary_has_its_own_varying_rtp():
    wagers, strategies = targets("1.01", "2")
    result = theory(
        assess_game_math(
            spec(crash(CONSTANT, boundary="exclusive"), wagers, strategies)
        )
    )
    # Exclusive pays only when M > t, i.e. M >= t + 0.01 on the grid.
    assert result["at1.01"].rtp == Fraction(3333, 3400)  # 1.01 * 0.99 / 1.02
    assert result["at2"].rtp == Fraction(66, 67)  # 2 * 0.99 / 2.01


def test_cashout_boundary_tie_is_settled_by_the_declared_rule():
    wagers, strategies = targets("2")
    inclusive = spec(crash(CONSTANT), wagers, strategies)
    exclusive = spec(crash(CONSTANT, boundary="exclusive"), wagers, strategies)
    paid_on_tie = [record("at2", multiplier="2", payouts=["2"], total="2")]
    lost_on_tie = [record("at2", multiplier="2", payouts=["0"], total="0")]

    assert assess_game_math(inclusive, settlements=paid_on_tie).outcome == "consistent"
    assert assess_game_math(exclusive, settlements=lost_on_tie).outcome == "consistent"
    (violation,) = assess_game_math(inclusive, settlements=lost_on_tie).violations
    assert violation.engaged == ["boundary"]
    (violation,) = assess_game_math(exclusive, settlements=paid_on_tie).violations
    assert (violation.expected, violation.observed) == ("0", "2")


def test_immediate_bust_probability_and_settlement():
    wagers, strategies = targets("1.01")
    declared = [{"strategy": "at1.01", "rtp": "0.99", "immediateBust": "1/100"}]
    result = assess_game_math(
        spec(crash(CONSTANT), wagers, strategies, declared=declared),
        settlements=[record("at1.01", multiplier="1", total="0")],
    )
    # The pre-grid edge is 1/100, but the 1.00x mass is 2/101 on this grid.
    (violation,) = result.violations
    assert (violation.rule, violation.expected, violation.observed) == (
        "declared-immediate-bust",
        "2/101",
        "1/100",
    )
    paid_bust = assess_game_math(
        spec(crash(CONSTANT), wagers, strategies),
        settlements=[record("at1.01", multiplier="1", total="1.01")],
    )
    (violation,) = paid_bust.violations
    assert violation.rule == "settlement" and "immediate_bust" in violation.engaged


def test_payout_limits_and_distinct_cap_implementations():
    wagers, strategies = targets("1000", stake="2")
    multiplier_cap = spec(
        crash(CONSTANT), wagers, strategies, settlement={"multiplierCap": "100"}
    )
    win_cap = spec(
        crash(CONSTANT),
        wagers,
        strategies,
        settlement={"winCap": {"amount": "100", "basis": "payout"}},
    )
    model_cap = spec(crash({**CONSTANT, "maxMultiplier": "100"}), wagers, strategies)
    # Multiplier cap: 2 * 100 * 0.99/1000 / 2 = 99/1000; win cap pays 100 of 2.
    assert theory(assess_game_math(multiplier_cap))["at1000"].rtp == Fraction(99, 1000)
    assert theory(assess_game_math(win_cap))["at1000"].rtp == Fraction(99, 2000)
    # A crash point capped at 100x never reaches a 1000x target.
    assert theory(assess_game_math(model_cap))["at1000"].rtp == 0

    paid_200 = [record("at1000", multiplier="1500", payouts=["200"], total="200")]
    paid_100 = [record("at1000", multiplier="1500", payouts=["100"], total="100")]
    assert (
        assess_game_math(multiplier_cap, settlements=paid_200).outcome == "consistent"
    )
    assert assess_game_math(win_cap, settlements=paid_100).outcome == "consistent"
    (violation,) = assess_game_math(multiplier_cap, settlements=paid_100).violations
    assert "multiplier_cap" in violation.engaged
    (violation,) = assess_game_math(win_cap, settlements=paid_200).violations
    assert "win_cap" in violation.engaged
    (violation,) = assess_game_math(model_cap, settlements=paid_100).violations
    assert violation.rule == "outcome-support"


def test_win_cap_basis_and_round_cap_are_distinct():
    wagers, strategies = targets("100")
    payout = spec(
        crash(CONSTANT),
        wagers,
        strategies,
        settlement={"winCap": {"amount": "50", "basis": "payout"}},
    )
    profit = spec(
        crash(CONSTANT),
        wagers,
        strategies,
        settlement={"winCap": {"amount": "50", "basis": "profit"}},
    )
    # P(M >= 100) = 0.99/100: payout basis pays 50, profit basis pays 1 + 50.
    assert theory(assess_game_math(payout))["at100"].rtp == Fraction(99, 200)
    assert theory(assess_game_math(profit))["at100"].rtp == Fraction(5049, 10000)

    dual = [
        {"id": "first", "stake": "1", "target": "100"},
        {"id": "second", "stake": "1", "target": "100"},
    ]
    both = [{"id": "dual", "bets": ["first", "second"]}]
    round_cap = spec(crash(CONSTANT), dual, both, settlement={"roundCap": "150"})
    bet_cap = spec(
        crash(CONSTANT),
        dual,
        both,
        settlement={"winCap": {"amount": "75", "basis": "payout"}},
    )
    # Both limit a winning round to 150 of 2 staked: 150 * 0.0099 / 2.
    assert theory(assess_game_math(round_cap))["dual"].rtp == Fraction(297, 400)
    assert theory(assess_game_math(bet_cap))["dual"].rtp == Fraction(297, 400)
    per_bet = [record("dual", multiplier="120", payouts=["75", "75"], total="150")]
    assert assess_game_math(bet_cap, settlements=per_bet).outcome == "consistent"
    # The round cap limits the round total only; per-bet amounts are 100 each.
    (violation,) = assess_game_math(round_cap, settlements=per_bet).violations
    assert violation.engaged == ["round_cap"]
    whole = [record("dual", multiplier="120", payouts=["100", "100"], total="150")]
    assert assess_game_math(round_cap, settlements=whole).outcome == "consistent"


def test_fees_and_bonus_features_are_settled_exactly():
    commission = spec(
        COIN, *one_bet(), settlement={"fee": {"rate": "1/20", "basis": "winnings"}}
    )
    # Heads pays 2 less 5% of the 1 won: (1/2) * 1.95 = 39/40.
    assert assess_game_math(commission).theoretical[0].rtp == Fraction(39, 40)
    bonus = table(
        {"id": "lose", "probability": "1/2", "multiplier": "0"},
        {"id": "even", "probability": "1/4", "multiplier": "1"},
        {
            "id": "feature",
            "probability": "1/4",
            "multiplier": "0",
            "bonus": {
                "draws": 2,
                "outcomes": [
                    {"probability": "1/2", "multiplier": "0"},
                    {"probability": "1/2", "multiplier": "2"},
                ],
            },
        },
    )
    # Two feature draws average 2: RTP = 1/4 * 1 + 1/4 * 2 = 3/4.
    result = assess_game_math(
        spec(bonus, *one_bet()),
        settlements=[record(outcome="feature", bonus=["2", "2"], total="4")],
    )
    assert result.theoretical[0].rtp == Fraction(3, 4)
    assert result.theoretical[0].return_bound == Fraction(4)
    assert result.outcome == "consistent"
    impossible = assess_game_math(
        spec(bonus, *one_bet()),
        settlements=[record(outcome="feature", bonus=["3", "0"], total="3")],
    )
    assert impossible.violations[0].rule == "outcome-support"


def test_seeded_monte_carlo_reaches_the_hand_derived_rtp():
    result = assess_game_math(
        spec(COIN, *one_bet()),
        simulation=SimulationRequest(source="model", samples=100_000, seed=5199),
    )
    (row,) = result.simulation.results
    assert (row.samples, row.target, row.target_source) == (
        100_000,
        Fraction(1),
        "theoretical",
    )
    assert (row.estimator, row.interval_method) == (
        "mean-return-per-unit-stake",
        "empirical-bernstein",
    )
    assert row.low <= 1 <= row.high and row.half_width < 0.02
    assert row.decision == "equivalent" and result.outcome == "consistent"
    # The theoretical section is separate and unchanged by the simulation.
    assert result.theoretical[0].rtp == Fraction(1)
    again = assess_game_math(
        spec(COIN, *one_bet()),
        simulation=SimulationRequest(source="model", samples=100_000, seed=5199),
    )
    assert again.simulation.results[0].mean == row.mean


def test_crash_monte_carlo_samples_the_declared_survival_model():
    wagers, strategies = targets("1.5", "3")
    result = assess_game_math(
        spec(crash(CONSTANT), wagers, strategies),
        simulation=SimulationRequest(source="model", samples=60_000, seed=7),
    )
    rows = {row.strategy: row for row in result.simulation.results}
    for row in rows.values():
        assert row.low <= 0.99 <= row.high
    # P(M >= 3) = 0.33: the paying event is expected 19,800 times.
    (event,) = rows["at3"].rare_events
    assert event.probability == Fraction(33, 100)
    assert abs(event.observed - 19_800) < 600


def test_sparse_tail_reports_insufficient_precision_despite_containing_target():
    result = assess_game_math(
        spec(JACKPOT, *one_bet()),
        simulation=SimulationRequest(source="model", samples=2_000, seed=3),
    )
    (row,) = result.simulation.results
    assert row.low <= 0.96 <= row.high
    assert row.decision == "insufficient_precision"
    assert result.outcome == "insufficient_precision"
    assert (row.samples, row.tolerance, row.confidence) == (
        2_000,
        Fraction(1, 50),
        Fraction(99, 100),
    )
    jackpot = next(e for e in row.rare_events if e.event == "outcome:jackpot")
    assert (jackpot.expected, jackpot.covered) == (2.0, False)
    assert row.rare_event_coverage == "incomplete"
    assert "rare_events_undercovered" in result.limitations
    assert not result.covered


def test_undercovered_paying_event_blocks_equivalence_despite_a_narrow_interval():
    run = SimulationRequest(source="model", samples=5_000, seed=11)
    (row,) = assess_game_math(
        spec(DOUBLE, *one_bet()), simulation=run
    ).simulation.results
    assert row.target == Fraction(1001, 1000)
    assert row.half_width < 0.02 and row.low <= 1.001 <= row.high
    double = next(e for e in row.rare_events if e.event == "outcome:double")
    assert (double.expected, double.covered) == (5.0, False)
    assert row.decision == "insufficient_precision"
    # The event floor is part of the specification's criteria.
    relaxed = spec(DOUBLE, *one_bet(), criteria={**CRITERIA, "minExpectedEvents": 5})
    (row,) = assess_game_math(relaxed, simulation=run).simulation.results
    assert row.decision == "equivalent"


@pytest.mark.parametrize("source", ["model", "records"])
def test_rare_bonus_draws_use_the_joint_parent_probability(source):
    # The feature triggers 1/10 of the time, then pays a bonus with chance 1/100.
    # Its joint probability is 1/1000, like the equivalent flat outcome table.
    game = table(
        {"id": "steady", "probability": "9/10", "multiplier": "1"},
        {
            "id": "feature",
            "probability": "1/10",
            "multiplier": "1",
            "bonus": {
                "draws": 1,
                "outcomes": [
                    {"probability": "99/100", "multiplier": "0"},
                    {"probability": "1/100", "multiplier": "1"},
                ],
            },
        },
    )
    request = SimulationRequest(
        source=source,
        **({"samples": 5_000, "seed": 11} if source == "model" else {}),
    )
    records = (
        [record(outcome="steady", total="1")] * 4_500
        + [record(outcome="feature", bonus=["0"], total="1")] * 495
        + [record(outcome="feature", bonus=["1"], total="2")] * 5
    )
    result = assess_game_math(
        spec(game, *one_bet()), simulation=request, records=records
    )
    (row,) = result.simulation.results
    rare = [
        event for event in row.rare_events if event.probability == Fraction(1, 1000)
    ]
    assert len(rare) == 1
    assert rare[0].expected == 5 and not rare[0].covered
    if source == "records":
        assert rare[0].observed == 5
    assert row.half_width < 0.02
    assert row.decision == "insufficient_precision"
    assert not result.covered


@pytest.mark.parametrize("partial", [False, True])
def test_recorded_runs_retain_every_unobserved_strategy(partial):
    wagers = [{"id": "bet", "stake": "1"}]
    strategies = [{"id": name, "bets": ["bet"]} for name in ("play", "other")]
    records = [record(outcome="heads", total="2")] * 10 if partial else []
    result = assess_game_math(
        spec(COIN, wagers, strategies),
        simulation=SimulationRequest(source="records"),
        records=records,
    )
    rows = {row.strategy: row for row in result.simulation.results}
    assert set(rows) == {"play", "other"}
    assert rows["other"].samples == 0 and rows["other"].mean is None
    assert rows["other"].decision == "incomplete"
    assert (result.status, result.outcome) == ("partial", "incomplete")
    assert result.simulation.status == "partial" and not result.covered


@pytest.mark.parametrize("source", ["model", "records"])
@pytest.mark.parametrize("at_start", [True, False])
@pytest.mark.parametrize("reason", ["cancelled", "time_budget_exhausted"])
def test_short_runs_observe_stop_before_sampling_and_before_finalizing(
    source, at_start, reason
):
    calls = count()
    kwargs = (
        {"should_stop": lambda: next(calls) >= (0 if at_start else 1)}
        if reason == "cancelled"
        else {"clock": lambda: 0 if next(calls) < (1 if at_start else 2) else 2}
    )
    result = assess_game_math(
        spec(COIN, *one_bet()),
        simulation=SimulationRequest(
            source=source,
            maxSeconds=1,
            **({"samples": 4, "seed": 1} if source == "model" else {}),
        ),
        records=[record(outcome="heads", total="2")] * 4,
        **kwargs,
    )
    (row,) = result.simulation.results
    assert row.samples == (0 if at_start else 4)
    assert result.simulation.stop_reason == reason
    assert row.decision == "incomplete"
    assert (result.status, result.outcome) == ("partial", "incomplete")
    assert not result.covered


def test_settlement_caches_remain_bounded_for_distinct_bonus_awards(monkeypatch):
    import random

    from moonmind.security import game_math

    monkeypatch.setattr(game_math, "MAX_CACHED_SETTLEMENTS", 16, raising=False)
    values = [str(2**n) for n in range(64)]
    game = table(
        {
            "id": "feature",
            "probability": "1",
            "multiplier": "0",
            "bonus": {
                "draws": 16,
                "outcomes": [
                    {"probability": "1/64", "multiplier": value} for value in values
                ],
            },
        }
    )
    specification = spec(game, *one_bet())
    sampler = game_math._Sampler(specification, random.Random(7))
    rounds = game_math._Rounds(specification)
    rng = random.Random(8)
    for _ in range(128):
        sampler.settle("play", sampler.draw())
        bonus = [rng.choice(values) for _ in range(16)]
        total = sum(map(int, bonus))
        _, settled = rounds.expected(
            record(outcome="feature", bonus=bonus, total=str(total))
        )
        assert settled.total == total
    assert len(sampler.settled["play"]) <= 16
    assert len(rounds.cache) <= 16


def test_low_sample_run_is_insufficient_precision():
    result = assess_game_math(
        spec(COIN, *one_bet()),
        simulation=SimulationRequest(source="model", samples=50, seed=1),
    )
    (row,) = result.simulation.results
    assert row.low <= 1 <= row.high and row.decision == "insufficient_precision"


def test_recorded_implementation_run_with_biased_outcomes_differs():
    # 45% heads with correct settlement: observed RTP 0.9 against 1.
    rows = [record(outcome="heads", total="2")] * 45_000 + [
        record(outcome="tails", total="0")
    ] * 55_000
    result = assess_game_math(
        spec(COIN, *one_bet()),
        simulation=SimulationRequest(source="records"),
        records=rows,
    )
    (row,) = result.simulation.results
    assert row.mean == pytest.approx(0.9)
    assert row.decision == "different" and row.high < 0.98
    assert [v.rule for v in result.violations] == ["simulated-rtp"]
    assert result.outcome == "violations"


def test_recorded_rounds_are_resettled_as_implementation_behavior():
    rows = [record(outcome="heads", total="1.9")] * 3 + [
        record(outcome="tails", total="0")
    ] * 3
    result = assess_game_math(
        spec(COIN, *one_bet()),
        simulation=SimulationRequest(source="records"),
        records=rows,
    )
    (violation,) = [v for v in result.violations if v.rule == "settlement"]
    assert (violation.case, violation.occurrences) == ("outcome=heads", 3)


def test_cancelled_simulation_is_partial_evidence_never_confirmed():
    calls = count()
    result = assess_game_math(
        spec(COIN, *one_bet()),
        simulation=SimulationRequest(source="model", samples=1_000_000, seed=1),
        should_stop=lambda: next(calls) >= 3,
    )
    (row,) = result.simulation.results
    assert result.simulation.stop_reason == "cancelled"
    assert 0 < row.samples < 1_000_000
    assert row.decision == "incomplete"
    assert (result.status, result.outcome) == ("partial", "incomplete")
    assert result.certification == "not_performed" and not result.covered
    assert "simulation_cancelled" in result.limitations


def test_time_budget_exhaustion_is_partial():
    ticks = count(step=10)
    result = assess_game_math(
        spec(COIN, *one_bet()),
        simulation=SimulationRequest(
            source="model", samples=1_000_000, seed=1, maxSeconds=25
        ),
        clock=lambda: next(ticks),
    )
    assert result.simulation.stop_reason == "time_budget_exhausted"
    assert (result.status, result.outcome) == ("partial", "incomplete")


def test_intractable_specification_needs_a_simulated_decision():
    alone = assess_game_math(spec(FEATURE, *one_bet()))
    (row,) = alone.theoretical
    assert (row.status, row.rtp) == ("intractable", None)
    assert (alone.status, alone.outcome) == ("partial", "incomplete")
    assert "theoretical_intractable" in alone.limitations


def test_result_contract_refuses_contradictory_promotions():
    result = assess_game_math(
        spec(COIN, *one_bet()),
        simulation=SimulationRequest(source="model", samples=50, seed=1),
    )
    raw = result.model_dump(mode="json", by_alias=True)
    for change in (
        {"outcome": "consistent"},
        {"certification": "certified"},
        {"status": "partial", "outcome": "consistent"},
    ):
        with pytest.raises(ValidationError):
            GameMathAssessment.model_validate({**raw, **change})
    assert set(raw) >= {"theoretical", "simulation", "violations", "criteria"}


@cache
def genuine(name):
    """Evaluator results that the forgeries below start from."""
    if name == "precise":
        run = SimulationRequest(source="model", samples=100_000, seed=5199)
        return assess_game_math(spec(COIN, *one_bet()), simulation=run)
    if name == "low-sample":
        run = SimulationRequest(source="model", samples=50, seed=1)
        return assess_game_math(spec(COIN, *one_bet()), simulation=run)
    if name == "undercovered":
        run = SimulationRequest(source="model", samples=5_000, seed=11)
        return assess_game_math(spec(DOUBLE, *one_bet()), simulation=run)
    if name == "biased":
        rows = [record(outcome="heads", total="2")] * 9_000 + [
            record(outcome="tails", total="0")
        ] * 11_000
        run = SimulationRequest(source="records")
        return assess_game_math(spec(COIN, *one_bet()), simulation=run, records=rows)
    return assess_game_math(spec(FEATURE, *one_bet()))


def promote(raw, **row):
    raw.update(status="complete", outcome="consistent")
    raw["simulation"]["results"][0].update(decision="equivalent", **row)


def cover_every_event(raw):
    promote(raw, rareEventCoverage="complete")
    for event in raw["simulation"]["results"][0]["rareEvents"]:
        event["covered"] = True


def drop_simulated_finding(raw):
    raw.update(violations=[], outcome="insufficient_precision")


FORGERIES = {
    # A cancelled run's interval proves nothing, whatever it shows.
    "decided-after-cancel": (
        "precise",
        lambda raw: raw["simulation"].update(status="partial", stopReason="cancelled"),
    ),
    "complete-run-with-stop-reason": (
        "precise",
        lambda raw: raw["simulation"].update(stopReason="time_budget_exhausted"),
    ),
    "complete-over-unfinished-run": (
        "precise",
        lambda raw: raw["simulation"].update(status="partial", unusableRecords=1),
    ),
    "wide-interval-equivalent": ("low-sample", promote),
    "tolerance-relaxed-after-evaluation": (
        "low-sample",
        lambda raw: promote(raw, tolerance="2"),
    ),
    "undercovered-equivalent": ("undercovered", promote),
    "coverage-floor-ignored": ("undercovered", cover_every_event),
    "different-without-its-finding": ("biased", drop_simulated_finding),
    "intractable-without-decision": (
        "intractable",
        lambda raw: raw.update(status="complete", outcome="consistent"),
    ),
}


@pytest.mark.parametrize("forgery", FORGERIES)
def test_result_contract_refuses_summaries_contradicting_their_own_evidence(
    forgery,
):
    source, edit = FORGERIES[forgery]
    raw = json.loads(genuine(source).model_dump_json(by_alias=True))
    # The evaluator's own result survives the retained JSON form unchanged.
    retained = GameMathAssessment.model_validate(raw)
    assert retained.model_dump() == genuine(source).model_dump()
    edit(raw)
    with pytest.raises(ValidationError):
        GameMathAssessment.model_validate(raw)


def test_cli_writes_partial_result_for_unusable_records(tmp_path):
    path = tmp_path / "spec.json"
    path.write_text(spec(COIN, *one_bet()).model_dump_json(by_alias=True))
    records = tmp_path / "rounds.jsonl"
    records.write_text(
        '{"strategy":"play","outcome":"heads","total":"2"}\nnot json\n'
        '{"strategy":"other","outcome":"heads","total":"2"}\n'
    )
    output = tmp_path / "out" / "assessment.json"
    assert (
        main(["--spec", str(path), "--records", str(records), "--output", str(output)])
        == 0
    )
    result = GameMathAssessment.model_validate_json(output.read_text())
    assert result.simulation.unusable_records == 2
    assert result.status == "partial"
    assert "unusable_records" in result.limitations
    assert json.loads(output.read_text())["schemaVersion"] == "game-math-assessment/v1"


def test_cli_refuses_an_invalid_specification(tmp_path, capsys):
    path = tmp_path / "spec.json"
    path.write_text('{"schemaVersion": "game-math-spec/v1"}')
    output = tmp_path / "assessment.json"
    assert main(["--spec", str(path), "--output", str(output)]) == 2
    assert not output.exists()
    assert "invalid game math input" in capsys.readouterr().err
