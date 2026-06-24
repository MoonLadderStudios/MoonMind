"""Boundary tests for the ``resilience.compile_policy`` activity (MM-880).

These exercise the real worker/activity invocation shape: the handler method as
bound onto the activity implementation, the catalog/runtime registration, and
fail-fast handling for unsupported or missing policy values.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from temporalio.exceptions import ApplicationError

from moonmind.schemas.resilience_policy_models import (
    RESILIENCE_POLICY_CONTENT_TYPE,
    ResiliencePolicyEnvelope,
)
from moonmind.workflows.temporal.activity_catalog import (
    ARTIFACTS_FLEET,
    ARTIFACTS_TASK_QUEUE,
    TemporalActivityCatalog,
    TemporalActivityDefinition,
    TemporalActivityRetries,
    TemporalActivityTimeouts,
    TemporalWorkerFleet,
    build_default_activity_catalog,
)
from moonmind.workflows.temporal.activity_runtime import (
    _ACTIVITY_HANDLER_ATTRS,
    build_activity_bindings,
)
from moonmind.workflows.temporal.artifacts import TemporalArtifactActivities

pytestmark = [pytest.mark.asyncio]


def _activities() -> TemporalArtifactActivities:
    # The compile handler does not touch the artifact service, so a mock is fine.
    return TemporalArtifactActivities(AsyncMock())


def _request(**overrides) -> dict:
    request = {
        "workflowId": "wf-1",
        "runId": "run-1",
        "compiledAt": "2026-06-23T12:00:00+00:00",
        "runtimeId": "codex_cli",
        "model": "claude-opus-4-8",
        "effort": "high",
        "providerProfileId": "prof-1",
        "cooldownAfter429Seconds": 600,
        "rateLimitPolicy": {"strategy": "slot_cooldown"},
    }
    request.update(overrides)
    return request


async def test_compile_policy_returns_versioned_envelope() -> None:
    out = await _activities().resilience_compile_policy(_request())

    assert out["schemaVersion"] == "v1"
    assert out["contentType"] == RESILIENCE_POLICY_CONTENT_TYPE
    assert out["policyId"].startswith("resilience-policy-")
    # The activity output is itself a valid, digest-checked envelope.
    envelope = ResiliencePolicyEnvelope.model_validate(out)
    assert envelope.workflow_id == "wf-1"
    assert envelope.run_id == "run-1"
    assert envelope.provider_cooldown.cooldown_after_429_seconds == 600
    assert envelope.provider_cooldown.rate_limit_policy == {
        "strategy": "slot_cooldown"
    }
    assert envelope.cost_attribution.model == "claude-opus-4-8"
    # Required dimensions are present.
    for section in (
        "attempts",
        "timeouts",
        "providerCooldown",
        "checkpoints",
        "idempotency",
        "outboundScanning",
        "observability",
        "costAttribution",
    ):
        assert section in out


async def test_compile_policy_captures_self_heal_config_from_env(monkeypatch) -> None:
    monkeypatch.setenv("STEP_MAX_ATTEMPTS", "7")
    monkeypatch.setenv("STEP_TIMEOUT_SECONDS", "1234")

    out = await _activities().resilience_compile_policy(_request())

    # Proves the policy values are captured once, at compile time, from the
    # worker's resolved SelfHealConfig (not inferred later).
    assert out["attempts"]["stepMaxAttempts"] == 7
    assert out["timeouts"]["stepTimeoutSeconds"] == 1234


async def test_compile_policy_defaults_cooldown_when_absent() -> None:
    request = _request()
    request.pop("cooldownAfter429Seconds")

    out = await _activities().resilience_compile_policy(request)

    # Falls back to the provider-profile contract default rather than failing or
    # inferring from provider-manager state.
    assert out["providerCooldown"]["cooldownAfter429Seconds"] == 900


async def test_compile_policy_is_deterministic_for_same_inputs() -> None:
    activities = _activities()
    out_a = await activities.resilience_compile_policy(_request())
    out_b = await activities.resilience_compile_policy(
        _request(workflowId="wf-2", runId="run-2", compiledAt="2030-01-01T00:00:00+00:00")
    )

    # Identity/timestamp differ, governing values are identical.
    assert out_a["digest"] == out_b["digest"]
    assert out_a["policyId"] == out_b["policyId"]


async def test_compile_policy_fails_fast_on_invalid_input() -> None:
    with pytest.raises(ApplicationError) as exc_info:
        await _activities().resilience_compile_policy({"policyVersion": 0})

    assert exc_info.value.non_retryable is True
    assert exc_info.value.type == "INVALID_INPUT"


async def test_compile_policy_fails_fast_on_invalid_compiled_at() -> None:
    with pytest.raises(ApplicationError) as exc_info:
        await _activities().resilience_compile_policy(
            _request(compiledAt="not-a-timestamp")
        )

    assert exc_info.value.non_retryable is True
    assert exc_info.value.type == "INVALID_INPUT"


async def test_compile_policy_fails_fast_on_secret_like_rate_limit_policy() -> None:
    with pytest.raises(ApplicationError) as exc_info:
        await _activities().resilience_compile_policy(
            _request(rateLimitPolicy={"token": "raw-token"})
        )

    assert exc_info.value.non_retryable is True
    assert exc_info.value.type == "INVALID_INPUT"


def test_activity_is_registered_in_default_catalog() -> None:
    catalog = build_default_activity_catalog()
    by_type = {a.activity_type: a for a in catalog.activities}

    assert "resilience.compile_policy" in by_type
    definition = by_type["resilience.compile_policy"]
    assert definition.fleet == ARTIFACTS_FLEET

    # Runtime binding metadata must resolve to the artifacts handler.
    assert _ACTIVITY_HANDLER_ATTRS["resilience.compile_policy"] == (
        "artifacts",
        "resilience_compile_policy",
    )
    assert hasattr(TemporalArtifactActivities, "resilience_compile_policy")


def test_activity_binds_to_real_handler() -> None:
    catalog = TemporalActivityCatalog(
        activities=(
            TemporalActivityDefinition(
                "resilience.compile_policy",
                "execution",
                "artifacts",
                ARTIFACTS_TASK_QUEUE,
                ARTIFACTS_FLEET,
                TemporalActivityTimeouts(30, 60),
                TemporalActivityRetries(3, 30),
            ),
        ),
        fleets=(
            TemporalWorkerFleet(
                ARTIFACTS_FLEET,
                (ARTIFACTS_TASK_QUEUE,),
                ("artifacts",),
                ("artifact_store",),
                "test",
                ("resilience.compile_policy",),
            ),
        ),
    )

    bindings = {
        binding.activity_type: binding
        for binding in build_activity_bindings(
            catalog,
            artifact_activities=_activities(),
            fleets=["artifacts"],
        )
    }

    binding = bindings["resilience.compile_policy"]
    assert binding.fleet == ARTIFACTS_FLEET
    assert binding.task_queue == ARTIFACTS_TASK_QUEUE
    assert (
        binding.handler.__temporal_activity_definition.name
        == "resilience.compile_policy"
    )
