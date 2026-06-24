from __future__ import annotations

from datetime import datetime, timezone

from moonmind.workflows.provider_failures import (
    EXPAND_CREDENTIAL_SCOPE_RECOMMENDATION,
    PROVIDER_ERROR_CLASS_AUTH,
    PROVIDER_ERROR_CLASS_CAPACITY,
    PROVIDER_ERROR_CLASS_CREDENTIAL_SCOPE,
    PROVIDER_ERROR_CLASS_RATE_LIMIT,
    ProviderFailureEvent,
    build_provider_failure_event,
    classify_provider_failure,
    provider_error_class_for_code,
    provider_error_requires_cooldown,
    provider_failure_event_from_metadata,
    provider_failure_event_requires_cooldown,
    provider_failure_event_to_metadata,
    provider_failure_search_markers,
    resolve_provider_cooldown_seconds,
    sanitized_summary_for_class,
)

def test_classifies_high_demand_as_provider_capacity() -> None:
    result = classify_provider_failure(
        "We're currently experiencing high demand, which may cause temporary errors."
    )

    assert result is not None
    assert result.failure_class == "integration_error"
    assert result.provider_error_code == "provider_capacity"
    assert result.retry_recommendation == "retry_after_cooldown"

def test_classifies_http_500_as_provider_capacity() -> None:
    result = classify_provider_failure("http 500")

    assert result is not None
    assert result.provider_error_code == "provider_capacity"

def test_classifies_rate_limit_as_429() -> None:
    result = classify_provider_failure("Too many requests: rate limit")

    assert result is not None
    assert result.provider_error_code == "429"

def test_classifies_codex_usage_limit_as_429() -> None:
    result = classify_provider_failure(
        "You've hit your usage limit. To get more access now, send a request "
        "to your admin or try again at 4:45 AM."
    )

    assert result is not None
    assert result.failure_class == "integration_error"
    assert result.provider_error_code == "429"
    assert result.retry_recommendation == "retry_after_cooldown"

def test_classifies_codex_structured_usage_limit_as_429() -> None:
    result = classify_provider_failure("provider error type=usage_limit_reached")

    assert result is not None
    assert result.failure_class == "integration_error"
    assert result.provider_error_code == "429"
    assert result.retry_recommendation == "retry_after_cooldown"

def test_provider_failure_search_markers_cover_classification_markers() -> None:
    markers = provider_failure_search_markers()

    for expected in (
        "rate-limit",
        "http 503",
        "status 503",
        "temporary errors",
        "try again later",
        "usage_limit_reached",
        "http 401",
        "http 403",
        "insufficient scope",
    ):
        assert expected in markers

def test_classifies_claude_code_short_limit_as_429() -> None:
    """Claude Code's shorter wording omits 'usage' — markers must still match."""
    result = classify_provider_failure(
        "You've hit your limit · resets 1pm (UTC)"
    )

    assert result is not None
    assert result.failure_class == "integration_error"
    assert result.provider_error_code == "429"
    assert result.retry_recommendation == "retry_after_cooldown"

def test_classifies_claude_code_session_limit_as_429() -> None:
    result = classify_provider_failure(
        "You've hit your session limit · resets 3:20am (UTC)"
    )

    assert result is not None
    assert result.failure_class == "integration_error"
    assert result.provider_error_code == "429"
    assert result.retry_recommendation == "retry_after_cooldown"

def test_classifies_http_401_as_auth_user_error() -> None:
    result = classify_provider_failure("turn failed: http 401")

    assert result is not None
    assert result.failure_class == "user_error"
    assert result.provider_error_code == "401"
    assert result.retry_recommendation == "reauthenticate"

def test_classifies_claude_not_logged_in_as_auth_user_error() -> None:
    result = classify_provider_failure("Not logged in. Please run /login")

    assert result is not None
    assert result.failure_class == "user_error"
    assert result.provider_error_code == "401"
    assert result.retry_recommendation == "reauthenticate"

def test_provider_cooldown_accepts_capacity_code_or_retry_recommendation() -> None:
    assert provider_error_requires_cooldown(
        provider_error_code="provider_capacity",
        retry_recommendation=None,
    )
    assert provider_error_requires_cooldown(
        provider_error_code=None,
        retry_recommendation="retry_after_cooldown",
    )
    assert not provider_error_requires_cooldown(
        provider_error_code="branch_publish_failed",
        retry_recommendation=None,
    )

def test_provider_cooldown_rejects_auth_reauthenticate_failure() -> None:
    assert not provider_error_requires_cooldown(
        provider_error_code="401",
        retry_recommendation="reauthenticate",
    )

# ---------------------------------------------------------------------------
# MM-882: canonical structured provider failure event contract
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 6, 24, 12, 0, 0, tzinfo=timezone.utc)

