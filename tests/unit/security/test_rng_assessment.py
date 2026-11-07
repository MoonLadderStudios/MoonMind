"""Public deterministic RNG acceptance fixtures for MoonMind #5198."""

from __future__ import annotations

import importlib
import json

import pytest
from pydantic import ValidationError


@pytest.fixture
def rng():
    return importlib.import_module("moonmind.security.rng_assessment")


def specification(*, amount=32768, mapped=False, **changes):
    spec = {
        "schemaVersion": "rng-review-spec/v1",
        "gameRef": "game:rng-fixture",
        "implementationReview": [
            {
                "topic": "generator-api",
                "status": "concern",
                "observation": "Public repeating byte counter is entirely predictable.",
                "evidenceRefs": ["fixture:byte-counter"],
            }
        ],
        "raw": {
            "originRef": "fixture:byte-counter",
            "transformations": ["none"],
            "amount": amount,
        },
        "rawTests": ["bit-frequency", "pair-frequency"],
        "externalClaims": [
            {
                "claim": "Supplier reports a certified secure RNG",
                "reportRef": "report:supplier",
                "verifierRef": "lab:supplied",
            }
        ],
    }
    if mapped:
        spec["mapped"] = {
            "originRef": "fixture:counter-low-three-bits",
            "transformations": ["low three bits; modulo three or rejection"],
            "amount": amount,
        }
        spec["expectedOutcomes"] = {str(i): "1/3" for i in range(3)}
    return {**spec, **changes}


def evaluate(rng, *, raw=None, outcomes=None, spec=None, **kw):
    return rng.assess_rng(
        rng.RngReviewSpec.model_validate(spec or specification()),
        raw=raw,
        outcomes=outcomes,
        **kw,
    )


def test_predictable_statistically_plausible_fixture_never_gains_assurance(rng):
    result = evaluate(rng, raw=[bytes(range(256)) * 128])
    assert (result.status, result.outcome) == ("complete", "no_anomaly_detected")
    assert {r.decision for r in result.raw.results} == {"no_anomaly_detected"}
    assert result.cryptographic_security == "unverified"
    assert result.certification == "not_performed"
    assert result.game_fairness == "unverified"
    assert len(result.implementation_review) == len(rng.REVIEW_TOPICS)
    assert result.implementation_review[0].status == "concern"
    assert all(r.status == "unverified" for r in result.implementation_review[1:])
    assert result.external_claims[0].authentication == "unverified"
    assert "provenance" in result.interpretation.lower()
    assert evaluate(rng, raw=[bytes(range(256)) * 128]) == result


def test_biased_mapping_and_rejection_are_independent_of_raw_diagnostics(rng):
    raw = bytes(range(256)) * 128
    biased = [str((b & 7) % 3) for b in raw]
    accepted = [str((b & 7) % 3) for b in raw if (b & 7) < 6]
    bad = evaluate(rng, raw=[raw], outcomes=biased, spec=specification(mapped=True))
    good_spec = specification(mapped=True)
    good_spec["mapped"]["amount"] = len(accepted)
    good = evaluate(rng, raw=[raw], outcomes=accepted, spec=good_spec)
    assert bad.raw == good.raw
    assert bad.mapped.results[0].decision == "anomaly"
    assert good.mapped.results[0].decision == "no_anomaly_detected"
    assert good.mapped.results[0].counts == {"0": 8192, "1": 8192, "2": 8192}
    assert bad.outcome == "anomalies"
    assert bad.mapped.results[0].statistic > bad.mapped.results[0].uncertainty_bound
    assert bad.mapped.plan.origin_ref == "fixture:counter-low-three-bits"


@pytest.mark.parametrize(
    "data,amount,decision",
    [
        (b"\x00" * 32768, 32768, "anomaly"),
        (bytes(range(256)) * 128, 32768, "no_anomaly_detected"),
        (b"\x55", 1, "insufficient_sample"),
        (b"\x00" * 32, 32768, "incomplete"),
    ],
    ids=["anomalous", "healthy-reference", "insufficient", "incomplete"],
)
def test_reproducible_records_and_limits(rng, data, amount, decision):
    result = evaluate(rng, raw=[data], spec=specification(amount=amount))
    assert {r.decision for r in result.raw.results} == {decision}
    assert result.limitations
    assert result.raw.processed_amount == len(data)
    assert result.raw.results[0].samples == len(data) * 8
    assert result.raw.results[0].expected_distribution == {"0": "1/2", "1": "1/2"}
    assert result.raw.results[0].method == "hoeffding-union-bound"
    assert result == evaluate(rng, raw=[data], spec=specification(amount=amount))


