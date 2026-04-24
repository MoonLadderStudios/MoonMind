from __future__ import annotations

from moonmind.workflows.provider_failures import (
    classify_provider_failure,
    provider_error_requires_cooldown,
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

def test_classifies_http_401_as_auth_user_error() -> None:
    result = classify_provider_failure("turn failed: http 401")

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
