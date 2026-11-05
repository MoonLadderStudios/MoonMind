"""Game math and RTP assessment against an explicit specification.

A ``game-math-spec/v1`` document states the game: outcome probabilities or a
crash multiplier distribution, payouts, wagers, strategy assumptions,
settlement precision and rounding, caps, fees, bonuses, cashout boundary, and
the precision criteria a result must meet. Criteria are fixed with the
specification, so no evaluation chooses them after seeing its data.

Theoretical expectations are exact rationals computed from the specification
alone, independent of the implementation under test. Implementation behavior
arrives as recorded rounds: each is settled again under the specification, and
a recorded run is also estimated as a simulated observation. A seeded Monte
Carlo of the specification covers games too large to enumerate. The
``game-math-assessment/v1`` result keeps the two sections apart.

A simulated RTP is equivalent only when its whole interval lies within the
tolerance of the target and every paying event was expected often enough; an
interval that merely contains the target is insufficient precision. A
cancelled or budget-exhausted run is partial evidence. No result certifies a
game or confirms a PAR sheet.
"""

from __future__ import annotations

import argparse
import bisect
import math
import os
import random
import signal
import sys
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Annotated, Literal

from pydantic import (
    BeforeValidator,
    Field,
    StringConstraints,
    ValidationError,
    model_validator,
)

from moonmind.security.assessments import Contract, Text, content_key

#: Distinct settled outcomes one strategy may enumerate exactly.
MAX_SUPPORT = 100_000
MAX_VIOLATIONS = 256
MAX_SETTLEMENT_CASES = 4096
#: Retain hot settlements without growing with the number of sampled awards.
MAX_CACHED_SETTLEMENTS = 4096
ESTIMATOR = "mean-return-per-unit-stake"
#: Maurer and Pontil (2009), two-sided; valid for returns bounded in [0, B].
INTERVAL_METHOD = "empirical-bernstein"
GENERATOR = "python-random-mt19937"
_CHECK_EVERY = 1024


def _exact(value: object) -> object:
    # A binary float cannot state a probability or an amount exactly.
    if isinstance(value, bool) or not isinstance(value, (int, str, Fraction)):
        raise ValueError("exact values are integers or decimal/fraction strings")
    if isinstance(value, str) and len(value) > 64:
        raise ValueError("exact value text is limited to 64 characters")
    return value


Exact = Annotated[Fraction, BeforeValidator(_exact)]
Positive = Annotated[Exact, Field(gt=0)]
NonNegative = Annotated[Exact, Field(ge=0)]
Probability = Annotated[Exact, Field(gt=0, le=1)]
Ident = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")]


def _distribution(probabilities: Iterable[Fraction]) -> None:
    if sum(probabilities, Fraction(0)) != 1:
        raise ValueError("probabilities must sum to exactly 1")


def _unique(values: list) -> None:
    if len(values) != len(set(values)):
        raise ValueError("identities must be unique")


def _on(value: Fraction, quantum: Fraction) -> bool:
    return (value / quantum).denominator == 1


class Provenance(Contract):
    """The specification's source, recorded verbatim.

    An approval reference is the operator's record of an external approval,
    such as a regulatory filing; it is never MoonMind approval.
    """

    source_ref: Text
    content_digest: Annotated[str, StringConstraints(pattern=r"^sha256:[0-9a-f]{64}$")]
    approval_ref: Text | None = None


class BonusOutcome(Contract):
    probability: Probability
    multiplier: NonNegative


class Bonus(Contract):
    """Independent feature draws whose multipliers add to the triggering outcome's."""

    draws: Annotated[int, Field(ge=1, le=16)]
    outcomes: Annotated[list[BonusOutcome], Field(min_length=1, max_length=64)]

    @model_validator(mode="after")
    def _valid(self):
        _distribution(row.probability for row in self.outcomes)
        return self


class Outcome(Contract):
    id: Ident
    probability: Probability
    #: Gross return per unit stake before settlement rules.
    multiplier: NonNegative
    bonus: Bonus | None = None


class OutcomeTable(Contract):
    kind: Literal["outcome-table"]
    outcomes: Annotated[list[Outcome], Field(min_length=1, max_length=4096)]

    @model_validator(mode="after")
    def _valid(self):
        _unique([row.id for row in self.outcomes])
        _distribution(row.probability for row in self.outcomes)
        return self


class CrashPoint(Contract):
    multiplier: Annotated[Exact, Field(ge=1)]
    probability: Probability


class CrashTable(Contract):
    kind: Literal["table"]
    points: Annotated[list[CrashPoint], Field(min_length=1, max_length=4096)]

    @model_validator(mode="after")
    def _valid(self):
        _unique([row.multiplier for row in self.points])
        _distribution(row.probability for row in self.points)
        return self


class InverseSurvival(Contract):
    """P(M >= t) = (1 - houseEdge) / t on the grid above 1, and 0 above maxMultiplier.

    Inclusive cashout at any target then returns 1 - houseEdge: the constant-RTP
    model, which holds only under this distribution and boundary.
    """

    kind: Literal["inverse"]
    house_edge: Annotated[Exact, Field(ge=0, lt=1)]
    max_multiplier: Annotated[Exact, Field(gt=1)] | None = None


