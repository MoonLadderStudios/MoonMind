import pytest
from pydantic import ValidationError

from moonmind.provider_profiles.model_tiers import (
    ProviderModelEffortTier,
    coerce_model_effort_tier_policy,
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
