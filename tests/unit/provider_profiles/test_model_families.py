"""Provider Profile model-family policy: tier contract and family resolution."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from moonmind.provider_profiles.model_families import (
    catalog_rows_from_evidence,
    first_party_family_tier_template,
    pinned_model_status,
    resolve_family_model,
)
from moonmind.provider_profiles.model_tiers import (
    ProviderModelEffortTier,
    coerce_model_effort_tier_policy,
    initial_model_effort_tier_policy,
)


def test_legacy_tier_serialization_is_unchanged() -> None:
    tier = ProviderModelEffortTier.model_validate({"label": "Exact", "model": "gpt-x"})

    assert tier.model_dump(mode="json") == {
        "label": "Exact",
        "model": "gpt-x",
        "effort": None,
        "parameters": {},
        "annotations": {},
    }


def test_family_policy_round_trips_with_independent_effort() -> None:
    automatic = ProviderModelEffortTier.model_validate(
        {
            "label": "Opus",
            "family": "Opus",
            "model_mode": "automatic",
            "effort": "medium",
        }
    ).model_dump(mode="json")
    pinned = ProviderModelEffortTier.model_validate(
        {
            "label": "Opus",
            "family": "opus",
            "model_mode": "pinned",
            "model": "claude-opus-5",
        }
    ).model_dump(mode="json")

    assert automatic["family"] == "opus"
    assert automatic["model_mode"] == "automatic"
    assert automatic["model"] is None
    assert automatic["effort"] == "medium"
    assert pinned["model_mode"] == "pinned"
    assert pinned["model"] == "claude-opus-5"
    assert (
        ProviderModelEffortTier.model_validate(pinned).model_dump(mode="json") == pinned
    )


@pytest.mark.parametrize(
    "payload",
    [
        {"family": "opus"},
        {"model_mode": "automatic"},
        {"family": "opus", "model_mode": "automatic", "model": "claude-opus-5"},
        {"family": "opus", "model_mode": "pinned"},
        {"family": "Opus Family!", "model_mode": "automatic"},
    ],
)
def test_ambiguous_family_policy_is_rejected(payload) -> None:
    with pytest.raises(ValidationError):
        ProviderModelEffortTier.model_validate(payload)


@pytest.mark.parametrize(
    ("runtime_id", "provider_id", "families"),
    [
        ("codex_cli", "openai", ["astra", "sol", "terra", "luna"]),
        ("claude_code", "anthropic", ["fable", "opus", "sonnet", "haiku"]),
    ],
)
def test_first_party_templates_default_to_tier_two_with_medium(
    runtime_id, provider_id, families
) -> None:
    template = first_party_family_tier_template(runtime_id, provider_id)

    assert template is not None
    tiers, default_tier = template
    assert default_tier == 2
    assert [tier["family"] for tier in tiers] == families
    assert {tier["model_mode"] for tier in tiers} == {"automatic"}
    assert {tier["effort"] for tier in tiers} == {"medium"}
    assert all(tier["model"] is None for tier in tiers)
    for tier in tiers:
        ProviderModelEffortTier.model_validate(tier)


def test_other_routes_keep_the_runtime_default_initial_tier() -> None:
    assert first_party_family_tier_template("opencode", "openrouter") is None
    tiers, default_tier = initial_model_effort_tier_policy(
        runtime_id="claude_code",
        provider_id="minimax",
        legacy_default_model=None,
        legacy_default_effort=None,
    )
    assert default_tier == 1
    assert tiers[0]["label"] == "Runtime default"


def test_new_first_party_profile_without_tiers_receives_the_template() -> None:
    tiers, default_tier = coerce_model_effort_tier_policy(
        model_tiers=None,
        default_model_tier=None,
        legacy_default_model=None,
        legacy_default_effort=None,
        runtime_id="claude_code",
        provider_id="anthropic",
    )
    assert default_tier == 2
    assert [tier["family"] for tier in tiers] == ["fable", "opus", "sonnet", "haiku"]

    legacy, legacy_default = coerce_model_effort_tier_policy(
        model_tiers=None,
        default_model_tier=None,
        legacy_default_model="claude-opus-5",
        legacy_default_effort=None,
        runtime_id="claude_code",
        provider_id="anthropic",
    )
    assert legacy_default == 1
    assert legacy[0]["model"] == "claude-opus-5"


def _rows(*model_ids: str, **extra) -> list[dict]:
    return [{"id": model_id, **extra} for model_id in model_ids]


def test_automatic_opus_advances_to_a_new_release_only_in_its_family() -> None:
    before = _rows("claude-opus-5", "claude-opus-5-5", "claude-sonnet-5-5")
    after = _rows(
        "claude-opus-5",
        "claude-opus-5-5",
        "claude-opus-6",
        "claude-opus-6-preview",
        "claude-opus-6[1m]",
        "claude-sonnet-7",
    )

    assert (
        resolve_family_model("opus", provider_id="anthropic", catalog_rows=before).model
        == "claude-opus-5-5"
    )
    resolved = resolve_family_model("opus", provider_id="anthropic", catalog_rows=after)
    assert resolved.model == "claude-opus-6"
    assert resolved.status == "resolved"
    assert resolved.source == "profile_catalog"


@pytest.mark.parametrize(
    ("family", "provider", "rows", "expected"),
    [
        (
            "astra",
            "openai",
            ["gpt-6-astra", "gpt-5-astra", "gpt-6.1-sol"],
            "gpt-6-astra",
        ),
        (
            "sol",
            "openai",
            ["gpt-6-sol", "gpt-6.1-sol", "gpt-6.1-sol-codex"],
            "gpt-6.1-sol",
        ),
        ("terra", "openai", ["gpt-6.1-terra", "gpt-6.10-luna"], "gpt-6.1-terra"),
        (
            "luna",
            "openai",
            ["gpt-6-luna", "gpt-6.2-luna", "gpt-7-terra"],
            "gpt-6.2-luna",
        ),
        (
            "fable",
            "anthropic",
            ["claude-fable-5-1", "claude-fable-5"],
            "claude-fable-5-1",
        ),
        (
            "opus",
            "anthropic",
            ["claude-opus-5-5", "claude-opus-5-10"],
            "claude-opus-5-10",
        ),
        (
            "sonnet",
            "anthropic",
            ["claude-3-5-sonnet-20241022", "claude-sonnet-5-5"],
            "claude-sonnet-5-5",
        ),
        (
            "haiku",
            "anthropic",
            ["claude-haiku-5-5", "claude-haiku-4-5-20251001"],
            "claude-haiku-5-5",
        ),
    ],
)
def test_each_built_in_family_resolves_by_release_not_by_name(
    family, provider, rows, expected
) -> None:
    resolved = resolve_family_model(
        family, provider_id=provider, catalog_rows=_rows(*rows)
    )
    assert resolved.model == expected


def test_dated_snapshot_and_alias_prefer_the_alias_at_the_same_release() -> None:
    rows = _rows("claude-opus-5-5-20261001", "claude-opus-5-5")
    assert (
        resolve_family_model("opus", provider_id="anthropic", catalog_rows=rows).model
        == "claude-opus-5-5"
    )


def test_ineligible_rows_never_become_the_family_resolution() -> None:
    rows = [
        {"id": "claude-opus-6", "status": "retired"},
        {"id": "claude-opus-6-1", "disabled": True},
        {"id": "claude-opus-6-2", "hidden": True},
        {"id": "claude-opus-5-5", "status": "available"},
    ]
    assert (
        resolve_family_model("opus", provider_id="anthropic", catalog_rows=rows).model
        == "claude-opus-5-5"
    )


def test_declared_family_metadata_is_authoritative() -> None:
    rows = [{"id": "claude-opus-6", "line": "claude-sonnet"}, {"id": "claude-opus-5-5"}]
    assert (
        resolve_family_model("opus", provider_id="anthropic", catalog_rows=rows).model
        == "claude-opus-5-5"
    )


def test_effort_support_is_part_of_eligibility() -> None:
    rows = [
        {
            "id": "gpt-6.2-sol",
            "supportedReasoningEfforts": [{"reasoningEffort": "high"}],
        },
        {
            "id": "gpt-6.1-sol",
            "supportedReasoningEfforts": [{"reasoningEffort": "medium"}],
        },
    ]
    assert (
        resolve_family_model(
            "sol", provider_id="openai", catalog_rows=rows, effort="medium"
        ).model
        == "gpt-6.1-sol"
    )
    unavailable = resolve_family_model(
        "sol", provider_id="openai", catalog_rows=rows[:1], effort="medium"
    )
    assert unavailable.model is None
    assert unavailable.status == "unavailable"
    assert "medium" in (unavailable.reason or "")


def test_missing_family_in_an_observed_catalog_is_unavailable_not_substituted() -> None:
    resolved = resolve_family_model(
        "fable", provider_id="anthropic", catalog_rows=_rows("claude-opus-5-5")
    )
    assert resolved.model is None
    assert resolved.status == "unavailable"
    assert resolved.usable is False


def test_cold_start_uses_an_unverified_placeholder() -> None:
    resolved = resolve_family_model("sol", provider_id="openai", catalog_rows=None)
    assert resolved.model == "gpt-6.1-sol"
    assert resolved.status == "unverified"
    assert resolved.source == "cold_start_placeholder"


def test_family_never_crosses_provider() -> None:
    resolved = resolve_family_model(
        "opus", provider_id="openai", catalog_rows=_rows("claude-opus-6")
    )
    assert resolved.model is None
    assert resolved.status == "unavailable"


def test_qualified_catalog_ids_resolve_on_aggregator_routes() -> None:
    rows = [{"qualifiedId": "openrouter/anthropic/claude-opus-6"}]
    resolved = resolve_family_model("opus", provider_id="openrouter", catalog_rows=rows)
    assert resolved.model == "openrouter/anthropic/claude-opus-6"
    assert (
        resolve_family_model("opus", provider_id="openrouter", catalog_rows=None).model
        is None
    )


def test_pinned_model_status_reports_without_rewriting() -> None:
    rows = [{"id": "claude-opus-5", "status": "deprecated"}, {"id": "claude-opus-6"}]
    assert pinned_model_status("claude-opus-5", catalog_rows=rows) == "deprecated"
    assert pinned_model_status("claude-opus-6", catalog_rows=rows) == "available"
    assert pinned_model_status("claude-opus-4", catalog_rows=rows) == "unavailable"
    assert pinned_model_status("claude-opus-5", catalog_rows=None) == "unverified"


def test_catalog_rows_distinguish_never_observed_from_empty() -> None:
    assert catalog_rows_from_evidence(None) is None
    assert catalog_rows_from_evidence({"models": []}) is None
    assert catalog_rows_from_evidence({"models": [{"qualifiedId": "a"}]}) == [
        {"qualifiedId": "a"}
    ]
