from __future__ import annotations

from moonmind.billing.costs import (
    ModelTokenPricing,
    estimate_model_cost,
    pricing_from_profile_metadata,
)


def test_estimate_model_cost_uses_known_model_pricing() -> None:
    estimate = estimate_model_cost(
        model="gpt-4o-mini",
        input_tokens=1_000_000,
        output_tokens=500_000,
    )

    assert estimate is not None
    assert estimate.cost_estimate_usd == 0.45
    assert estimate.pricing_source == "built_in"


def test_estimate_model_cost_uses_explicit_pricing() -> None:
    estimate = estimate_model_cost(
        model="unlisted-provider-model",
        input_tokens=2000,
        output_tokens=1000,
        pricing=ModelTokenPricing(2.0, 10.0, "profile.billing"),
    )

    assert estimate is not None
    assert estimate.cost_estimate_usd == 0.014
    assert estimate.pricing_source == "profile.billing"


def test_unknown_model_without_pricing_does_not_fabricate_cost() -> None:
    assert (
        estimate_model_cost(
            model="unknown-model",
            input_tokens=1000,
            output_tokens=1000,
        )
        is None
    )


def test_pricing_from_profile_metadata_accepts_operator_billing_shape() -> None:
    pricing = pricing_from_profile_metadata(
        {
            "billing": {
                "inputPerMillionUsd": 1.25,
                "outputPerMillionUsd": 5.0,
            }
        }
    )

    assert pricing is not None
    assert pricing.input_per_million_usd == 1.25
    assert pricing.output_per_million_usd == 5.0
    assert pricing.source == "profile.billing"
