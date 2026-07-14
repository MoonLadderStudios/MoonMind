"""Canonical, bounded usage-attribution contracts and rollups."""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

UsageSource = Literal[
    "provider_reported", "bridge_reported", "moonmind_estimated", "unavailable"
]
UsageConfidence = Literal[
    "authoritative_for_request", "reported", "estimated", "unavailable"
]


class UsageAttributionRecord(BaseModel):
    """One idempotent provider-request usage observation at an attempt boundary."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    schema_version: Literal[1] = Field(1, alias="schemaVersion")
    usage_event_id: str = Field(alias="usageEventId", min_length=1, max_length=200)
    workflow_id: str = Field(alias="workflowId", min_length=1, max_length=200)
    run_id: str = Field(alias="runId", min_length=1, max_length=200)
    logical_step_id: str = Field(alias="logicalStepId", min_length=1, max_length=200)
    step_execution_id: str = Field(alias="stepExecutionId", min_length=1, max_length=200)
    attempt_id: str = Field(alias="attemptId", min_length=1, max_length=200)
    provider: str | None = Field(None, max_length=120)
    provider_request_id_hash: str | None = Field(
        None, alias="providerRequestIdHash", max_length=200
    )
    model: str | None = Field(None, max_length=200)
    usage_source: UsageSource = Field(alias="usageSource")
    confidence: UsageConfidence
    pricing_source: str | None = Field(None, alias="pricingSource", max_length=120)
    pricing_version: str | None = Field(None, alias="pricingVersion", max_length=120)
    input_tokens: int | None = Field(None, alias="inputTokens", ge=0)
    output_tokens: int | None = Field(None, alias="outputTokens", ge=0)
    cached_input_tokens: int | None = Field(None, alias="cachedInputTokens", ge=0)
    reasoning_tokens: int | None = Field(None, alias="reasoningTokens", ge=0)
    total_tokens: int | None = Field(None, alias="totalTokens", ge=0)
    observed_cost_usd: Decimal | None = Field(None, alias="observedCostUsd", ge=0)
    estimated_cost_usd: Decimal | None = Field(None, alias="estimatedCostUsd", ge=0)
    unavailable_reason_code: str | None = Field(
        None, alias="unavailableReasonCode", max_length=120
    )

    @model_validator(mode="after")
    def validate_provenance(self) -> "UsageAttributionRecord":
        if self.usage_source == "provider_reported":
            if not self.provider or not self.provider_request_id_hash:
                raise ValueError("provider_reported usage requires provider request identity")
        if self.estimated_cost_usd is not None and (
            not self.pricing_source or not self.pricing_version
        ):
            raise ValueError("estimated cost requires pricing source and version")
        if self.observed_cost_usd is not None and self.estimated_cost_usd is not None:
            raise ValueError("observed and estimated cost cannot share one record")
        components = [
            value
            for value in (self.input_tokens, self.output_tokens, self.reasoning_tokens)
            if value is not None
        ]
        if self.total_tokens is not None and components and self.total_tokens < sum(components):
            raise ValueError("total tokens cannot be smaller than known components")
        has_usage = any(
            value is not None
            for value in (
                self.input_tokens,
                self.output_tokens,
                self.cached_input_tokens,
                self.reasoning_tokens,
                self.total_tokens,
                self.observed_cost_usd,
                self.estimated_cost_usd,
            )
        )
        if self.usage_source == "unavailable":
            if has_usage or self.confidence != "unavailable" or not self.unavailable_reason_code:
                raise ValueError("unavailable usage requires a reason and no numeric usage")
        elif self.unavailable_reason_code is not None:
            raise ValueError("available usage cannot include unavailableReasonCode")
        return self


class UsageRollup(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")
    input_tokens: int = Field(0, alias="inputTokens")
    output_tokens: int = Field(0, alias="outputTokens")
    total_tokens: int = Field(0, alias="totalTokens")
    observed_cost_usd: Decimal = Field(Decimal("0"), alias="observedCostUsd")
    estimated_cost_usd: Decimal = Field(Decimal("0"), alias="estimatedCostUsd")
    unavailable_requests: int = Field(0, alias="unavailableRequests")


def roll_up_usage(records: list[UsageAttributionRecord]) -> UsageRollup:
    """Deduplicate events and prefer reported evidence over an estimate per request."""
    by_event = {record.usage_event_id: record for record in records}
    by_request: dict[str, UsageAttributionRecord] = {}
    for record in by_event.values():
        key = record.provider_request_id_hash or record.usage_event_id
        previous = by_request.get(key)
        if previous is None or (
            previous.usage_source == "moonmind_estimated"
            and record.usage_source in {"provider_reported", "bridge_reported"}
        ):
            by_request[key] = record
    return UsageRollup(
        inputTokens=sum(record.input_tokens or 0 for record in by_request.values()),
        outputTokens=sum(record.output_tokens or 0 for record in by_request.values()),
        totalTokens=sum(record.total_tokens or 0 for record in by_request.values()),
        observedCostUsd=sum(
            (record.observed_cost_usd or Decimal("0") for record in by_request.values()),
            Decimal("0"),
        ),
        estimatedCostUsd=sum(
            (record.estimated_cost_usd or Decimal("0") for record in by_request.values()),
            Decimal("0"),
        ),
        unavailableRequests=sum(
            record.usage_source == "unavailable" for record in by_request.values()
        ),
    )