class CrashGame(Contract):
    """A stake cashing out at target t pays t when the crash point M reaches it.

    Multipliers lie on the grid 1 + k * precision. ``inclusive`` pays when
    M >= t; ``exclusive`` only when M > t.
    """

    kind: Literal["crash"]
    precision: Positive
    boundary: Literal["inclusive", "exclusive"]
    min_target: Annotated[Exact, Field(gt=1)]
    max_target: Exact | None = None
    survival: Annotated[CrashTable | InverseSurvival, Field(discriminator="kind")]

    @model_validator(mode="after")
    def _valid(self):
        survival = self.survival
        grid = [self.min_target, self.max_target]
        if survival.kind == "table":
            grid += [row.multiplier for row in survival.points]
        else:
            grid.append(survival.max_multiplier)
        if not all(self.on_grid(value) for value in grid if value is not None):
            raise ValueError("crash multipliers must lie on the precision grid")
        if self.max_target is not None and self.max_target < self.min_target:
            raise ValueError("maxTarget is below minTarget")
        return self

    def on_grid(self, value: Fraction) -> bool:
        return value >= 1 and _on(value - 1, self.precision)

    def threshold(self, target: Fraction) -> Fraction:
        """Smallest grid crash point that pays a cashout at ``target``."""
        return target if self.boundary == "inclusive" else target + self.precision

    def survival_at(self, value: Fraction) -> Fraction:
        """P(M >= value) for a grid value."""
        survival = self.survival
        if value <= 1:
            return Fraction(1)
        if survival.kind == "table":
            return sum(
                (row.probability for row in survival.points if row.multiplier >= value),
                Fraction(0),
            )
        if survival.max_multiplier is not None and value > survival.max_multiplier:
            return Fraction(0)
        return (1 - survival.house_edge) / value

    def possible(self, value: Fraction) -> bool:
        survival = self.survival
        if not self.on_grid(value):
            return False
        if survival.kind == "table":
            return any(row.multiplier == value for row in survival.points)
        return survival.max_multiplier is None or value <= survival.max_multiplier


class Wager(Contract):
    id: Ident
    stake: Positive
    #: Crash cashout target: the strategy's assumption about when to cash out.
    target: Exact | None = None


class Strategy(Contract):
    """Bets placed together in every round, settled on the same outcome."""

    id: Ident
    bets: Annotated[list[Ident], Field(min_length=1, max_length=8)]
    assumption: Text | None = None


class WinCap(Contract):
    """Per-bet limit on the payout, or on the profit above the stake."""

    amount: Positive
    basis: Literal["payout", "profit"]


class Fee(Contract):
    """Deducted from a paying bet: a share of its payout, or of its winnings."""

    rate: Annotated[Exact, Field(ge=0, lt=1)]
    basis: Literal["payout", "winnings"]


class Settlement(Contract):
    """Applied per bet in order: multiplier cap, fee, win cap, rounding.

    The round cap then limits the round's total; per-bet amounts are each
    bet's settlement before it.
    """

    quantum: Positive
    rounding: Literal["floor", "ceiling", "half_up", "half_even"]
    multiplier_cap: Annotated[Exact, Field(gt=0)] | None = None
    win_cap: WinCap | None = None
    round_cap: Positive | None = None
    fee: Fee | None = None


class Declared(Contract):
    """A value the specification itself publishes, such as a PAR sheet's RTP.

    ``resolution`` is the published figure's rounding unit; 0 means exact.
    """

    strategy: Ident
    rtp: NonNegative
    immediate_bust: Annotated[Exact, Field(ge=0, le=1)] | None = None
    resolution: NonNegative = Fraction(0)


class Criteria(Contract):
    """Required precision, fixed with the specification before any evaluation."""

    #: Equivalence margin around the target RTP.
    tolerance: Positive
    confidence: Annotated[Exact, Field(gt=0, lt=1)]
    #: Each paying event must be expected at least this often in a run.
    min_expected_events: Annotated[int, Field(ge=1, le=1_000_000)] = 10


class GameMathSpec(Contract):
    schema_version: Literal["game-math-spec/v1"] = "game-math-spec/v1"
    #: The assessed game; an asset in the assessment scope.
    game_ref: Text
    provenance: Provenance
    game: Annotated[OutcomeTable | CrashGame, Field(discriminator="kind")]
    wagers: Annotated[list[Wager], Field(min_length=1, max_length=32)]
    strategies: Annotated[list[Strategy], Field(min_length=1, max_length=32)]
    settlement: Settlement
    declared: Annotated[list[Declared], Field(max_length=32)] = Field(
        default_factory=list
    )
    criteria: Criteria

    @model_validator(mode="after")
    def _valid(self):
        _unique([row.id for row in self.wagers])
        _unique([row.id for row in self.strategies])
        _unique([row.strategy for row in self.declared])
        wagers = {row.id for row in self.wagers}
        if any(set(row.bets) - wagers for row in self.strategies):
            raise ValueError("strategy names an undeclared wager")
        if {row.strategy for row in self.declared} - {s.id for s in self.strategies}:
            raise ValueError("declared value names an undeclared strategy")
        rules, game = self.settlement, self.game
        amounts = [row.stake for row in self.wagers]
        amounts += [rules.round_cap] if rules.round_cap is not None else []
        amounts += [rules.win_cap.amount] if rules.win_cap is not None else []
        if not all(_on(value, rules.quantum) for value in amounts):
            raise ValueError("stakes and cap amounts must be multiples of the quantum")
        if game.kind == "crash":
            for row in self.wagers:
                if row.target is None or not game.on_grid(row.target):
                    raise ValueError("crash wagers need a grid cashout target")
                if row.target < game.min_target or (
                    game.max_target is not None and row.target > game.max_target
                ):
                    raise ValueError("cashout target outside the declared range")
        elif any(row.target is not None for row in self.wagers):
            raise ValueError("only crash wagers declare a cashout target")
        elif any(row.immediate_bust is not None for row in self.declared):
            raise ValueError("immediate bust applies to crash games only")
        return self

    def wager(self, wager_id: str) -> Wager:
        return next(row for row in self.wagers if row.id == wager_id)

    def bets(self, strategy: Strategy) -> list[Wager]:
        return [self.wager(wager_id) for wager_id in strategy.bets]


