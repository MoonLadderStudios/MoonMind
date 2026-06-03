"""Billing-aware token cost helpers for model and provider routing."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Any, Mapping

from moonmind.utils.metrics import get_metrics_emitter

logger = logging.getLogger(__name__)

_ENV_PRICING_KEY = "MOONMIND_MODEL_PRICING_JSON"


@dataclass(frozen=True, slots=True)
class ModelTokenPricing:
    """Per-million-token price metadata for one model or profile."""

    input_per_million_usd: float
    output_per_million_usd: float
    source: str = "configured"

    @property
    def blended_per_million_usd(self) -> float:
        return self.input_per_million_usd + self.output_per_million_usd


@dataclass(frozen=True, slots=True)
class ModelCostEstimate:
    """Estimated provider cost for an LLM usage record."""

    input_tokens: int
    output_tokens: int
    total_tokens: int
    cost_estimate_usd: float
    input_per_million_usd: float
    output_per_million_usd: float
    pricing_source: str

    def to_metadata(self) -> dict[str, Any]:
        return {
            "inputTokens": self.input_tokens,
            "outputTokens": self.output_tokens,
            "totalTokens": self.total_tokens,
            "costEstimateUsd": self.cost_estimate_usd,
            "inputPerMillionUsd": self.input_per_million_usd,
            "outputPerMillionUsd": self.output_per_million_usd,
            "pricingSource": self.pricing_source,
        }


_DEFAULT_MODEL_PRICING: dict[str, ModelTokenPricing] = {
    "gpt-4o-mini": ModelTokenPricing(0.15, 0.60, "built_in"),
    "gpt-4o": ModelTokenPricing(5.00, 15.00, "built_in"),
    "gpt-3.5-turbo": ModelTokenPricing(0.50, 1.50, "built_in"),
    "claude-3-5-sonnet": ModelTokenPricing(3.00, 15.00, "built_in"),
    "claude-sonnet": ModelTokenPricing(3.00, 15.00, "built_in"),
    "claude-3-5-haiku": ModelTokenPricing(0.80, 4.00, "built_in"),
    "claude-haiku": ModelTokenPricing(0.80, 4.00, "built_in"),
    "gemini-1.5-flash": ModelTokenPricing(0.075, 0.30, "built_in"),
    "gemini-1.5-pro": ModelTokenPricing(1.25, 5.00, "built_in"),
    "gemini-pro": ModelTokenPricing(1.25, 5.00, "built_in"),
}


def _coerce_non_negative_float(value: object) -> float | None:
    try:
        parsed = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if parsed < 0:
        return None
    return parsed


def _pricing_from_mapping(
    payload: Mapping[str, Any],
    *,
    source: str,
) -> ModelTokenPricing | None:
    input_price = _coerce_non_negative_float(
        payload.get("inputPerMillionUsd")
        or payload.get("input_per_million_usd")
        or payload.get("promptPerMillionUsd")
        or payload.get("prompt_per_million_usd")
    )
    output_price = _coerce_non_negative_float(
        payload.get("outputPerMillionUsd")
        or payload.get("output_per_million_usd")
        or payload.get("completionPerMillionUsd")
        or payload.get("completion_per_million_usd")
    )
    if input_price is None or output_price is None:
        return None
    return ModelTokenPricing(input_price, output_price, source)


def _env_pricing() -> dict[str, ModelTokenPricing]:
    raw = os.getenv(_ENV_PRICING_KEY)
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("%s is not valid JSON; ignoring model pricing override", _ENV_PRICING_KEY)
        return {}
    if not isinstance(parsed, dict):
        logger.warning("%s must be a JSON object; ignoring model pricing override", _ENV_PRICING_KEY)
        return {}

    pricing: dict[str, ModelTokenPricing] = {}
    for model_id, model_payload in parsed.items():
        if not isinstance(model_payload, Mapping):
            continue
        normalized = str(model_id or "").strip().lower()
        if not normalized:
            continue
        model_pricing = _pricing_from_mapping(model_payload, source="env")
        if model_pricing:
            pricing[normalized] = model_pricing
    return pricing


def pricing_from_profile_metadata(
    profile_metadata: Mapping[str, Any] | None,
) -> ModelTokenPricing | None:
    """Extract pricing from provider-profile metadata JSON fields."""

    if not isinstance(profile_metadata, Mapping):
        return None
    for key in ("billing", "pricing", "cost"):
        value = profile_metadata.get(key)
        if isinstance(value, Mapping):
            pricing = _pricing_from_mapping(value, source=f"profile.{key}")
            if pricing:
                return pricing
    return _pricing_from_mapping(profile_metadata, source="profile")


def pricing_for_model(model: str | None) -> ModelTokenPricing | None:
    normalized_model = str(model or "").strip().lower()
    if not normalized_model:
        return None
    env_pricing = _env_pricing()
    if normalized_model in env_pricing:
        return env_pricing[normalized_model]
    for known_model, pricing in _DEFAULT_MODEL_PRICING.items():
        if normalized_model == known_model or known_model in normalized_model:
            return pricing
    return None


def estimate_model_cost(
    *,
    model: str | None,
    input_tokens: int | None,
    output_tokens: int | None,
    pricing: ModelTokenPricing | None = None,
) -> ModelCostEstimate | None:
    resolved_pricing = pricing or pricing_for_model(model)
    if resolved_pricing is None:
        return None
    safe_input_tokens = max(0, int(input_tokens or 0))
    safe_output_tokens = max(0, int(output_tokens or 0))
    total_tokens = safe_input_tokens + safe_output_tokens
    cost = (
        (safe_input_tokens * resolved_pricing.input_per_million_usd)
        + (safe_output_tokens * resolved_pricing.output_per_million_usd)
    ) / 1_000_000
    return ModelCostEstimate(
        input_tokens=safe_input_tokens,
        output_tokens=safe_output_tokens,
        total_tokens=total_tokens,
        cost_estimate_usd=round(cost, 8),
        input_per_million_usd=resolved_pricing.input_per_million_usd,
        output_per_million_usd=resolved_pricing.output_per_million_usd,
        pricing_source=resolved_pricing.source,
    )


def emit_llm_cost_telemetry(
    *,
    provider: str | None,
    model: str | None,
    estimate: ModelCostEstimate | None,
) -> None:
    """Emit best-effort operator-visible cost telemetry."""

    if estimate is None:
        return
    tags = {
        "provider": provider or "unknown",
        "model": model or "unknown",
        "pricing_source": estimate.pricing_source,
    }
    get_metrics_emitter().increment(
        "llm_cost_estimate_usd_total",
        value=estimate.cost_estimate_usd,
        tags=tags,
    )
    logger.info(
        "moonmind.cost_estimate_usd",
        extra={
            "moonmind.provider": provider or "unknown",
            "moonmind.model": model or "unknown",
            "moonmind.token_input": estimate.input_tokens,
            "moonmind.token_output": estimate.output_tokens,
            "moonmind.cost_estimate_usd": estimate.cost_estimate_usd,
            "moonmind.pricing_source": estimate.pricing_source,
        },
    )


__all__ = [
    "ModelCostEstimate",
    "ModelTokenPricing",
    "emit_llm_cost_telemetry",
    "estimate_model_cost",
    "pricing_for_model",
    "pricing_from_profile_metadata",
]
