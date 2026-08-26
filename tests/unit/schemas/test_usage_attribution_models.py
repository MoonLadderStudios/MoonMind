from decimal import Decimal

import pytest
from pydantic import ValidationError

from moonmind.schemas.usage_attribution_models import UsageAttributionRecord, roll_up_usage


def record(**overrides):
    values = dict(
        usageEventId="event-1", workflowId="workflow", runId="run",
        logicalStepId="step", stepExecutionId="execution", attemptId="attempt",
        provider="openai", providerRequestIdHash="sha256:request",
        usageSource="provider_reported", confidence="authoritative_for_request",
        inputTokens=3, outputTokens=2, totalTokens=5,
    )
    values.update(overrides)
    return UsageAttributionRecord.model_validate(values)


def test_provider_usage_requires_request_boundary():
    with pytest.raises(ValidationError, match="provider request identity"):
        record(providerRequestIdHash=None)


def test_unknown_usage_is_explicit_and_not_zero():
    unavailable = record(
        usageSource="unavailable", confidence="unavailable", providerRequestIdHash=None,
        provider=None, inputTokens=None, outputTokens=None, totalTokens=None,
        unavailableReasonCode="runtime_did_not_report",
    )
    total = roll_up_usage([unavailable])
    assert total.unavailable_requests == 1
    assert total.total_tokens == 0


def test_reported_usage_replaces_estimate_without_double_counting():
    estimated = record(
        usageEventId="estimate", usageSource="moonmind_estimated", confidence="estimated",
        inputTokens=4, outputTokens=1, totalTokens=5, estimatedCostUsd="0.12",
        pricingSource="built_in", pricingVersion="2026-07-01",
    )
    reported = record(usageEventId="final", observedCostUsd="0.10")
    total = roll_up_usage([estimated, reported, reported])
    assert total.total_tokens == 5
    assert total.observed_cost_usd == Decimal("0.10")
    assert total.estimated_cost_usd == Decimal("0")


def test_total_cannot_be_smaller_than_components():
    with pytest.raises(ValidationError, match="smaller"):
        record(totalTokens=4)