class RoundRecord(Contract):
    """One round as the implementation under test settled it.

    ``payouts`` are per bet in strategy order, before any round cap; omit them
    when the implementation reports only the round total.
    """

    strategy: Ident
    outcome: Ident | None = None
    bonus: Annotated[list[Exact], Field(max_length=16)] = Field(default_factory=list)
    multiplier: Exact | None = None
    payouts: Annotated[list[NonNegative], Field(max_length=8)] | None = None
    total: NonNegative


class SimulationRequest(Contract):
    """A seeded run of the specification, or a recorded implementation run."""

    source: Literal["model", "records"]
    samples: Annotated[int, Field(ge=1, le=100_000_000)] | None = None
    seed: Annotated[int, Field(ge=0, lt=2**63)] | None = None
    max_seconds: Annotated[int, Field(ge=1, le=86_400)] = 300

    @model_validator(mode="after")
    def _valid(self):
        if (self.source == "model") != (
            self.samples is not None and self.seed is not None
        ):
            raise ValueError("a model run, and only it, needs samples and a seed")
        return self


class Theoretical(Contract):
    strategy: Ident
    status: Literal["exact", "intractable"]
    method: Literal["enumeration", "survival-function"]
    #: Settled outcomes enumerated; 0 when the bound was exceeded.
    support: int
    rtp: Exact | None
    rtp_decimal: str | None
    #: Largest settled return per unit stake, the interval bound B.
    return_bound: Exact
    immediate_bust: Exact | None = None
    declared_rtp: Exact | None = None
    declared_immediate_bust: Exact | None = None
    consistent: bool | None = None


class RareEvent(Contract):
    event: Text
    probability: Exact
    expected: float
    observed: int
    covered: bool


Decision = Literal[
    "equivalent", "different", "insufficient_precision", "incomplete", "not_compared"
]


class Simulated(Contract):
    strategy: Ident
    samples: int
    estimator: Literal["mean-return-per-unit-stake"] = ESTIMATOR
    mean: float | None
    interval_method: Literal["empirical-bernstein"] = INTERVAL_METHOD
    confidence: Exact
    low: float
    high: float
    half_width: float | None
    return_bound: float
    target: Exact | None
    target_source: Literal["theoretical", "declared"] | None
    tolerance: Exact
    rare_events: list[RareEvent]
    rare_event_coverage: Literal["complete", "incomplete"]
    decision: Decision


class Simulation(Contract):
    source: Literal["model", "records"]
    generator: str | None = None
    seed: int | None = None
    requested_samples: int | None = None
    status: Literal["complete", "partial"]
    stop_reason: Literal["cancelled", "time_budget_exhausted"] | None = None
    unusable_records: int = 0
    results: list[Simulated]


class Violation(Contract):
    rule: Literal[
        "settlement",
        "outcome-support",
        "declared-rtp",
        "declared-immediate-bust",
        "simulated-rtp",
    ]
    strategy: Ident
    case: Text
    expected: Text
    observed: Text
    #: Settlement rules that determined the expected value.
    engaged: list[str] = Field(default_factory=list)
    occurrences: int = 1

    @property
    def identity(self) -> str:
        return f"{self.rule}:{self.strategy}:{self.case}"


class GameMathAssessment(Contract):
    """Theoretical and simulated evidence about one game, never a certification."""

    schema_version: Literal["game-math-assessment/v1"] = "game-math-assessment/v1"
    game_ref: Text
    spec_digest: str
    provenance: Provenance
    criteria: Criteria
    theoretical: list[Theoretical]
    settlement_cases: int
    simulation: Simulation | None
    violations: Annotated[list[Violation], Field(max_length=MAX_VIOLATIONS)]
    status: Literal["complete", "partial"]
    outcome: Literal["consistent", "violations", "insufficient_precision", "incomplete"]
    certification: Literal["not_performed"] = "not_performed"
    limitations: list[str]

    @model_validator(mode="after")
    def _consistent(self):
        decisions = {
            row.decision for row in (self.simulation.results if self.simulation else [])
        }
        if bool(self.violations) != (self.outcome == "violations"):
            raise ValueError("outcome must report violations exactly when found")
        if self.status == "partial" and self.outcome not in {
            "violations",
            "incomplete",
        }:
            raise ValueError("partial evidence cannot report a settled outcome")
        if self.outcome == "consistent" and (
            decisions - {"equivalent"}
            or any(row.consistent is False for row in self.theoretical)
        ):
            raise ValueError("consistency requires every check to agree")
        return self

    @property
    def covered(self) -> bool:
        """Whether every check reached a decision, so absence is comparable."""
        decisions = {
            row.decision for row in (self.simulation.results if self.simulation else [])
        }
        return self.status == "complete" and decisions <= {"equivalent", "different"}


