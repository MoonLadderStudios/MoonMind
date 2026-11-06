"""RNG implementation review and statistical diagnostics (#5198).

Statistic values are NIST SP 800-22 Rev. 1a worked examples; distributions of
declared transformations are counted by hand. Mapping fixtures are the
implementations under test: the same public raw bytes mapped by biased modulo
and by valid rejection sampling.
"""

from __future__ import annotations

import base64
import json
from fractions import Fraction
from itertools import count

import pytest
from pydantic import ValidationError

from moonmind.security.rng_assessment import (
    RngAssessment,
    RngAssessmentRequest,
    assess_rng,
    fixture_sample,
    main,
    measure,
)

SUBJECT = "rng:fixture"
SEED = 5198
DIMENSIONS = (
    "generator",
    "seeding",
    "restartFork",
    "stateHandling",
    "predictableInputs",
)
REVIEWED = {
    name: {"status": "no_concern_noted", "summary": f"{name} reviewed"}
    for name in DIMENSIONS
}
# The first 100 binary digits of pi, SP 800-22 Rev. 1a sections 2.1.8 to 2.13.8.
PI_100 = (
    "11001001000011111101101010100010001000010110100011"
    "00001000110100110001001100011001100010100010111000"
)
CARDS = {
    "description": "card index dealt by the implementation under test",
    "transformations": ["byte to card index"],
    "model": [{"id": f"card{i}", "probability": "1/52"} for i in range(52)],
}


def request(**sections) -> RngAssessmentRequest:
    return RngAssessmentRequest.model_validate(
        {
            "schemaVersion": "rng-assessment-request/v1",
            "subjectRef": SUBJECT,
            **sections,
        }
    )


def fixture(name="sha256-counter", seed=SEED, bits=200_000, **extra):
    return {
        "origin": {"kind": "fixture", "fixture": {"name": name, "seed": seed}},
        "bits": bits,
        **extra,
    }


def recorded(bits, **extra):
    return {
        "origin": {
            "kind": "recorded",
            "description": "staging generator output captured at the deal service",
            "transformations": ["none"],
        },
        "bits": bits,
        **extra,
    }


def bit_tests(result):
    return {row.test: row for row in result.raw_bits.tests}


def modulo_cards(data: bytes) -> list[str]:
    """Biased: 256 is not a multiple of 52, so cards 0-47 are dealt 5/256."""
    return [f"card{value % 52}" for value in data]


def rejection_cards(data: bytes) -> list[str]:
    """Valid: bytes at or above 208 (4 * 52) are discarded and redrawn."""
    return [f"card{value % 52}" for value in data if value < 208]


def test_diagnostic_statistics_reproduce_nist_sp800_22_worked_examples():
    assert measure("frequency", PI_100).p_values == pytest.approx((0.109599,), abs=1e-6)
    block = measure("block-frequency", PI_100, block_size=10)
    assert block.settings == {"blockSize": 10, "blocks": 10}
    assert block.statistics["chiSquare"] == pytest.approx(7.2)
    assert block.p_values == pytest.approx((0.706438,), abs=1e-6)
    runs = measure("runs", PI_100)
    assert runs.statistics["runs"] == 52
    assert runs.p_values == pytest.approx((0.500798,), abs=1e-6)
    sums = measure("cumulative-sums", PI_100)
    assert (sums.statistics["forwardMaximum"], sums.statistics["backwardMaximum"]) == (
        16,
        19,
    )
    assert sums.p_values == pytest.approx((0.219194, 0.114866), abs=1e-6)
    serial = measure("serial", "0011011101", pattern_length=3)
    assert serial.statistics["psiSquared"] == pytest.approx(2.8)
    assert serial.p_values == pytest.approx((0.808792, 0.670320), abs=1e-6)


def test_runs_prerequisite_failure_is_an_anomaly_without_a_runs_statistic():
    measured = measure("runs", "1" * 80 + "0" * 20)
    assert measured.p_values == (0.0,)
    assert measured.note == "frequency prerequisite failed"


