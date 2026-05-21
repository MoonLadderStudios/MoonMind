"""Terminology guardrails for workflow execution backend contracts."""

from __future__ import annotations

from pathlib import Path


def test_no_moonmind_owned_lifecycle_event_uses_task_prefix() -> None:
    """MoonMind lifecycle events use workflow_execution.* terminology."""

    production_roots = [
        Path("api_service"),
        Path("moonmind"),
    ]
    offenders: list[str] = []
    for root in production_roots:
        for path in root.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            for event_name in (
                "task.created",
                "task.updated",
                "task.completed",
                "task.failed",
                "task.canceled",
                "task.rerun_requested",
            ):
                if event_name in text:
                    offenders.append(f"{path}:{event_name}")

    assert offenders == []


def test_known_workflow_execution_events_are_allowed() -> None:
    allowed_events = {
        "workflow_execution.started",
        "workflow_execution.updated",
        "workflow_execution.completed",
        "workflow_execution.failed",
        "workflow_execution.canceled",
        "workflow_execution.new_run_requested",
    }

    assert all(event.startswith("workflow_execution.") for event in allowed_events)