@dataclass(frozen=True)
class Settled:
    payouts: tuple[Fraction, ...]
    total: Fraction
    engaged: tuple[str, ...]


def _round(value: Fraction, quantum: Fraction, mode: str) -> Fraction:
    units = value / quantum
    if mode == "floor":
        whole = math.floor(units)
    elif mode == "ceiling":
        whole = math.ceil(units)
    elif mode == "half_up":
        whole = math.floor(units + Fraction(1, 2))
    else:
        whole = round(units)  # Fraction rounds half to even.
    return whole * quantum


def _settle(spec: GameMathSpec, bets: list[Wager], raws, engaged=()) -> Settled:
    rules = spec.settlement
    engaged = set(engaged)
    payouts = []
    for wager, multiplier in zip(bets, raws):
        if rules.multiplier_cap is not None and multiplier > rules.multiplier_cap:
            multiplier = rules.multiplier_cap
            engaged.add("multiplier_cap")
        payout = wager.stake * multiplier
        if rules.fee is not None and payout > 0:
            base = payout if rules.fee.basis == "payout" else payout - wager.stake
            if base > 0 and rules.fee.rate:
                payout -= rules.fee.rate * base
                engaged.add("fee")
        if rules.win_cap is not None:
            limit = rules.win_cap.amount
            if rules.win_cap.basis == "profit":
                limit += wager.stake
            if payout > limit:
                payout = limit
                engaged.add("win_cap")
        rounded = _round(payout, rules.quantum, rules.rounding)
        if rounded != payout:
            engaged.add("rounding")
        payouts.append(rounded)
    total = sum(payouts, Fraction(0))
    if rules.round_cap is not None and total > rules.round_cap:
        total = rules.round_cap
        engaged.add("round_cap")
    return Settled(tuple(payouts), total, tuple(sorted(engaged)))


def _crash_settle(spec: GameMathSpec, bets: list[Wager], crash: Fraction) -> Settled:
    game = spec.game
    engaged = set()
    raws = []
    for wager in bets:
        if crash == wager.target:
            engaged.add("boundary")
        raws.append(wager.target if crash >= game.threshold(wager.target) else 0)
    if crash < 1 + game.precision:
        engaged.add("immediate_bust")
    return _settle(spec, bets, raws, engaged)


def _awards(game: OutcomeTable, limit: int) -> dict[Fraction, Fraction] | None:
    """Exact distribution of a round's gross multiplier, or None past ``limit``."""
    awards: dict[Fraction, Fraction] = {}
    for outcome in game.outcomes:
        local = {outcome.multiplier: outcome.probability}
        for _ in range(outcome.bonus.draws if outcome.bonus else 0):
            if len(local) * len(outcome.bonus.outcomes) > limit:
                return None
            following: dict[Fraction, Fraction] = {}
            for award, probability in local.items():
                for draw in outcome.bonus.outcomes:
                    key = award + draw.multiplier
                    following[key] = (
                        following.get(key, 0) + probability * draw.probability
                    )
            local = following
        for award, probability in local.items():
            awards[award] = awards.get(award, 0) + probability
        if len(awards) > limit:
            return None
    return awards


def _max_award(outcome: Outcome) -> Fraction:
    bonus = outcome.bonus
    extra = bonus.draws * max(row.multiplier for row in bonus.outcomes) if bonus else 0
    return outcome.multiplier + extra


def _regions(spec: GameMathSpec, bets: list[Wager]) -> list[tuple[Fraction, Fraction]]:
    """Representative crash point and probability of each distinct paying region."""
    game = spec.game
    thresholds = sorted({game.threshold(wager.target) for wager in bets})
    upper = [*thresholds[1:], None]
    regions = [(Fraction(1), 1 - game.survival_at(thresholds[0]))]
    for low, high in zip(thresholds, upper):
        tail = game.survival_at(high) if high is not None else Fraction(0)
        regions.append((low, game.survival_at(low) - tail))
    return regions


def _decimal(value: Fraction, places: int = 8) -> str:
    whole = round(value * 10**places)
    sign = "-" if whole < 0 else ""
    digits = str(abs(whole)).rjust(places + 1, "0")
    return f"{sign}{digits[:-places]}.{digits[-places:]}"