def test_review_records_each_dimension_and_exact_transformation_distributions():
    transformations = [
        {"id": "die", "kind": "modulo", "inputBits": 8, "range": 6},
        {"id": "die-redraw", "kind": "rejection", "inputBits": 8, "range": 6},
        {"id": "die-scaled", "kind": "scale", "inputBits": 8, "range": 6},
        {
            "id": "deck",
            "kind": "shuffle",
            "review": {
                "status": "no_concern_noted",
                "summary": "Fisher-Yates over the rejection-sampled range",
                "evidenceRefs": ["src/deck.py:41"],
            },
        },
    ]
    review = {
        **REVIEWED,
        "generator": {
            "status": "concern",
            "summary": "falls back to Math.random when crypto is unavailable",
            "evidenceRefs": ["src/rng.ts:12"],
        },
        "transformations": transformations,
    }
    result = assess_rng(request(review=review))
    review_result = result.implementation
    assert [row.dimension for row in review_result.dimensions] == list(DIMENSIONS)
    generator = review_result.dimensions[0]
    assert (generator.status, generator.evidence_refs) == ("concern", ["src/rng.ts:12"])
    rows = {row.id: row for row in review_result.transformations}
    # 256 = 6 * 42 + 4: four faces are drawn 43 times in 256, two 42 times.
    assert (rows["die"].distribution, rows["die"].min_probability) == (
        "biased",
        Fraction(42, 256),
    )
    assert rows["die"].max_probability == Fraction(43, 256)
    assert (rows["die-scaled"].min_probability, rows["die-scaled"].max_probability) == (
        Fraction(42, 256),
        Fraction(43, 256),
    )
    redraw = rows["die-redraw"]
    assert (redraw.distribution, redraw.min_probability, redraw.max_probability) == (
        "uniform",
        Fraction(1, 6),
        Fraction(1, 6),
    )
    assert redraw.rejection_rate == Fraction(4, 256)
    assert rows["deck"].distribution == "not_analyzed"
    assert rows["deck"].review.evidence_refs == ["src/deck.py:41"]
    assert review_result.coverage == "complete"
    assert result.findings() == [
        ("rng-review", "review:generator", "generator"),
        ("rng-review", "transformation-bias", "die"),
        ("rng-review", "transformation-bias", "die-scaled"),
    ]
    assert result.outcome == "findings"


def test_review_with_an_unreviewed_dimension_is_incomplete():
    review = {**REVIEWED, "restartFork": {"status": "not_reviewed"}}
    result = assess_rng(request(review=review))
    assert result.implementation.coverage == "partial"
    assert (result.status, result.outcome) == ("partial", "incomplete")
    assert "review_incomplete" in result.limitations


@pytest.mark.parametrize(
    "change",
    [
        {"generator": {"status": "concern"}},
        {"transformations": [{"id": "die", "kind": "modulo"}]},
        {"transformations": [{"id": "deck", "kind": "shuffle", "range": 52}]},
        {
            "transformations": [
                {"id": "x", "kind": "rejection", "inputBits": 4, "range": 52}
            ]
        },
        {
            "transformations": [
                {"id": "x", "kind": "other"},
                {"id": "x", "kind": "other"},
            ]
        },
    ],
)
def test_review_refuses_unstated_or_malformed_observations(change):
    with pytest.raises(ValidationError):
        request(review={**REVIEWED, **change})


def test_diagnostic_record_declares_origin_amount_settings_and_assumptions():
    result = assess_rng(request(diagnostics=fixture(bits=100_000)))
    raw = result.raw_bits
    sample = raw.sample
    assert (sample.origin, sample.fixture.name, sample.fixture.seed) == (
        "fixture",
        "sha256-counter",
        SEED,
    )
    assert (sample.requested_bits, sample.bits) == (100_000, 100_000)
    assert sample.digest.startswith("sha256:") and len(sample.digest) == 71
    assert raw.expected_distribution == "independent-uniform-bits"
    assert (raw.alpha, raw.correction) == (Fraction(1, 100), "bonferroni")
    rows = bit_tests(result)
    assert list(rows) == [
        "frequency",
        "block-frequency",
        "runs",
        "serial",
        "cumulative-sums",
    ]
    # Seven p-values share the family alpha, fixed before any data was read.
    assert {row.threshold for row in rows.values()} == {Fraction(1, 700)}
    assert rows["block-frequency"].settings == {"blockSize": 1011, "blocks": 98}
    assert rows["serial"].settings == {"patternLength": 13}
    assert all(row.min_bits == 100 for row in rows.values())
    assert all(row.bits == 100_000 for row in rows.values())
    assert "public_fixture_sample" in result.limitations
    assert "statistics_do_not_establish_unpredictability" in result.limitations


