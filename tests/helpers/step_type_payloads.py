"""Reusable Step Type payload fixtures for MM-569 tests."""

from __future__ import annotations

from typing import Any


def tool_step(
    *,
    step_id: str = "fetch-issue",
    title: str = "Fetch issue",
    tool_id: str = "jira.get_issue",
    inputs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a valid explicit Tool step payload."""

    return {
        "id": step_id,
        "title": title,
        "type": "tool",
        "instructions": "Fetch the Jira issue.",
        "tool": {
            "id": tool_id,
            "inputs": inputs or {"issueKey": "MM-569"},
            "requiredCapabilities": ["jira"],
        },
    }


def skill_step(
    *,
    step_id: str = "implement",
    title: str = "Implement story",
    skill_id: str = "moonspec-implement",
    args: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a valid explicit Skill step payload."""

    return {
        "id": step_id,
        "title": title,
        "type": "skill",
        "instructions": "Implement the selected MoonSpec story.",
        "skill": {
            "id": skill_id,
            "args": args or {"issueKey": "MM-569"},
            "requiredCapabilities": ["git"],
        },
    }


def preset_step(
    *,
    step_id: str = "run-preset",
    title: str = "Run preset",
    slug: str = "child-flow",
    version: str = "1.0.0",
    inputs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a valid draft Preset step payload."""

    return {
        "id": step_id,
        "title": title,
        "type": "preset",
        "instructions": "Apply the child preset.",
        "preset": {
            "slug": slug,
            "version": version,
            "inputs": inputs or {"issue_key": "MM-569"},
        },
    }


def legacy_skill_step(
    *,
    step_id: str = "legacy-skill",
    title: str = "Legacy skill",
) -> dict[str, Any]:
    """Return the legacy reader shape without an explicit type discriminator."""

    return {
        "id": step_id,
        "title": title,
        "instructions": "Run a legacy skill-shaped step.",
        "skill": {"id": "auto", "args": {"issueKey": "MM-569"}},
    }


def mixed_tool_skill_step() -> dict[str, Any]:
    """Return an invalid mixed Step Type payload."""

    step = tool_step()
    step["skill"] = {"id": "moonspec-implement", "args": {"issueKey": "MM-569"}}
    return step


def task_payload(*steps: dict[str, Any]) -> dict[str, Any]:
    """Wrap Step Type fixtures in a canonical task payload."""

    return {
        "repository": "moonmind/moonmind",
        "task": {
            "instructions": "Validate explicit Step Type payloads for MM-569.",
            "steps": list(steps),
        },
    }
