from __future__ import annotations

from moonmind.billing import costs
from moonmind.billing.costs import (
    ModelTokenPricing,
    estimate_model_cost,
    pricing_for_model,
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


def test_pricing_from_profile_metadata_preserves_zero_prices() -> None:
    pricing = pricing_from_profile_metadata(
        {
            "billing": {
                "inputPerMillionUsd": 0,
                "outputPerMillionUsd": 0.0,
            }
        }
    )

    assert pricing is not None
    assert pricing.input_per_million_usd == 0
    assert pricing.output_per_million_usd == 0


def test_env_pricing_preserves_zero_prices_and_caches_by_raw_payload(
    monkeypatch,
) -> None:
    raw = '{"local-free":{"inputPerMillionUsd":0,"outputPerMillionUsd":0}}'
    calls = 0
    real_loads = costs.json.loads
    costs._parse_env_pricing.cache_clear()

    def counting_loads(value: str):
        nonlocal calls
        calls += 1
        return real_loads(value)

    monkeypatch.setenv("MOONMIND_MODEL_PRICING_JSON", raw)
    monkeypatch.setattr(costs.json, "loads", counting_loads)

    first = pricing_for_model("local-free")
    second = pricing_for_model("local-free")

    assert first is not None
    assert first.input_per_million_usd == 0
    assert first.output_per_million_usd == 0
    assert second == first
    assert calls == 1
    costs._parse_env_pricing.cache_clear()


def test_pricing_for_model_prefers_longest_builtin_substring_match() -> None:
    pricing = pricing_for_model("azure/gpt-4o-mini-latest")

    assert pricing is not None
    assert pricing.input_per_million_usd == 0.15
    assert pricing.output_per_million_usd == 0.60