def test_classification_exposes_canonical_provider_error_class() -> None:
    assert classify_provider_failure("http 401").provider_error_class == (
        PROVIDER_ERROR_CLASS_AUTH
    )
    assert classify_provider_failure("rate limit").provider_error_class == (
        PROVIDER_ERROR_CLASS_RATE_LIMIT
    )
    assert classify_provider_failure("http 503").provider_error_class == (
        PROVIDER_ERROR_CLASS_CAPACITY
    )

def test_classifies_credential_scope_forbidden_as_distinct_class() -> None:
    result = classify_provider_failure(
        "HTTP 403 Forbidden: your token is missing the required scope"
    )

    assert result is not None
    assert result.provider_error_class == PROVIDER_ERROR_CLASS_CREDENTIAL_SCOPE
    assert result.provider_error_code == "403"
    assert result.failure_class == "user_error"
    assert result.retry_recommendation == EXPAND_CREDENTIAL_SCOPE_RECOMMENDATION

def test_build_event_from_structured_fields_prefers_explicit_values() -> None:
    event = build_provider_failure_event(
        provider_error_class=PROVIDER_ERROR_CLASS_RATE_LIMIT,
        retry_after_seconds=42,
        reset_at="2026-06-24T12:05:00Z",
        quota_scope="account",
        provider_request_id="req_abc123",
        raw_error_ref="art_raw_error_1",
    )

    assert event is not None
    assert event.provider_error_class == PROVIDER_ERROR_CLASS_RATE_LIMIT
    assert event.provider_error_code == "429"
    assert event.retry_after_seconds == 42
    assert event.reset_at == "2026-06-24T12:05:00Z"
    assert event.quota_scope == "account"
    assert event.provider_request_id == "req_abc123"
    assert event.raw_error_ref == "art_raw_error_1"
    assert event.sanitized_summary is not None

def test_build_event_text_marker_fallback_when_no_structured_fields() -> None:
    event = build_provider_failure_event(reason="You've hit your usage limit")

    assert event is not None
    assert event.provider_error_class == PROVIDER_ERROR_CLASS_RATE_LIMIT
    assert event.provider_error_code == "429"
    assert event.retry_recommendation == "retry_after_cooldown"
    # No structured retry metadata available from text markers.
    assert event.retry_after_seconds is None
    assert event.reset_at is None

def test_build_event_derives_class_from_provider_error_code_only() -> None:
    event = build_provider_failure_event(provider_error_code="429")

    assert event is not None
    assert event.provider_error_class == PROVIDER_ERROR_CLASS_RATE_LIMIT

def test_build_event_returns_none_without_any_failure_signal() -> None:
    assert build_provider_failure_event() is None
    assert build_provider_failure_event(reason="all good, finished cleanly") is None

def test_build_event_unknown_provider_error_class_is_preserved() -> None:
    event = build_provider_failure_event(provider_error_class="moon_specific_glitch")

    assert event is not None
    assert event.provider_error_class == "moon_specific_glitch"
    # Unknown class still produces a distinct, raw-text-free operator summary.
    assert event.sanitized_summary == (
        "Provider reported an unclassified failure "
        "(provider_error_class=moon_specific_glitch)."
    )
    # Unknown classes do not map to a cooldown by themselves.
    assert provider_failure_event_requires_cooldown(event) is False

def test_distinct_sanitized_summaries_per_class() -> None:
    summaries = {
        PROVIDER_ERROR_CLASS_AUTH: sanitized_summary_for_class(
            PROVIDER_ERROR_CLASS_AUTH
        ),
        PROVIDER_ERROR_CLASS_RATE_LIMIT: sanitized_summary_for_class(
            PROVIDER_ERROR_CLASS_RATE_LIMIT
        ),
        PROVIDER_ERROR_CLASS_CAPACITY: sanitized_summary_for_class(
            PROVIDER_ERROR_CLASS_CAPACITY
        ),
        PROVIDER_ERROR_CLASS_CREDENTIAL_SCOPE: sanitized_summary_for_class(
            PROVIDER_ERROR_CLASS_CREDENTIAL_SCOPE
        ),
    }

    assert all(value for value in summaries.values())
    # Every class yields a distinct operator-facing summary.
    assert len(set(summaries.values())) == 4

def test_provider_error_class_for_code_mapping() -> None:
    assert provider_error_class_for_code("401") == PROVIDER_ERROR_CLASS_AUTH
    assert provider_error_class_for_code("403") == (
        PROVIDER_ERROR_CLASS_CREDENTIAL_SCOPE
    )
    assert provider_error_class_for_code("provider_capacity") == (
        PROVIDER_ERROR_CLASS_CAPACITY
    )
    assert provider_error_class_for_code("totally_unknown") is None

def test_event_cooldown_decision_prefers_structured_retry_after() -> None:
    event = build_provider_failure_event(
        provider_error_class=PROVIDER_ERROR_CLASS_RATE_LIMIT,
        retry_after_seconds=120,
    )

    assert provider_failure_event_requires_cooldown(event) is True
    assert (
        resolve_provider_cooldown_seconds(event, now=_NOW, default_seconds=900)
        == 120
    )

