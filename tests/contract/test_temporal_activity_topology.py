"""Contract tests for the Temporal activity catalog and shared envelopes."""

from __future__ import annotations

import pytest

from moonmind.workflows.skills.skill_plan_contracts import (
    ContractValidationError,
    parse_skill_definition,
)
from moonmind.workflows.temporal import (
    build_activity_invocation_envelope,
    build_compact_activity_result,
    build_default_activity_catalog,
)


def test_temporal_activity_topology_contract_uses_canonical_v1_queue_set() -> None:
    catalog = build_default_activity_catalog()

    queues = {
        route.task_queue
        for route in (
            catalog.resolve_activity("artifact.create"),
            catalog.resolve_activity("plan.generate"),
            catalog.resolve_activity("sandbox.checkout_repo"),
            catalog.resolve_activity("integration.jules.start"),
        )
    }
    assert queues == {
        "mm.activity.artifacts",
        "mm.activity.llm",
        "mm.activity.sandbox",
        "mm.activity.integrations",
    }
    assert {fleet.task_queues[0] for fleet in catalog.fleets} == {
        "mm.workflow",
        "mm.activity.artifacts",
        "mm.activity.llm",
        "mm.activity.sandbox",
        "mm.activity.integrations",
        "mm.activity.agent_runtime",
    }


def test_shared_envelope_contract_requires_idempotency_for_side_effecting_inputs() -> (
    None
):
    with pytest.raises(ContractValidationError, match="idempotency_key"):
        build_activity_invocation_envelope(
            correlation_id="corr-1",
            side_effecting=True,
        )

    result = build_compact_activity_result(
        output_refs=["art_01HJ4M3Y7RM4C5S2P3Q8G6T7V9"],
        summary={"status": "ok"},
        diagnostics_ref="art_01HJ4M3Y7RM4C5S2P3Q8G6T7VA",
    )
    assert result.to_payload()["diagnostics_ref"] == "art_01HJ4M3Y7RM4C5S2P3Q8G6T7VA"


def test_explicit_skill_binding_requires_declared_operational_reason() -> None:
    with pytest.raises(ContractValidationError, match="binding_reason"):
        parse_skill_definition(
            {
                "name": "integration.fetch",
                "version": "1.0.0",
                "description": "Fetch integration output",
                "inputs": {"schema": {"type": "object", "properties": {}}},
                "outputs": {"schema": {"type": "object", "properties": {}}},
                "executor": {
                    "activity_type": "integration.jules.fetch_result",
                    "selector": {"mode": "by_capability"},
                },
                "requirements": {"capabilities": ["integration:jules"]},
                "policies": {
                    "timeouts": {
                        "start_to_close_seconds": 30,
                        "schedule_to_close_seconds": 60,
                    },
                    "retries": {"max_attempts": 1},
                },
            }
        )