def _theory(
    spec: GameMathSpec, limit: int
) -> tuple[list[Theoretical], list[Violation]]:
    game = spec.game
    declared = {row.strategy: row for row in spec.declared}
    awards = _awards(game, limit) if game.kind == "outcome-table" else None
    rows, violations = [], []
    for strategy in spec.strategies:
        bets = spec.bets(strategy)
        staked = sum((wager.stake for wager in bets), Fraction(0))
        bust = None
        if game.kind == "crash":
            method = "survival-function"
            settled = [
                (probability, _crash_settle(spec, bets, crash).total)
                for crash, probability in _regions(spec, bets)
            ]
            top = max(game.threshold(wager.target) for wager in bets)
            bound = _crash_settle(spec, bets, top).total
            bust = 1 - game.survival_at(1 + game.precision)
        else:
            method = "enumeration"
            settled = (
                None
                if awards is None
                else [
                    (p, _settle(spec, bets, [a] * len(bets)).total)
                    for a, p in awards.items()
                ]
            )
            top = max(_max_award(row) for row in game.outcomes)
            bound = _settle(spec, bets, [top] * len(bets)).total
        if settled is not None and len(settled) > limit:
            settled = None
        rtp = (
            None
            if settled is None
            else sum((p * total for p, total in settled), Fraction(0)) / staked
        )
        published = declared.get(strategy.id)
        consistent = None
        if published is not None and rtp is not None:
            margin = published.resolution / 2
            consistent = abs(rtp - published.rtp) <= margin
            if not consistent:
                violations.append(
                    Violation(
                        rule="declared-rtp",
                        strategy=strategy.id,
                        case="rtp",
                        expected=str(rtp),
                        observed=str(published.rtp),
                    )
                )
            if published.immediate_bust is not None and (
                abs(bust - published.immediate_bust) > margin
            ):
                consistent = False
                violations.append(
                    Violation(
                        rule="declared-immediate-bust",
                        strategy=strategy.id,
                        case="immediate-bust",
                        expected=str(bust),
                        observed=str(published.immediate_bust),
                    )
                )
        rows.append(
            Theoretical(
                strategy=strategy.id,
                status="exact" if rtp is not None else "intractable",
                method=method,
                support=len(settled) if settled is not None else 0,
                rtp=rtp,
                rtp_decimal=_decimal(rtp) if rtp is not None else None,
                return_bound=bound / staked,
                immediate_bust=bust,
                declared_rtp=published.rtp if published else None,
                declared_immediate_bust=published.immediate_bust if published else None,
                consistent=consistent,
            )
        )
    return rows, violations


class _Violations:
    """Distinct violations with occurrence counts, bounded."""

    def __init__(self):
        self.rows: dict[str, Violation] = {}
        self.truncated = False

    def add(self, violation: Violation) -> None:
        existing = self.rows.get(violation.identity)
        if existing is not None:
            existing.occurrences += 1
        elif len(self.rows) < MAX_VIOLATIONS:
            self.rows[violation.identity] = violation
        else:
            self.truncated = True


class _Rounds:
    """Expected settlement of recorded rounds, cached by case."""

    def __init__(self, spec: GameMathSpec):
        self.spec = spec
        self.strategies = {row.id: row for row in spec.strategies}
        self.cache: dict[tuple, tuple[str, Settled | None]] = {}

    def expected(self, record: RoundRecord) -> tuple[str, Settled | None]:
        """The case label and its settlement, or None outside the game's support."""
        spec, game = self.spec, self.spec.game
        strategy = self.strategies.get(record.strategy)
        if strategy is None or (
            record.payouts is not None and len(record.payouts) != len(strategy.bets)
        ):
            raise ValueError("record does not match a declared strategy")
        key = (record.strategy, record.outcome, tuple(record.bonus), record.multiplier)
        if key in self.cache:
            return self.cache[key]
        bets = spec.bets(strategy)
        if game.kind == "crash":
            if record.multiplier is None or record.outcome or record.bonus:
                raise ValueError("crash records name only a crash multiplier")
            case = f"M={record.multiplier}"
            settled = (
                _crash_settle(spec, bets, record.multiplier)
                if game.possible(record.multiplier)
                else None
            )
        else:
            if record.outcome is None or record.multiplier is not None:
                raise ValueError("outcome-table records name an outcome")
            case = f"outcome={record.outcome}" + "".join(
                f",bonus={value}" for value in record.bonus
            )
            outcome = next(
                (row for row in game.outcomes if row.id == record.outcome), None
            )
            draws = outcome.bonus.draws if outcome and outcome.bonus else 0
            allowed = (
                {row.multiplier for row in outcome.bonus.outcomes}
                if outcome and outcome.bonus
                else set()
            )
            settled = (
                _settle(
                    spec,
                    bets,
                    [outcome.multiplier + sum(record.bonus, Fraction(0))] * len(bets),
                )
                if outcome is not None
                and len(record.bonus) == draws
                and set(record.bonus) <= allowed
                else None
            )
        if len(self.cache) < MAX_CACHED_SETTLEMENTS:
            self.cache[key] = (case, settled)
        return case, settled

    def check(self, record: RoundRecord, violations: _Violations) -> None:
        case, settled = self.expected(record)
        if settled is None:
            violations.add(
                Violation(
                    rule="outcome-support",
                    strategy=record.strategy,
                    case=case,
                    expected="an outcome the specification can produce",
                    observed=case,
                )
            )
            return
        if record.total != settled.total:
            expected, observed = str(settled.total), str(record.total)
        elif record.payouts is not None and tuple(record.payouts) != settled.payouts:
            expected = ",".join(map(str, settled.payouts))
            observed = ",".join(map(str, record.payouts))
        else:
            return
        violations.add(
            Violation(
                rule="settlement",
                strategy=record.strategy,
                case=case,
                expected=expected,
                observed=observed,
                engaged=list(settled.engaged),
            )
        )


class _Estimate:
    """Welford mean and variance of per-round returns."""

    def __init__(self):
        self.n, self.mean, self.m2, self.top = 0, 0.0, 0.0, 0.0

    def add(self, value: float) -> None:
        self.n += 1
        delta = value - self.mean
        self.mean += delta / self.n
        self.m2 += delta * (value - self.mean)
        self.top = max(self.top, value)


