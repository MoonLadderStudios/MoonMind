"""Admission's account requirement follows the runtime planner's node types."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from moonmind.workflows.executions import execution_contract
from moonmind.workflows.temporal.worker_runtime import _build_runtime_planner


@pytest.mark.parametrize(
    ("task", "requires_agent"),
    [
        pytest.param(
            {"instructions": "Perform the requested work"}, True, id="instructions-only"
        ),
        pytest.param({"runtime": {"model": "chosen-model"}}, True, id="runtime-only"),
        pytest.param(
            {"container": {"image": "python:3.12"}},
            True,
            id="container-agent-workspace",
        ),
        pytest.param(
            {
                "steps": [
                    {
                        "type": "tool",
                        "tool": {"id": "github.resolve_pull_request_target"},
                    }
                ]
            },
            False,
            id="single-native-tool",
        ),
        pytest.param(
            {"steps": [{"type": "tool", "tool": {"id": "container.run"}}]},
            False,
            id="single-container-tool",
        ),
        pytest.param(
            {
                "steps": [
                    {"type": "tool", "tool": {"id": "document.discover"}},
                    {"type": "tool", "tool": {"id": "container.run"}},
                ]
            },
            False,
            id="tool-and-container-only",
        ),
        pytest.param(
            {
                "steps": [
                    {"type": "tool", "tool": {"id": "document.discover"}},
                    {"type": "skill", "skill": {"name": "custom-skill"}},
                ]
            },
            True,
            id="mixed-tool-and-agent",
        ),
        pytest.param(
            {"steps": [{"type": "skill", "skill": {"name": "custom-skill"}}]},
            True,
            id="explicit-agent-skill",
        ),
        pytest.param(
            {"steps": [{"tool": {"id": "document.discover"}}]},
            True,
            id="untyped-single-step-retains-planner-fallback",
        ),
        pytest.param(
            {
                "steps": [
                    {"tool": {"id": "document.discover"}},
                    {"instructions": "Continue"},
                ]
            },
            True,
            id="untyped-steps-are-agents",
        ),
        pytest.param(
            {
                "steps": [
                    {"type": "skill", "skill": {"name": "story.create_github_issues"}}
                ]
            },
            False,
            id="story-tool-in-legacy-skill-step",
        ),
        pytest.param(
            {"tool": {"id": "story.create_github_issues"}},
            False,
            id="top-level-story-tool",
        ),
        pytest.param(
            {"skill": {"name": "story.create_document_update_tasks"}},
            False,
            id="top-level-legacy-story-tool",
        ),
        pytest.param(
            {"tool": {"id": "story.create_github_issues"}, "stepCount": "2"},
            True,
            id="story-tool-step-count-expands-to-agents",
        ),
        pytest.param(
            {"plan": [{"tool": {"type": "skill", "name": "document.discover"}}]},
            False,
            id="explicit-tool-plan",
        ),
        pytest.param(
            {"plan": [{"tool": {"kind": " container ", "name": "container.run"}}]},
            False,
            id="explicit-container-plan-kind",
        ),
        pytest.param(
            {
                "plan": [
                    {"tool": {"type": "skill", "name": "document.discover"}},
                    {"tool": {"type": "agent_runtime", "name": "codex_cli"}},
                ]
            },
            True,
            id="explicit-mixed-plan",
        ),
        pytest.param(
            {"plan": [{"tool": {"name": "custom-skill"}}]},
            True,
            id="explicit-plan-omitted-type-is-agent",
        ),
        pytest.param(
            {"plan": [{"skill": {"type": "skill", "name": "custom-skill"}}]},
            True,
            id="explicit-plan-legacy-skill-is-agent",
        ),
        pytest.param(
            {"plan": [{"skill": {"name": "story.create_github_issues"}}]},
            True,
            id="explicit-plan-legacy-story-skill-is-agent",
        ),
        pytest.param(
            {
                "plan": [{"tool": {"type": "skill", "name": "document.discover"}}],
                "steps": [{"type": "skill", "skill": {"name": "custom-skill"}}],
            },
            False,
            id="explicit-plan-takes-precedence-over-steps",
        ),
        pytest.param(
            {
                "plan": [],
                "steps": [{"type": "tool", "tool": {"id": "document.discover"}}],
            },
            False,
            id="empty-plan-falls-back-to-steps",
        ),
    ],
)
def test_workflow_agent_requirement_matches_planner(task, requires_agent) -> None:
    original_task = deepcopy(task)
    plan = _build_runtime_planner()(
        inputs={"workflow": task},
        parameters={
            "instructions": "Execute the declared workflow",
            "publishMode": "none",
        },
        snapshot=SimpleNamespace(digest="reg:test", artifact_ref="art_registry_test"),
    )
    actual_requires_agent = any(
        node["tool"]["type"] == "agent_runtime" for node in plan["nodes"]
    )
    assert actual_requires_agent is requires_agent
    assert execution_contract.workflow_requires_agent(task) is actual_requires_agent
    assert task == original_task


@pytest.mark.parametrize("task_count", [None, 0, 1, "invalid"])
def test_workflow_agent_requirement_uses_parameter_step_count(task_count) -> None:
    task = {"tool": {"id": "story.create_github_issues"}}
    if task_count is not None:
        task["stepCount"] = task_count
    parameters = {"stepCount": 2, "instructions": "Run story output"}
    plan = _build_runtime_planner()(
        inputs={"workflow": task},
        parameters=parameters,
        snapshot=SimpleNamespace(digest="reg:test", artifact_ref="art_registry_test"),
    )
    requires_agent = any(
        node["tool"]["type"] == "agent_runtime" for node in plan["nodes"]
    )
    assert (
        execution_contract.workflow_requires_agent(task, parameters) is requires_agent
    )


@pytest.mark.parametrize(
    "pending_preset",
    [
        {"taskTemplate": {"slug": "custom-preset"}},
        {"task_template": {"slug": "custom-preset"}},
        {
            "steps": [
                {"kind": "include", "slug": "custom-preset"},
                {"type": "tool", "tool": {"id": "document.discover"}},
            ]
        },
        {
            "includes": [{"slug": "custom-preset"}],
            "steps": [{"type": "tool", "tool": {"id": "document.discover"}}],
        },
    ],
)
def test_workflow_agent_requirement_keeps_unexpanded_presets_conservative(
    pending_preset,
) -> None:
    task = {"tool": {"id": "story.create_github_issues"}, **pending_preset}
    assert execution_contract.workflow_requires_agent(task) is True


def test_workflow_agent_requirement_matches_deduplicated_single_step() -> None:
    step = {"id": "same", "tool": {"id": "story.create_github_issues"}}
    task = {"instructions": "Run output", "steps": [step, deepcopy(step)]}
    plan = _build_runtime_planner()(
        inputs={"workflow": task},
        parameters={},
        snapshot=SimpleNamespace(digest="reg:test", artifact_ref="art_registry_test"),
    )
    assert len(plan["nodes"]) == 1
    assert plan["nodes"][0]["tool"]["type"] == "agent_runtime"
    assert execution_contract.workflow_requires_agent(task) is True


def test_workflow_agent_requirement_leaves_invalid_steps_to_validation() -> None:
    task = {
        "instructions": "Run tools",
        "steps": [
            {"id": "same", "type": "tool", "tool": {"id": "document.discover"}},
            {"id": "same", "type": "tool", "tool": {"id": "container.run"}},
        ],
    }
    assert execution_contract.workflow_requires_agent(task) is True
    with pytest.raises(RuntimeError, match="duplicated with different payloads"):
        _build_runtime_planner()(
            inputs={"workflow": task},
            parameters={},
            snapshot=SimpleNamespace(
                digest="reg:test", artifact_ref="art_registry_test"
            ),
        )
