"""Bounded RNG observations, never a cryptographic or certification verdict.

Raw bytes and final mapped outcomes are independent captured inputs. Only
aggregate counts leave the evaluator. Statistical tests cover marginal bit,
disjoint pair and declared outcome frequencies, not unpredictability or general
independence. Review observations and external claims keep their own provenance.
"""

from __future__ import annotations

import argparse
import math
import os
import re
import signal
import sys
import time
from collections import Counter
from collections.abc import Callable, Iterable
from fractions import Fraction
from pathlib import Path
from typing import Annotated, Literal

from pydantic import AfterValidator, Field, ValidationError, model_validator

from moonmind.security.assessments import Contract, Text, content_key
from moonmind.security.game_math import Ident, Probability
from moonmind.utils.logging import redact_sensitive_text

REVIEW_TOPICS = (
    "generator-api",
    "entropy-seeding",
    "restart-fork",
    "state-handling",
    "predictable-inputs-fallbacks",
    "scaling",
    "shuffling",
    "rejection-sampling",
    "other-transformations",
)
ReviewTopic = Literal[
    "generator-api",
    "entropy-seeding",
    "restart-fork",
    "state-handling",
    "predictable-inputs-fallbacks",
    "scaling",
    "shuffling",
    "rejection-sampling",
    "other-transformations",
]
RawTest = Literal["bit-frequency", "pair-frequency"]
StopReason = Literal["cancelled", "time_budget", "input_incomplete", "invalid_samples"]
Decision = Literal[
    "anomaly", "no_anomaly_detected", "insufficient_sample", "incomplete"
]
INTERPRETATION = (
    "Statistical diagnostics do not establish cryptographic strength, RNG "
    "certification or game fairness. Implementation observations are reviewer "
    "records, not automated source analysis. External verifier/report references "
    "are supplied provenance only; MoonMind has not authenticated those claims. "
    "Unavailable specialist or laboratory verification remains Unverified."
)
LIMITATIONS = [
    "frequency_subset_only_no_general_independence_or_unpredictability_test",
    "no_anomaly_detected_is_not_proof_of_the_intended_distribution",
    "bounds_assume_independent_draws_and_preselected_tests_and_significance",
    "no_live_seed_state_or_raw_sample_export",
]


def _safe_text(value: str) -> str:
    # Extend the maintained credential redactor only for this evidence type's
    # seed/state/sample assignments. Unstructured prose must still be nonsensitive.
    value = re.sub(
        r"(?i)\b((?:[\w.-]*[_-])?(?:seed|state|samples?)(?:[_-][\w.-]*)?\s*[:=]\s*)"
        r"(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)",
        r"\1[REDACTED]",
        value,
    )
    return redact_sensitive_text(value)


SafeText = Annotated[Text, AfterValidator(_safe_text)]
Refs = Annotated[list[SafeText], Field(max_length=64)]


class ReviewObservation(Contract):
    topic: ReviewTopic
    status: Literal["reviewed", "concern", "unverified"] = "unverified"
    observation: SafeText = "Unverified: reviewer evidence unavailable."
    evidence_refs: Refs = Field(default_factory=list)

    @model_validator(mode="after")
    def _evidence(self):
        if self.status != "unverified" and not self.evidence_refs:
            raise ValueError("reviewed observations require evidence references")
        return self


class ExternalClaim(Contract):
    claim: SafeText
    report_ref: SafeText
    verifier_ref: SafeText | None = None
    authentication: Literal["unverified"] = "unverified"


class SamplePlan(Contract):
    origin_ref: SafeText
    transformations: Annotated[list[SafeText], Field(min_length=1, max_length=32)]
    #: Bytes for raw inputs; draws for mapped outcomes. A prefix is intentional.
    amount: Annotated[int, Field(strict=True, ge=1, le=64 * 1024 * 1024)]


class Criteria(Contract):
    family_significance: Annotated[
        float, Field(ge=1e-12, lt=1, allow_inf_nan=False)
    ] = 0.01
    min_samples: Annotated[int, Field(strict=True, ge=1, le=1_000_000)] = 1024
    min_expected_events: Annotated[int, Field(strict=True, ge=1, le=1_000_000)] = 10


