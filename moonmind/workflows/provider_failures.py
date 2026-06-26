"""Shared classification helpers for managed-provider failures.

MM-882: this module owns the canonical structured provider failure event
contract emitted by runtime adapters. Provider-manager cooldown and retry
decisions prefer the structured fields (``provider_error_class``,
``retry_after_seconds``, ``reset_at``, ``quota_scope``) and fall back to brittle
text-marker classification only when those structured fields are absent.

The event deliberately carries no raw provider text: the operator-facing
``sanitized_summary`` is derived from the canonical class, and raw provider
detail is referenced by ``raw_error_ref`` (an artifact ref) so it is never
leaked into ordinary logs or workflow payloads.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

PROVIDER_RATE_LIMIT_ERROR_CODE = "429"
PROVIDER_CAPACITY_ERROR_CODE = "provider_capacity"
PROVIDER_AUTH_ERROR_CODE = "401"
PROVIDER_CREDENTIAL_SCOPE_ERROR_CODE = "403"
RETRY_AFTER_COOLDOWN_RECOMMENDATION = "retry_after_cooldown"
REAUTHENTICATE_RECOMMENDATION = "reauthenticate"
EXPAND_CREDENTIAL_SCOPE_RECOMMENDATION = "expand_credential_scope"

# Canonical ``provider_error_class`` values (MM-882). Runtime adapters set these
# from structured provider responses where available; the text-marker classifier
# derives them as a fallback.
PROVIDER_ERROR_CLASS_AUTH = "auth"
PROVIDER_ERROR_CLASS_RATE_LIMIT = "rate_limit"
PROVIDER_ERROR_CLASS_CAPACITY = "capacity"
PROVIDER_ERROR_CLASS_CREDENTIAL_SCOPE = "credential_scope"

_MAX_PROVIDER_ERROR_CLASS_LEN = 64

_RATE_LIMIT_MARKERS = (
    "429",
    "rate limit",
    "rate-limit",
    "too many requests",
    "usage limit",
    "usage_limit_reached",
    "hit your limit",
    "hit your session limit",
    "send a request to your admin",
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
    "not logged in",
    "please run /login",
)

# Credential-scope markers are deliberately scoped to HTTP 403 wording or
# explicit credential/scope phrasing. A bare ``permission denied`` is omitted on
# purpose: it also appears in ordinary shell/git/filesystem errors (for example
# ``bash: ./script: Permission denied`` or ``remote: Permission denied``) that
# ``provider_failure_search_markers()`` would otherwise surface from recovery log
# scans and misclassify as a provider ``403``/``credential_scope`` failure.
_CREDENTIAL_SCOPE_MARKERS = (
    "http 403",
    "status 403",
    "403 forbidden",
    "forbidden",
    "insufficient scope",
    "insufficient_scope",
    "insufficient permission",
    "insufficient permissions",
    "missing scope",
    "missing required scope",
    "requires the following scopes",
)

# Distinct sanitized operator summaries per canonical class. These intentionally
# contain no raw provider text, so they are safe for ordinary logs and workflow
# payloads (raw detail is referenced via ``raw_error_ref``).
_SANITIZED_SUMMARY_BY_CLASS: dict[str, str] = {
    PROVIDER_ERROR_CLASS_AUTH: (
        "Provider authentication failed; reauthenticate the selected provider profile."
    ),
    PROVIDER_ERROR_CLASS_RATE_LIMIT: (
        "Provider rate limit reached; the run will retry after a profile cooldown."
    ),
    PROVIDER_ERROR_CLASS_CAPACITY: (
        "Provider capacity is temporarily exhausted; the run will retry after a "
        "profile cooldown."
    ),
    PROVIDER_ERROR_CLASS_CREDENTIAL_SCOPE: (
        "Provider credentials lack the required scope; grant the missing "
        "credential scope before retrying."
    ),
}

_CODE_TO_CLASS: dict[str, str] = {
    PROVIDER_AUTH_ERROR_CODE: PROVIDER_ERROR_CLASS_AUTH,
    PROVIDER_RATE_LIMIT_ERROR_CODE: PROVIDER_ERROR_CLASS_RATE_LIMIT,
    PROVIDER_CAPACITY_ERROR_CODE: PROVIDER_ERROR_CLASS_CAPACITY,
    PROVIDER_CREDENTIAL_SCOPE_ERROR_CODE: PROVIDER_ERROR_CLASS_CREDENTIAL_SCOPE,
}

_CLASS_TO_CODE: dict[str, str] = {
    PROVIDER_ERROR_CLASS_AUTH: PROVIDER_AUTH_ERROR_CODE,
    PROVIDER_ERROR_CLASS_RATE_LIMIT: PROVIDER_RATE_LIMIT_ERROR_CODE,
    PROVIDER_ERROR_CLASS_CAPACITY: PROVIDER_CAPACITY_ERROR_CODE,
    PROVIDER_ERROR_CLASS_CREDENTIAL_SCOPE: PROVIDER_CREDENTIAL_SCOPE_ERROR_CODE,
}

_CLASS_TO_RECOMMENDATION: dict[str, str] = {
    PROVIDER_ERROR_CLASS_AUTH: REAUTHENTICATE_RECOMMENDATION,
    PROVIDER_ERROR_CLASS_RATE_LIMIT: RETRY_AFTER_COOLDOWN_RECOMMENDATION,
    PROVIDER_ERROR_CLASS_CAPACITY: RETRY_AFTER_COOLDOWN_RECOMMENDATION,
    PROVIDER_ERROR_CLASS_CREDENTIAL_SCOPE: EXPAND_CREDENTIAL_SCOPE_RECOMMENDATION,
}

# Canonical classes whose default disposition is a profile cooldown retry.
_COOLDOWN_CLASSES = frozenset(
    {PROVIDER_ERROR_CLASS_RATE_LIMIT, PROVIDER_ERROR_CLASS_CAPACITY}
)

@dataclass(frozen=True)
class ProviderFailureClassification:
    """Text-marker classification result (the fallback path).

    ``provider_error_class`` carries the canonical class derived from the marker
    match so callers can build a :class:`ProviderFailureEvent` without
    re-deriving it from ``provider_error_code``.
    """

    failure_class: str
    provider_error_code: str
    retry_recommendation: str
    reason: str
    provider_error_class: str

@dataclass(frozen=True)
class ProviderFailureEvent:
    """Canonical structured provider failure event emitted by runtime adapters.

    Adapters populate the structured fields they can observe directly from the
    provider response (status, headers, structured error body). Downstream
    provider-manager cooldown/retry decisions prefer these canonical fields over
    brittle text-marker classification.
    """

    provider_error_class: str | None = None
    provider_error_code: str | None = None
    retry_recommendation: str | None = None
    retry_after_seconds: int | None = None
    reset_at: str | None = None
    quota_scope: str | None = None
    credential_scope: str | None = None
    provider_request_id: str | None = None
    raw_error_ref: str | None = None
    sanitized_summary: str | None = None

    def requires_cooldown(self) -> bool:
        return provider_failure_event_requires_cooldown(self)

    def to_metadata(self) -> dict[str, Any]:
        return provider_failure_event_to_metadata(self)

def classify_provider_failure(
    reason: Any,
) -> ProviderFailureClassification | None:
    """Return structured metadata for provider failures that need policy handling.

    This is the text-marker fallback path. Canonical structured event data is
    preferred by callers; see :func:`build_provider_failure_event`.
    """

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
            provider_error_class=PROVIDER_ERROR_CLASS_AUTH,
        )
    if any(marker in normalized for marker in _CREDENTIAL_SCOPE_MARKERS):
        return ProviderFailureClassification(
            failure_class="user_error",
            provider_error_code=PROVIDER_CREDENTIAL_SCOPE_ERROR_CODE,
            retry_recommendation=EXPAND_CREDENTIAL_SCOPE_RECOMMENDATION,
            reason=rendered,
            provider_error_class=PROVIDER_ERROR_CLASS_CREDENTIAL_SCOPE,
        )
    if any(marker in normalized for marker in _RATE_LIMIT_MARKERS):
        return ProviderFailureClassification(
            failure_class="integration_error",
            provider_error_code=PROVIDER_RATE_LIMIT_ERROR_CODE,
            retry_recommendation=RETRY_AFTER_COOLDOWN_RECOMMENDATION,
            reason=rendered,
            provider_error_class=PROVIDER_ERROR_CLASS_RATE_LIMIT,
        )
    if any(marker in normalized for marker in _CAPACITY_MARKERS):
        return ProviderFailureClassification(
            failure_class="integration_error",
            provider_error_code=PROVIDER_CAPACITY_ERROR_CODE,
            retry_recommendation=RETRY_AFTER_COOLDOWN_RECOMMENDATION,
            reason=rendered,
            provider_error_class=PROVIDER_ERROR_CLASS_CAPACITY,
        )
    return None

def _coerce_optional_str(value: Any) -> str | None:
    normalized = str(value).strip() if value is not None else ""
    return normalized or None

def _coerce_optional_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None

def provider_error_class_for_code(provider_error_code: Any) -> str | None:
    """Map a known ``provider_error_code`` onto its canonical class."""

    normalized = _coerce_optional_str(provider_error_code)
    if normalized is None:
        return None
    return _CODE_TO_CLASS.get(normalized.lower())

def sanitized_summary_for_class(provider_error_class: Any) -> str | None:
    """Return a distinct, raw-text-free operator summary for a class."""

    normalized = _coerce_optional_str(provider_error_class)
    if normalized is None:
        return None
    summary = _SANITIZED_SUMMARY_BY_CLASS.get(normalized.lower())
    if summary is not None:
        return summary
    # Unknown/unclassified class: still produce a distinct, raw-text-free summary
    # that names the reported class label (bounded so a noisy token cannot bloat
    # the payload).
    safe_label = normalized[:_MAX_PROVIDER_ERROR_CLASS_LEN]
    return (
        "Provider reported an unclassified failure "
        f"(provider_error_class={safe_label})."
    )

def build_provider_failure_event(
    *,
    classification: ProviderFailureClassification | None = None,
    reason: Any = None,
    provider_error_class: Any = None,
    provider_error_code: Any = None,
    retry_recommendation: Any = None,
    retry_after_seconds: Any = None,
    reset_at: Any = None,
    quota_scope: Any = None,
    credential_scope: Any = None,
    provider_request_id: Any = None,
    raw_error_ref: Any = None,
) -> ProviderFailureEvent | None:
    """Build the canonical structured failure event.

    Explicit structured fields win. When no structured class/code is supplied,
    classification falls back to text-marker matching of *reason*. Returns
    ``None`` when no failure signal (class, code, retry metadata, quota, or
    credential scope) is present.
    """

    resolved_class = _coerce_optional_str(provider_error_class)
    resolved_code = _coerce_optional_str(provider_error_code)
    resolved_recommendation = _coerce_optional_str(retry_recommendation)

    if (
        classification is None
        and resolved_class is None
        and resolved_code is None
        and reason is not None
    ):
        classification = classify_provider_failure(reason)

    if classification is not None:
        resolved_class = resolved_class or classification.provider_error_class
        resolved_code = resolved_code or classification.provider_error_code
        resolved_recommendation = (
            resolved_recommendation or classification.retry_recommendation
        )

    if resolved_class is None and resolved_code is not None:
        resolved_class = provider_error_class_for_code(resolved_code)
    if resolved_code is None and resolved_class is not None:
        resolved_code = _CLASS_TO_CODE.get(resolved_class.lower())
    if resolved_recommendation is None and resolved_class is not None:
        resolved_recommendation = _CLASS_TO_RECOMMENDATION.get(resolved_class.lower())

    retry_after = _coerce_optional_int(retry_after_seconds)
    if retry_after is not None and retry_after <= 0:
        retry_after = None
    reset = _coerce_optional_str(reset_at)
    quota = _coerce_optional_str(quota_scope)
    credential = _coerce_optional_str(credential_scope)
    request_id = _coerce_optional_str(provider_request_id)
    raw_ref = _coerce_optional_str(raw_error_ref)

    has_signal = any(
        (
            resolved_class is not None,
            resolved_code is not None,
            retry_after is not None,
            reset is not None,
            quota is not None,
            credential is not None,
            request_id is not None,
        )
    )
    if not has_signal:
        return None

    return ProviderFailureEvent(
        provider_error_class=resolved_class,
        provider_error_code=resolved_code,
        retry_recommendation=resolved_recommendation,
        retry_after_seconds=retry_after,
        reset_at=reset,
        quota_scope=quota,
        credential_scope=credential,
        provider_request_id=request_id,
        raw_error_ref=raw_ref,
        sanitized_summary=sanitized_summary_for_class(resolved_class),
    )

def provider_error_requires_cooldown(
    *,
    provider_error_code: str | None,
    retry_recommendation: str | None = None,
) -> bool:
    """Return whether an agent result should be routed through profile cooldown.

    This is the text-marker / code fallback. Structured callers should prefer
    :func:`provider_failure_event_requires_cooldown`.
    """

    normalized_code = str(provider_error_code or "").strip().lower()
    normalized_retry = str(retry_recommendation or "").strip().lower()
    return normalized_code in {
        PROVIDER_RATE_LIMIT_ERROR_CODE,
        PROVIDER_CAPACITY_ERROR_CODE,
    } or normalized_retry == RETRY_AFTER_COOLDOWN_RECOMMENDATION

def provider_failure_event_requires_cooldown(
    event: ProviderFailureEvent | None,
) -> bool:
    """Decide cooldown routing, preferring structured fields over markers."""

    if event is None:
        return False
    # Structured retry/quota signals take precedence over marker-derived codes.
    if event.retry_after_seconds is not None and event.retry_after_seconds > 0:
        return True
    if event.reset_at:
        return True
    if event.quota_scope:
        return True
    normalized_class = (event.provider_error_class or "").strip().lower()
    if normalized_class in _COOLDOWN_CLASSES:
        return True
    return provider_error_requires_cooldown(
        provider_error_code=event.provider_error_code,
        retry_recommendation=event.retry_recommendation,
    )

def _seconds_until_reset(reset_at: str | None, *, now: datetime) -> int | None:
    normalized = _coerce_optional_str(reset_at)
    if normalized is None:
        return None
    candidate = normalized
    if candidate.endswith("Z"):
        candidate = candidate[:-1] + "+00:00"
    try:
        reset_dt = datetime.fromisoformat(candidate)
    except ValueError:
        return None
    if reset_dt.tzinfo is None:
        reset_dt = reset_dt.replace(tzinfo=timezone.utc)
    reference = now if now.tzinfo is not None else now.replace(tzinfo=timezone.utc)
    delta = (reset_dt - reference).total_seconds()
    if delta <= 0:
        return None
    return int(delta)

def resolve_provider_cooldown_seconds(
    event: ProviderFailureEvent | None,
    *,
    now: datetime,
    default_seconds: int,
) -> int:
    """Resolve cooldown seconds, preferring retry_after_seconds then reset_at.

    Falls back to *default_seconds* (e.g. the profile's configured cooldown)
    when no structured retry metadata is present.
    """

    default = max(int(default_seconds), 0)
    if event is None:
        return default
    if event.retry_after_seconds is not None and event.retry_after_seconds > 0:
        return event.retry_after_seconds
    derived = _seconds_until_reset(event.reset_at, now=now)
    if derived is not None and derived > 0:
        return derived
    return default

def provider_failure_event_to_metadata(
    event: ProviderFailureEvent,
) -> dict[str, Any]:
    """Serialize a failure event into the canonical ``providerFailure`` envelope."""

    metadata: dict[str, Any] = {}
    if event.provider_error_class is not None:
        metadata["providerErrorClass"] = event.provider_error_class
    if event.provider_error_code is not None:
        metadata["providerErrorCode"] = event.provider_error_code
    if event.retry_recommendation is not None:
        metadata["retryRecommendation"] = event.retry_recommendation
    if event.retry_after_seconds is not None:
        metadata["retryAfterSeconds"] = event.retry_after_seconds
    if event.reset_at is not None:
        metadata["resetAt"] = event.reset_at
    if event.quota_scope is not None:
        metadata["quotaScope"] = event.quota_scope
    if event.credential_scope is not None:
        metadata["credentialScope"] = event.credential_scope
    if event.provider_request_id is not None:
        metadata["providerRequestId"] = event.provider_request_id
    if event.raw_error_ref is not None:
        metadata["rawErrorRef"] = event.raw_error_ref
    if event.sanitized_summary is not None:
        metadata["sanitizedSummary"] = event.sanitized_summary
    return metadata

def provider_failure_event_from_metadata(
    metadata: Mapping[str, Any] | None,
) -> ProviderFailureEvent | None:
    """Reconstruct a failure event from a ``providerFailure`` envelope."""

    if not isinstance(metadata, Mapping):
        return None
    return build_provider_failure_event(
        provider_error_class=metadata.get("providerErrorClass"),
        provider_error_code=metadata.get("providerErrorCode"),
        retry_recommendation=metadata.get("retryRecommendation"),
        retry_after_seconds=metadata.get("retryAfterSeconds"),
        reset_at=metadata.get("resetAt"),
        quota_scope=metadata.get("quotaScope"),
        credential_scope=metadata.get("credentialScope"),
        provider_request_id=metadata.get("providerRequestId"),
        raw_error_ref=metadata.get("rawErrorRef"),
    )

def provider_failure_search_markers() -> tuple[str, ...]:
    """Return the provider-failure markers suitable for coarse log searches."""

    return tuple(
        dict.fromkeys(
            (
                *_AUTH_FAILURE_MARKERS,
                *_CREDENTIAL_SCOPE_MARKERS,
                *_RATE_LIMIT_MARKERS,
                *_CAPACITY_MARKERS,
            )
        )
    )

__all__ = [
    "PROVIDER_AUTH_ERROR_CODE",
    "PROVIDER_CAPACITY_ERROR_CODE",
    "PROVIDER_CREDENTIAL_SCOPE_ERROR_CODE",
    "PROVIDER_RATE_LIMIT_ERROR_CODE",
    "PROVIDER_ERROR_CLASS_AUTH",
    "PROVIDER_ERROR_CLASS_CAPACITY",
    "PROVIDER_ERROR_CLASS_CREDENTIAL_SCOPE",
    "PROVIDER_ERROR_CLASS_RATE_LIMIT",
    "EXPAND_CREDENTIAL_SCOPE_RECOMMENDATION",
    "REAUTHENTICATE_RECOMMENDATION",
    "RETRY_AFTER_COOLDOWN_RECOMMENDATION",
    "ProviderFailureClassification",
    "ProviderFailureEvent",
    "build_provider_failure_event",
    "classify_provider_failure",
    "provider_error_class_for_code",
    "provider_error_requires_cooldown",
    "provider_failure_event_from_metadata",
    "provider_failure_event_requires_cooldown",
    "provider_failure_event_to_metadata",
    "provider_failure_search_markers",
    "resolve_provider_cooldown_seconds",
    "sanitized_summary_for_class",
]
