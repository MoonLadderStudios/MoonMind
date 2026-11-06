"""RNG implementation review and statistical diagnostics with separate assurance.

An ``rng-assessment-request/v1`` document names one generator, the reviewer's
observations of its implementation, the raw-bit diagnostics to run, the
intended distribution of final game outcomes, and any claims an outside
specialist or laboratory reported. Test selection and significance are fixed
with the request, so no evaluation chooses them after seeing its data.

The ``rng-assessment/v1`` result keeps four kinds of evidence apart:

- implementation observations: the review, plus the exact output distribution
  of declared modulo, scaling and rejection-sampling steps;
- raw-bit diagnostics: a subset of NIST SP 800-22 tests over a public fixture
  or a recorded sample;
- mapped outcomes: recorded final game outcomes against the intended model;
- external claims: what an outside verifier reported, never verified here.

Statistical tests cannot establish unpredictability: MT19937 passes them and is
predictable from 624 outputs. Cryptographic strength, certification and game
fairness are therefore set only by a reported external claim. Samples and
outcome sequences never enter the result; a recorded sample appears only as its
digest and size. A cancelled or budget-exhausted run is partial evidence and
cannot report an absence of findings.
"""

from __future__ import annotations

import argparse
import hashlib
import math
import os
import random
import signal
import sys
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from fractions import Fraction
from itertools import accumulate
from pathlib import Path
from statistics import NormalDist
from typing import Annotated, Literal

from pydantic import (
    Field,
    StringConstraints,
    TypeAdapter,
    ValidationError,
    model_validator,
)
from pydantic.alias_generators import to_camel

from moonmind.security.assessments import Contract, Text, content_key
from moonmind.security.game_math import Exact, Ident, Probability

#: Largest raw-bit sample one run analyzes: 2 MiB.
MAX_BITS = 2**24
#: SP 800-22's minimum sequence length for each implemented test.
MIN_BITS = 100
MAX_UNSUPPORTED = 64
TESTS = ("frequency", "block-frequency", "runs", "serial", "cumulative-sums")
#: p-values per test; the Bonferroni family is fixed by the selection.
P_VALUES = {
    "frequency": 1,
    "block-frequency": 1,
    "runs": 1,
    "serial": 2,
    "cumulative-sums": 2,
}
DIMENSIONS = (
    "generator",
    "seeding",
    "restart_fork",
    "state_handling",
    "predictable_inputs",
)
ANALYZED = ("modulo", "scale", "rejection")
_CHUNK = 1 << 16
_CHECK_BITS = 1 << 20
_CHECK_OUTCOMES = 1024
_STEP = {"0": -1, "1": 1}
_IDENT = TypeAdapter(Ident)

TestName = Literal["frequency", "block-frequency", "runs", "serial", "cumulative-sums"]
StopReason = Literal["cancelled", "time_budget_exhausted"]
Refs = Annotated[list[Text], Field(max_length=16)]
Steps = Annotated[list[Text], Field(max_length=16)]
Alpha = Annotated[Exact, Field(gt=0, le=Fraction(1, 2))]
Digest = Annotated[str, StringConstraints(pattern=r"^sha256:[0-9a-f]{64}$")]


def _unique(values: list) -> None:
    if len(values) != len(set(values)):
        raise ValueError("identities must be unique")


class ReviewNote(Contract):
    """One reviewed aspect: what the reviewer observed, and where."""

    status: Literal["concern", "no_concern_noted", "not_reviewed"] = "not_reviewed"
    summary: Text | None = None
    evidence_refs: Refs = Field(default_factory=list)

    @model_validator(mode="after")
    def _stated(self):
        if self.status != "not_reviewed" and self.summary is None:
            raise ValueError("a reviewed aspect states what was observed")
        return self


class Transformation(Contract):
    """One step mapping generator output to a game value, as the code does it.

    For an ``inputBits``-bit value k, ``modulo`` returns k mod range, ``scale``
    returns floor(k * range / 2**inputBits), and ``rejection`` redraws k at or
    above the largest multiple of range before taking k mod range. Their exact
    distributions are computed; ``shuffle`` and ``other`` steps carry only the
    review.
    """

    id: Ident
    kind: Literal["modulo", "scale", "rejection", "shuffle", "other"]
    input_bits: Annotated[int, Field(ge=1, le=64)] | None = None
    range: Annotated[int, Field(ge=2, le=2**32)] | None = None
    review: ReviewNote = Field(default_factory=ReviewNote)

    @model_validator(mode="after")
    def _valid(self):
        analyzed = self.kind in ANALYZED
        if (self.input_bits is not None, self.range is not None) != (analyzed,) * 2:
            raise ValueError(
                "modulo, scale and rejection steps, and only they, declare "
                "inputBits and range"
            )
        if analyzed and self.range > 2**self.input_bits:
            raise ValueError("range exceeds the input values")
        return self


