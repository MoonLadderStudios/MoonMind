"""Planning memory adapter for repo-scoped Beads work items."""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from moonmind.rag.context_pack import ContextItem


class PlanningAdapterError(RuntimeError):
    """Raised when planning memory cannot complete and fail-open is disabled."""


@dataclass(frozen=True, slots=True)
class PlanningFollowup:
    title: str
    description: str = ""
    issue_type: str = "task"
    priority: int | None = None


@dataclass(frozen=True, slots=True)
class BeadsPlanningAdapter:
    """Command adapter for the Beads (`bd`) git-backed planning substrate."""

    repo_root: Path
    command: str = "bd"
    timeout_seconds: float = 5.0

    def available(self) -> bool:
        return bool(shutil.which(self.command)) and (self.repo_root / ".beads").exists()

    def prefetch(self, planning_ref: str) -> ContextItem | None:
        issue_id = self._require_ref(planning_ref)
        if not self.available():
            return None

        issue = self._run_json("show", issue_id)
        ready = self._run_json("ready")
        text = self._format_context(issue=issue, ready=ready)
        if not text:
            return None
        return ContextItem(
            score=1.0,
            source=f"beads:{issue_id}",
            text=text,
            trust_class="planning",
            payload={
                "record_kind": "planning",
                "planning_ref": issue_id,
                "planning_source": "beads",
                "repo_root": str(self.repo_root),
            },
        )

    def claim(self, planning_ref: str, *, assignee: str | None = None) -> Mapping[str, Any]:
        issue_id = self._require_ref(planning_ref)
        args = ["update", issue_id, "--claim"]
        if assignee:
            args.extend(["--assignee", assignee])
        return self._run_json(*args)

    def close(self, planning_ref: str, *, reason: str) -> Mapping[str, Any]:
        issue_id = self._require_ref(planning_ref)
        args = ["close", issue_id]
        if reason.strip():
            args.extend(["--reason", reason.strip()])
        return self._run_json(*args)

    def create_followups(
        self,
        *,
        planning_ref: str,
        followups: Sequence[PlanningFollowup],
    ) -> list[Mapping[str, Any]]:
        parent_id = self._require_ref(planning_ref)
        created: list[Mapping[str, Any]] = []
        for followup in followups:
            title = followup.title.strip()
            if not title:
                continue
            args = ["create", title, "--type", followup.issue_type or "task"]
            if followup.description.strip():
                args.extend(["--description", followup.description.strip()])
            if followup.priority is not None:
                args.extend(["--priority", str(followup.priority)])
            args.extend(["--deps", f"discovered-from:{parent_id}"])
            created.append(self._run_json(*args))
        return created

    def _run_json(self, *args: str) -> Mapping[str, Any]:
        if not self.available():
            raise PlanningAdapterError("Beads is not available for this repository.")
        command = [self.command, "--no-daemon", "--json", *args]
        try:
            completed = subprocess.run(
                command,
                cwd=self.repo_root,
                check=True,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
            )
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            raise PlanningAdapterError(f"Beads command failed: {args[0]}") from exc
        raw = completed.stdout.strip() or "{}"
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise PlanningAdapterError("Beads command returned invalid JSON.") from exc
        if isinstance(parsed, Mapping):
            return parsed
        return {"items": parsed}

    @staticmethod
    def _require_ref(planning_ref: str) -> str:
        value = str(planning_ref or "").strip()
        if not value:
            raise PlanningAdapterError("Planning reference is required.")
        return value

    @staticmethod
    def _format_context(*, issue: Mapping[str, Any], ready: Mapping[str, Any]) -> str:
        item = _first_mapping(issue)
        if not item:
            return ""
        lines = [
            "Planning Memory (Beads)",
            f"id: {_value(item, 'id')}",
            f"title: {_value(item, 'title')}",
            f"status: {_value(item, 'status')}",
            f"priority: {_value(item, 'priority')}",
        ]
        description = _value(item, "description")
        if description:
            lines.append(f"description: {description}")
        dependencies = _sequence_value(item, "dependencies")
        if dependencies:
            lines.append(f"dependencies: {', '.join(dependencies[:8])}")
        dependents = _sequence_value(item, "dependents")
        if dependents:
            lines.append(f"dependents: {', '.join(dependents[:8])}")

        ready_items = _items(ready)
        if ready_items:
            lines.append("ready siblings:")
            for ready_item in ready_items[:5]:
                ready_id = _value(ready_item, "id")
                ready_title = _value(ready_item, "title")
                ready_status = _value(ready_item, "status")
                lines.append(f"- {ready_id}: {ready_title} ({ready_status})")
        return "\n".join(line for line in lines if line.strip())


def _first_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    if "items" in value:
        items = _items(value)
        return items[0] if items else {}
    return value


def _items(value: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    raw_items = value.get("items", value.get("issues", []))
    if isinstance(raw_items, list):
        return [item for item in raw_items if isinstance(item, Mapping)]
    return []


def _value(item: Mapping[str, Any], key: str) -> str:
    raw = item.get(key)
    if raw is None:
        return ""
    return str(raw).strip()


def _sequence_value(item: Mapping[str, Any], key: str) -> list[str]:
    raw = item.get(key)
    if not isinstance(raw, list):
        return []
    values: list[str] = []
    for entry in raw:
        if isinstance(entry, Mapping):
            entry_id = _value(entry, "id") or _value(entry, "target")
            if entry_id:
                values.append(entry_id)
        elif str(entry).strip():
            values.append(str(entry).strip())
    return values