@pytest.mark.parametrize("stop_reason", ["cancelled", "time_budget"])
def test_cooperative_stops_preserve_completed_raw_observations(rng, stop_reason):
    stopped = []

    def progress(stage):
        stopped.append(stage)

    clock = lambda: 2.0 if stopped and stop_reason == "time_budget" else 0.0
    result = evaluate(
        rng,
        raw=[bytes(range(256)) * 128],
        outcomes=["0"] * 32768,
        spec=specification(mapped=True),
        progress=progress,
        should_stop=lambda: bool(stopped) and stop_reason == "cancelled",
        clock=clock,
        max_seconds=1,
    )
    assert result.raw.status == "complete"
    assert result.raw.processed_amount == 32768
    assert (result.status, result.outcome) == ("partial", "incomplete")
    assert result.mapped.stop_reason == stop_reason
    assert result.mapped.processed_amount == 0
    assert result.implementation_review[0].status == "concern"


def test_no_diagnostics_is_valid_implementation_review_only(rng):
    result = evaluate(rng, spec=specification(raw=None, rawTests=[]))
    assert result.raw is None and result.mapped is None
    assert result.outcome == "not_requested"
    assert result.cryptographic_security == "unverified"


@pytest.mark.parametrize(
    "changes",
    [
        {"seed": "LIVE_SEED_DO_NOT_EXPORT"},
        {"internalState": "LIVE_STATE_DO_NOT_EXPORT"},
        {"expectedOutcomes": {"0": "1/3", "1": "1/3"}},
        {"rawTests": ["bit-frequency", "bit-frequency"]},
        {
            "externalClaims": [
                {
                    "claim": "secure",
                    "reportRef": "report:x",
                    "authentication": "verified",
                }
            ]
        },
    ],
)
def test_refuse_protected_fields_invalid_models_and_authenticated_claims(rng, changes):
    with pytest.raises(ValidationError):
        rng.RngReviewSpec.model_validate(specification(**changes))


def test_summary_cannot_promote_incomplete_evidence_or_assurance(rng):
    result = evaluate(rng, raw=[b"\x55"])
    for patch in [
        {"status": "complete", "outcome": "no_anomaly_detected"},
        {"cryptographicSecurity": "secure"},
        {"certification": "certified"},
    ]:
        with pytest.raises(ValidationError):
            rng.RngAssessment.model_validate(
                {**result.model_dump(mode="json", by_alias=True), **patch}
            )


def test_protected_prose_and_samples_are_excluded_from_reports_and_errors(
    rng, tmp_path, capsys
):
    spec = specification(amount=4096)
    spec["implementationReview"][0]["observation"] = (
        "seed=LIVE_SEED_DO_NOT_EXPORT internal_state=LIVE_STATE_DO_NOT_EXPORT "
        "password=CREDENTIAL_DO_NOT_EXPORT"
    )
    secret_sample = b"SAMPLE_DO_NOT_EXPORT" * 300
    result = evaluate(rng, raw=[secret_sample], spec=spec)
    encoded = result.model_dump_json(by_alias=True)
    for secret in [
        "LIVE_SEED_DO_NOT_EXPORT",
        "LIVE_STATE_DO_NOT_EXPORT",
        "CREDENTIAL_DO_NOT_EXPORT",
        "SAMPLE_DO_NOT_EXPORT",
    ]:
        assert secret not in encoded
    source = tmp_path / "spec.json"
    source.write_text(json.dumps({**spec, "internalState": "LIVE_STATE_DO_NOT_EXPORT"}))
    output = tmp_path / "result.json"
    assert rng.main(["--spec", str(source), "--output", str(output)]) == 2
    assert not output.exists()
    assert "LIVE_STATE_DO_NOT_EXPORT" not in capsys.readouterr().err


def test_invalid_mapped_record_is_incomplete_without_echoing_it(rng):
    result = evaluate(
        rng,
        raw=[bytes(range(256)) * 128],
        outcomes=["SENSITIVE_OUTCOME_DO_NOT_EXPORT"],
        spec=specification(mapped=True),
    )
    assert result.mapped.stop_reason == "invalid_samples"
    assert result.outcome == "incomplete"
    assert "SENSITIVE_OUTCOME_DO_NOT_EXPORT" not in result.model_dump_json()


def test_imported_raw_diagnostic_cannot_change_the_uniform_null(rng):
    result = evaluate(rng, raw=[b"\x00" * 32768])
    payload = result.model_dump(mode="json", by_alias=True)
    row = payload["raw"]["results"][0]
    row.update(
        expectedDistribution={"0": "999/1000", "1": "1/1000"},
        statistic=0.0010000000000000009,
        decision="no_anomaly_detected",
    )
    # Both counts and significance could otherwise agree with a substituted
    # null; the raw-bit test's null must stay uniform.
    with pytest.raises(ValidationError):
        rng.RngAssessment.model_validate(payload)


def test_invalid_reference_fraction_is_a_validation_error(rng):
    result = evaluate(rng, raw=[bytes(range(256)) * 128])
    payload = result.model_dump(mode="json", by_alias=True)
    payload["raw"]["results"][0]["expectedDistribution"]["0"] = "1/0"
    with pytest.raises(ValidationError):
        rng.RngAssessment.model_validate(payload)