class ImplementationReview(Contract):
    """Observations of how the generator is built and used, never of its output."""

    #: Generator and API selection.
    generator: ReviewNote = Field(default_factory=ReviewNote)
    #: Entropy sources and seeding assumptions.
    seeding: ReviewNote = Field(default_factory=ReviewNote)
    #: Behavior across process restart and fork.
    restart_fork: ReviewNote = Field(default_factory=ReviewNote)
    state_handling: ReviewNote = Field(default_factory=ReviewNote)
    #: Predictable inputs or fallbacks.
    predictable_inputs: ReviewNote = Field(default_factory=ReviewNote)
    transformations: Annotated[list[Transformation], Field(max_length=32)] = Field(
        default_factory=list
    )

    @model_validator(mode="after")
    def _valid(self):
        _unique([row.id for row in self.transformations])
        return self


class Fixture(Contract):
    """A public deterministic generator; its seed is public, not protected state."""

    name: Literal["sha256-counter", "mt19937", "biased"]
    seed: Annotated[int, Field(ge=0, lt=2**63)]


class SampleOrigin(Contract):
    kind: Literal["fixture", "recorded"]
    fixture: Fixture | None = None
    #: Where and how a recorded sample was captured.
    description: Text | None = None
    #: Steps between the generator and the sampled bits, in order.
    transformations: Steps = Field(default_factory=list)

    @model_validator(mode="after")
    def _valid(self):
        if (self.kind == "fixture") != (self.fixture is not None):
            raise ValueError("a fixture origin, and only it, names a fixture")
        if self.kind == "recorded" and self.description is None:
            raise ValueError("a recorded sample states where it was captured")
        return self


class Diagnostics(Contract):
    """Raw-bit tests and their significance, fixed before any data is read."""

    origin: SampleOrigin
    #: Bits to analyze; a recorded sample may hold fewer.
    bits: Annotated[int, Field(ge=1, le=MAX_BITS)]
    tests: Annotated[list[TestName], Field(min_length=1, max_length=len(TESTS))] = (
        Field(default_factory=lambda: list(TESTS))
    )
    #: Family significance level, shared by every selected p-value.
    alpha: Alpha = Fraction(1, 100)
    block_size: Annotated[int, Field(ge=2, le=MAX_BITS)] | None = None
    pattern_length: Annotated[int, Field(ge=2, le=16)] | None = None

    @model_validator(mode="after")
    def _valid(self):
        _unique(self.tests)
        return self


class ModelOutcome(Contract):
    id: Ident
    probability: Probability


class OutcomeModel(Contract):
    """The intended distribution of final game outcomes, and how it is tested."""

    #: Where and how the recorded outcomes were captured.
    description: Text
    #: Steps from generator output to the recorded outcome, in order.
    transformations: Steps = Field(default_factory=list)
    model: Annotated[list[ModelOutcome], Field(min_length=2, max_length=4096)]
    alpha: Alpha = Fraction(1, 100)
    #: The chi-square approximation needs every expected count at least this.
    min_expected: Annotated[int, Field(ge=1, le=1_000_000)] = 5

    @model_validator(mode="after")
    def _valid(self):
        _unique([row.id for row in self.model])
        if sum((row.probability for row in self.model), Fraction(0)) != 1:
            raise ValueError("probabilities must sum to exactly 1")
        return self


Claim = Literal["cryptographic_strength", "rng_certification", "game_fairness"]


class ExternalClaim(Contract):
    """What an outside specialist or laboratory reported about the generator."""

    claim: Claim
    verifier: Text
    #: The operator's reference to the verifier's report or certificate.
    report_ref: Text | None = None
    #: The standard and version applied, with its final or draft status.
    standard: Text | None = None


class RngAssessmentRequest(Contract):
    schema_version: Literal["rng-assessment-request/v1"] = "rng-assessment-request/v1"
    #: The assessed generator; an asset in the assessment scope.
    subject_ref: Text
    review: ImplementationReview | None = None
    diagnostics: Diagnostics | None = None
    outcomes: OutcomeModel | None = None
    external_claims: Annotated[list[ExternalClaim], Field(max_length=16)] = Field(
        default_factory=list
    )

    @model_validator(mode="after")
    def _valid(self):
        if self.review is None and self.diagnostics is None and self.outcomes is None:
            raise ValueError("an assessment reviews, diagnoses or evaluates outcomes")
        return self


Finding = tuple[str, str, str]


class DimensionResult(ReviewNote):
    dimension: Text


class TransformationResult(Transformation):
    distribution: Literal["uniform", "biased", "not_analyzed"]
    min_probability: Exact | None = None
    max_probability: Exact | None = None
    #: Share of draws a rejection step discards and redraws.
    rejection_rate: Exact | None = None

    @model_validator(mode="after")
    def _exact(self):
        computed = _distribution(self)
        if any(getattr(self, key) != value for key, value in computed.items()):
            raise ValueError("distribution must follow the declared step")
        return self