def _interval(estimate: _Estimate, bound: float, confidence: Fraction):
    """Two-sided empirical-Bernstein interval, clipped to [0, bound]."""
    if estimate.n < 2:
        return 0.0, bound, None
    log_term = math.log(4 / float(1 - confidence))
    variance = estimate.m2 / (estimate.n - 1)
    half = math.sqrt(2 * variance * log_term / estimate.n) + (
        7 * bound * log_term / (3 * (estimate.n - 1))
    )
    return max(0.0, estimate.mean - half), min(bound, estimate.mean + half), half


class _Run:
    """Per-strategy returns and paying-event counts for one simulation."""

    def __init__(self, spec: GameMathSpec, theory: list[Theoretical]):
        self.spec = spec
        self.theory = {row.strategy: row for row in theory}
        game = spec.game
        self.estimates = {row.id: _Estimate() for row in spec.strategies}
        self.events: dict[str, list[tuple[str, Fraction]]] = {}
        for strategy in spec.strategies:
            if game.kind == "crash":
                self.events[strategy.id] = [
                    (
                        f"pays:{wager.id}",
                        game.survival_at(game.threshold(wager.target)),
                    )
                    for wager in spec.bets(strategy)
                ]
            else:
                self.events[strategy.id] = [
                    (f"outcome:{row.id}", row.probability)
                    for row in game.outcomes
                    if _max_award(row) > 0
                ]
                for row in game.outcomes:
                    if row.bonus is None:
                        continue
                    probabilities: dict[Fraction, Fraction] = {}
                    for bonus in row.bonus.outcomes:
                        if bonus.multiplier > 0:
                            probabilities[bonus.multiplier] = (
                                probabilities.get(bonus.multiplier, Fraction(0))
                                + bonus.probability
                            )
                    self.events[strategy.id].extend(
                        (
                            f"bonus:{row.id}:{draw}:{award}",
                            row.probability * probability,
                        )
                        for draw in range(row.bonus.draws)
                        for award, probability in probabilities.items()
                    )
        self.counts = {row.id: {} for row in spec.strategies}

    def add(self, strategy: str, value: float, events: Iterable[str]) -> None:
        self.estimates[strategy].add(value)
        counts = self.counts[strategy]
        for event in events:
            counts[event] = counts.get(event, 0) + 1

    def results(self, stopped: bool) -> list[Simulated]:
        criteria, declared = self.spec.criteria, {
            row.strategy: row for row in self.spec.declared
        }
        rows = []
        for strategy in self.spec.strategies:
            estimate, theory = self.estimates[strategy.id], self.theory[strategy.id]
            # A recorded run that overpays is still bounded by what it paid.
            bound = max(float(theory.return_bound), estimate.top)
            low, high, half = _interval(estimate, bound, criteria.confidence)
            if theory.rtp is not None:
                target, source = theory.rtp, "theoretical"
            elif strategy.id in declared:
                target, source = declared[strategy.id].rtp, "declared"
            else:
                target, source = None, None
            events = [
                RareEvent(
                    event=name,
                    probability=probability,
                    expected=estimate.n * float(probability),
                    observed=self.counts[strategy.id].get(name, 0),
                    covered=estimate.n * probability >= criteria.min_expected_events,
                )
                for name, probability in self.events[strategy.id]
            ]
            covered = all(event.covered for event in events)
            if stopped or not estimate.n:
                decision = "incomplete"
            elif target is None:
                decision = "not_compared"
            elif half is not None and (
                high < target - criteria.tolerance or low > target + criteria.tolerance
            ):
                decision = "different"
            elif (
                half is not None
                and covered
                and target - criteria.tolerance <= low
                and high <= target + criteria.tolerance
            ):
                decision = "equivalent"
            else:
                decision = "insufficient_precision"
            rows.append(
                Simulated(
                    strategy=strategy.id,
                    samples=estimate.n,
                    mean=estimate.mean if estimate.n else None,
                    confidence=criteria.confidence,
                    low=low,
                    high=high,
                    half_width=half,
                    return_bound=bound,
                    target=target,
                    target_source=source,
                    tolerance=criteria.tolerance,
                    rare_events=events,
                    rare_event_coverage="complete" if covered else "incomplete",
                    decision=decision,
                )
            )
        return rows


