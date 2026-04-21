"""Unit tests for the Temporal activity catalog and routing topology."""

from __future__ import annotations

import pytest

from moonmind.workflows.skills.skill_plan_contracts import parse_skill_definition
from moonmind.workflows.temporal.activity_catalog import (
    ARTIFACTS_FLEET,
    ARTIFACTS_TASK_QUEUE,
    INTEGRATIONS_FLEET,
    INTEGRATIONS_TASK_QUEUE,
    LLM_FLEET,
    LLM_TASK_QUEUE,
    SANDBOX_FLEET,
    SANDBOX_TASK_QUEUE,
    AGENT_RUNTIME_FLEET,
    AGENT_RUNTIME_TASK_QUEUE,
    TemporalActivityCatalogError,
    build_default_activity_catalog,
    manifest_ingest_activity_routes,
)


def _skill_definition(
    *,
    activity_type: str = "mm.skill.execute",
    capabilities: list[str] | None = None,
    start_to_close_seconds: int = 120,
    schedule_to_close_seconds: int = 300,
    binding_reason: str | None = None,
) -> object:
    return parse_skill_definition(
        {
            "name": "test.skill",
            "version": "1.0.0",
            "description": "Test skill",
            "inputs": {"schema": {"type": "object", "properties": {}}},
            "outputs": {"schema": {"type": "object", "properties": {}}},
            "executor": {
                "activity_type": activity_type,
                "selector": {"mode": "by_capability"},
                **(
                    {"binding_reason": binding_reason}
                    if binding_reason is not None
                    else {}
                ),
            },
            "requirements": {"capabilities": capabilities or ["llm"]},
            "policies": {
                "timeouts": {
                    "start_to_close_seconds": start_to_close_seconds,
                    "schedule_to_close_seconds": schedule_to_close_seconds,
                },
                "retries": {"max_attempts": 2, "non_retryable_error_codes": []},
            },
        }
    )


def test_default_catalog_exposes_canonical_queues_and_fleets():
    catalog = build_default_activity_catalog()

    assert catalog.resolve_activity("artifact.read").task_queue == ARTIFACTS_TASK_QUEUE
    assert catalog.resolve_activity("plan.generate").task_queue == LLM_TASK_QUEUE
    assert (
        catalog.resolve_activity("sandbox.checkout_repo").task_queue
        == SANDBOX_TASK_QUEUE
    )
    assert (
        catalog.resolve_activity("sandbox.apply_patch").task_queue == SANDBOX_TASK_QUEUE
    )
    assert (
        catalog.resolve_activity("sandbox.run_command").task_queue == SANDBOX_TASK_QUEUE
    )
    assert (
        catalog.resolve_activity("sandbox.run_tests").task_queue == SANDBOX_TASK_QUEUE
    )
    assert (
        catalog.resolve_activity("integration.jules.start").task_queue
        == INTEGRATIONS_TASK_QUEUE
    )
    assert (
        catalog.resolve_activity("workload.run").task_queue
        == AGENT_RUNTIME_TASK_QUEUE
    )
    assert catalog.resolve_activity("workload.run").fleet == AGENT_RUNTIME_FLEET
    assert catalog.resolve_activity("workload.run").capability_class == "docker_workload"
    assert (
        catalog.resolve_activity("oauth_session.start_auth_runner").task_queue
        == AGENT_RUNTIME_TASK_QUEUE
    )
    assert catalog.resolve_activity("oauth_session.start_auth_runner").fleet == AGENT_RUNTIME_FLEET

    fleets = {fleet.fleet: fleet for fleet in catalog.fleets}
    assert fleets[ARTIFACTS_FLEET].task_queues == (ARTIFACTS_TASK_QUEUE,)
    assert fleets[LLM_FLEET].task_queues == (LLM_TASK_QUEUE,)
    assert fleets[SANDBOX_FLEET].task_queues == (SANDBOX_TASK_QUEUE,)
    assert fleets[INTEGRATIONS_FLEET].task_queues == (INTEGRATIONS_TASK_QUEUE,)
    assert "docker_workload" in fleets[AGENT_RUNTIME_FLEET].capabilities


def test_resolve_skill_uses_capability_routing_for_mm_skill_execute():
    catalog = build_default_activity_catalog()

    llm_route = catalog.resolve_skill(_skill_definition(capabilities=["llm"]))
    sandbox_route = catalog.resolve_skill(_skill_definition(capabilities=["sandbox"]))
    docker_workload_route = catalog.resolve_skill(
        _skill_definition(capabilities=["docker_workload"])
    )
    integration_route = catalog.resolve_skill(
        _skill_definition(capabilities=["integration:jules"])
    )
    artifact_route = catalog.resolve_skill(
        _skill_definition(capabilities=["artifacts"])
    )

    assert llm_route.fleet == LLM_FLEET
    assert llm_route.task_queue == LLM_TASK_QUEUE
    assert sandbox_route.fleet == SANDBOX_FLEET
    assert sandbox_route.task_queue == SANDBOX_TASK_QUEUE
    assert docker_workload_route.fleet == AGENT_RUNTIME_FLEET
    assert docker_workload_route.task_queue == AGENT_RUNTIME_TASK_QUEUE
    assert docker_workload_route.capability_class == "docker_workload"
    assert integration_route.fleet == INTEGRATIONS_FLEET
    assert integration_route.task_queue == INTEGRATIONS_TASK_QUEUE
    assert artifact_route.fleet == ARTIFACTS_FLEET
    assert artifact_route.task_queue == ARTIFACTS_TASK_QUEUE


def test_resolve_skill_preserves_skill_policy_timeouts():
    catalog = build_default_activity_catalog()
    route = catalog.resolve_skill(
        _skill_definition(
            capabilities=["sandbox"],
            start_to_close_seconds=42,
            schedule_to_close_seconds=420,
        )
    )

    assert route.task_queue == SANDBOX_TASK_QUEUE
    assert route.timeouts.start_to_close_seconds == 42
    assert route.timeouts.schedule_to_close_seconds == 420


def test_resolve_skill_rejects_incompatible_routing_capabilities():
    catalog = build_default_activity_catalog()

    with pytest.raises(TemporalActivityCatalogError, match="incompatible"):
        catalog.resolve_skill(_skill_definition(capabilities=["llm", "sandbox"]))


def test_resolve_explicit_activity_uses_catalog_binding():
    catalog = build_default_activity_catalog()
    route = catalog.resolve_skill(
        _skill_definition(
            activity_type="integration.jules.fetch_result",
            capabilities=["integration:jules"],
            binding_reason="specialized_credentials",
        )
    )

    assert route.activity_type == "integration.jules.fetch_result"
    assert route.fleet == INTEGRATIONS_FLEET
    assert route.task_queue == INTEGRATIONS_TASK_QUEUE


def test_resolve_explicit_activity_requires_binding_reason():
    catalog = build_default_activity_catalog()

    with pytest.raises(ValueError, match="binding_reason"):
        catalog.resolve_skill(
            _skill_definition(
                activity_type="integration.jules.fetch_result",
                capabilities=["integration:jules"],
            )
        )


def test_manifest_ingest_activity_routes_stay_at_activity_queue_boundaries():
    routes = manifest_ingest_activity_routes(build_default_activity_catalog())

    assert [route.activity_type for route in routes] == [
        "manifest.compile",
        "manifest.write_summary",
    ]
    assert [route.task_queue for route in routes] == [
        ARTIFACTS_TASK_QUEUE,
        ARTIFACTS_TASK_QUEUE,
    ]