class RngReviewSpec(Contract):
    schema_version: Literal["rng-review-spec/v1"] = "rng-review-spec/v1"
    game_ref: SafeText
    implementation_review: Annotated[list[ReviewObservation], Field(max_length=9)] = (
        Field(default_factory=list)
    )
    raw: SamplePlan | None = None
    raw_tests: Annotated[list[RawTest], Field(max_length=2)] = Field(
        default_factory=list
    )
    mapped: SamplePlan | None = None
    expected_outcomes: Annotated[dict[Ident, Probability], Field(max_length=256)] = (
        Field(default_factory=dict)
    )
    criteria: Criteria = Field(default_factory=Criteria)
    external_claims: Annotated[list[ExternalClaim], Field(max_length=32)] = Field(
        default_factory=list
    )

    @model_validator(mode="after")
    def _valid(self):
        topics = [row.topic for row in self.implementation_review]
        if len(topics) != len(set(topics)) or len(self.raw_tests) != len(
            set(self.raw_tests)
        ):
            raise ValueError("review topics and diagnostic selections must be unique")
        if bool(self.raw) != bool(self.raw_tests):
            raise ValueError(
                "raw samples and raw test selections must be declared together"
            )
        if bool(self.mapped) != bool(self.expected_outcomes):
            raise ValueError("mapped samples require their intended outcome model")
        if (
            self.expected_outcomes
            and sum(self.expected_outcomes.values(), Fraction()) != 1
        ):
            raise ValueError("outcome probabilities must sum to exactly one")
        return self


class Diagnostic(Contract):
    test: Literal["bit-frequency", "pair-frequency", "mapped-frequency"]
    tool: Literal["moonmind-rng-frequency/v1"] = "moonmind-rng-frequency/v1"
    method: Literal["hoeffding-union-bound"] = "hoeffding-union-bound"
    samples: Annotated[int, Field(ge=0)]
    counts: Annotated[
        dict[Ident, Annotated[int, Field(ge=0)]], Field(min_length=1, max_length=256)
    ]
    # Canonical fractions can be longer than the admitted decimal spelling.
    expected_distribution: Annotated[
        dict[Ident, str], Field(min_length=1, max_length=256)
    ]
    significance: Annotated[float, Field(gt=0, lt=1, allow_inf_nan=False)]
    statistic: Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)] | None
    uncertainty_bound: Annotated[float, Field(ge=0, allow_inf_nan=False)] | None
    decision: Decision

    @model_validator(mode="after")
    def _valid(self):
        if (
            set(self.counts) != set(self.expected_distribution)
            or sum(self.counts.values()) != self.samples
        ):
            raise ValueError("diagnostic counts differ from the declared distribution")
        try:
            probabilities = [Fraction(p) for p in self.expected_distribution.values()]
        except (ValueError, ZeroDivisionError) as exc:
            raise ValueError("invalid diagnostic probability") from exc
        if any(p <= 0 or p > 1 for p in probabilities) or sum(probabilities) != 1:
            raise ValueError("invalid diagnostic distribution")
        uniform = {
            "bit-frequency": {"0": Fraction(1, 2), "1": Fraction(1, 2)},
            "pair-frequency": {format(i, "02b"): Fraction(1, 4) for i in range(4)},
        }.get(self.test)
        if (
            uniform is not None
            and dict(zip(self.expected_distribution, probabilities)) != uniform
        ):
            raise ValueError(
                "raw-bit diagnostics require their uniform null distribution"
            )
        expected = _statistics(
            self.counts, self.expected_distribution, self.significance
        )
        if (self.statistic, self.uncertainty_bound) != expected:
            raise ValueError("diagnostic statistics differ from recorded counts")
        return self