class _Sampler:
    """Draws rounds from the specification and returns each strategy's result."""

    def __init__(self, spec: GameMathSpec, rng: random.Random):
        self.spec, self.rng, game = spec, rng, spec.game
        self.settled: dict[str, dict] = {row.id: {} for row in spec.strategies}
        self.bets = {row.id: spec.bets(row) for row in spec.strategies}
        self.staked = {
            key: float(sum((w.stake for w in bets), Fraction(0)))
            for key, bets in self.bets.items()
        }
        if game.kind == "crash":
            survival, step = game.survival, game.precision
            self.step = step
            if survival.kind == "table":
                points = sorted(survival.points, key=lambda row: row.multiplier)
                self.grid = [int((row.multiplier - 1) / step) for row in points]
                self.cumulative = _cumulative(row.probability for row in points)
            else:
                self.keep = float(1 - survival.house_edge)
                self.cap = (
                    int((survival.max_multiplier - 1) / step)
                    if survival.max_multiplier is not None
                    else None
                )
            # Grid index of each bet's paying threshold, per strategy.
            self.thresholds = {
                key: [int((game.threshold(w.target) - 1) / step) for w in bets]
                for key, bets in self.bets.items()
            }
        else:
            self.cumulative = _cumulative(row.probability for row in game.outcomes)
            self.bonus = {
                index: (
                    row.bonus,
                    _cumulative(b.probability for b in row.bonus.outcomes),
                )
                for index, row in enumerate(game.outcomes)
                if row.bonus
            }

    def _pick(self, cumulative: list[float]) -> int:
        return min(
            bisect.bisect_right(cumulative, self.rng.random()), len(cumulative) - 1
        )

    def draw(self):
        game = self.spec.game
        if game.kind == "crash":
            if game.survival.kind == "table":
                return self.grid[self._pick(self.cumulative)]
            room = 1.0 - self.rng.random()
            if room > self.keep:
                return 0
            index = int((self.keep / room - 1) / float(self.step))
            return index if self.cap is None else min(index, self.cap)
        index = self._pick(self.cumulative)
        award = game.outcomes[index].multiplier
        events = [f"outcome:{game.outcomes[index].id}"]
        if index in self.bonus:
            bonus, cumulative = self.bonus[index]
            values = [
                bonus.outcomes[self._pick(cumulative)].multiplier
                for _ in range(bonus.draws)
            ]
            award += sum(values, Fraction(0))
            events.extend(_bonus_events(game.outcomes[index].id, values))
        return index, award, events

    def settle(self, strategy: str, draw) -> tuple[float, list[str]]:
        spec, game = self.spec, self.spec.game
        if game.kind == "crash":
            limits = self.thresholds[strategy]
            paying = tuple(draw >= limit for limit in limits)
            key = paying
            cache = self.settled[strategy]
            if key in cache:
                value = cache[key]
            else:
                bets = self.bets[strategy]
                raws = [w.target if pays else 0 for w, pays in zip(bets, paying)]
                total = _settle(spec, bets, raws).total
                value = float(total) / self.staked[strategy]
                if len(cache) < MAX_CACHED_SETTLEMENTS:
                    cache[key] = value
            events = [
                f"pays:{w.id}" for w, pays in zip(self.bets[strategy], paying) if pays
            ]
            return value, events
        _, award, events = draw
        cache = self.settled[strategy]
        if award in cache:
            value = cache[award]
        else:
            bets = self.bets[strategy]
            total = _settle(spec, bets, [award] * len(bets)).total
            value = float(total) / self.staked[strategy]
            if len(cache) < MAX_CACHED_SETTLEMENTS:
                cache[award] = value
        return value, events


def _cumulative(probabilities: Iterable[Fraction]) -> list[float]:
    total, rows = Fraction(0), []
    for probability in probabilities:
        total += probability
        rows.append(float(total))
    return rows


def _bonus_events(outcome: str, values: Iterable[Fraction]) -> list[str]:
    return [
        f"bonus:{outcome}:{draw}:{value}"
        for draw, value in enumerate(values)
        if value > 0
    ]


def _record_events(spec: GameMathSpec, record: RoundRecord) -> list[str]:
    game = spec.game
    if game.kind == "crash":
        strategy = next(row for row in spec.strategies if row.id == record.strategy)
        return [
            f"pays:{w.id}"
            for w in spec.bets(strategy)
            if record.multiplier >= game.threshold(w.target)
        ]
    return [f"outcome:{record.outcome}", *_bonus_events(record.outcome, record.bonus)]


