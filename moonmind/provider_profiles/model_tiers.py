"""Provider Profile model/effort tier contract helpers."""

from __future__ import annotations

from typing import Any, Mapping

from pydantic import BaseModel, ConfigDict, Field, field_validator

_SENSITIVE_KEY_TERMS = (
    "token",
    "password",
    "secret",
    "api_key",
    "apikey",
    "credential",
    "authorization",
    "auth_header",
    "private_key",
    "refresh",
    "oauth",
    "cookie",
    "session",
)


class ProviderModelEffortTier(BaseModel):
    """Profile-local model/effort tier definition."""

    model_config = ConfigDict(extra="forbid")

    label: str | None = None
    model: str | None = None
    effort: str | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    annotations: dict[str, Any] = Field(default_factory=dict)

    @field_validator("parameters", "annotations", mode="after")
    @classmethod
    def _reject_raw_credential_keys(
        cls, value: dict[str, Any]
    ) -> dict[str, Any]:
        if _contains_sensitive_key(value):
            raise ValueError("tier metadata must not contain raw credential-like keys")
        return value


def runtime_default_model_effort_tier() -> dict[str, Any]:
    return {
        "label": "Runtime default",
        "model": None,
        "effort": None,
        "parameters": {},
        "annotations": {},
    }


def coerce_model_effort_tier_policy(
    *,
    model_tiers: Any,
    default_model_tier: int | None,
    legacy_default_model: str | None,
    legacy_default_effort: str | None,
    empty_as_missing: bool = False,
) -> tuple[list[dict[str, Any]], int]:
    """Validate and normalize persisted Provider Profile tier policy.

    Missing tier policy is migrated from legacy defaults when present, otherwise
    it receives a runtime-default tier.
    """

    if model_tiers is None or (empty_as_missing and model_tiers == []):
        if legacy_default_model is not None or legacy_default_effort is not None:
            raw_tiers: Any = [
                {
                    "label": "Legacy default",
                    "model": legacy_default_model,
                    "effort": legacy_default_effort,
                    "parameters": {},
                    "annotations": {},
                }
            ]
        else:
            raw_tiers = [runtime_default_model_effort_tier()]
    else:
        raw_tiers = model_tiers

    if not isinstance(raw_tiers, list):
        raise ValueError("model_tiers must be a JSON array")
    if not raw_tiers:
        raise ValueError("model_tiers must contain at least one tier")

    normalized = [
        ProviderModelEffortTier.model_validate(item).model_dump(mode="json")
        for item in raw_tiers
    ]

    normalized_default = 1 if default_model_tier is None else int(default_model_tier)
    if normalized_default < 1 or normalized_default > len(normalized):
        raise ValueError("default_model_tier must be within configured model_tiers")
    return normalized, normalized_default


def _contains_sensitive_key(value: Any) -> bool:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            normalized_key = str(key).strip().lower().replace("-", "_")
            if any(term in normalized_key for term in _SENSITIVE_KEY_TERMS):
                return True
            if _contains_sensitive_key(nested):
                return True
    elif isinstance(value, list):
        return any(_contains_sensitive_key(item) for item in value)
    return False