@pytest.mark.parametrize(
    "change",
    [
        {"tests": ["frequency", "frequency"]},
        {"tests": ["frequency", "dieharder"]},
        {"tests": []},
        {"alpha": "0.6"},
        {"alpha": 0.01},
        {"bits": 2**24 + 1},
    ],
)
def test_diagnostic_selection_and_significance_are_fixed_and_bounded(change):
    with pytest.raises(ValidationError):
        request(diagnostics=fixture(**change))


def test_diagnostic_origin_must_match_its_sample():
    with pytest.raises(ValidationError):
        request(diagnostics={**recorded(1000), "origin": {"kind": "recorded"}})
    with pytest.raises(ValidationError):
        request(
            diagnostics={
                **recorded(1000),
                "origin": {
                    **recorded(1000)["origin"],
                    "fixture": {"name": "mt19937", "seed": 1},
                },
            }
        )
    with pytest.raises(ValueError, match="recorded sample"):
        assess_rng(request(diagnostics=recorded(1000)))
    with pytest.raises(ValueError, match="fixture"):
        assess_rng(request(diagnostics=fixture()), sample=b"\x00" * 200)


def test_healthy_reference_has_no_anomaly_and_a_reproducible_record():
    first = assess_rng(request(diagnostics=fixture()))
    assert first.raw_bits.outcome == "no_anomaly_detected"
    assert {row.decision for row in first.raw_bits.tests} == {"no_anomaly_detected"}
    assert (first.status, first.outcome) == ("complete", "no_findings")
    again = assess_rng(request(diagnostics=fixture()))
    assert again.model_dump_json(by_alias=True) == first.model_dump_json(by_alias=True)


def test_known_anomalous_fixture_keeps_its_statistics_inspectable():
    # Each bit is one 17/32 of the time: S = 0.0625 n, far outside chance.
    result = assess_rng(request(diagnostics=fixture("biased", bits=100_000)))
    frequency = bit_tests(result)["frequency"]
    assert frequency.decision == "anomaly"
    assert frequency.p_values[0] < frequency.threshold
    assert frequency.statistics["ones"] > 53_000
    assert result.raw_bits.outcome == "anomalies"
    assert ("rng-diagnostics", "raw-bits:frequency", "frequency") in result.findings()
    assert result.outcome == "findings"
    again = assess_rng(request(diagnostics=fixture("biased", bits=100_000)))
    assert again.model_dump_json() == result.model_dump_json()


def test_insufficient_sample_is_never_reported_as_no_anomaly():
    result = assess_rng(request(diagnostics=recorded(1000)), sample=bytes(range(10)))
    assert result.raw_bits.sample.bits == 80
    assert {row.decision for row in result.raw_bits.tests} == {"insufficient_sample"}
    assert all(
        row.p_values == [] and row.min_bits == 100 for row in result.raw_bits.tests
    )
    assert result.raw_bits.outcome == "insufficient_sample"
    assert (result.status, result.outcome) == ("complete", "insufficient_sample")
    assert {"sample_shorter_than_requested", "below_minimum_sample"} <= set(
        result.limitations
    )
    again = assess_rng(request(diagnostics=recorded(1000)), sample=bytes(range(10)))
    assert again.model_dump_json() == result.model_dump_json()


