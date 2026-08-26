"""Integration-style contract checks for the Temporal boundary inventory."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from moonmind.schemas.temporal_models import (
    SUPPORTED_SIGNAL_NAMES,
    SUPPORTED_UPDATE_NAMES,
)
from moonmind.workflows.temporal.activity_catalog import (
    build_default_activity_catalog,
)
from moonmind.workflows.temporal.boundary_inventory import (
    iter_temporal_boundary_contracts,
)

pytestmark = [pytest.mark.integration, pytest.mark.integration_ci]


def test_literal_workflow_activity_calls_exist_in_default_catalog() -> None:
    """Derive routed calls from workflow source instead of a hand-kept list."""
    workflow_root = (
        Path(__file__).resolve().parents[3]
        / "moonmind"
        / "workflows"
        / "temporal"
        / "workflows"
    )
    routed_calls: dict[str, set[str]] = {}
    for workflow_path in workflow_root.glob("*.py"):
        tree = ast.parse(workflow_path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(
                node.func, ast.Attribute
            ):
                continue
            if node.func.attr != "_execute_routed_activity" or not node.args:
                continue
            activity_name = node.args[0]
            if not isinstance(activity_name, ast.Constant) or not isinstance(
                activity_name.value, str
            ):
                continue
            routed_calls.setdefault(activity_name.value, set()).add(workflow_path.name)

    assert routed_calls, "no literal routed workflow activity calls were discovered"
    catalog = build_default_activity_catalog()
    for activity_name, source_files in sorted(routed_calls.items()):
        route = catalog.resolve_activity(activity_name)
        assert route.activity_type == activity_name, sorted(source_files)


def test_inventory_activity_names_exist_in_default_activity_catalog() -> None:
    catalog = build_default_activity_catalog()
    activity_names = {
        contract.name
        for contract in iter_temporal_boundary_contracts()
        if contract.kind == "activity"
    }

    for activity_name in activity_names:
        route = catalog.resolve_activity(activity_name)
        assert route.activity_type == activity_name


def test_inventory_workflow_message_names_match_known_constants() -> None:
    signal_names = {
        contract.name
        for contract in iter_temporal_boundary_contracts()
        if contract.kind == "signal" and contract.owner == "MoonMind.UserWorkflow"
    }
    update_names = {
        contract.name
        for contract in iter_temporal_boundary_contracts()
        if contract.kind == "update" and contract.owner == "MoonMind.UserWorkflow"
    }

    assert signal_names <= set(SUPPORTED_SIGNAL_NAMES)
    assert update_names <= set(SUPPORTED_UPDATE_NAMES)

def test_inventory_preserves_existing_temporal_names() -> None:
    names = {(contract.kind, contract.owner, contract.name) for contract in iter_temporal_boundary_contracts()}

    assert ("workflow", "MoonMind.AgentSession", "MoonMind.AgentSession") in names
    assert ("continue_as_new", "MoonMind.AgentSession", "MoonMind.AgentSession") in names
    assert ("activity", "artifact", "artifact.read") in names
    assert ("query", "MoonMind.AgentSession", "get_status") in names
    assert ("update", "MoonMind.AgentSession", "SendFollowUp") in names
    assert ("signal", "MoonMind.UserWorkflow", "DependencyResolved") in names
