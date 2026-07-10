"""Validation helpers for authored workflow runtime intent."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

MODEL_TIER_KEY = "modelTier"
TIER_FALLBACK_KEY = "tierFallback"
HARD_OVERRIDE_AUDIT_KEY = "hardOverrideAudit"
SUPPORTED_TIER_FALLBACKS = frozenset({"clamp", "strict"})


class RuntimeIntentValidationError(ValueError):
    """Raised when authored runtime tier intent is invalid."""


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