def test_incomplete_run_cannot_appear_as_a_clean_assessment():
    result = assess_rng(request(diagnostics=fixture()), should_stop=lambda: True)
    raw = result.raw_bits
    assert (raw.status, raw.stop_reason, raw.outcome) == (
        "partial",
        "cancelled",
        "incomplete",
    )
    assert {row.decision for row in raw.tests} == {"not_run"}
    assert (result.status, result.outcome) == ("partial", "incomplete")
    assert "run_cancelled" in result.limitations
    document = result.model_dump(mode="json", by_alias=True)
    for change in (
        {"outcome": "no_findings"},
        {"status": "complete"},
        {"rawBits": {**document["rawBits"], "outcome": "no_anomaly_detected"}},
        {"rawBits": {**document["rawBits"], "status": "complete", "stopReason": None}},
    ):
        with pytest.raises(ValidationError):
            RngAssessment.model_validate({**document, **change})


def test_time_budget_keeps_completed_tests_as_collected_evidence():
    ticks = count()
    result = assess_rng(
        request(diagnostics=fixture()), max_seconds=4, clock=lambda: next(ticks)
    )
    rows = bit_tests(result)
    assert result.raw_bits.stop_reason == "time_budget_exhausted"
    assert rows["frequency"].decision == "no_anomaly_detected"
    assert rows["frequency"].p_values
    assert rows["cumulative-sums"].decision == "not_run"
    assert (result.status, result.outcome) == ("partial", "incomplete")
    assert "time_budget_exhausted" in result.limitations


def test_cancelled_run_keeps_its_anomaly_and_exports_no_protected_material():
    data = fixture_sample("biased", 77, 4096)
    checks = count()
    result = assess_rng(
        request(diagnostics=recorded(len(data) * 8)),
        sample=data,
        should_stop=lambda: next(checks) >= 2,
    )
    rows = bit_tests(result)
    assert rows["frequency"].decision == "anomaly"
    assert rows["cumulative-sums"].decision == "not_run"
    assert (result.status, result.outcome) == ("partial", "findings")
    assert_no_protected_material(result, data)


def assert_no_protected_material(result: RngAssessment, data: bytes) -> None:
    text = result.model_dump_json(by_alias=True)
    for window in (data[:16], data[-16:], data[1000:1016]):
        assert window.hex() not in text
        assert base64.b64encode(window).decode()[:20] not in text
    bits = "".join(f"{value:08b}" for value in data[:8])
    assert bits not in text
    sample = json.loads(text)["rawBits"]["sample"]
    assert set(sample) == {
        "origin",
        "fixture",
        "description",
        "transformations",
        "requestedBits",
        "bits",
        "digest",
    }
    assert sample["fixture"] is None


def test_protected_sample_and_outcomes_appear_only_as_digest_and_counts():
    data = fixture_sample("mt19937", 9, 25_000)
    dealt = rejection_cards(data)
    result = assess_rng(
        request(diagnostics=recorded(len(data) * 8), outcomes=CARDS),
        sample=data,
        outcomes=dealt,
    )
    assert_no_protected_material(result, data)
    mapped = json.loads(result.model_dump_json(by_alias=True))["mappedOutcomes"]
    assert mapped["samples"] == len(dealt)
    assert sum(row["observed"] for row in mapped["rows"]) == len(dealt)
    # A recorded origin has nowhere to carry a seed.
    with pytest.raises(ValidationError):
        request(
            diagnostics={
                **recorded(1000),
                "origin": {**recorded(1000)["origin"], "seed": 1},
            }
        )


def test_predictable_generator_passes_battery_without_cryptographic_result():
    # MT19937 is fully predictable from 624 outputs, yet statistically plausible.
    result = assess_rng(
        request(review=REVIEWED, diagnostics=fixture("mt19937", bits=200_000))
    )
    assert result.raw_bits.outcome == "no_anomaly_detected"
    assert (result.status, result.outcome) == ("complete", "no_findings")
    assurance = result.assurance
    assert (
        assurance.cryptographic_strength,
        assurance.certification,
        assurance.game_fairness,
    ) == ("not_established", "not_performed", "not_established")
    document = result.model_dump(mode="json", by_alias=True)
    for field, value in (
        ("cryptographicStrength", "externally_reported"),
        ("certification", "externally_reported"),
        ("gameFairness", "externally_reported"),
    ):
        with pytest.raises(ValidationError):
            RngAssessment.model_validate(
                {**document, "assurance": {**document["assurance"], field: value}}
            )


