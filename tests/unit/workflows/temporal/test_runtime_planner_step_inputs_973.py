"""Instruction-only Steps retain their explicit authored inputs through planning."""

from types import SimpleNamespace

import pytest

from moonmind.workflows.temporal.worker_runtime import _build_runtime_planner


@pytest.mark.parametrize(
    "authored_inputs",
    [
        None,
        {},
        {
            "documentPaths": {
                "ref": {"node": "discover", "json_pointer": "/outputs/documentPaths"}
            }
        },
    ],
)
def test_runtime_planner_preserves_explicit_instruction_step_inputs_only(authored_inputs):
    consume = {
        "id": "consume",
        "type": "skill",
        "instructions": "Summarize the discovered documents.",
    }
    if authored_inputs is not None:
        consume["inputs"] = authored_inputs
    plan = _build_runtime_planner()(
        inputs={},
        parameters={
            "task": {
                "instructions": "Discover and summarize documents.",
                "inputs": {"parentOnly": "Do not inherit this stale input"},
                "steps": [
                    {
                        "id": "discover",
                        "type": "tool",
                        "tool": {
                            "id": "document.discover",
                            "inputs": {"directory": "docs"},
                        },
                    },
                    consume,
                ],
            }
        },
        snapshot=SimpleNamespace(
            digest="reg:sha256:test", artifact_ref="art_registry_123"
        ),
    )
    node = next(item for item in plan["nodes"] if item["id"] == "consume")
    assert node["tool"]["type"] == "agent_runtime"
    assert "selectedSkill" not in node["inputs"]
    assert "skill" not in node["inputs"]
    if authored_inputs is None:
        assert "inputs" not in node["inputs"]
    else:
        assert node["inputs"]["inputs"] == authored_inputs
        assert "parentOnly" not in node["inputs"]["inputs"]