def test_event_cooldown_decision_derives_seconds_from_reset_at() -> None:
    event = build_provider_failure_event(
        provider_error_class=PROVIDER_ERROR_CLASS_CAPACITY,
        reset_at="2026-06-24T12:03:00Z",
    )

    assert provider_failure_event_requires_cooldown(event) is True
    # reset_at is 3 minutes after _NOW.
    assert (
        resolve_provider_cooldown_seconds(event, now=_NOW, default_seconds=900)
        == 180
    )

def test_event_cooldown_quota_scope_present_triggers_cooldown() -> None:
    # Even an otherwise-unknown class is routed to cooldown when a quota scope
    # marker is present (structured fields preferred over markers).
    event = build_provider_failure_event(
        provider_error_class="weird_quota_state",
        quota_scope="model",
    )

    assert event is not None
    assert provider_failure_event_requires_cooldown(event) is True

def test_event_cooldown_missing_retry_metadata_falls_back_to_default() -> None:
    # Rate-limit class with no retry_after_seconds/reset_at must still cooldown,
    # using the caller-provided default (e.g. the profile's configured value).
    event = build_provider_failure_event(
        provider_error_class=PROVIDER_ERROR_CLASS_RATE_LIMIT,
    )

    assert provider_failure_event_requires_cooldown(event) is True
    assert event.retry_after_seconds is None
    assert event.reset_at is None
    assert (
        resolve_provider_cooldown_seconds(event, now=_NOW, default_seconds=900)
        == 900
    )

def test_event_cooldown_ignores_past_reset_at() -> None:
    event = build_provider_failure_event(
        provider_error_class=PROVIDER_ERROR_CLASS_RATE_LIMIT,
        reset_at="2020-01-01T00:00:00Z",
    )

    # A stale reset_at must not produce a zero/negative cooldown.
    assert (
        resolve_provider_cooldown_seconds(event, now=_NOW, default_seconds=900)
        == 900
    )

def test_credential_scope_event_does_not_cooldown() -> None:
    event = build_provider_failure_event(
        provider_error_class=PROVIDER_ERROR_CLASS_CREDENTIAL_SCOPE,
    )

    assert event is not None
    assert provider_failure_event_requires_cooldown(event) is False

def test_resolve_cooldown_seconds_none_event_returns_default() -> None:
    assert resolve_provider_cooldown_seconds(None, now=_NOW, default_seconds=900) == 900

def test_event_metadata_round_trips() -> None:
    event = build_provider_failure_event(
        provider_error_class=PROVIDER_ERROR_CLASS_RATE_LIMIT,
        retry_after_seconds=30,
        reset_at="2026-06-24T12:05:00Z",
        quota_scope="account",
        provider_request_id="req_round_trip",
        raw_error_ref="art_raw_2",
    )
    assert event is not None

    metadata = provider_failure_event_to_metadata(event)
    assert metadata["providerErrorClass"] == PROVIDER_ERROR_CLASS_RATE_LIMIT
    assert metadata["retryAfterSeconds"] == 30
    assert metadata["resetAt"] == "2026-06-24T12:05:00Z"
    assert metadata["quotaScope"] == "account"
    assert metadata["providerRequestId"] == "req_round_trip"
    assert metadata["rawErrorRef"] == "art_raw_2"
    assert metadata["sanitizedSummary"] == event.sanitized_summary
    # Raw provider text is never serialized into the envelope.
    assert "reason" not in metadata

    rebuilt = provider_failure_event_from_metadata(metadata)
    assert rebuilt == event

def test_event_from_metadata_legacy_marker_payload_still_cools_down() -> None:
    # In-flight runs may carry the previous marker-only envelope shape.
    legacy_metadata = {
        "providerErrorCode": "429",
        "retryRecommendation": "retry_after_cooldown",
    }

    event = provider_failure_event_from_metadata(legacy_metadata)
    assert event is not None
    assert event.provider_error_class == PROVIDER_ERROR_CLASS_RATE_LIMIT
    assert provider_failure_event_requires_cooldown(event) is True
    assert (
        resolve_provider_cooldown_seconds(event, now=_NOW, default_seconds=900)
        == 900
    )

def test_event_from_metadata_handles_missing_or_blank_envelope() -> None:
    assert provider_failure_event_from_metadata(None) is None
    assert provider_failure_event_from_metadata({}) is None

def test_event_to_metadata_omits_unset_optional_fields() -> None:
    event = ProviderFailureEvent(
        provider_error_class=PROVIDER_ERROR_CLASS_AUTH,
        provider_error_code="401",
        retry_recommendation="reauthenticate",
        sanitized_summary=sanitized_summary_for_class(PROVIDER_ERROR_CLASS_AUTH),
    )

    metadata = provider_failure_event_to_metadata(event)
    assert "retryAfterSeconds" not in metadata
    assert "resetAt" not in metadata
    assert "quotaScope" not in metadata
    assert "rawErrorRef" not in metadata
