"""Shared routing predicate for automatic issue implementation occurrences."""

from collections.abc import Mapping


def is_automatic_issue_search(parameters: Mapping) -> bool:
    task = parameters.get("workflow") or parameters.get("task") or {}
    if not isinstance(task, Mapping):
        return False
    return any(
        isinstance(step, Mapping)
        and isinstance(step.get("tool"), Mapping)
        and step["tool"].get("id") == "github.load_issue_preset_brief"
        and "issueSearch" in (step["tool"].get("inputs") or {})
        for step in task.get("steps") or []
    )
