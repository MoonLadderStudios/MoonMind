"""Provider Profile model/effort tier contract helpers."""

from __future__ import annotations

import re
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

_SENSITIVE_VALUE_PATTERNS = (
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?i)(?:token|password|secret|api[_-]?key)\s*="),
    re.compile(
        r"^(?:sk-[A-Za-z0-9_-]{8,}|ghp_[A-Za-z0-9_]{8,}|"
        r"github_pat_[A-Za-z0-9_]{8,})"
    ),
    re.compile(
        r"^(?:AKIA[0-9A-Z]{12,}|AIza[0-9A-Za-z_-]{20,}|"
        r"xox[baprs]-[A-Za-z0-9-]{10,})"
    ),
)

_SAFE_KEY_EXCLUSIONS = frozenset(
    {
        "max_tokens",
        "prompt_tokens",
        "completion_tokens",
        "tokens_per_minute",
        "session_timeout",
        "refresh_interval",
        "auto_refresh",
    }
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


def legacy_default_model_effort_tier(
    *,
    legacy_default_model: str | None,
    legacy_default_effort: str | None,
) -> dict[str, Any]:
    return {
        "label": "Legacy default",
        "model": legacy_default_model,
        "effort": legacy_default_effort,
        "parameters": {},
        "annotations": {},
    }


def is_single_runtime_default_model_effort_tier(model_tiers: Any) -> bool:
    if not isinstance(model_tiers, list) or len(model_tiers) != 1:
        return False
    tier = model_tiers[0]
    if not isinstance(tier, Mapping):
        return False
    return (
        tier.get("label") == "Runtime default"
        and tier.get("model") is None
        and tier.get("effort") is None
        and (tier.get("parameters") or {}) == {}
        and (tier.get("annotations") or {}) == {}
    )


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

    has_legacy_default = (
        legacy_default_model is not None or legacy_default_effort is not None
    )
    if (
        model_tiers is None
        or (empty_as_missing and model_tiers == [])
        or (
            empty_as_missing
            and has_legacy_default
            and is_single_runtime_default_model_effort_tier(model_tiers)
        )
    ):
        if has_legacy_default:
            raw_tiers: Any = [
                legacy_default_model_effort_tier(
                    legacy_default_model=legacy_default_model,
                    legacy_default_effort=legacy_default_effort,
                )
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
    if isinstance(value, str):
        normalized_value = value.strip()
        return any(
            pattern.search(normalized_value)
            for pattern in _SENSITIVE_VALUE_PATTERNS
        )
    if isinstance(value, Mapping):
        for key, nested in value.items():
            normalized_key = str(key).strip().lower().replace("-", "_")
            if normalized_key in _SAFE_KEY_EXCLUSIONS:
                if _contains_sensitive_key(nested):
                    return True
                continue
            if any(term in normalized_key for term in _SENSITIVE_KEY_TERMS):
                return True
            if _contains_sensitive_key(nested):
                return True
    elif isinstance(value, list):
        return any(_contains_sensitive_key(item) for item in value)
    return False