def _distribution(step: Transformation) -> dict:
    """The exact output distribution of an analyzable step."""
    values = {
        "distribution": "not_analyzed",
        "min_probability": None,
        "max_probability": None,
        "rejection_rate": None,
    }
    if step.kind not in ANALYZED:
        return values
    inputs, outcomes = 2**step.input_bits, step.range
    if step.kind == "rejection":
        exact = Fraction(1, outcomes)
        return values | {
            "distribution": "uniform",
            "min_probability": exact,
            "max_probability": exact,
            "rejection_rate": Fraction(inputs % outcomes, inputs),
        }
    # Modulo and scaling give each outcome the floor or ceiling of inputs/range.
    low = Fraction(inputs // outcomes, inputs)
    high = Fraction(-(-inputs // outcomes), inputs)
    return values | {
        "distribution": "uniform" if low == high else "biased",
        "min_probability": low,
        "max_probability": high,
    }


def _review_coverage(dimensions, transformations) -> str:
    reviewed = all(row.status != "not_reviewed" for row in dimensions) and all(
        row.distribution != "not_analyzed" or row.review.status != "not_reviewed"
        for row in transformations
    )
    return "complete" if reviewed else "partial"


class ReviewResult(Contract):
    dimensions: list[DimensionResult]
    transformations: list[TransformationResult]
    coverage: Literal["complete", "partial"]

    @model_validator(mode="after")
    def _consistent(self):
        if [row.dimension for row in self.dimensions] != list(
            map(to_camel, DIMENSIONS)
        ):
            raise ValueError("a review records every dimension in order")
        if self.coverage != _review_coverage(self.dimensions, self.transformations):
            raise ValueError("coverage must follow the reviewed dimensions")
        return self

    @property
    def partial(self) -> bool:
        return self.coverage == "partial"

    @property
    def insufficient(self) -> bool:
        return False

    @property
    def covered(self) -> bool:
        return self.coverage == "complete"

    def findings(self) -> list[Finding]:
        rows = [
            ("rng-review", f"review:{row.dimension}", row.dimension)
            for row in self.dimensions
            if row.status == "concern"
        ]
        for row in self.transformations:
            if row.review.status == "concern":
                rows.append(("rng-review", "review:transformation", row.id))
            if row.distribution == "biased":
                rows.append(("rng-review", "transformation-bias", row.id))
        return rows

    def rules(self) -> list[str]:
        """Rules whose absence this review establishes."""
        rules = [
            f"review:{row.dimension}"
            for row in self.dimensions
            if row.status != "not_reviewed"
        ]
        steps = self.transformations
        if steps and all(row.review.status != "not_reviewed" for row in steps):
            rules.append("review:transformation")
        if any(row.distribution != "not_analyzed" for row in steps):
            rules.append("transformation-bias")
        return rules


class SampleRecord(Contract):
    """What was analyzed, identified without exporting it."""

    origin: Literal["fixture", "recorded"]
    fixture: Fixture | None
    description: Text | None
    transformations: list[Text]
    requested_bits: int
    bits: int
    #: SHA-256 of the bytes read; the bytes themselves are never retained.
    digest: Digest


BitDecision = Literal[
    "anomaly", "no_anomaly_detected", "insufficient_sample", "not_run"
]


class BitTest(Contract):
    test: TestName
    bits: int
    min_bits: int
    settings: dict[str, int]
    statistics: dict[str, float]
    p_values: list[float]
    #: Each p-value is compared with this share of the family alpha.
    threshold: Exact
    decision: BitDecision
    note: Text | None = None

    @model_validator(mode="after")
    def _decided(self):
        decided = self.decision in {"anomaly", "no_anomaly_detected"}
        if decided != bool(self.p_values):
            raise ValueError("a decided test, and only it, reports p-values")
        if decided and (min(self.p_values) < self.threshold) != (
            self.decision == "anomaly"
        ):
            raise ValueError("the decision must follow the p-values")
        return self


def _raw_outcome(decisions: set[str], partial: bool) -> str:
    if "anomaly" in decisions:
        return "anomalies"
    if partial:
        return "incomplete"
    if "insufficient_sample" in decisions:
        return "insufficient_sample"
    return "no_anomaly_detected"


class RawBits(Contract):
    sample: SampleRecord
    tool: Literal["moonmind-rng-diagnostics"] = "moonmind-rng-diagnostics"
    suite: Literal["nist-sp800-22-subset"] = "nist-sp800-22-subset"
    #: The null hypothesis every test assumes.
    expected_distribution: Literal["independent-uniform-bits"] = (
        "independent-uniform-bits"
    )
    alpha: Exact
    correction: Literal["bonferroni"] = "bonferroni"
    tests: Annotated[list[BitTest], Field(min_length=1, max_length=len(TESTS))]
    status: Literal["complete", "partial"]
    stop_reason: StopReason | None = None
    outcome: Literal[
        "anomalies", "no_anomaly_detected", "insufficient_sample", "incomplete"
    ]

    @model_validator(mode="after")
    def _consistent(self):
        decisions = {row.decision for row in self.tests}
        if self.partial != (self.stop_reason is not None):
            raise ValueError("a run is partial exactly when it stopped")
        if not self.partial and "not_run" in decisions:
            raise ValueError("a complete run decides every selected test")
        if self.outcome != _raw_outcome(decisions, self.partial):
            raise ValueError("the outcome must follow every test")
        return self

    @property
    def partial(self) -> bool:
        return self.status == "partial"

    @property
    def insufficient(self) -> bool:
        return any(row.decision == "insufficient_sample" for row in self.tests)

    @property
    def covered(self) -> bool:
        """Whether every selected test reached a decision."""
        return not self.partial and not self.insufficient

    def findings(self) -> list[Finding]:
        return [
            ("rng-diagnostics", f"raw-bits:{row.test}", row.test)
            for row in self.tests
            if row.decision == "anomaly"
        ]

    def rules(self) -> list[str]:
        return [
            f"raw-bits:{row.test}"
            for row in self.tests
            if row.decision in {"anomaly", "no_anomaly_detected"}
        ]


class OutcomeRow(Contract):
    outcome: Ident
    probability: Exact
    expected: float
    observed: int
    frequency: float | None
    #: Wilson score interval for this outcome's frequency.
    low: float
    high: float
    #: Adjusted residual: (observed - expected) / sqrt(expected * (1 - p)).
    residual: float | None


def _mapped_decision(unsupported: int, p_value, alpha, partial: bool) -> str:
    if unsupported or (p_value is not None and p_value < alpha):
        return "deviation_detected"
    if partial:
        return "incomplete"
    if p_value is None:
        return "insufficient_sample"
    return "no_deviation_detected"


class MappedOutcomes(Contract):
    description: Text
    transformations: list[Text]
    samples: int
    unusable: int
    unsupported_observed: int
    unsupported_ids: Annotated[list[Ident], Field(max_length=MAX_UNSUPPORTED)]
    method: Literal["chi-square-goodness-of-fit"] = "chi-square-goodness-of-fit"
    alpha: Exact
    interval_confidence: Exact
    min_expected: int
    statistic: float | None
    degrees_of_freedom: int
    p_value: float | None
    rows: list[OutcomeRow]
    status: Literal["complete", "partial"]
    stop_reason: StopReason | None = None
    decision: Literal[
        "deviation_detected",
        "no_deviation_detected",
        "insufficient_sample",
        "incomplete",
    ]

    @model_validator(mode="after")
    def _consistent(self):
        if self.partial != (self.stop_reason is not None or self.unusable > 0):
            raise ValueError("outcomes are partial exactly when stopped or unusable")
        if (self.statistic is None) != (self.p_value is None) or (
            self.p_value is not None
            and min(row.expected for row in self.rows) < self.min_expected
        ):
            raise ValueError("a p-value needs every expected count above the floor")
        if self.decision != _mapped_decision(
            self.unsupported_observed, self.p_value, self.alpha, self.partial
        ):
            raise ValueError("the decision must follow the outcome evidence")
        return self

    @property
    def partial(self) -> bool:
        return self.status == "partial"

    @property
    def insufficient(self) -> bool:
        return self.decision == "insufficient_sample"

    @property
    def covered(self) -> bool:
        """Whether every recorded outcome was read and the model was tested."""
        return not self.partial and self.p_value is not None

    def findings(self) -> list[Finding]:
        rows = [
            ("rng-outcomes", "mapped-outcome:support", outcome)
            for outcome in self.unsupported_ids
        ]
        if self.p_value is not None and self.p_value < self.alpha:
            rows.append(("rng-outcomes", "mapped-outcome:distribution", "chi-square"))
        return rows

    def rules(self) -> list[str]:
        if not self.covered:
            return []
        return ["mapped-outcome:distribution", "mapped-outcome:support"]


class ClaimRecord(ExternalClaim):
    #: ``reported`` when the operator supplied the verifier's report reference.
    #: MoonMind verifies neither kind.
    status: Literal["reported", "unverified"]

    @model_validator(mode="after")
    def _reported(self):
        if (self.status == "reported") != (self.report_ref is not None):
            raise ValueError("a claim is reported exactly when it cites a report")
        return self


class Assurance(Contract):
    """What only external evidence can establish; statistics never set it."""

    cryptographic_strength: Literal["not_established", "externally_reported"] = (
        "not_established"
    )
    certification: Literal["not_performed", "externally_reported"] = "not_performed"
    game_fairness: Literal["not_established", "externally_reported"] = "not_established"


def _assurance(claims: Iterable[ClaimRecord]) -> Assurance:
    reported = {row.claim for row in claims if row.status == "reported"}
    return Assurance(
        cryptographic_strength=(
            "externally_reported"
            if "cryptographic_strength" in reported
            else "not_established"
        ),
        certification=(
            "externally_reported"
            if "rng_certification" in reported
            else "not_performed"
        ),
        game_fairness=(
            "externally_reported" if "game_fairness" in reported else "not_established"
        ),
    )


def _sections(implementation, raw_bits, mapped_outcomes) -> list:
    return [
        row for row in (implementation, raw_bits, mapped_outcomes) if row is not None
    ]


def _verdict(sections) -> tuple[str, str]:
    partial = any(section.partial for section in sections)
    if any(section.findings() for section in sections):
        outcome = "findings"
    elif partial:
        outcome = "incomplete"
    elif any(section.insufficient for section in sections):
        outcome = "insufficient_sample"
    else:
        outcome = "no_findings"
    return ("partial" if partial else "complete"), outcome


class RngAssessment(Contract):
    """Implementation, statistical and external evidence kept apart; no certification."""

    schema_version: Literal["rng-assessment/v1"] = "rng-assessment/v1"
    subject_ref: Text
    request_digest: Digest
    implementation: ReviewResult | None
    raw_bits: RawBits | None
    mapped_outcomes: MappedOutcomes | None
    external_claims: list[ClaimRecord]
    assurance: Assurance
    status: Literal["complete", "partial"]
    outcome: Literal["findings", "incomplete", "insufficient_sample", "no_findings"]
    limitations: list[str]

    @model_validator(mode="after")
    def _consistent(self):
        if self.assurance != _assurance(self.external_claims):
            raise ValueError("assurance comes only from reported external claims")
        if (self.status, self.outcome) != _verdict(self.sections):
            raise ValueError("status and outcome must follow every section")
        return self

    @property
    def sections(self) -> list:
        return _sections(self.implementation, self.raw_bits, self.mapped_outcomes)

    def findings(self) -> list[Finding]:
        """(tool, rule, native id) of every finding, in section order."""
        return [row for section in self.sections for row in section.findings()]

    @property
    def interpretation(self) -> str:
        review = self.implementation
        if review is None:
            observed = "not requested"
        else:
            concerns = len(review.findings())
            observed = (
                f"{concerns} concern{'' if concerns == 1 else 's'}, "
                f"review {review.coverage}"
            )
        raw = self.raw_bits.outcome if self.raw_bits else "not_requested"
        mapped = (
            self.mapped_outcomes.decision if self.mapped_outcomes else "not_requested"
        )
        assurance = self.assurance
        return (
            f"Implementation observations: {observed}. Statistical findings: raw "
            f"bits {raw}; mapped outcomes {mapped}. External claims: cryptographic "
            f"strength {assurance.cryptographic_strength}, certification "
            f"{assurance.certification}, game fairness {assurance.game_fairness}. "
            "MoonMind verified no external report, and statistical results "
            "establish none of these."
        )


@dataclass(frozen=True)
class Measured:
    settings: dict[str, int]
    statistics: dict[str, float]
    p_values: tuple[float, ...]
    note: str | None = None


class _Stopped(Exception):
    pass


def _igamc(a: float, x: float) -> float:
    """Regularized upper incomplete gamma Q(a, x)."""
    if x <= 0:
        return 1.0
    scale = math.exp(a * math.log(x) - x - math.lgamma(a))
    if x < a + 1:
        term = total = 1.0 / a
        denominator = a
        while abs(term) > abs(total) * 1e-16:
            denominator += 1
            term *= x / denominator
            total += term
        return min(1.0, max(0.0, 1.0 - total * scale))
    # Modified Lentz continued fraction.
    tiny = 1e-300
    b = x + 1 - a
    c, d = 1 / tiny, 1 / b
    result = d
    for step in range(1, 100_000):
        term = -step * (step - a)
        b += 2
        d = term * d + b
        d = 1 / (d if abs(d) > tiny else tiny)
        c = b + term / c
        c = c if abs(c) > tiny else tiny
        result *= d * c
        if abs(d * c - 1) < 1e-16:
            break
    return min(1.0, max(0.0, result * scale))


def _frequency(bits: str, settings: dict, stop) -> Measured:
    n, ones = len(bits), bits.count("1")
    total = 2 * ones - n
    p = math.erfc(abs(total) / math.sqrt(n) / math.sqrt(2))
    return Measured(settings, {"ones": ones, "sum": total}, (p,))


def _block_frequency(bits: str, settings: dict, stop) -> Measured:
    size = settings["blockSize"]
    blocks = len(bits) // size
    chi = (
        4
        * size
        * sum(
            (bits.count("1", i * size, (i + 1) * size) / size - 0.5) ** 2
            for i in range(blocks)
        )
    )
    return Measured(
        settings | {"blocks": blocks},
        {"chiSquare": chi},
        (_igamc(blocks / 2, chi / 2),),
    )


def _runs(bits: str, settings: dict, stop) -> Measured:
    n = len(bits)
    share = bits.count("1") / n
    if abs(share - 0.5) >= 2 / math.sqrt(n):
        return Measured(
            settings, {"proportion": share}, (0.0,), "frequency prerequisite failed"
        )
    # Pairs "01" and "10" cannot overlap themselves, so counts are exact.
    runs = 1 + bits.count("01") + bits.count("10")
    spread = share * (1 - share)
    p = math.erfc(abs(runs - 2 * n * spread) / (2 * math.sqrt(2 * n) * spread))
    return Measured(settings, {"proportion": share, "runs": runs}, (p,))


def _serial(bits: str, settings: dict, stop) -> Measured:
    n, length = len(bits), settings["patternLength"]
    mask = (1 << length) - 1
    counts = [0] * (1 << length)
    # Overlapping patterns over the sequence read as a cycle.
    wrapped = (bits + bits[: length - 1]).encode()
    window = int(bits[: length - 1], 2)
    for start in range(length - 1, len(wrapped), _CHECK_BITS):
        if stop():
            raise _Stopped
        for value in wrapped[start : start + _CHECK_BITS]:
            window = ((window << 1) | (value & 1)) & mask
            counts[window] += 1
    levels = {length: counts}
    for size in range(length - 1, max(0, length - 3), -1):
        above = levels[size + 1]
        levels[size] = [above[i] + above[i + 1] for i in range(0, len(above), 2)]

    def psi(size: int) -> float:
        if size < 1:
            return 0.0
        return (2**size / n) * sum(count * count for count in levels[size]) - n

    first, second, third = psi(length), psi(length - 1), psi(length - 2)
    delta, delta2 = first - second, first - 2 * second + third
    return Measured(
        settings,
        {"psiSquared": first, "delta": delta, "delta2": delta2},
        (_igamc(2 ** (length - 2), delta / 2), _igamc(2 ** (length - 3), delta2 / 2)),
    )


def _cusum_p(n: int, z: int) -> float:
    def normal(x: float) -> float:
        return 0.5 * math.erfc(-x / math.sqrt(2))

    root, ratio = math.sqrt(n), n / z
    end = math.floor((ratio - 1) / 4)
    first = sum(
        normal((4 * k + 1) * z / root) - normal((4 * k - 1) * z / root)
        for k in range(math.trunc((-ratio + 1) / 4), end + 1)
    )
    second = sum(
        normal((4 * k + 3) * z / root) - normal((4 * k + 1) * z / root)
        for k in range(math.trunc((-ratio - 3) / 4), end + 1)
    )
    return min(1.0, max(0.0, 1 - first + second))


def _cumulative_sums(bits: str, settings: dict, stop) -> Measured:
    n = len(bits)
    total = 2 * bits.count("1") - n
    # Extremes of the partial sums S_0 .. S_{n-1}; S_0 is 0.
    head = bits[:-1]
    high = max(0, max(accumulate(map(_STEP.__getitem__, head)), default=0))
    low = min(0, min(accumulate(map(_STEP.__getitem__, head)), default=0))
    forward = max(high, -low, abs(total))
    backward = max(abs(total - low), abs(total - high))
    return Measured(
        settings,
        {"forwardMaximum": forward, "backwardMaximum": backward},
        (_cusum_p(n, forward), _cusum_p(n, backward)),
    )


_MEASURES = {
    "frequency": _frequency,
    "block-frequency": _block_frequency,
    "runs": _runs,
    "serial": _serial,
    "cumulative-sums": _cumulative_sums,
}


def _settings(
    test: str, n: int, block_size: int | None, pattern_length: int | None
) -> tuple[dict[str, int], int]:
    """Settings for ``test`` over ``n`` bits, and the bits it needs."""
    if test == "block-frequency":
        if block_size is not None:
            return {"blockSize": block_size}, max(MIN_BITS, block_size)
        # SP 800-22 recommends M >= 20, M > n/100 and fewer than 100 blocks.
        return {"blockSize": max(20, n // 99 + 1)}, MIN_BITS
    if test == "serial":
        if pattern_length is not None:
            return {"patternLength": pattern_length}, max(
                MIN_BITS, 2 ** (pattern_length + 3)
            )
        # SP 800-22 recommends m < floor(log2 n) - 2.
        return {"patternLength": max(2, min(16, n.bit_length() - 4))}, MIN_BITS
    return {}, MIN_BITS


def measure(
    test: str,
    bits: str,
    *,
    block_size: int | None = None,
    pattern_length: int | None = None,
) -> Measured:
    """One raw-bit test over a string of ``0`` and ``1``, regardless of minimums."""
    settings, _ = _settings(test, len(bits), block_size, pattern_length)
    return _MEASURES[test](bits, settings, lambda: False)


def fixture_sample(
    name: str, seed: int, size: int, should_stop: Callable[[], bool] = lambda: False
) -> bytes:
    """``size`` bytes of a public deterministic fixture, fewer when stopped.

    ``sha256-counter`` is a healthy reference: SHA-256 of the seed and a
    counter. ``mt19937`` is Python's Mersenne Twister, statistically plausible
    and predictable from 624 outputs. ``biased`` sets each bit with
    probability 17/32, a known anomaly. Every fixture is predictable from its
    public seed.
    """
    generator = random.Random(seed)
    chunks, total, counter = [], 0, 0
    while total < size and not should_stop():
        step = min(_CHUNK, size - total)
        if name == "sha256-counter":
            blocks = []
            for _ in range(-(-step // 32)):
                material = seed.to_bytes(8, "big") + counter.to_bytes(8, "big")
                blocks.append(hashlib.sha256(material).digest())
                counter += 1
            chunk = b"".join(blocks)[:step]
        elif name == "mt19937":
            chunk = generator.randbytes(step)
        elif name == "biased":
            words = [int.from_bytes(generator.randbytes(step), "big") for _ in range(5)]
            value = words[0] | (words[1] & words[2] & words[3] & words[4])
            chunk = value.to_bytes(step, "big")
        else:
            raise ValueError(f"unknown fixture {name}")
        chunks.append(chunk)
        total += step
    return b"".join(chunks)


class _Budget:
    """Cancellation and time budget; once stopped, the reason stays."""

    def __init__(self, should_stop, clock, max_seconds: int):
        self.should_stop, self.clock = should_stop, clock
        self.deadline = clock() + max_seconds
        self.reason: str | None = None

    def __call__(self) -> bool:
        if self.reason is None:
            if self.should_stop():
                self.reason = "cancelled"
            elif self.clock() >= self.deadline:
                self.reason = "time_budget_exhausted"
        return self.reason is not None


def _review(review: ImplementationReview) -> ReviewResult:
    dimensions = [
        DimensionResult(dimension=to_camel(name), **getattr(review, name).model_dump())
        for name in DIMENSIONS
    ]
    transformations = [
        TransformationResult(**step.model_dump(), **_distribution(step))
        for step in review.transformations
    ]
    return ReviewResult(
        dimensions=dimensions,
        transformations=transformations,
        coverage=_review_coverage(dimensions, transformations),
    )


def _diagnose(diagnostics: Diagnostics, sample: bytes | None, budget) -> RawBits:
    origin = diagnostics.origin
    size = -(-diagnostics.bits // 8)
    if origin.kind == "fixture":
        if sample is not None:
            raise ValueError("a fixture origin generates its own sample")
        data = fixture_sample(origin.fixture.name, origin.fixture.seed, size, budget)
    else:
        if sample is None:
            raise ValueError("a recorded origin needs its recorded sample")
        data = sample[:size]
    n = min(diagnostics.bits, len(data) * 8)
    bits = format(int.from_bytes(data, "big"), f"0{len(data) * 8}b")[:n] if n else ""
    threshold = diagnostics.alpha / sum(P_VALUES[test] for test in diagnostics.tests)
    rows = []
    for test in (name for name in TESTS if name in diagnostics.tests):
        settings, minimum = _settings(
            test, n, diagnostics.block_size, diagnostics.pattern_length
        )
        row = {
            "test": test,
            "bits": n,
            "min_bits": minimum,
            "settings": settings,
            "statistics": {},
            "p_values": [],
            "threshold": threshold,
        }
        if budget():
            rows.append(BitTest(**row, decision="not_run"))
            continue
        if n < minimum:
            rows.append(BitTest(**row, decision="insufficient_sample"))
            continue
        try:
            measured = _MEASURES[test](bits, settings, budget)
        except _Stopped:
            rows.append(BitTest(**row, decision="not_run"))
            continue
        rows.append(
            BitTest(
                **row
                | {
                    "settings": measured.settings,
                    "statistics": measured.statistics,
                    "p_values": list(measured.p_values),
                },
                decision=(
                    "anomaly"
                    if min(measured.p_values) < threshold
                    else "no_anomaly_detected"
                ),
                note=measured.note,
            )
        )
    partial = budget.reason is not None
    return RawBits(
        sample=SampleRecord(
            origin=origin.kind,
            fixture=origin.fixture,
            description=origin.description,
            transformations=origin.transformations,
            requested_bits=diagnostics.bits,
            bits=n,
            digest="sha256:" + hashlib.sha256(data).hexdigest(),
        ),
        alpha=diagnostics.alpha,
        tests=rows,
        status="partial" if partial else "complete",
        stop_reason=budget.reason,
        outcome=_raw_outcome({row.decision for row in rows}, partial),
    )


def _wilson(observed: int, samples: int, z: float) -> tuple[float, float]:
    if not samples:
        return 0.0, 1.0
    share, spread = observed / samples, z * z / samples
    centre = (share + spread / 2) / (1 + spread)
    half = (
        z
        * math.sqrt(share * (1 - share) / samples + spread / (4 * samples))
        / (1 + spread)
    )
    return max(0.0, centre - half), min(1.0, centre + half)


def _evaluate(model: OutcomeModel, outcomes, budget) -> MappedOutcomes:
    """Recorded final outcomes against the intended model; only counts are kept."""
    counts = {row.id: 0 for row in model.model}
    unsupported: dict[str, None] = {}
    unsupported_observed = unusable = 0
    if not budget():
        for index, value in enumerate(outcomes, 1):
            if value in counts:
                counts[value] += 1
            elif value is None or not _valid_ident(value):
                unusable += 1
            else:
                unsupported_observed += 1
                if len(unsupported) < MAX_UNSUPPORTED:
                    unsupported[value] = None
            if index % _CHECK_OUTCOMES == 0 and budget():
                break
    samples = sum(counts.values())
    z = NormalDist().inv_cdf(1 - float(model.alpha) / 2)
    rows = []
    for row in model.model:
        observed, share = counts[row.id], float(row.probability)
        expected = samples * share
        low, high = _wilson(observed, samples, z)
        rows.append(
            OutcomeRow(
                outcome=row.id,
                probability=row.probability,
                expected=expected,
                observed=observed,
                frequency=observed / samples if samples else None,
                low=low,
                high=high,
                residual=(
                    (observed - expected) / math.sqrt(expected * (1 - share))
                    if samples
                    else None
                ),
            )
        )
    sufficient = samples > 0 and min(row.expected for row in rows) >= model.min_expected
    statistic = (
        sum((row.observed - row.expected) ** 2 / row.expected for row in rows)
        if sufficient
        else None
    )
    degrees = len(rows) - 1
    p_value = _igamc(degrees / 2, statistic / 2) if sufficient else None
    partial = budget.reason is not None or unusable > 0
    return MappedOutcomes(
        description=model.description,
        transformations=model.transformations,
        samples=samples,
        unusable=unusable,
        unsupported_observed=unsupported_observed,
        unsupported_ids=sorted(unsupported),
        alpha=model.alpha,
        interval_confidence=1 - model.alpha,
        min_expected=model.min_expected,
        statistic=statistic,
        degrees_of_freedom=degrees,
        p_value=p_value,
        rows=rows,
        status="partial" if partial else "complete",
        stop_reason=budget.reason,
        decision=_mapped_decision(unsupported_observed, p_value, model.alpha, partial),
    )


def _valid_ident(value: object) -> bool:
    try:
        _IDENT.validate_python(value)
    except ValidationError:
        return False
    return True


def assess_rng(
    request: RngAssessmentRequest,
    *,
    sample: bytes | None = None,
    outcomes: Iterable[str | None] | None = None,
    max_seconds: int = 300,
    should_stop: Callable[[], bool] = lambda: False,
    clock: Callable[[], float] = time.monotonic,
) -> RngAssessment:
    """Review, diagnose and evaluate one generator within a time budget.

    ``sample`` is a recorded sample's bytes; ``outcomes`` are recorded final
    outcome identities, where ``None`` stands for an unusable row.
    """
    if (request.outcomes is None) != (outcomes is None):
        raise ValueError("an outcome model and recorded outcomes go together")
    if request.diagnostics is None and sample is not None:
        raise ValueError("a sample needs raw-bit diagnostics")
    if max_seconds < 1:
        raise ValueError("the time budget must be positive")
    budget = _Budget(should_stop, clock, max_seconds)
    implementation = _review(request.review) if request.review else None
    raw_bits = (
        _diagnose(request.diagnostics, sample, budget) if request.diagnostics else None
    )
    mapped = _evaluate(request.outcomes, outcomes, budget) if request.outcomes else None
    claims = [
        ClaimRecord(
            **row.model_dump(),
            status="reported" if row.report_ref is not None else "unverified",
        )
        for row in request.external_claims
    ]
    limitations = []
    if implementation and implementation.partial:
        limitations.append("review_incomplete")
    if raw_bits:
        limitations.append("statistics_do_not_establish_unpredictability")
        if raw_bits.sample.origin == "fixture":
            limitations.append("public_fixture_sample")
        if raw_bits.sample.bits < raw_bits.sample.requested_bits:
            limitations.append("sample_shorter_than_requested")
    if mapped:
        limitations.append("outcome_tests_do_not_establish_fairness")
        if mapped.unusable:
            limitations.append("unusable_outcomes")
    if any(section.insufficient for section in (raw_bits, mapped) if section):
        limitations.append("below_minimum_sample")
    if budget.reason == "cancelled":
        limitations.append("run_cancelled")
    elif budget.reason:
        limitations.append(budget.reason)
    if claims:
        limitations.append("external_claims_not_verified")
    assurance = _assurance(claims)
    if assurance.certification == "not_performed":
        limitations.append("certification_not_performed")
    status, outcome = _verdict(_sections(implementation, raw_bits, mapped))
    return RngAssessment(
        subject_ref=request.subject_ref,
        request_digest="sha256:"
        + content_key(request.model_dump(mode="json", by_alias=True)),
        implementation=implementation,
        raw_bits=raw_bits,
        mapped_outcomes=mapped,
        external_claims=claims,
        assurance=assurance,
        status=status,
        outcome=outcome,
        limitations=limitations,
    )


class _OutcomeRecord(Contract):
    outcome: Ident


def _outcomes(path: str):
    """Recorded final outcomes as JSON Lines; an unusable line yields None."""
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                yield _OutcomeRecord.model_validate_json(line).outcome
            except ValidationError:
                yield None


def main(argv: list[str] | None = None) -> int:
    """Run one assessment inside a workload-profile container job.

    SIGTERM (the job's cancellation) stops the run between and within tests;
    the partial result is still written before exit.
    """
    parser = argparse.ArgumentParser(prog="python -m moonmind.security.rng_assessment")
    parser.add_argument("--request", required=True)
    parser.add_argument("--sample")
    parser.add_argument("--outcomes")
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
            request = RngAssessmentRequest.model_validate_json(
                Path(args.request).read_bytes()
            )
            sample = None
            if args.sample is not None:
                size = -(-request.diagnostics.bits // 8) if request.diagnostics else 0
                with open(args.sample, "rb") as handle:
                    sample = handle.read(size)
            print("rng assessment started", file=sys.stderr, flush=True)
            result = assess_rng(
                request,
                sample=sample,
                outcomes=_outcomes(args.outcomes) if args.outcomes else None,
                max_seconds=args.max_seconds,
                should_stop=lambda: bool(stopping),
            )
        except (OSError, ValueError) as exc:
            detail = type(exc).__name__
            if isinstance(exc, ValidationError):
                detail = f"{exc.error_count()} validation errors"
            print(f"invalid rng assessment input: {detail}", file=sys.stderr)
            return 2
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        staged = output.with_name(output.name + ".partial")
        staged.write_text(result.model_dump_json(by_alias=True))
        os.replace(staged, output)
        print(f"rng assessment {result.status}: {result.outcome}", flush=True)
        return 0
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)


if __name__ == "__main__":
    sys.exit(main())
