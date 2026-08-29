import pytest
from pydantic import ValidationError

from moonmind.provider_profiles.model_tiers import (
    ProviderModelEffortTier,
    coerce_model_effort_tier_policy,
    is_single_legacy_default_model_effort_tier,
    is_single_runtime_default_model_effort_tier,
)


def test_mm1169_legacy_defaults_migrate_to_one_model_tier() -> None:
    model_tiers, default_model_tier = coerce_model_effort_tier_policy(
        model_tiers=None,
        default_model_tier=None,
        legacy_default_model="gpt-custom",
        legacy_default_effort="xhigh",
    )

    assert default_model_tier == 1
    assert model_tiers == [
        {
            "label": "Legacy default",
            "model": "gpt-custom",
            "effort": "xhigh",
            "parameters": {},
            "annotations": {},
        }
    ]


def test_mm1169_missing_legacy_defaults_get_runtime_default_tier() -> None:
    model_tiers, default_model_tier = coerce_model_effort_tier_policy(
        model_tiers=None,
        default_model_tier=None,
        legacy_default_model=None,
        legacy_default_effort=None,
    )

    assert default_model_tier == 1
    assert model_tiers == [
        {
            "label": "Runtime default",
            "model": None,
            "effort": None,
            "parameters": {},
            "annotations": {},
        }
    ]


def test_mm1169_default_model_tier_must_be_in_configured_range() -> None:
    with pytest.raises(ValueError, match="default_model_tier"):
        coerce_model_effort_tier_policy(
            model_tiers=[{"label": "Only", "model": "opaque", "effort": "opaque"}],
            default_model_tier=2,
            legacy_default_model=None,
            legacy_default_effort=None,
        )


def test_mm1169_tier_metadata_rejects_raw_credential_like_keys() -> None:
    with pytest.raises(ValidationError, match="credential-like"):
        ProviderModelEffortTier.model_validate(
            {
                "label": "Unsafe",
                "model": "opaque",
                "effort": "opaque",
                "parameters": {"api_key": "sk-secret"},
                "annotations": {},
            }
        )

    with pytest.raises(ValidationError, match="credential-like"):
        ProviderModelEffortTier.model_validate(
            {
                "label": "Unsafe annotation",
                "model": "opaque",
                "effort": "opaque",
                "parameters": {},
                "annotations": {"billing": {"token": "secret"}},
            }
        )


def test_mm1169_tier_metadata_rejects_raw_credential_like_values() -> None:
    with pytest.raises(ValidationError, match="credential-like"):
        ProviderModelEffortTier.model_validate(
            {
                "label": "Unsafe value",
                "model": "opaque",
                "effort": "opaque",
                "parameters": {"header": "token=blocked-secret-value"},
                "annotations": {},
            }
        )

    with pytest.raises(ValidationError, match="credential-like"):
        ProviderModelEffortTier.model_validate(
            {
                "label": "Unsafe annotation value",
                "model": "opaque",
                "effort": "opaque",
                "parameters": {},
                "annotations": {"note": "sk-1234567890abcdef"},
            }
        )


def test_migrated_runtime_default_tier_is_still_recognized() -> None:
    """MoonLadderStudios/MoonMind#3793: migration provenance is not user metadata."""

    migrated = [
        {
            "label": "Runtime default",
            "model": None,
            "effort": None,
            "parameters": {},
            "annotations": {"migratedFrom": "runtime_default"},
        }
    ]

    assert is_single_runtime_default_model_effort_tier(migrated)
    # Profiles migrated before provenance stamping keep working unchanged.
    assert is_single_runtime_default_model_effort_tier(
        [{**migrated[0], "annotations": {}}]
    )


def test_operator_annotated_runtime_default_tier_is_not_a_migrated_tier() -> None:
    assert not is_single_runtime_default_model_effort_tier(
        [
            {
                "label": "Runtime default",
                "model": None,
                "effort": None,
                "parameters": {},
                "annotations": {"owner": "platform"},
            }
        ]
    )
    assert not is_single_runtime_default_model_effort_tier(
        [
            {
                "label": "Runtime default",
                "model": None,
                "effort": None,
                "parameters": {},
                "annotations": {"migratedFrom": "default_model_default_effort"},
            }
        ]
    )


def test_migrated_legacy_default_tier_is_recognized_while_mirroring_defaults() -> None:
    """MoonLadderStudios/MoonMind#3793: migrated legacy tiers stay refreshable."""

    migrated = [
        {
            "label": "Legacy default",
            "model": "gpt-custom",
            "effort": "xhigh",
            "parameters": {},
            "annotations": {"migratedFrom": "default_model_default_effort"},
        }
    ]

    assert is_single_legacy_default_model_effort_tier(
        migrated,
        legacy_default_model="gpt-custom",
        legacy_default_effort="xhigh",
    )
    assert is_single_legacy_default_model_effort_tier(
        [{**migrated[0], "annotations": {}}],
        legacy_default_model="gpt-custom",
        legacy_default_effort="xhigh",
    )
    # A tier the operator has moved off the legacy defaults is not a mirror.
    assert not is_single_legacy_default_model_effort_tier(
        migrated,
        legacy_default_model="other-model",
        legacy_default_effort="xhigh",
    )
    assert not is_single_legacy_default_model_effort_tier(
        [{**migrated[0], "annotations": {"owner": "platform"}}],
        legacy_default_model="gpt-custom",
        legacy_default_effort="xhigh",
    )


def test_migrated_tier_policy_round_trips_through_coercion() -> None:
    migrated = [
        {
            "label": "Legacy default",
            "model": "gpt-custom",
            "effort": "xhigh",
            "parameters": {},
            "annotations": {"migratedFrom": "default_model_default_effort"},
        }
    ]

    model_tiers, default_model_tier = coerce_model_effort_tier_policy(
        model_tiers=migrated,
        default_model_tier=1,
        legacy_default_model="gpt-custom",
        legacy_default_effort="xhigh",
        empty_as_missing=True,
    )

    assert model_tiers == migrated
    assert default_model_tier == 1