class DiagnosticRun(Contract):
    plan: SamplePlan
    processed_amount: Annotated[int, Field(ge=0)]
    status: Literal["complete", "partial"]
    stop_reason: StopReason | None = None
    results: Annotated[list[Diagnostic], Field(min_length=1, max_length=2)]

    @model_validator(mode="after")
    def _valid(self):
        complete = (
            self.processed_amount == self.plan.amount and self.stop_reason is None
        )
        if (
            self.processed_amount > self.plan.amount
            or (self.status == "complete") != complete
        ):
            raise ValueError("diagnostic run status differs from its sample coverage")
        for row in self.results:
            scale = {"bit-frequency": 8, "pair-frequency": 4, "mapped-frequency": 1}[
                row.test
            ]
            if row.samples != scale * self.processed_amount:
                raise ValueError("diagnostic sample amount differs from run coverage")
            if (row.decision == "incomplete") != (self.status == "partial"):
                raise ValueError("partial diagnostics cannot acquire a clean decision")
        return self


class JobOutcome(Contract):
    terminal_state: Literal["succeeded", "failed", "canceled", "timed_out", "rejected"]
    exit_code: int | None = None


class RngAssessment(Contract):
    schema_version: Literal["rng-assessment/v1"] = "rng-assessment/v1"
    game_ref: SafeText
    spec_digest: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
    implementation_review: Annotated[
        list[ReviewObservation], Field(min_length=9, max_length=9)
    ]
    criteria: Criteria
    raw: DiagnosticRun | None = None
    mapped: DiagnosticRun | None = None
    external_claims: Annotated[list[ExternalClaim], Field(max_length=32)] = Field(
        default_factory=list
    )
    cryptographic_security: Literal["unverified"] = "unverified"
    certification: Literal["not_performed"] = "not_performed"
    game_fairness: Literal["unverified"] = "unverified"
    interpretation: Literal[INTERPRETATION] = INTERPRETATION
    status: Literal["complete", "partial"]
    outcome: Literal[
        "anomalies",
        "no_anomaly_detected",
        "insufficient_sample",
        "incomplete",
        "not_requested",
    ]
    limitations: Annotated[list[SafeText], Field(min_length=1, max_length=32)]
    job: JobOutcome | None = None

    @model_validator(mode="after")
    def _valid(self):
        if {row.topic for row in self.implementation_review} != set(REVIEW_TOPICS):
            raise ValueError("every implementation review topic must be accounted for")
        runs = [run for run in (self.raw, self.mapped) if run]
        tests = [row for run in runs for row in run.results]
        if self.raw and any(r.test == "mapped-frequency" for r in self.raw.results):
            raise ValueError("mapped outcomes cannot be reported as raw-bit results")
        if self.mapped and [r.test for r in self.mapped.results] != [
            "mapped-frequency"
        ]:
            raise ValueError("mapped results must test final outcomes")
        if len({row.test for row in tests}) != len(tests):
            raise ValueError("diagnostics must be unique")
        for run in runs:
            for row in run.results:
                significance = self.criteria.family_significance / len(tests)
                expected = _diagnostic(
                    row.test,
                    row.counts,
                    row.expected_distribution,
                    significance,
                    self.criteria,
                    run.status == "partial",
                )
                if row != expected:
                    raise ValueError(
                        "diagnostic decision differs from its counts and criteria"
                    )
        partial = any(run.status == "partial" for run in runs) or bool(
            self.job
            and (self.job.terminal_state != "succeeded" or self.job.exit_code != 0)
        )
        if (self.status == "partial") != partial or self.outcome != _outcome(
            runs, partial
        ):
            raise ValueError("RNG outcome differs from coverage and job termination")
        return self


def _statistics(counts, distribution, significance):
    n = sum(counts.values())
    if not n:
        return None, None
    deviation = max(
        abs(counts[key] / n - float(Fraction(p))) for key, p in distribution.items()
    )
    radius = math.sqrt(math.log(2 * len(counts) / significance) / (2 * n))
    return deviation, radius


