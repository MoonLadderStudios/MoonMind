"""Validation helpers for authored workflow runtime intent."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
)

MODEL_TIER_KEY = "modelTier"
TIER_FALLBACK_KEY = "tierFallback"
HARD_OVERRIDE_AUDIT_KEY = "hardOverrideAudit"
TIER_PREVIEW_KEY = "tierPreview"
SUPPORTED_TIER_FALLBACKS = frozenset({"clamp", "strict"})


class RuntimeIntentValidationError(ValueError):
    """Raised when authored runtime tier intent is invalid."""


class RuntimeTierPreview(BaseModel):
    """Shape-validated advisory Provider Profile snapshot.

    See MoonLadderStudios/MoonMind#3798.
    """

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    profile_id: str = Field(..., alias="profileId")
    profile_version: int | str = Field(..., alias="profileVersion")
    model: str | None = Field(..., alias="model")
    effort: str | None = Field(..., alias="effort")
    requested_tier: int | None = Field(None, alias="requestedTier")
    effective_tier: int | None = Field(None, alias="effectiveTier")
    fallback_reason: str | None = Field(None, alias="fallbackReason")

    @field_validator("profile_id", mode="before")
    @classmethod
    def _validate_profile_id(cls, value: object) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("must be a non-empty string")
        return value.strip()

    @field_validator("profile_version", mode="before")
    @classmethod
    def _validate_profile_version(cls, value: object) -> int | str:
        if isinstance(value, bool) or not isinstance(value, (int, str)):
            raise ValueError("must be a positive integer or non-empty string")
        if isinstance(value, int):
            if value < 1:
                raise ValueError("must be a positive integer or non-empty string")
            return value
        normalized = value.strip()
        if not normalized:
            raise ValueError("must be a positive integer or non-empty string")
        return normalized

    @field_validator("model", "effort", mode="before")
    @classmethod
    def _validate_optional_resolution_string(cls, value: object) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str) or not value.strip():
            raise ValueError("must be null or a non-empty string")
        return value.strip()

    @field_validator("requested_tier", "effective_tier", mode="before")
    @classmethod
    def _validate_optional_tier(cls, value: object) -> int | None:
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError("must be null or an integer greater than or equal to 1")
        return value

    @field_validator("fallback_reason", mode="before")
    @classmethod
    def _validate_optional_fallback_reason(cls, value: object) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str) or not value.strip():
            raise ValueError("must be null or a non-empty string")
        return value.strip()


def _preview_validation_message(
    *,
    field_name: str,
    error: ValidationError,
) -> str:
    detail = error.errors(include_url=False)[0]
    location = ".".join(str(part) for part in detail.get("loc", ()))
    message = str(detail.get("msg") or "is invalid")
    if message.startswith("Value error, "):
        message = message.removeprefix("Value error, ")
    path = f"{field_name}.{TIER_PREVIEW_KEY}"
    if location:
        path = f"{path}.{location}"
    return f"{path} {message}."


def validate_runtime_tier_intent(
    runtime: Mapping[str, Any] | None,
    *,
    field_name: str,
) -> dict[str, Any]:
    """Return a copied runtime payload after validating tier intent fields.

    MM-1171 implements the preset/workflow submission boundary from MM-1168's
    provider profile tier design. Generic runtime metadata remains open-ended,
    but modelTier and tierFallback are now explicit contract fields.
    """

    payload = dict(runtime or {})
    if MODEL_TIER_KEY in payload:
        model_tier = payload[MODEL_TIER_KEY]
        if isinstance(model_tier, bool) or not isinstance(model_tier, int):
            raise RuntimeIntentValidationError(
                f"{field_name}.modelTier must be an integer."
            )
        if model_tier < 1:
            raise RuntimeIntentValidationError(
                f"{field_name}.modelTier must be greater than or equal to 1."
            )
    if TIER_FALLBACK_KEY in payload:
        tier_fallback = payload[TIER_FALLBACK_KEY]
        if tier_fallback not in SUPPORTED_TIER_FALLBACKS:
            supported = ", ".join(sorted(SUPPORTED_TIER_FALLBACKS))
            raise RuntimeIntentValidationError(
                f"{field_name}.tierFallback must be one of: {supported}."
            )
    if TIER_PREVIEW_KEY in payload:
        try:
            preview = RuntimeTierPreview.model_validate(payload[TIER_PREVIEW_KEY])
        except ValidationError as exc:
            raise RuntimeIntentValidationError(
                _preview_validation_message(field_name=field_name, error=exc)
            ) from exc
        payload[TIER_PREVIEW_KEY] = preview.model_dump(
            mode="json",
            by_alias=True,
            exclude_unset=True,
        )
    if HARD_OVERRIDE_AUDIT_KEY in payload:
        hard_override_audit = payload[HARD_OVERRIDE_AUDIT_KEY]
        if not isinstance(hard_override_audit, Mapping) or not hard_override_audit:
            raise RuntimeIntentValidationError(
                f"{field_name}.hardOverrideAudit must be a non-empty object."
            )
    if MODEL_TIER_KEY in payload:
        override_fields = [
            key for key in ("model", "effort") if payload.get(key) is not None
        ]
        if override_fields and HARD_OVERRIDE_AUDIT_KEY not in payload:
            payload[HARD_OVERRIDE_AUDIT_KEY] = {
                "source": "runtime_metadata",
                "fields": override_fields,
                "trace": ["MM-1168", "MM-1171"],
            }
    return payload