def test_cryptographic_assurance_comes_only_from_reported_external_claims():
    claims = [
        {
            "claim": "cryptographic_strength",
            "verifier": "independent specialist",
            "reportRef": "report:crypto-review-7",
            "standard": "NIST SP 800-90A Rev. 1 (final)",
        },
        {"claim": "rng_certification", "verifier": "accredited laboratory"},
    ]
    result = assess_rng(
        request(diagnostics=fixture("biased", bits=100_000), externalClaims=claims)
    )
    statuses = [(row.claim, row.status) for row in result.external_claims]
    assert statuses == [
        ("cryptographic_strength", "reported"),
        ("rng_certification", "unverified"),
    ]
    assert result.assurance.cryptographic_strength == "externally_reported"
    assert result.assurance.certification == "not_performed"
    # A reported claim neither clears nor hides the statistical anomaly.
    assert result.raw_bits.outcome == "anomalies" and result.outcome == "findings"
    assert "external_claims_not_verified" in result.limitations
    document = result.model_dump(mode="json", by_alias=True)
    with pytest.raises(ValidationError):
        RngAssessment.model_validate(
            {
                **document,
                "externalClaims": [
                    {**document["externalClaims"][0], "status": "unverified"},
                    document["externalClaims"][1],
                ],
            }
        )


@pytest.mark.parametrize(
    ("deal", "decision", "finding"),
    [
        (modulo_cards, "deviation_detected", True),
        (rejection_cards, "no_deviation_detected", False),
    ],
)
def test_biased_mapping_and_rejection_sampling_are_judged_apart_from_raw_bits(
    deal, decision, finding
):
    data = fixture_sample("sha256-counter", SEED, 100_000)
    result = assess_rng(
        request(
            diagnostics=recorded(len(data) * 8),
            outcomes=CARDS,
            review={
                **REVIEWED,
                "transformations": [
                    {
                        "id": "deal",
                        "kind": "modulo" if deal is modulo_cards else "rejection",
                        "inputBits": 8,
                        "range": 52,
                    }
                ],
            },
        ),
        sample=data,
        outcomes=deal(data),
    )
    # The same raw bits are unremarkable under either mapping.
    assert result.raw_bits.outcome == "no_anomaly_detected"
    mapped = result.mapped_outcomes
    assert mapped.decision == decision
    assert (mapped.p_value < mapped.alpha) == finding
    (transformation,) = result.implementation.transformations
    assert transformation.distribution == ("biased" if finding else "uniform")
    if finding:
        assert (transformation.min_probability, transformation.max_probability) == (
            Fraction(4, 256),
            Fraction(5, 256),
        )
    else:
        assert transformation.rejection_rate == Fraction(48, 256)
    rules = {rule for _, rule, _ in result.findings()}
    assert rules == (
        {"mapped-outcome:distribution", "transformation-bias"} if finding else set()
    )


def test_mapping_keeps_uncertainty_inspectable_for_each_outcome():
    data = fixture_sample("sha256-counter", 3, 60_000)
    result = assess_rng(request(outcomes=CARDS), outcomes=modulo_cards(data))
    mapped = result.mapped_outcomes
    assert (mapped.method, mapped.samples, mapped.degrees_of_freedom) == (
        "chi-square-goodness-of-fit",
        60_000,
        51,
    )
    assert mapped.statistic > 0 and mapped.interval_confidence == Fraction(99, 100)
    row = mapped.rows[0]
    assert row.expected == pytest.approx(60_000 / 52)
    assert all(row.low < row.frequency < row.high for row in mapped.rows)
    # Modulo deals cards 48-51 4/256 of the time against 1/52: about 6.5
    # standard deviations short each at this sample size.
    assert all(row.residual < -3 for row in mapped.rows[48:])
    assert result.raw_bits is None


def test_mapping_flags_outcomes_the_model_cannot_produce_and_unusable_rows():
    dealt = rejection_cards(fixture_sample("sha256-counter", 4, 20_000))
    result = assess_rng(
        request(outcomes=CARDS), outcomes=[*dealt, "card52", "card52", None]
    )
    mapped = result.mapped_outcomes
    assert (mapped.unsupported_observed, mapped.unsupported_ids) == (2, ["card52"])
    assert (mapped.unusable, mapped.status, mapped.decision) == (
        1,
        "partial",
        "deviation_detected",
    )
    assert ("rng-outcomes", "mapped-outcome:support", "card52") in result.findings()
    assert {"unusable_outcomes"} <= set(result.limitations)