def _diagnostic(test, counts, distribution, significance, criteria, partial):
    n = sum(counts.values())
    statistic, radius = _statistics(counts, distribution, significance)
    sufficient = n >= criteria.min_samples and all(
        n * Fraction(p) >= criteria.min_expected_events for p in distribution.values()
    )
    decision = (
        "incomplete"
        if partial
        else (
            "insufficient_sample"
            if not sufficient
            else "anomaly" if statistic > radius else "no_anomaly_detected"
        )
    )
    return Diagnostic(
        test=test,
        samples=n,
        counts=counts,
        expected_distribution=distribution,
        significance=significance,
        statistic=statistic,
        uncertainty_bound=radius,
        decision=decision,
    )


def _outcome(runs, partial):
    if partial:
        return "incomplete"
    decisions = {row.decision for run in runs for row in run.results}
    if "anomaly" in decisions:
        return "anomalies"
    if "insufficient_sample" in decisions:
        return "insufficient_sample"
    return "no_anomaly_detected" if decisions else "not_requested"


def assess_rng(
    spec: RngReviewSpec,
    *,
    raw: Iterable[bytes] | None = None,
    outcomes: Iterable[str] | None = None,
    max_seconds: float = 300,
    should_stop: Callable[[], bool] = lambda: False,
    clock: Callable[[], float] = time.monotonic,
    progress: Callable[[str], None] = lambda _: None,
) -> RngAssessment:
    if not math.isfinite(max_seconds) or not 0 < max_seconds <= 3600:
        raise ValueError("time budget must be positive and at most 3600 seconds")
    deadline = clock() + max_seconds

    def stop():
        return (
            "cancelled"
            if should_stop()
            else "time_budget" if clock() >= deadline else None
        )

    count_tests = len(spec.raw_tests) + bool(spec.mapped)
    significance = spec.criteria.family_significance / max(1, count_tests)
    raw_run = mapped_run = None
    if spec.raw:
        histogram = Counter()
        amount, reason = 0, stop()
        if not reason:
            for chunk in raw if raw is not None else ():
                if not isinstance(chunk, bytes):
                    reason = "invalid_samples"
                    break
                for offset in range(0, min(len(chunk), spec.raw.amount - amount), 4096):
                    reason = stop()
                    if reason:
                        break
                    part = chunk[offset : offset + min(4096, spec.raw.amount - amount)]
                    histogram.update(part)
                    amount += len(part)
                if reason or amount >= spec.raw.amount:
                    break
        reason = reason or ("input_incomplete" if amount < spec.raw.amount else None)
        bits = sum(value.bit_count() * n for value, n in histogram.items())
        counts = {"0": amount * 8 - bits, "1": bits}
        pairs = {
            format(i, "02b"): sum(
                n * sum((value >> shift) & 3 == i for shift in (0, 2, 4, 6))
                for value, n in histogram.items()
            )
            for i in range(4)
        }
        results = [
            _diagnostic(
                test,
                counts if test == "bit-frequency" else pairs,
                (
                    {k: "1/2" for k in counts}
                    if test == "bit-frequency"
                    else {k: "1/4" for k in pairs}
                ),
                significance,
                spec.criteria,
                bool(reason),
            )
            for test in spec.raw_tests
        ]
        raw_run = DiagnosticRun(
            plan=spec.raw,
            processed_amount=amount,
            status="partial" if reason else "complete",
            stop_reason=reason,
            results=results,
        )
        progress("raw")
    if spec.mapped:
        counts = dict.fromkeys(spec.expected_outcomes, 0)
        amount, reason = 0, stop()
        if not reason:
            for value in outcomes if outcomes is not None else ():
                if amount % 256 == 0:
                    reason = stop()
                    if reason:
                        break
                if not isinstance(value, str) or value not in counts:
                    reason = "invalid_samples"
                    break
                counts[value] += 1
                amount += 1
                if amount >= spec.mapped.amount:
                    break
        reason = reason or ("input_incomplete" if amount < spec.mapped.amount else None)
        result = _diagnostic(
            "mapped-frequency",
            counts,
            {k: str(p) for k, p in spec.expected_outcomes.items()},
            significance,
            spec.criteria,
            bool(reason),
        )
        mapped_run = DiagnosticRun(
            plan=spec.mapped,
            processed_amount=amount,
            status="partial" if reason else "complete",
            stop_reason=reason,
            results=[result],
        )
        progress("mapped")
    runs = [r for r in (raw_run, mapped_run) if r]
    partial = any(r.status == "partial" for r in runs)
    observations = {row.topic: row for row in spec.implementation_review}
    return RngAssessment(
        game_ref=spec.game_ref,
        spec_digest="sha256:"
        + content_key(spec.model_dump(mode="json", by_alias=True)),
        implementation_review=[
            observations.get(topic, ReviewObservation(topic=topic))
            for topic in REVIEW_TOPICS
        ],
        criteria=spec.criteria,
        raw=raw_run,
        mapped=mapped_run,
        external_claims=spec.external_claims,
        status="partial" if partial else "complete",
        outcome=_outcome(runs, partial),
        limitations=[
            *LIMITATIONS,
            *sorted({r.stop_reason for r in runs if r.stop_reason}),
        ],
    )


