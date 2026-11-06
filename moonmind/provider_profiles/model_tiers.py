"""Provider Profile model/effort tier contract helpers."""

from __future__ import annotations

import re
from typing import Any, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

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


_FAMILY_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


class ModelEffortTierPolicy(BaseModel):
    """Tier fields and model-family policy shared by every tier schema.

    ``family`` + ``model_mode`` express model-family policy: ``automatic``
    follows the newest eligible release of the family (``model`` stays null),
    while ``pinned`` keeps the concrete ``model`` until the operator restores
    automatic selection. Without a family, ``model`` keeps its existing
    meaning: an exact model, or null for the Runtime default. Effort is
    independent of the model mode in both cases.
    """

    model_config = ConfigDict(extra="forbid")

    label: str | None = None
    # Tiers without family policy keep their established serialized shape.
    family: str | None = Field(default=None, exclude_if=lambda value: value is None)
    model_mode: Literal["automatic", "pinned"] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    model: str | None = None
    effort: str | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    annotations: dict[str, Any] = Field(default_factory=dict)

    @field_validator("family", mode="before")
    @classmethod
    def _normalize_family(cls, value: Any) -> Any:
        if value is None:
            return None
        text = str(value).strip().lower()
        if not text:
            return None
        if not _FAMILY_ID_PATTERN.match(text):
            raise ValueError(
                "family must be a short machine-readable identifier such as 'opus'"
            )
        return text

    @model_validator(mode="after")
    def _require_unambiguous_family_policy(self) -> "ModelEffortTierPolicy":
        model = (self.model or "").strip() or None
        if self.model_mode is None:
            if self.family is not None:
                raise ValueError(
                    "a family tier requires model_mode automatic or pinned"
                )
            return self
        if self.family is None:
            raise ValueError("model_mode requires a family")
        if self.model_mode == "automatic" and model is not None:
            raise ValueError(
                "an automatic family tier must not also pin a model; use "
                "model_mode pinned to keep a specific version"
            )
        if self.model_mode == "pinned" and model is None:
            raise ValueError("a pinned family tier requires a concrete model")
        return self


class ProviderModelEffortTier(ModelEffortTierPolicy):
    """Profile-local model/effort tier definition accepted on writes."""

    @field_validator("parameters", "annotations", mode="after")
    @classmethod
    def _reject_raw_credential_keys(
        cls, value: dict[str, Any]
    ) -> dict[str, Any]:
        ensure_tier_metadata_is_credential_free(value)
        return value


MODEL_TIER_MIGRATION_ANNOTATION = "migratedFrom"
"""Annotation key stamping the provenance of a migration-backfilled tier."""

LEGACY_DEFAULT_TIER_MIGRATION_SOURCE = "default_model_default_effort"
"""``migratedFrom`` value for tiers backfilled from legacy model/effort defaults."""

RUNTIME_DEFAULT_TIER_MIGRATION_SOURCE = "runtime_default"
"""``migratedFrom`` value for tiers backfilled for profiles without legacy defaults."""


def _carries_only_migration_provenance(annotations: Any, *, source: str) -> bool:
    """Return True when annotations are empty or only the migration provenance mark."""

    resolved = annotations or {}
    if not isinstance(resolved, Mapping):
        return False
    if not resolved:
        return True
    return dict(resolved) == {MODEL_TIER_MIGRATION_ANNOTATION: source}


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
        and _carries_only_migration_provenance(
            tier.get("annotations"),
            source=RUNTIME_DEFAULT_TIER_MIGRATION_SOURCE,
        )
    )


def is_single_legacy_default_model_effort_tier(
    model_tiers: Any,
    *,
    legacy_default_model: str | None,
    legacy_default_effort: str | None,
) -> bool:
    """Return True for a lone tier still mirroring the profile's legacy defaults."""

    if not isinstance(model_tiers, list) or len(model_tiers) != 1:
        return False
    tier = model_tiers[0]
    if not isinstance(tier, Mapping):
        return False
    return (
        tier.get("label") == "Legacy default"
        and tier.get("model") == legacy_default_model
        and tier.get("effort") == legacy_default_effort
        and (tier.get("parameters") or {}) == {}
        and _carries_only_migration_provenance(
            tier.get("annotations"),
            source=LEGACY_DEFAULT_TIER_MIGRATION_SOURCE,
        )
    )


def initial_model_effort_tier_policy(
    *,
    runtime_id: Any,
    provider_id: Any,
    legacy_default_model: str | None,
    legacy_default_effort: str | None,
) -> tuple[list[dict[str, Any]], int]:
    """Return the tier policy a new profile receives when none was authored.

    Legacy defaults become one Legacy default tier. Otherwise the first-party
    OpenAI/Codex and Anthropic/Claude Code routes receive their automatic
    family template (default Tier 2), and every other route one Runtime
    default tier.
    """

    if legacy_default_model is not None or legacy_default_effort is not None:
        return [
            legacy_default_model_effort_tier(
                legacy_default_model=legacy_default_model,
                legacy_default_effort=legacy_default_effort,
            )
        ], 1
    from moonmind.provider_profiles.model_families import (
        first_party_family_tier_template,
    )

    template = first_party_family_tier_template(runtime_id, provider_id)
    if template is not None:
        return template
    return [runtime_default_model_effort_tier()], 1


def coerce_model_effort_tier_policy(
    *,
    model_tiers: Any,
    default_model_tier: int | None,
    legacy_default_model: str | None,
    legacy_default_effort: str | None,
    empty_as_missing: bool = False,
    runtime_id: Any = None,
    provider_id: Any = None,
) -> tuple[list[dict[str, Any]], int]:
    """Validate and normalize persisted Provider Profile tier policy.

    Missing tier policy is migrated from legacy defaults when present. A new
    profile on a route with a built-in family template (when ``runtime_id``
    and ``provider_id`` are supplied) receives that template; otherwise it
    receives a runtime-default tier.
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
        elif runtime_id is not None and model_tiers is None:
            raw_tiers, template_default = initial_model_effort_tier_policy(
                runtime_id=runtime_id,
                provider_id=provider_id,
                legacy_default_model=None,
                legacy_default_effort=None,
            )
            if default_model_tier is None:
                default_model_tier = template_default
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


def ensure_tier_metadata_is_credential_free(value: Any) -> None:
    """Reject credential-like keys or values in tier-controlled metadata."""

    if _contains_sensitive_key(value):
        raise ValueError("tier metadata must not contain raw credential-like keys")


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
