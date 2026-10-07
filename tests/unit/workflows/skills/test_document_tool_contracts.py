"""The registered document discovery tool's normal plan contract."""

from __future__ import annotations

import pytest

from moonmind.workflows.skills.artifact_store import InMemoryArtifactStore
from moonmind.workflows.skills.plan_validation import (
    PlanValidationError,
    validate_plan_payload,
)
from moonmind.workflows.skills.tool_definitions import default_registry_tool_payload
from moonmind.workflows.skills.tool_plan_contracts import parse_tool_definition
from moonmind.workflows.skills.tool_registry import (
    create_registry_snapshot,
    parse_tool_registry,
)
from moonmind.workflows.temporal.activity_catalog import build_default_activity_catalog
from moonmind.workflows.temporal.activity_runtime import _default_skill_registry_payload
from moonmind.workflows.temporal.story_output_tools import discover_documents
from moonmind.workflows.temporal.worker_runtime import _build_runtime_planner


def _admitted_plan(inputs, *, with_consumer=False):
    steps = [
        {
            "id": "discover",
            "type": "tool",
            "tool": {"id": "document.discover", "inputs": inputs},
        }
    ]
    if with_consumer:
        steps.append(
            {
                "id": "consume",
                "type": "tool",
                "tool": {
                    "id": "story.create_document_update_tasks",
                    "inputs": {
                        "documentPaths": {
                            "ref": {
                                "node": "discover",
                                "json_pointer": "/outputs/documentPaths",
                            }
                        }
                    },
                },
            }
        )
    parameters = {
        "workflow": {
            "instructions": "Discover the local documentation scope.",
            "steps": steps,
        }
    }
    snapshot = create_registry_snapshot(
        skills=parse_tool_registry(
            _default_skill_registry_payload(parameters=parameters)
        ),
        artifact_store=InMemoryArtifactStore(),
    )
    plan = _build_runtime_planner()(None, parameters, snapshot)
    return plan, snapshot


def test_document_discovery_definition_describes_its_registered_result():
    definition = parse_tool_definition(
        default_registry_tool_payload(name="document.discover")
    )
    route = build_default_activity_catalog().resolve_skill(definition)

    assert route.activity_type == "mm.tool.execute"
    assert definition.required_capabilities == ("sandbox",)
    assert definition.input_schema["properties"]["directory"] == {"type": "string"}
    assert definition.input_schema["properties"]["path"] == {"type": "string"}
    assert definition.output_schema["properties"]["documentPaths"] == {
        "type": "array",
        "items": {"type": "string"},
    }
    assert definition.output_schema["properties"]["documentCount"] == {
        "type": "integer"
    }
    assert definition.output_schema["properties"]["error"] == {"type": "string"}


def test_normal_document_plan_admits_recorded_document_paths_without_integration():
    plan, snapshot = _admitted_plan(
        {"directory": "docs", "repoRoot": "/workspace/admitted"},
        with_consumer=True,
    )

    admitted = validate_plan_payload(payload=plan, registry_snapshot=snapshot)

    assert admitted.topological_order == ("discover", "consume")
    assert all(node.tool_type == "skill" for node in admitted.plan.nodes)
    assert admitted.plan.nodes[0].inputs == {
        "directory": "docs",
        "repoRoot": "/workspace/admitted",
    }
    assert admitted.plan.nodes[1].inputs["documentPaths"] == {
        "ref": {"node": "discover", "json_pointer": "/outputs/documentPaths"}
    }
    for node in admitted.plan.nodes:
        assert not {
            "runtime", "profileId", "providerProfile", "repositoryConnectionId"
        }.intersection(node.inputs)


@pytest.mark.parametrize(
    "inputs",
    [
        {"directory": 42},
        {"directory": "docs", "extensions": ".md"},
        {"path": "docs", "extensions": [".md", 42]},
    ],
)
def test_document_plan_rejects_malformed_discovery_inputs(inputs):
    plan, snapshot = _admitted_plan(inputs)

    with pytest.raises(PlanValidationError, match="must be"):
        validate_plan_payload(payload=plan, registry_snapshot=snapshot)


@pytest.mark.parametrize("inputs", [{}, {"path": "docs"}])
def test_document_plan_preserves_path_alias_and_missing_input_business_failure(inputs):
    plan, snapshot = _admitted_plan(inputs)

    admitted = validate_plan_payload(payload=plan, registry_snapshot=snapshot)

    assert admitted.plan.nodes[0].inputs == inputs


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "inputs",
    [
        {"directory": 42},
        {"directory": "docs", "extensions": ".md"},
        {"directory": "docs", "extensions": [".md", 42]},
    ],
)
async def test_document_handler_rejects_malformed_resolved_inputs_before_reading(
    inputs, monkeypatch
):
    def unexpected_read(**kwargs):
        pytest.fail("Malformed inputs must not reach workspace or repository discovery")

    monkeypatch.setattr(
        "moonmind.workflows.temporal.story_output_tools._resolve_local_document_root",
        unexpected_read,
    )

    with pytest.raises(PlanValidationError, match="must be"):
        await discover_documents(inputs)