def test_mapping_with_too_few_expected_outcomes_is_insufficient_sample():
    result = assess_rng(request(outcomes=CARDS), outcomes=["card1"] * 100)
    mapped = result.mapped_outcomes
    assert (mapped.decision, mapped.p_value) == ("insufficient_sample", None)
    assert mapped.rows[1].observed == 100
    assert (result.status, result.outcome) == ("complete", "insufficient_sample")


def test_mapping_model_must_be_an_exact_distribution():
    with pytest.raises(ValidationError):
        request(outcomes={**CARDS, "model": CARDS["model"][:51]})
    with pytest.raises(ValidationError):
        request(
            outcomes={
                **CARDS,
                "model": [
                    {"id": "a", "probability": 0.5},
                    {"id": "b", "probability": 0.5},
                ],
            }
        )
    with pytest.raises(ValueError, match="outcomes"):
        assess_rng(request(outcomes=CARDS))


def test_report_keeps_observations_statistics_and_external_claims_apart():
    data = fixture_sample("sha256-counter", SEED, 30_000)
    result = assess_rng(
        request(
            review={
                **REVIEWED,
                "seeding": {"status": "concern", "summary": "seeded from boot time"},
            },
            diagnostics=recorded(len(data) * 8),
            outcomes=CARDS,
            externalClaims=[{"claim": "game_fairness", "verifier": "laboratory"}],
        ),
        sample=data,
        outcomes=rejection_cards(data),
    )
    document = json.loads(result.model_dump_json(by_alias=True))
    assert set(document) == {
        "schemaVersion",
        "subjectRef",
        "requestDigest",
        "implementation",
        "rawBits",
        "mappedOutcomes",
        "externalClaims",
        "assurance",
        "status",
        "outcome",
        "limitations",
    }
    assert document["schemaVersion"] == "rng-assessment/v1"
    assert document["externalClaims"] == [
        {
            "claim": "game_fairness",
            "verifier": "laboratory",
            "reportRef": None,
            "standard": None,
            "status": "unverified",
        }
    ]
    assert document["assurance"] == {
        "cryptographicStrength": "not_established",
        "certification": "not_performed",
        "gameFairness": "not_established",
    }
    assert result.findings() == [("rng-review", "review:seeding", "seeding")]
    text = result.interpretation
    assert "Implementation observations: 1 concern" in text
    assert "raw bits no_anomaly_detected" in text
    assert "game fairness not_established" in text


def test_cli_writes_the_result_and_refuses_an_invalid_request(tmp_path, capsys):
    data = fixture_sample("sha256-counter", 1, 2048)
    path = tmp_path / "request.json"
    path.write_text(
        request(diagnostics=recorded(len(data) * 8), outcomes=CARDS).model_dump_json(
            by_alias=True
        )
    )
    sample = tmp_path / "sample.bin"
    sample.write_bytes(data)
    outcomes = tmp_path / "outcomes.jsonl"
    outcomes.write_text('{"outcome":"card1"}\nnot json\n{"outcome":"card2"}\n')
    output = tmp_path / "out" / "rng-assessment.json"
    argv = ["--request", str(path), "--sample", str(sample), "--output", str(output)]
    assert main([*argv, "--outcomes", str(outcomes)]) == 0
    result = RngAssessment.model_validate_json(output.read_text())
    assert (result.mapped_outcomes.samples, result.mapped_outcomes.unusable) == (2, 1)
    assert result.raw_bits.sample.bits == len(data) * 8
    # Declared outcomes without their records, and malformed requests, write nothing.
    missing = tmp_path / "missing.json"
    assert main([*argv[:-1], str(missing)]) == 2
    path.write_text('{"schemaVersion": "rng-assessment-request/v1"}')
    assert main([*argv[:-1], str(missing)]) == 2
    assert not missing.exists()
    assert "invalid rng assessment input" in capsys.readouterr().err
