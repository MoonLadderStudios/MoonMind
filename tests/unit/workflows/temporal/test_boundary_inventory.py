"""Unit tests for the deterministic Temporal boundary inventory."""

from __future__ import annotations

from moonmind.workflows.temporal.boundary_inventory import (
    get_temporal_boundary_inventory,
    iter_temporal_boundary_contracts,
)


def test_inventory_preserves_mm327_tool_source() -> None:
    inventory = get_temporal_boundary_inventory()

    assert inventory.source_issue_key == "MM-327"
    assert inventory.board_scope == "TOOL"
    assert inventory.contracts == iter_temporal_boundary_contracts()


def test_inventory_accessors_return_defensive_copies() -> None:
    inventory = get_temporal_boundary_inventory()
    inventory.source_issue_key = "MUTATED"

    contracts = iter_temporal_boundary_contracts()
    contracts[0].name = "mutated.activity"

    assert get_temporal_boundary_inventory().source_issue_key == "MM-327"
    assert iter_temporal_boundary_contracts()[0].name == "artifact.read"


def test_inventory_covers_required_boundary_kinds() -> None:
    kinds = {contract.kind for contract in iter_temporal_boundary_contracts()}

    assert {
        "activity",
        "workflow",
        "signal",
        "update",
        "query",
        "continue_as_new",
    }.issubset(kinds)


def test_inventory_entries_have_request_models_and_source_coverage() -> None:
    for contract in iter_temporal_boundary_contracts():
        assert contract.request_model.name
        assert contract.schema_home
        assert contract.coverage_ids
        if contract.status != "modeled" or contract.response_model is None:
            assert contract.rationale


def test_inventory_includes_representative_temporal_contracts() -> None:
    by_name = {
        (contract.kind, contract.name): contract
        for contract in iter_temporal_boundary_contracts()
    }

    assert by_name[("activity", "artifact.read")].response_model is not None
    assert by_name[("workflow", "MoonMind.AgentSession")].request_model.name == (
        "CodexManagedSessionWorkflowInput"
    )
    assert by_name[("update", "SendFollowUp")].request_model.name == (
        "CodexManagedSessionSendFollowUpRequest"
    )
    assert by_name[("signal", "DependencyResolved")].request_model.name == (
        "DependencyResolvedSignalPayload"
    )
    assert by_name[("query", "get_status")].response_model is not None
    assert by_name[("continue_as_new", "MoonMind.AgentSession")].request_model.name == (
        "CodexManagedSessionWorkflowInput"
    )
