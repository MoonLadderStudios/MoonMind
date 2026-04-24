"""Shared classification helpers for managed-provider failures."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

PROVIDER_RATE_LIMIT_ERROR_CODE = "429"
PROVIDER_CAPACITY_ERROR_CODE = "provider_capacity"
PROVIDER_AUTH_ERROR_CODE = "401"
RETRY_AFTER_COOLDOWN_RECOMMENDATION = "retry_after_cooldown"
REAUTHENTICATE_RECOMMENDATION = "reauthenticate"

_RATE_LIMIT_MARKERS = (
    "429",
    "rate limit",
    "rate-limit",
    "too many requests",
)

_CAPACITY_MARKERS = (
    "http 500",
    "http 502",
    "http 503",
    "http 504",
    "status 500",
    "status 502",
    "status 503",
    "status 504",
    "500 internal server error",
    "502 bad gateway",
    "503 service unavailable",
    "504 gateway timeout",
    "high demand",
    "overloaded",
    "temporarily unavailable",
    "temporary errors",
    "service unavailable",
    "gateway timeout",
    "try again later",
)

_AUTH_FAILURE_MARKERS = (
    "http 401",
    "status 401",
    "401 unauthorized",
    "unauthorized",
    "invalid api key",
    "invalid token",
    "expired token",
    "authentication failed",
)

@dataclass(frozen=True)
class ProviderFailureClassification:
    failure_class: str
    provider_error_code: str
    retry_recommendation: str
    reason: str

def classify_provider_failure(
    reason: Any,
) -> ProviderFailureClassification | None:
    """Return structured metadata for provider failures that need policy handling."""

    rendered = str(reason or "").strip()
    if not rendered:
        return None
    normalized = rendered.lower()
    if any(marker in normalized for marker in _AUTH_FAILURE_MARKERS):
        return ProviderFailureClassification(
            failure_class="user_error",
            provider_error_code=PROVIDER_AUTH_ERROR_CODE,
            retry_recommendation=REAUTHENTICATE_RECOMMENDATION,
            reason=rendered,
        )
    if any(marker in normalized for marker in _RATE_LIMIT_MARKERS):
        return ProviderFailureClassification(
            failure_class="integration_error",
            provider_error_code=PROVIDER_RATE_LIMIT_ERROR_CODE,
            retry_recommendation=RETRY_AFTER_COOLDOWN_RECOMMENDATION,
            reason=rendered,
        )
    if any(marker in normalized for marker in _CAPACITY_MARKERS):
        return ProviderFailureClassification(
            failure_class="integration_error",
            provider_error_code=PROVIDER_CAPACITY_ERROR_CODE,
            retry_recommendation=RETRY_AFTER_COOLDOWN_RECOMMENDATION,
            reason=rendered,
        )
    return None

def provider_error_requires_cooldown(
    *,
    provider_error_code: str | None,
    retry_recommendation: str | None = None,
) -> bool:
    """Return whether an agent result should be routed through profile cooldown."""

    normalized_code = str(provider_error_code or "").strip().lower()
    normalized_retry = str(retry_recommendation or "").strip().lower()
    return normalized_code in {
        PROVIDER_RATE_LIMIT_ERROR_CODE,
        PROVIDER_CAPACITY_ERROR_CODE,
    } or normalized_retry == RETRY_AFTER_COOLDOWN_RECOMMENDATION

__all__ = [
    "PROVIDER_AUTH_ERROR_CODE",
    "PROVIDER_CAPACITY_ERROR_CODE",
    "PROVIDER_RATE_LIMIT_ERROR_CODE",
    "REAUTHENTICATE_RECOMMENDATION",
    "RETRY_AFTER_COOLDOWN_RECOMMENDATION",
    "ProviderFailureClassification",
    "classify_provider_failure",
    "provider_error_requires_cooldown",
]