def bind_job_result(
    payload: bytes, terminal_state: str, exit_code: int | None
) -> bytes:
    """Validate at collection and retain adverse job outcomes beside observations."""
    report = RngAssessment.model_validate_json(payload)
    data = report.model_dump(mode="json", by_alias=True)
    data["job"] = {"terminalState": terminal_state, "exitCode": exit_code}
    if terminal_state != "succeeded" or exit_code != 0:
        data.update(status="partial", outcome="incomplete")
        data["limitations"] = sorted({*data["limitations"], "job_did_not_succeed"})
    return RngAssessment.model_validate(data).model_dump_json(by_alias=True).encode()


def _raw_chunks(path):
    if path:
        with open(path, "rb") as handle:
            while chunk := handle.read(4096):
                yield chunk


def _outcomes(path):
    if path:
        with open(path, "rb") as handle:
            while line := handle.readline(1026):
                # No arbitrary line can make unbounded memory or enter a report.
                if len(line) > 1025:
                    yield None
                    return
                try:
                    yield line.decode("ascii").strip()
                except UnicodeDecodeError:
                    yield None
                    return


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m moonmind.security.rng_assessment")
    parser.add_argument("--spec", required=True)
    parser.add_argument("--raw")
    parser.add_argument("--outcomes")
    parser.add_argument("--max-seconds", type=float, default=300)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    stopped = []
    previous = {
        n: signal.signal(n, lambda *_: stopped.append(True))
        for n in (signal.SIGTERM, signal.SIGINT)
    }
    try:
        try:
            with open(args.spec, "rb") as handle:
                body = handle.read(256 * 1024 + 1)
            if len(body) > 256 * 1024:
                raise ValueError("specification too large")
            spec = RngReviewSpec.model_validate_json(body)
            if bool(args.raw) != bool(spec.raw) or bool(args.outcomes) != bool(
                spec.mapped
            ):
                raise ValueError("sample inputs differ from the specification")
            print("rng diagnostics started", file=sys.stderr, flush=True)
            result = assess_rng(
                spec,
                raw=_raw_chunks(args.raw),
                outcomes=_outcomes(args.outcomes),
                max_seconds=args.max_seconds,
                should_stop=lambda: bool(stopped),
                progress=lambda stage: print(
                    f"rng {stage} observations completed", file=sys.stderr, flush=True
                ),
            )
        except (OSError, ValueError) as exc:
            detail = (
                f"{exc.error_count()} validation errors"
                if isinstance(exc, ValidationError)
                else type(exc).__name__
            )
            print(f"invalid RNG input: {detail}", file=sys.stderr)
            return 2
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        staged = output.with_name(output.name + ".partial")
        staged.write_text(result.model_dump_json(by_alias=True))
        os.replace(staged, output)
        print(f"rng diagnostics {result.status}: {result.outcome}", flush=True)
        return 0
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)


if __name__ == "__main__":
    sys.exit(main())
