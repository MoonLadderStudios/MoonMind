"""Deterministic preset selection for goal-only task submissions."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Mapping

_JIRA_ISSUE_KEY_PATTERN = re.compile(r"\b([A-Z][A-Z0-9]+-\d+)\b")


@dataclass(frozen=True, slots=True)
class GoalPresetSchedule:
    """Preset selected from a plain goal before task-template expansion."""

    goal: str
    slug: str
    version: str
    inputs: dict[str, Any]
    reason: str
    issue_key: str | None = None


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _first_text(*values: Any) -> str:
    for value in values:
        candidate = _text(value)
        if candidate:
            return candidate
    return ""


def _contains_any(value: str, needles: tuple[str, ...]) -> bool:
    return any(needle in value for needle in needles)


def _jira_project_key(goal: str) -> str:
    match = re.search(r"\bproject\s+([A-Z][A-Z0-9]+)\b", goal, flags=re.IGNORECASE)
    return match.group(1).upper() if match else "MM"


def _issue_key(goal: str) -> str | None:
    match = _JIRA_ISSUE_KEY_PATTERN.search(goal)
    return match.group(1).upper() if match else None


def goal_from_payloads(
    *,
    task_payload: Mapping[str, Any],
    input_payload: Mapping[str, Any] | None = None,
    parameter_payload: Mapping[str, Any] | None = None,
) -> str:
    """Return the explicit goal text from supported task payload locations."""

    return _first_text(
        task_payload.get("goal"),
        task_payload.get("objective"),
        (input_payload or {}).get("goal"),
        (input_payload or {}).get("objective"),
        (parameter_payload or {}).get("goal"),
        (parameter_payload or {}).get("objective"),
    )


def task_is_already_authored(task_payload: Mapping[str, Any]) -> bool:
    """Return whether the task already carries an explicit execution shape."""

    if isinstance(task_payload.get("steps"), list) and task_payload.get("steps"):
        return True
    if isinstance(task_payload.get("plan"), list) and task_payload.get("plan"):
        return True
    if isinstance(task_payload.get("taskTemplate"), Mapping):
        return True
    if isinstance(task_payload.get("task_template"), Mapping):
        return True
    if isinstance(task_payload.get("tool"), Mapping):
        return True
    if isinstance(task_payload.get("skill"), Mapping):
        return True
    return False


def schedule_preset_from_goal(goal: str) -> GoalPresetSchedule | None:
    """Select a seeded task preset from a plain goal.

    The selector is intentionally deterministic and conservative: it uses
    stable Jira-key and task-shape signals, then falls back to MoonSpec
    orchestration for a general implementation goal.
    """

    normalized_goal = _text(goal)
    if not normalized_goal:
        return None

    lowered = normalized_goal.lower()
    issue_key = _issue_key(normalized_goal)
    wants_breakdown = _contains_any(
        lowered,
        ("break down", "breakdown", "split ", "stories", "story candidates"),
    )
    wants_implementation = _contains_any(
        lowered,
        ("implement", "complete", "fix", "build", "deliver", "code"),
    )
    wants_orchestration = _contains_any(
        lowered,
        ("orchestrate", "moonspec", "moon spec", "spec-driven"),
    )

    if issue_key:
        slug = (
            "jira-orchestrate"
            if wants_orchestration and not wants_implementation
            else "jira-implement"
        )
        return GoalPresetSchedule(
            goal=normalized_goal,
            slug=slug,
            version="1.0.0",
            issue_key=issue_key,
            inputs={
                "jira_issue_key": issue_key,
                "source_design_path": "",
                "constraints": "",
            },
            reason="jira_issue_goal",
        )

    if wants_breakdown:
        return GoalPresetSchedule(
            goal=normalized_goal,
            slug="jira-breakdown-orchestrate",
            version="1.0.0",
            inputs={
                "feature_request": normalized_goal,
                "jira_project_key": _jira_project_key(normalized_goal),
                "jira_issue_type": "Story",
                "jira_board_id": "",
                "jira_dependency_mode": "linear_blocker_chain",
                "publish_mode": "pr_with_merge_automation",
                "source_issue_key": "",
            },
            reason="story_breakdown_goal",
        )

    return GoalPresetSchedule(
        goal=normalized_goal,
        slug="moonspec-orchestrate",
        version="1.0.0",
        inputs={
            "feature_request": normalized_goal,
            "source_design_path": "",
            "constraints": "",
        },
        reason="implementation_goal",
    )


__all__ = [
    "GoalPresetSchedule",
    "goal_from_payloads",
    "schedule_preset_from_goal",
    "task_is_already_authored",
]