def assess_game_math(
    spec: GameMathSpec,
    *,
    settlements: Iterable[RoundRecord | None] = (),
    simulation: SimulationRequest | None = None,
    records: Iterable[RoundRecord | None] | None = None,
    should_stop: Callable[[], bool] = lambda: False,
    clock: Callable[[], float] = time.monotonic,
    max_support: int = MAX_SUPPORT,
) -> GameMathAssessment:
    """Evaluate ``spec`` exactly, check recorded behavior, and optionally simulate.

    ``settlements`` are targeted implementation cases; ``records`` are a
    recorded implementation run for a ``records`` simulation. ``None`` stands
    for an unusable row, which keeps the result partial.
    """
    theory, found = _theory(spec, max_support)
    violations = _Violations()
    for violation in found:
        violations.add(violation)
    rounds = _Rounds(spec)
    unusable = cases = 0
    for row in settlements:
        cases += 1
        if cases > MAX_SETTLEMENT_CASES:
            raise ValueError("too many settlement cases")
        try:
            if row is None:
                raise ValueError("unusable settlement case")
            rounds.check(row, violations)
        except ValueError:
            unusable += 1
    limitations = ["certification_not_performed"]
    if spec.provenance.approval_ref is None:
        limitations.append("approval_reference_absent")
    run_result = None
    if simulation is not None:
        run = _Run(spec, theory)
        deadline = clock() + simulation.max_seconds
        stop_reason = None
        unusable_records = 0

        def stop() -> str | None:
            if should_stop():
                return "cancelled"
            return "time_budget_exhausted" if clock() >= deadline else None

        if simulation.source == "model":
            sampler = _Sampler(spec, random.Random(simulation.seed))
            for index in range(simulation.samples):
                if index % _CHECK_EVERY == 0 and (stop_reason := stop()):
                    break
                draw = sampler.draw()
                for strategy in spec.strategies:
                    value, events = sampler.settle(strategy.id, draw)
                    run.add(strategy.id, value, events)
        else:
            staked = {
                row.id: sum((w.stake for w in spec.bets(row)), Fraction(0))
                for row in spec.strategies
            }
            for index, row in enumerate(records or ()):
                if index % _CHECK_EVERY == 0 and (stop_reason := stop()):
                    break
                try:
                    if row is None:
                        raise ValueError("unusable record")
                    rounds.check(row, violations)
                except ValueError:
                    unusable_records += 1
                    continue
                run.add(
                    row.strategy,
                    float(row.total / staked[row.strategy]),
                    _record_events(spec, row),
                )
        stop_reason = stop_reason or stop()
        results = run.results(stop_reason is not None)
        missing_samples = any(row.samples == 0 for row in results)
        for row in results:
            if row.decision == "different":
                violations.add(
                    Violation(
                        rule="simulated-rtp",
                        strategy=row.strategy,
                        case="rtp",
                        expected=f"{row.target} +/- {row.tolerance}",
                        observed=f"[{row.low:.6f}, {row.high:.6f}]",
                    )
                )
        run_result = Simulation(
            source=simulation.source,
            generator=GENERATOR if simulation.source == "model" else None,
            seed=simulation.seed,
            requested_samples=simulation.samples,
            status=(
                "partial"
                if stop_reason or unusable_records or missing_samples
                else "complete"
            ),
            stop_reason=stop_reason,
            unusable_records=unusable_records,
            results=results,
        )
        if stop_reason == "cancelled":
            limitations.append("simulation_cancelled")
        elif stop_reason:
            limitations.append(stop_reason)
        unusable += unusable_records
        if simulation.source == "records" and missing_samples:
            limitations.append("unobserved_strategies")
        if any(row.rare_event_coverage == "incomplete" for row in results):
            limitations.append("rare_events_undercovered")
    decided = {
        row.strategy
        for row in (run_result.results if run_result else [])
        if row.decision in {"equivalent", "different"}
    }
    intractable = [
        row.strategy
        for row in theory
        if row.status == "intractable" and row.strategy not in decided
    ]
    if any(row.status == "intractable" for row in theory):
        limitations.append("theoretical_intractable")
    if unusable:
        limitations.append("unusable_records")
    if violations.truncated:
        limitations.append("violations_truncated")
    partial = bool(
        intractable
        or unusable
        or violations.truncated
        or (run_result and run_result.status == "partial")
    )
    decisions = {row.decision for row in (run_result.results if run_result else [])}
    if violations.rows:
        outcome = "violations"
    elif partial:
        outcome = "incomplete"
    elif decisions - {"equivalent"}:
        outcome = "insufficient_precision"
    else:
        outcome = "consistent"
    return GameMathAssessment(
        game_ref=spec.game_ref,
        spec_digest="sha256:"
        + content_key(spec.model_dump(mode="json", by_alias=True)),
        provenance=spec.provenance,
        criteria=spec.criteria,
        theoretical=theory,
        settlement_cases=cases,
        simulation=run_result,
        violations=list(violations.rows.values()),
        status="partial" if partial else "complete",
        outcome=outcome,
        limitations=limitations,
    )


def _rows(path: str | None, limit: int | None = None):
    """Recorded rounds as JSON Lines; an unusable line yields None."""
    if path is None:
        return
    with open(path, encoding="utf-8") as handle:
        for index, line in enumerate(handle):
            if limit is not None and index >= limit:
                raise ValueError("too many settlement cases")
            if not line.strip():
                continue
            try:
                yield RoundRecord.model_validate_json(line)
            except ValidationError:
                yield None


def main(argv: list[str] | None = None) -> int:
    """Run one assessment inside a workload-profile container job.

    SIGTERM (the job's cancellation) stops sampling; the partial result is
    still written before exit.
    """
    parser = argparse.ArgumentParser(prog="python -m moonmind.security.game_math")
    parser.add_argument("--spec", required=True)
    parser.add_argument("--settlements")
    parser.add_argument("--records")
    parser.add_argument("--samples", type=int)
    parser.add_argument("--seed", type=int)
    parser.add_argument("--max-seconds", type=int, default=300)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    stopping = []
    previous = {
        number: signal.signal(number, lambda *_: stopping.append(True))
        for number in (signal.SIGTERM, signal.SIGINT)
    }
    # The stop handler stays installed until the result is written, so a
    # late cancellation cannot kill the process before its partial evidence.
    try:
        try:
            spec = GameMathSpec.model_validate_json(Path(args.spec).read_bytes())
            simulation = None
            if args.records or args.samples is not None or args.seed is not None:
                simulation = SimulationRequest(
                    source="records" if args.records else "model",
                    samples=args.samples,
                    seed=args.seed,
                    maxSeconds=args.max_seconds,
                )
            print("game math assessment started", file=sys.stderr, flush=True)
            result = assess_game_math(
                spec,
                settlements=list(_rows(args.settlements, MAX_SETTLEMENT_CASES)),
                simulation=simulation,
                records=_rows(args.records),
                should_stop=lambda: bool(stopping),
            )
        except (OSError, ValueError) as exc:
            detail = type(exc).__name__
            if isinstance(exc, ValidationError):
                detail = f"{exc.error_count()} validation errors"
            print(f"invalid game math input: {detail}", file=sys.stderr)
            return 2
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        staged = output.with_name(output.name + ".partial")
        staged.write_text(result.model_dump_json(by_alias=True))
        os.replace(staged, output)
        print(f"game math assessment {result.status}: {result.outcome}", flush=True)
        return 0
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)


if __name__ == "__main__":
    sys.exit(main())
